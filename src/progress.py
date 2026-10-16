"""Part 6: progress over time, from the saved trips, the journal and the profile history. Pure: no I/O."""
import re
from datetime import datetime, timedelta, timezone

from .live_stats import summarize_finds
from .plan_economy import prestige_requirements

STRETCH_GAP = 600   # seconds between trips beyond which the time is not fishing
# A run with no stop record whose journal was written this recently is taken as still going (one started from the
# command line has no running id here). Each trip writes several events, so a run that is going is well inside it.
LIVE_GAP = 300
PAID = re.compile(r'\bfor \$([\d,]+(?:\.\d+)?)')   # a reply's note naming what was charged: "bought 500 ... for $17,500"
RESULTS = ('acknowledged', 'resolved', 'abandoned')


def empty_index():
    return {'version': 1, 'files': [], 'last_at': None, 'days': {}}


def parse_time(text):
    """An ISO time as an aware datetime; a time without a zone is taken as UTC."""
    moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _day(moment, tz):
    return moment.astimezone(tz).date().isoformat()


def index_trips(index, records, tz=None):
    """The index with these trip records added by local day: trips, XP, chest money, chests (and by kind), charms,
    special fish, pets, the highest level reached and fishing time (gaps over STRETCH_GAP are breaks). A record
    already counted (by session id) is skipped. A day saved before chests were kept by kind gains them from here."""
    index = {**index, 'files': list(index['files']), 'days': {day: dict(v) for day, v in index['days'].items()}}
    seen = set(index['files'])
    last = parse_time(index['last_at']) if index.get('last_at') else None
    for record in sorted(records, key=lambda r: r['captured_at']):
        if record['session_id'] in seen:
            continue
        seen.add(record['session_id'])
        index['files'].append(record['session_id'])
        moment, tally = parse_time(record['captured_at']), record.get('tally') or {}
        day = index['days'].setdefault(_day(moment, tz), {'trips': 0, 'xp': 0, 'chest_money': 0, 'chests': 0,
                                                          'chest_kinds': {}, 'charms': 0, 'special_fish': 0,
                                                          'pets': 0, 'level': None, 'seconds': 0})
        finds = summarize_finds([tally])
        day['trips'] += tally.get('trips') or 0
        day['xp'] += tally.get('fishing_xp') or 0
        day['chest_money'] += tally.get('chest_money') or 0
        for key in ('chests', 'charms', 'special_fish', 'pets'):
            day[key] += sum(finds.get(key, {}).values())
        kinds = day['chest_kinds'] = dict(day['chest_kinds']) if isinstance(day.get('chest_kinds'), dict) else {}
        for kind, count in finds.get('chests', {}).items():
            kinds[kind] = kinds.get(kind, 0) + count
        if tally.get('level_ups'):
            day['level'] = max([day['level'] or 0, *tally['level_ups']])
        if last is not None and 0 < (moment - last).total_seconds() <= STRETCH_GAP:
            day['seconds'] += round((moment - last).total_seconds())
        last = moment if last is None or moment > last else last
    index['last_at'] = last.isoformat() if last else None
    return index


def _readable(events):
    """The events that are dicts with a readable time, oldest first; the rest are skipped."""
    kept = []
    for found in events or []:
        if not isinstance(found, dict) or not isinstance(found.get('at'), str):
            continue
        try:
            moment = parse_time(found['at'])
        except ValueError:
            continue
        kept.append((moment, found))
    kept.sort(key=lambda pair: pair[0])
    return [found for _, found in kept]


def _by(events, key):
    grouped = {}
    for found in events:
        if found.get(key):
            grouped.setdefault(found[key], []).append(found)
    return grouped


def _run_row(run_id, found):
    """One run's row from its events, oldest first; 'stopped' is None when it has no stop record."""
    started = next((e['at'] for e in found if e.get('type') == 'run_started'), found[0]['at'])
    stopped = next((e for e in reversed(found) if e.get('type') == 'run_stopped'), None)
    end = stopped['at'] if stopped else found[-1]['at']
    tallies = [e['tally'] for e in found if e.get('type') == 'acknowledged' and isinstance(e.get('tally'), dict)
               and str(e.get('action_id') or '').endswith('-fish')]
    finds = summarize_finds(tallies)
    return {'run_id': run_id, 'start': started, 'end': end,
            'seconds': round((parse_time(end) - parse_time(started)).total_seconds()),
            'trips': sum(t.get('trips') or 0 for t in tallies),
            'xp': sum(t.get('fishing_xp') or 0 for t in tallies),
            'chests': sum(finds['chests'].values()), 'charms': sum(finds['charms'].values()),
            'stopped': (stopped.get('reason') or 'Stopped.') if stopped else None}


def _act_part(found):
    """What action_history reads of an `act` intent, or of a result that may be one's (a fish trip's never is)."""
    if found.get('type') == 'intent':
        intent = found.get('intent')
        if not isinstance(intent, dict) or intent.get('kind') != 'act':
            return None
        return {'type': 'intent', 'at': found['at'], 'action_id': found.get('action_id'),
                'intent': {key: intent.get(key) for key in ('kind', 'arguments', 'preconditions')}}
    if found.get('type') in RESULTS and not str(found.get('action_id') or '').endswith('-fish'):
        return {key: found.get(key) for key in ('type', 'at', 'action_id', 'result', 'note', 'reason')}
    return None


def journal_digest(events):
    """What the Progress view needs of some journal events, small enough to keep per run: each run's row ('stopped'
    None without a stop record) and the parts of `act` actions. Digests of separate runs are joined by adding their
    lists (a later run can resolve an earlier run's action)."""
    readable = _readable(events)
    return {'runs': [_run_row(run_id, found) for run_id, found in _by(readable, 'run_id').items()],
            'acts': [part for part in map(_act_part, readable) if part]}


def _runs(rows, running_id=None, now=None):
    """The run rows newest first, each saying why it stopped: its stop record; else still running when it is this
    run (`running_id`) or the newest run with its journal written under LIVE_GAP before `now`; else ended without
    a stop record."""
    rows = sorted(rows, key=lambda r: parse_time(r['start']), reverse=True)
    shown = []
    for number, row in enumerate(rows):
        stopped = row['stopped']
        if stopped is None:
            live = row['run_id'] == running_id or (
                number == 0 and now is not None and (now - parse_time(row['end'])).total_seconds() <= LIVE_GAP)
            stopped = 'Still running.' if live else 'Ended without a stop record.'
        shown.append({**row, 'stopped': stopped})
    return shown


def run_history(events, running_id=None, now=None):
    """Each run newest first: start, end, length, trips confirmed, XP, chests and charms (from its trips' tallies),
    and why it stopped."""
    return _runs(journal_digest(events)['runs'], running_id, now)


def _cost(action, result, note):
    """What a done action cost: the amount the game's reply named ("bought 500 wise bait for $17,500"), else the
    plan's price marked as planned. Blank when not done (a failed or unknown buy paid nothing known) and for
    something already owned."""
    if result != 'done' or note.startswith('already owned'):
        return ''
    paid = PAID.search(note)
    if paid:
        return f'${paid[1]}'
    price = action.get('price') if isinstance(action, dict) else None
    if not isinstance(price, (int, float)) or not price:
        return ''
    shop = action.get('shop')
    if shop == 'league':
        return f'{price:,.0f} Hooks (planned)'
    if shop == 'special':
        return f'{price:,.0f} {action.get("currency") or "exotic fish"} (planned)'
    return f'${price:,.0f} (planned)'


def action_history(events):
    """Each action the run carried out, newest first: when, the step, done / failed / unknown, the game's note and
    what it cost (when the journal has the action)."""
    actions = []
    for found in _by(_readable(events), 'action_id').values():
        intent = next((e for e in found if e.get('type') == 'intent' and isinstance(e.get('intent'), dict)), None)
        if intent is None or intent['intent'].get('kind') != 'act':
            continue
        arguments = intent['intent'].get('arguments') or [None]
        result, note = 'unknown', ''
        for later in found:
            if later.get('type') == 'acknowledged':
                result, note = ('done' if later.get('result') == 'done' else 'failed'), later.get('note') or ''
            elif later.get('type') == 'resolved':
                result, note = 'unknown', later.get('reason') or ''
            elif later.get('type') == 'abandoned':
                result, note = 'failed', later.get('reason') or ''
        preconditions = intent['intent'].get('preconditions') or ['an action']
        actions.append({'at': intent['at'], 'label': str(preconditions[0]), 'result': result, 'note': note,
                        'cost': _cost(arguments[0], result, str(note))})
    return sorted(actions, key=lambda a: parse_time(a['at']), reverse=True)


def _noon(day):
    return f'{day}T12:00:00+00:00'


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _moment_or_none(text):
    try:
        return parse_time(text)
    except (AttributeError, TypeError, ValueError):
        return None


def _in_order(reads):
    """The reads with a readable time, oldest first."""
    timed = [(moment, read) for read in reads for moment in [_moment_or_none(read.get('at'))] if moment is not None]
    return [read for _, read in sorted(timed, key=lambda pair: pair[0])]


def _net_worth(profiles):
    """(at, balance + fish value) per /profile read: a read without the fish value uses the last one read before it;
    one with none read yet is skipped (balance alone would drop the line)."""
    points, fish = [], None
    for read in _in_order(profiles):
        if _number(read.get('fish_value')):
            fish = read['fish_value']
        if _number(read.get('balance')) and fish is not None:
            points.append((read['at'], read['balance'] + fish))
    return points


def chart_series(index, history, days, now):
    """The charts' points, oldest first, within the last `days` days of `now` (None: all): level (trip level-ups by
    day before the first /profile read, then the reads: a day's highest level-up never sits beside reads), charms
    found (/charms reads), XP per fishing hour by day, trips by day, and net worth (balance + fish value, /profile
    reads). Points that cannot be read are skipped."""
    start = None if days is None else now - timedelta(days=days)
    daily = sorted((day, v) for day, v in ((index or {}).get('days') or {}).items() if isinstance(v, dict))
    reads = [h for h in history or [] if isinstance(h, dict)]
    profiles = [h for h in reads if h.get('kind') == 'profile']
    levels = _in_order([h for h in profiles if _number(h.get('level'))])
    first = parse_time(levels[0]['at']) if levels else None
    # Index days are local days: before both the UTC and the local day of the first read.
    before = min(first.date(), first.astimezone().date()).isoformat() if first else None
    series = {
        'level': [(_noon(day), v['level']) for day, v in daily
                  if _number(v.get('level')) and v['level'] and (before is None or day < before)]
        + [(h['at'], h['level']) for h in levels],
        'charms': [(h.get('at'), h['found']) for h in reads if h.get('kind') == 'charms' and _number(h.get('found'))],
        'xp_per_hour': [(_noon(day), round(v.get('xp', 0) / v['seconds'] * 3600, 1)) for day, v in daily
                        if _number(v.get('seconds')) and v['seconds'] >= 60],
        'trips': [(_noon(day), v['trips']) for day, v in daily if _number(v.get('trips'))],
        'net_worth': _net_worth(profiles),
    }
    result = {}
    for name, points in series.items():
        kept = []
        for at, value in points:
            moment = _moment_or_none(at)
            if moment is not None and (start is None or moment >= start):
                kept.append((moment, at, value))
        result[name] = [(at, value) for _, at, value in sorted(kept, key=lambda p: p[0])]
    return result


def countdown(plan, profile, charms):
    """How far prestige is: the plan's ETA (or what it does not reach), level / money / charms against the
    requirement, and the milestones ahead (each later phase's first step, then prestige). Without a plan the bars
    still come from the profile and charms; the ETAs need the plan."""
    profile = profile or {}
    requirement = prestige_requirements(profile['prestige']) if _number(profile.get('prestige')) else None
    bars = []
    if requirement:
        level, money, charm_goal = requirement
        bars = [{'label': 'Level', 'now': profile.get('level') or 0, 'goal': level},
                {'label': 'Money', 'now': (profile.get('balance') or 0) + (profile.get('fish_value') or 0),
                 'goal': money},
                {'label': 'Charms', 'now': (charms or {}).get('found') or 0,
                 'goal': (charms or {}).get('goal') or charm_goal}]
    if not isinstance(plan, dict) or not plan:
        return {'reached': False, 'seconds': None, 'missing': ['No plan yet.'], 'bars': bars, 'milestones': []}
    prestige = plan.get('prestige') or {}
    milestones, seen = [], {plan.get('phase_now')}
    for step in plan.get('steps') or []:
        if not isinstance(step, dict) or not step.get('phase') or step['phase'] in seen \
                or not _number(step.get('seconds')):
            continue
        seen.add(step['phase'])
        milestones.append({'label': step.get('phase_label') or str(step['phase']), 'seconds': step['seconds']})
    if prestige.get('reached'):
        milestones.append({'label': 'Prestige', 'seconds': prestige.get('seconds')})
    return {'reached': bool(prestige.get('reached')), 'seconds': prestige.get('seconds'),
            'missing': list(prestige.get('missing') or []), 'bars': bars, 'milestones': milestones}


def progress_view(index, history, journal, plan, profile, charms, now, running_id=None, notes=()):
    """Everything the Progress tab shows. `journal`: the journal's events, or their journal_digest."""
    digest = journal if isinstance(journal, dict) else journal_digest(journal)
    return {'countdown': countdown(plan, profile or {}, charms),
            'charts': {'7': chart_series(index, history, 7, now), '30': chart_series(index, history, 30, now),
                       'all': chart_series(index, history, None, now)},
            'runs': _runs(digest['runs'], running_id, now), 'actions': action_history(digest['acts']),
            'notes': list(notes)}
