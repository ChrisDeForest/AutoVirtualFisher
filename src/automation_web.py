"""The /automation page: start a live or simulated fishing run and watch your stats move.

The page holds the start form; /automation/panel renders the live part (status, level line, and the profile, buffs,
run, inventory and recent-trip boxes), which the page swaps in every two seconds."""
from html import escape

MAX_TRIPS, MAX_MINUTES = 1000, 600
DEFAULT_CHANNEL = 'Virtual Fisher'
PHASES = {'BOOTSTRAPPING': 'Starting', 'WAITING_REPLY': 'Waiting for Virtual Fisher',
          'WAITING_DELIVERY': 'Waiting for Discord to send', 'WAITING_COOLDOWN': 'Cooling down',
          'RETRYING_STARTUP': 'Reading your profile again', 'PAUSED': 'Paused', 'READY': 'Ready',
          'SENDING': 'Sending', 'NEEDS_REVIEW': 'Needs you', 'FAILED': 'Stopped by a problem', 'STOPPED': 'Stopped'}
BUFFS = (('sell_price', 'Sell price'), ('fish_catch', 'Fish catch'), ('fish_quality', 'Fish quality'),
         ('treasure_chance', 'Treasure chance'), ('treasure_quality', 'Treasure quality'),
         ('xp_multiplier', 'XP multiplier'))
CATEGORIES = (('fish', 'Fish'), ('exotic_fish', 'Exotic fish'), ('special', 'Special'))
STYLE = """<style>
.strip { display:flex; flex-wrap:wrap; align-items:center; gap:10px 18px; padding:12px 16px; border:1px solid var(--line);
  border-radius:6px; margin:0 0 28px; }
.strip .actions { margin:0 0 0 auto; } .strip form { margin:0; }
.phase { font-weight:650; } .phase::before { content:''; display:inline-block; width:9px; height:9px; border-radius:50%;
  margin-right:9px; background:var(--muted); vertical-align:1px; }
.on .phase::before { background:var(--sea); } .halted .phase::before { background:#ffda8b; }
.why { color:var(--muted); font-size:14px; flex:1 1 280px; }
.level { margin:0 0 34px; }
.level-head { display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 18px; }
.level-number { font:normal clamp(40px,6vw,64px)/1 Georgia,serif; margin:0; }
.level-meta { color:var(--muted); }
.line { position:relative; height:26px; margin-top:14px; }
.line::before { content:''; position:absolute; left:0; right:0; top:12px; border-top:2px dashed #3d5a69; }
.line .cast { position:absolute; left:0; top:12px; border-top:2px solid var(--sea); }
.line .bobber { position:absolute; top:5px; width:16px; height:16px; margin-left:-8px; border-radius:50%;
  background:var(--sea); box-shadow:0 0 0 4px #101e29; }
.boxes { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:22px 34px; }
.box { border-top:1px solid var(--line); padding-top:14px; min-width:0; }
.box.wide { grid-column:span 2; }
.box h2 { font-size:17px; color:var(--sea); margin:0 0 6px; }
.box .stat { padding:6px 0; font-size:15px; }
.box .note { margin:10px 0 0; }
.gain { color:var(--sea); font-size:13px; margin-left:8px; }
.columns { columns:2 220px; column-gap:34px; } .columns .stat { break-inside:avoid; }
.trips-list { list-style:none; padding:0; margin:0; font-size:14px; }
.trips-list li { padding:7px 0; border-bottom:1px solid #263d4a; display:flex; justify-content:space-between; gap:12px; }
.start fieldset { border:0; padding:0; margin:0; } .start legend { font-weight:600; margin-bottom:6px; }
.start label input[type=radio] { margin-right:8px; } .start fieldset label { margin:6px 0; font-weight:400; }
.start input[type=text] { font:inherit; width:100%; max-width:360px; border:1px solid #496575; border-radius:6px;
  padding:9px 12px; background:#0b1720; color:var(--ink); }
.start .pair { display:grid; grid-template-columns:repeat(2,minmax(0,360px)); gap:0 24px; }
.empty { color:var(--muted); }
.box .stat.sub dt { padding-left:16px; } .box .stat.group { border-bottom:0; padding-bottom:0; }
.box .stat.group dt { color:var(--ink); font-weight:600; }
.scroll { max-height:340px; overflow-y:auto; padding-right:10px; scrollbar-color:#3d5a69 transparent; }
.scroll:focus-visible { outline-offset:2px; }
.finds { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:0 28px; }
.finds h3 { font-size:15px; color:var(--ink); margin:10px 0 2px; }
.mini { width:100%; border-collapse:collapse; font-size:14px; font-variant-numeric:tabular-nums; }
.mini th, .mini td { padding:6px 0; border-bottom:1px solid #263d4a; text-align:right; font-weight:400; }
.mini th[scope=row], .mini thead th:first-child { text-align:left; padding-right:12px; }
.mini thead th { color:var(--muted); font-size:13px; } .mini td + td, .mini th + th { padding-left:14px; }
.visually-hidden { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); }
@media(max-width:900px) { .finds { grid-template-columns:minmax(0,1fr); } }
@media(max-width:900px) { .boxes { grid-template-columns:minmax(0,1fr); } .box.wide { grid-column:auto; }
  .start .pair { grid-template-columns:minmax(0,1fr); } }
@media(prefers-reduced-motion:no-preference) { .line .cast, .line .bobber { transition:all .6s ease-out; } }
</style>"""
SCRIPT = """<script nonce="{nonce}">
(function () {
  var panel = document.getElementById('panel');
  if (!panel) return;
  setInterval(function () {
    fetch('/automation/panel', {cache: 'no-store'}).then(function (r) { return r.text(); }).then(function (html) {
      var was = panel.getAttribute('data-running');
      panel.innerHTML = html;
      var now = panel.querySelector('[data-running]');
      if (now && now.getAttribute('data-running') !== was) { location.reload(); }
    }).catch(function () {});
  }, 2000);
})();
</script>"""


def parse_limits(fields: dict) -> tuple[int | None, int | None, str]:
    """(trip limit, minute limit, problem) from the start form. A blank limit means none; the problem is ''
    when both are valid."""
    values = []
    for name in ('max_trips', 'max_minutes'):
        text = fields.get(name, [''])[0].strip()
        if not text:
            values.append(None)
            continue
        try:
            values.append(int(text))
        except ValueError:
            return None, None, (f'Enter whole numbers or leave blank: trips between 1 and {MAX_TRIPS}, '
                                f'minutes between 1 and {MAX_MINUTES}.')
    trips, minutes = values
    if (trips is not None and not 1 <= trips <= MAX_TRIPS) or (minutes is not None and not 1 <= minutes <= MAX_MINUTES):
        return None, None, f'Trips must be between 1 and {MAX_TRIPS} and minutes between 1 and {MAX_MINUTES}.'
    return trips, minutes, ''


def _num(value, prefix=''):
    return f'{prefix}{value:,.0f}' if isinstance(value, (int, float)) else None


def _duration(seconds):
    if seconds is None:
        return None
    minutes = round(seconds / 60)
    if minutes < 1:
        return 'under a minute'
    if minutes < 90:
        return f'about {minutes} min'
    return f'about {minutes / 60:.1f} h'


def _row(label, value, gain=None):
    shown = (f'<dd class="known">{escape(str(value))}' if value is not None else '<dd class="unknown">not read')
    if gain and value is not None:
        shown += f'<span class="gain">+{gain:,}</span>'
    return f'<div class="stat"><dt>{escape(label)}</dt>{shown}</dd></div>'


def _box(title, body, wide=False, note=''):
    return (f'<section class="box{" wide" if wide else ""}"><h2>{escape(title)}</h2>{body}'
            + (f'<p class="note">{escape(note)}</p>' if note else '') + '</section>')


def _level_line(profile, dashboard):
    level, xp, required = profile.get('level'), profile.get('xp_current'), profile.get('xp_required')
    if level is None:
        return ''
    title = f'Level {level}' + (f' (prestige {profile["prestige"]})' if profile.get('prestige') else '')
    meta = []
    if xp is not None and required:
        meta.append(f'{xp:,} of {required:,} XP')
    else:
        meta.append('XP not read yet; /profile is read again in a few trips')
    eta = _duration(dashboard.get('next_level_in_seconds'))
    if eta:
        meta.append(f'{eta} to level {level + 1}')
    line = ''
    if xp is not None and required:
        share = max(0.0, min(1.0, xp / required)) * 100
        line = (f'<div class="line" role="img" aria-label="{escape(meta[0])}"><span class="cast" '
                f'style="width:{share:.1f}%"></span><span class="bobber" style="left:{share:.1f}%"></span></div>')
    return (f'<div class="level"><div class="level-head"><p class="level-number">{escape(title)}</p>'
            + ''.join(f'<span class="level-meta">{escape(item)}</span>' for item in meta) + f'</div>{line}</div>')


def _pet(profile):
    name, level = profile.get('pet'), profile.get('pet_level')
    if level is None:
        return name
    return f'{name}, level {level}' if name else f'level {level} (name not read)'


def _catch_text(catches):
    return ', '.join(f'{count:,} {name}' for name, count in catches.items()) or 'nothing'


def state_label(status) -> str:
    """What the run is doing, in one or two words."""
    phase = status['phase']
    if not status['running']:
        return 'Stopped by a problem' if phase == 'FAILED' else 'Stopped'
    if status.get('paused') or phase in ('PAUSED', 'PAUSED_BY_YOU'):
        return 'Paused'
    if phase == 'NEEDS_REVIEW':
        return 'Needs you'
    if status.get('pending_kind') in ('refresh_profile', 'refresh_buffs') or phase in ('RETRYING_STARTUP',
                                                                                        'BOOTSTRAPPING'):
        if status['trips']:
            return 'Refreshing stats'
        return 'Reading your buffs' if status.get('pending_kind') == 'refresh_buffs' else 'Reading your profile'
    return 'Fishing'


def _button(action, label, secondary=False):
    style = ' class="secondary"' if secondary else ''
    return (f'<form method="post" action="/automation"><input type="hidden" name="action" value="{action}">'
            f'<button type="submit"{style}>{escape(label)}</button></form>')


def _scroll(label, inner):
    return f'<div class="scroll" tabindex="0" role="region" aria-label="{escape(label)}">{inner}</div>'


def _finds_box(finds):
    run, every = finds.get('run') or {}, finds.get('all') or {}

    def group(title, key, levels=False):
        names = sorted(set(every.get(key, {})) | set(run.get(key, {})),
                       key=lambda name: -every.get(key, {}).get(name, 0))
        if not names:
            return f'<h3>{escape(title)}</h3><p class="empty">None yet.</p>'
        head = '<th scope="col">This run</th><th scope="col">All runs</th>' + (
            '<th scope="col">Level</th>' if levels else '')
        rows = ''
        for name in names:
            level = every.get('charm_levels', {}).get(name)
            rows += (f'<tr><th scope="row">{escape(name)}</th><td>{run.get(key, {}).get(name, 0):,}</td>'
                     f'<td>{every.get(key, {}).get(name, 0):,}</td>'
                     + (f'<td>{level if level is not None else "?"}</td>' if levels else '') + '</tr>')
        table = (f'<table class="mini"><thead><tr><th scope="col"><span class="visually-hidden">Name</span></th>'
                 f'{head}</tr></thead><tbody>{rows}</tbody></table>')
        return f'<h3>{escape(title)}</h3>' + _scroll(title, table)

    body = (f'<div class="finds"><div>{group("Chests", "chests")}</div>'
            f'<div>{group("Special fish", "special_fish")}</div>'
            f'<div>{group("Charms", "charms", levels=True)}</div></div>')
    return _box('Finds', body, wide=True, note='Charms are a prestige requirement.')


def _source_rows(title, values):
    rows = f'<div class="stat group"><dt>{escape(title)}</dt><dd></dd></div>'
    rows += f'<div class="stat sub"><dt>Money</dt><dd class="known">${values.get("money", 0):,}</dd></div>'
    if 'xp' in values:
        rows += f'<div class="stat sub"><dt>XP</dt><dd class="known">{values["xp"]:,}</dd></div>'
    if 'other' in values:
        other = values['other']
        rows += (f'<div class="stat sub"><dt>Other</dt><dd class="known">'
                 f'{escape(_catch_text(other)) if other else "nothing"}</dd></div>')
    return rows


def render_panel(status, mode=None) -> str:
    """The live part of the page: run status, level line, and the stat boxes."""
    if not status:
        return ('<div data-running="0"><p class="empty">No run yet. Start one above; your profile, buffs and '
                'inventory appear here once Virtual Fisher replies.</p></div>')
    running = status['running']
    label = state_label(status)
    buttons = ''
    if running:
        toggle = _button('resume', 'Resume') if status.get('paused') else _button('pause', 'Pause', secondary=True)
        buttons = f'<div class="actions">{toggle}{_button("stop", "Stop now")}</div>'
    active = label in ('Fishing', 'Refreshing stats', 'Reading your profile', 'Reading your buffs')
    tone = 'on' if active else ('halted' if running else '')
    strip = (f'<div class="strip {tone}"><span class="phase">{escape(label)}</span>'
             f'<span class="why">{escape(status["reason"])}</span>{buttons}</div>')
    dashboard = status.get('dashboard') or {}
    profile = dashboard.get('profile') or {}
    gained = dashboard.get('gained') or {}
    run = dashboard.get('run') or {}
    per_hour = dashboard.get('per_hour') or {}
    buffs = dashboard.get('buffs') or {}

    profile_box = _box('Profile', '<dl>' + ''.join((
        _row('Balance', _num(profile.get('balance'), '$'), gained.get('money')),
        _row('Fish value', _num(profile.get('fish_value'), '$'), gained.get('fish_value')),
        _row('Rod', profile.get('rod')), _row('Biome', profile.get('biome')),
        _row('Pet', _pet(profile)),
        _row('Bait', profile.get('bait') or ('none' if 'bait' in profile and profile.get('rod') else None)),
    )) + '</dl>')

    def multiplier(name):
        value = buffs.get(name)
        return f'{value["value"]:.2f}×' if isinstance(value, dict) else None
    cooldown = buffs.get('fishing_cooldown')
    buffs_box = _box('Buffs', '<dl>' + ''.join(_row(label, multiplier(name)) for name, label in BUFFS)
                     + _row('Fishing cooldown', f'{cooldown["value"]:g} s' if isinstance(cooldown, dict) else None)
                     + '</dl>')

    sources = run.get('sources') or {}
    limit = status['config']['max_trips']
    next_fish = status.get('next_eligible_in')
    sync_note = ''
    if dashboard.get('next_sync_in') is not None and dashboard.get('synced_at'):
        sync_note = (f'Profile and buffs read {dashboard["trips_since_sync"]:,} trips ago; read again in '
                     f'{dashboard["next_sync_in"]:,} trips. Between reads, numbers are worked out from each trip.')
    run_box = _box('This run', '<dl>' + ''.join((
        _row('Mode', 'Live' if mode == 'live' else 'Simulation'),
        _row('Trips', f'{status["trips"]:,}' + (f' of {limit:,}' if limit else '')),
        _row('Fish caught', _num(run.get('catches'))),
        _row('XP gained', _num(run.get('xp'))),
        _row('Trips per hour', _num(per_hour.get('trips'))),
        _row('XP per hour', _num(per_hour.get('xp'))),
        _row('Fish value per hour', _num(per_hour.get('fish_value'), '$')),
        _row('Next fish', (f'in {next_fish:.1f} s' if next_fish else 'now') if running else 'not running'),
    )) + '</dl>', note=sync_note)
    rewards_box = _box('Rewards', '<dl>'
                       + _source_rows('Quests', sources.get('quests') or {'money': 0, 'xp': 0, 'other': {}})
                       + _source_rows('Chests', sources.get('chests') or {'money': 0, 'xp': 0, 'other': {}})
                       + _source_rows('Level-ups', sources.get('level_ups') or {'money': 0}) + '</dl>',
                       note='This run. All of it is already in your balance and XP above.')

    inventory = dashboard.get('inventory') or {}
    changes = {**(dashboard.get('changes') or {}).get('fish', {}), **(dashboard.get('changes') or {}).get('other', {})}
    rows = ''.join(_row(name, f'{count:,}', changes.get(name))
                   for key, _ in CATEGORIES for name, count in sorted((inventory.get(key) or {}).items(),
                                                                       key=lambda item: -item[1]))
    inventory_box = _box('Inventory', _scroll('Inventory', f'<dl class="columns">{rows}</dl>') if rows else
                         '<p class="empty">Not read yet.</p>', wide=True,
                         note='Green numbers are added since your profile was last read.' if changes else '')

    recent = dashboard.get('recent') or []
    items = ''.join(
        f'<li><span>{escape(_catch_text(trip["catches"]))}'
        + ''.join(f'<br><span class="note">{escape(event)}</span>' for event in trip['events'])
        + f'</span><span>+{trip["xp"]:,} XP</span></li>' for trip in recent)
    trips_box = _box('Recent trips', _scroll('Recent trips', f'<ul class="trips-list">{items}</ul>')
                     if recent else '<p class="empty">No trips yet.</p>')

    return (f'<div data-running="{1 if running else 0}">{strip}{_level_line(profile, dashboard)}'
            f'<div class="boxes">{profile_box}{buffs_box}{run_box}{rewards_box}'
            f'{_finds_box(dashboard.get("finds") or {})}{inventory_box}{trips_box}</div></div>')


def _field(name, label, value, hint=''):
    return (f'<div><label for="{name}">{escape(label)}</label>'
            f'<input id="{name}" name="{name}" type="text" value="{escape(value or "")}" autocomplete="off">'
            + (f'<p class="note">{escape(hint)}</p>' if hint else '') + '</div>')


def render_automation(head, status, notice='', live_problem='', settings=None, mode=None, nonce=''):
    """Render the page. `status` is AutomationController.status(), or None before the first run; `mode` is the
    current run's mode; `settings` holds the last live account, username and channel."""
    settings = settings or {}
    running = bool(status and status['running'])
    head = head.replace('<title>Player logbook · AutoVirtualFisher</title>',
                        '<title>Automation · AutoVirtualFisher</title>').replace('</head>', STYLE + '</head>')
    notice_html = f'<p class="save-status">{escape(notice)}</p>' if notice else ''
    if running and mode == 'live':
        control = ('<p class="status"><strong>Switch to Discord</strong> to fish. The run types into Discord only '
                   'while it is in front; switching away pauses it until you come back.</p>')
    elif running:
        control = ''
    else:
        live_attrs = ' disabled' if live_problem else ' checked'
        sim_attrs = ' checked' if live_problem else ''
        control = ('<form class="start" method="post" action="/automation"><input type="hidden" name="action" '
                   'value="start"><fieldset><legend>Mode</legend>'
                   f'<label><input type="radio" name="mode" value="live"{live_attrs}>Live: fish in Discord</label>'
                   f'<label><input type="radio" name="mode" value="simulation"{sim_attrs}>Simulation: nothing is '
                   'sent to Discord</label>'
                   + (f'<p class="note">{escape(live_problem)}</p>' if live_problem else '') + '</fieldset>'
                   '<div class="pair">'
                   + _field('account', 'Name in "… used /fish"', settings.get('account'),
                            'Also the name in "Inventory of …".')
                   + _field('username', 'Name in "…\'s current multipliers"', settings.get('username'),
                            'Your Discord username, from /buffs.')
                   + '</div>'
                   + _field('channel', 'Channel', settings.get('channel') or DEFAULT_CHANNEL,
                            'Your DM with Virtual Fisher, as Discord\'s search box names it.')
                   + '<div class="pair"><div>'
                   f'<label for="max_trips">Stop after this many trips (blank: no limit, up to {MAX_TRIPS:,})</label>'
                   f'<input id="max_trips" name="max_trips" type="number" min="1" max="{MAX_TRIPS}" value=""></div><div>'
                   f'<label for="max_minutes">Or this many minutes (blank: no limit, up to {MAX_MINUTES})</label>'
                   f'<input id="max_minutes" name="max_minutes" type="number" min="1" max="{MAX_MINUTES}" value="">'
                   '</div></div><div class="actions"><button type="submit">Start</button></div></form>')
    return (f'<!doctype html><html lang="en">{head}<body>'
            '<header><a class="brand" href="/">AutoVirtualFisher</a><span class="local">Automation</span></header>'
            '<main><h1>Automation</h1>'
            f'{notice_html}{control}'
            f'<div id="panel" data-running="{1 if running else 0}">{render_panel(status, mode)}</div>'
            f'</main>{SCRIPT.replace("{nonce}", escape(nonce))}</body></html>')
