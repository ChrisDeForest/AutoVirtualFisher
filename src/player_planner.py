"""Plan levels and quests from saved snapshots, fishing sessions, and the wiki level table."""
from datetime import datetime, timedelta, timezone
import json
from math import ceil
from pathlib import Path
import re

from .automation_state import section_freshness
from .bait_compare import bait_cost_per_trip, load_bait_reference
from .catch_check import load_fish_reference
from .fish_value import base_value, latest_sell_multiplier, value_check
from .fishing_sessions import DEFAULT_SESSION_ROOT, fishing_totals, load_sessions
from .player_snapshots import DEFAULT_SNAPSHOT_ROOT, SECTIONS, load_history

LEVEL_REFERENCE_PATH = Path(__file__).parent / 'reference' / 'level_xp.json'
CHEST_NAME = re.compile(r'\b(box|chest|crate)\b', re.I)
# Most recent matching trips used for a strategy estimate.
STRATEGY_SAMPLE_TRIPS = 500
STRATEGY_CONDITIONS = ('biome', 'rod')
# A trip saved without any bait record (not the same as no bait).
NOT_SET = object()


def load_level_increments(path: Path = LEVEL_REFERENCE_PATH) -> list[int] | None:
    try:
        increments = json.loads(Path(path).read_text(encoding='utf-8'))['xp_increment']
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    return increments if isinstance(increments, list) else None


def xp_required(level: int, increments: list[int]) -> int | None:
    """XP needed to advance from `level`: the running total of the wiki column through that level."""
    if not increments or level < 1 or level > len(increments):
        return None
    return sum(increments[:level])


def xp_between(level: int, xp_to_next: int, target: int, increments: list[int]) -> int | None:
    """XP needed to go from the current position in `level` to reaching `target`."""
    if target <= level:
        return None
    total = xp_to_next
    for passing in range(level + 1, target):
        requirement = xp_required(passing, increments)
        if requirement is None:
            return None
        total += requirement
    return total


def _trips(amount, rate):
    return ceil(amount / rate) if amount is not None and rate else None


def _model_check(snapshots, increments):
    matches, mismatches = 0, []
    for snapshot in snapshots:
        profile = snapshot['state'].get('profile', {})
        level, observed = profile.get('level'), profile.get('xp_required')
        if not isinstance(level, int) or not isinstance(observed, int):
            continue
        expected = xp_required(level, increments)
        if expected == observed:
            matches += 1
        else:
            mismatches.append({'level': level, 'observed': observed, 'expected': expected})
    return {'matches': matches, 'mismatches': mismatches, 'usable': bool(increments) and not mismatches}


def _quest_estimate(quest, totals, level, xp_to_next, xp_per_trip, increments, usable):
    remaining = quest['goal'] - quest['progress']
    if remaining <= 0:
        return 'complete', None, 'Complete'
    task = quest.get('task') or ''
    trips = totals['trips']
    if re.search(r'\bcatch\b.*\bfish\b', task, re.I):
        rate, unit = totals['fish_per_trip'], 'fish'
    elif re.search(r'\bfind\b.*\bchests?\b', task, re.I):
        found = sum(count for name, count in totals['finds'].items() if CHEST_NAME.search(name))
        rate, unit = (found / trips if trips else None), 'chests'
    elif re.search(r'\blevel up\b', task, re.I):
        if level is None or xp_to_next is None or (remaining > 1 and not usable):
            return 'no_data', None, 'Needs a full snapshot with level and XP'
        needed = xp_between(level, xp_to_next, level + remaining, increments) if remaining > 1 else xp_to_next
        if not xp_per_trip or needed is None:
            return 'no_data', None, 'Needs a saved fishing session'
        return 'estimate', _trips(needed, xp_per_trip), f'{needed:,} XP at {xp_per_trip:,.2f} per trip'
    elif re.search(r'\bpets?\b', task, re.I):
        return 'no_data', None, 'No pet finds in your fishing sessions yet'
    else:
        return 'unknown_task', None, 'This quest type is not estimated yet'
    if not rate:
        return 'no_data', None, f'No {unit} in your fishing sessions yet'
    return 'estimate', _trips(remaining, rate), f'{remaining:,} {unit} at {rate:,.2f} per trip'


def _current_bait(profile):
    """(known, bait): the profile's bait, None for "NONE"; unknown when the profile did not show one."""
    bait = profile.get('bait')
    if not isinstance(bait, str):
        return False, None
    return True, (None if bait.casefold() in ('none', 'no bait') else bait)


def _net(gross, cost):
    return gross - cost if gross is not None and cost is not None else None


def _strategy(sessions, profile, reference, multiplier, bait_reference):
    """Gross and net per trip from recent trips fished under the current biome, rod, and bait only.

    Sessions without recorded conditions (pasted ones, or older live ones) cannot be matched and are excluded.
    """
    bait_known, bait = _current_bait(profile)
    conditions = {name: profile.get(name) for name in STRATEGY_CONDITIONS}
    conditions['bait'] = bait if bait_known else None
    missing = [name for name in STRATEGY_CONDITIONS if not isinstance(conditions[name], str)]
    missing += [] if bait_known else ['bait']
    total = matched = 0
    catches = {}
    for session in sessions:
        recorded = session['tally'].get('conditions') or {}
        same = not missing and all(isinstance(recorded.get(name), str)
                                   and recorded[name].casefold() == conditions[name].casefold()
                                   for name in STRATEGY_CONDITIONS)
        for trip in session['tally'].get('trip_details', []):
            total += 1
            trip_bait = trip.get('bait', NOT_SET)
            if (same and matched < STRATEGY_SAMPLE_TRIPS and trip_bait is not NOT_SET
                    and (trip_bait or '').casefold() == (bait or '').casefold()):
                matched += 1
                for name, count in trip['catches'].items():
                    catches[name] = catches.get(name, 0) + count
    gross = None
    if matched and reference is not None and multiplier is not None:
        base, unknown = base_value(catches, reference)
        gross = None if unknown else base / matched * multiplier
    cost = bait_cost_per_trip(bait, bait_reference) if bait_known else None
    return {'conditions': conditions, 'missing_conditions': missing, 'trips': matched,
            'excluded_trips': total - matched, 'gross_per_trip': gross, 'bait_cost_per_trip': cost,
            'net_per_trip': _net(gross, cost)}



def _money(history, profile, totals, reference, money_target, sessions=(), bait_reference=None):
    """Gross catch value per trip at the latest /buffs sell price, net of bait when its cost is known, and trips
    until a target balance. Gross per trip averages all saved trips, whatever conditions they were fished in."""
    sell = latest_sell_multiplier(history)
    multiplier, multiplier_as_of = sell if sell else (None, None)
    base_per_trip = unknown = None
    if totals['trips'] and reference is not None:
        base, unknown = base_value(totals['catches'], reference)
        base_per_trip = base / totals['trips']
    per_trip = base_per_trip * multiplier if base_per_trip is not None and multiplier is not None else None
    bait_known, bait = _current_bait(profile)
    cost = bait_cost_per_trip(bait, bait_reference) if bait_known else None
    cooldown = (history[0]['state'].get('buffs', {}).get('fishing_cooldown') if history else None) or {}
    seconds = cooldown.get('value') if cooldown.get('unit') == 'seconds' else None
    money = {'multiplier': multiplier, 'multiplier_as_of': multiplier_as_of, 'base_per_trip': base_per_trip,
             'per_trip': per_trip, 'gross_per_trip': per_trip, 'bait': bait if bait_known else None,
             'bait_cost_per_trip': cost, 'net_per_trip': _net(per_trip, cost),
             # An upper bound: one trip per observed cooldown, with no time between trips.
             'gross_per_hour': per_trip * 3600 / seconds if per_trip is not None and seconds else None,
             'strategy': _strategy(sessions, profile, reference, multiplier, bait_reference),
             'unknown_fish': unknown or [],
             'check': value_check(history, reference) if reference is not None else None, 'target': None}
    balance, fish_value = profile.get('balance'), profile.get('fish_value')
    if money_target and isinstance(balance, (int, float)):
        unsold = isinstance(fish_value, (int, float))
        have = balance + (fish_value if unsold else 0)
        needed = max(money_target - have, 0)
        money['target'] = {'amount': money_target, 'have': have, 'needed': needed,
                           'trips': 0 if not needed else _trips(needed, per_trip),
                           'includes_unsold_fish': unsold}
    return money


def plan(account_name: str, snapshot_root: Path = DEFAULT_SNAPSHOT_ROOT,
         fishing_root: Path = DEFAULT_SESSION_ROOT, target: int | None = None,
         now: datetime | None = None, increments: list[int] | None = None,
         money_target: int | None = None, fish_reference: dict | None = None) -> dict:
    increments = load_level_increments() if increments is None else increments
    fish_reference = load_fish_reference() if fish_reference is None else fish_reference
    history, warnings = load_history(account_name, snapshot_root)
    full = [item for item in history if item.get('validity') == 'full']
    totals = fishing_totals(account_name, fishing_root)
    warnings += totals['warnings']
    if not full:
        return {'status': 'needs_snapshot', 'warnings': warnings}
    latest = full[0]
    moment = now or datetime.now(timezone.utc)
    # How old each part of the latest snapshot is, by when it was observed; unknown for pasted or older snapshots.
    section_ages = {section: section_freshness(latest, section, moment, max_age=float('inf'))
                    for section in SECTIONS}
    profile = latest['state'].get('profile', {})
    level, xp_to_next = profile.get('level'), profile.get('xp_to_next_level')
    xp_per_trip = totals['xp_per_trip']
    model = _model_check(full, increments)

    targets = []
    if isinstance(level, int) and isinstance(xp_to_next, (int, float)):
        wanted = [level + 1] + ([level + 5, level + 10] if model['usable'] else [])
        if target and model['usable'] and target > level:
            wanted.append(target)
        for goal in sorted(set(wanted)):
            needed = xp_to_next if goal == level + 1 else xp_between(level, xp_to_next, goal, increments)
            if needed is not None:
                targets.append({'level': goal, 'xp_needed': needed, 'trips': _trips(needed, xp_per_trip)})

    with_quests = next((item for item in history if any(
        item['state'].get('quests', {}).get(group) for group in ('daily', 'special'))), None)
    quests, quests_reset = [], False
    if with_quests:
        state_quests = with_quests['state']['quests']
        for group in ('daily', 'special'):
            for name, quest in (state_quests.get(group) or {}).items():
                status, trips, note = _quest_estimate(quest, totals, level, xp_to_next, xp_per_trip,
                                                      increments, model['usable'])
                quests.append({'group': group, 'name': name, 'progress': quest['progress'], 'goal': quest['goal'],
                               'task': quest.get('task'), 'status': status, 'trips': trips, 'note': note})
        minutes = state_quests.get('resets_in_minutes')
        if minutes is not None:
            reset_at = datetime.fromisoformat(with_quests['captured_at']) + timedelta(minutes=minutes)
            quests_reset = moment >= reset_at
    return {'status': 'ok', 'as_of': latest['captured_at'], 'level': level, 'xp_to_next': xp_to_next,
            'xp_per_trip': xp_per_trip, 'fishing_trips': totals['trips'], 'model': model, 'targets': targets,
            'quests': quests, 'quests_as_of': with_quests['captured_at'] if with_quests else None,
            'quests_reset': quests_reset, 'section_ages': section_ages,
            'money': _money(history, profile, totals, fish_reference, money_target,
                            load_sessions(account_name, fishing_root)[0], load_bait_reference()),
            'warnings': warnings}
