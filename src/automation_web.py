"""The /automation page: start a live or simulated fishing run and watch your stats move.

The page holds the start form; /automation/panel renders the live part (status, level line, and the profile, buffs,
run, inventory and recent-trip boxes), which the page swaps in every two seconds."""
from datetime import datetime, timezone
from functools import cache
from html import escape
import json
import re
from .fishing_log import CHARM_NAMES
from .game_model import round_half_up
from .player_charts import line_chart_svg

MAX_TRIPS, MAX_MINUTES = 1000, 600
DEFAULT_CHANNEL = 'Virtual Fisher'
PHASES = {'BOOTSTRAPPING': 'Starting', 'WAITING_REPLY': 'Waiting for Virtual Fisher',
          'WAITING_DELIVERY': 'Waiting for Discord to send', 'WAITING_COOLDOWN': 'Cooling down',
          'RETRYING_STARTUP': 'Reading your profile again', 'PAUSED': 'Paused', 'READY': 'Ready',
          'SENDING': 'Sending', 'NEEDS_REVIEW': 'Needs you', 'FAILED': 'Stopped by a problem', 'STOPPED': 'Stopped'}
# What the run is reading while an account read (or its page 2) waits for its reply.
ACCOUNT_READ_LABELS = {
    'refresh_upgrades': 'Reading the upgrade shop',
    'refresh_rod': 'Reading your rods', 'refresh_bait': 'Reading your bait', 'refresh_biome': 'Reading your biome',
    'refresh_boats': 'Reading the boat shop', 'refresh_pets': 'Reading your pets',
    'refresh_prestige_shop': 'Reading the prestige shop', 'refresh_league': 'Reading the league shop',
    'refresh_special': 'Reading the special shop',
    'refresh_boosts': 'Reading your boosts'}
BUFFS = (('sell_price', 'Sell price'), ('fish_catch', 'Fish catch'), ('fish_quality', 'Fish quality'),
         ('treasure_chance', 'Treasure chance'), ('treasure_quality', 'Treasure quality'),
         ('xp_multiplier', 'XP multiplier'))
CATEGORIES = (('fish', 'Fish'), ('exotic_fish', 'Exotic fish'), ('special', 'Special'))
PHASE_INTRO = {
    'xp_grind_1': 'Levelling on the money setup, buying what pays for itself, until the charm grind rod.',
    'charm_grind_1': 'Fishing the charm setup for charms until the first exit.',
    'xp_grind_2': 'Back to levelling and money, up to level 250.',
    'charm_grind_2': 'Level 250: the second charm grind, up to the charm exit.',
    'xp_grind_3': 'The last stretch: the level and money prestige needs.',
    'prestige': 'The requirements are met: prestige by hand.',
}
STYLE = """<style>
.strip { display:flex; flex-wrap:wrap; align-items:center; gap:10px 18px; padding:12px 16px; border:1px solid var(--line);
  border-radius:6px; margin:0 0 28px; }
.strip .actions { margin:0 0 0 auto; } .strip form { margin:0; }
.strip .phase { font-weight:650; } .strip .phase::before { content:''; display:inline-block; width:9px; height:9px;
  border-radius:50%; margin-right:9px; background:var(--muted); vertical-align:1px; }
.on .phase::before { background:var(--sea); } .halted .phase::before { background:#ffda8b; }
.why { color:var(--muted); font-size:14px; flex:1 1 280px; }
.level { margin:0 0 34px; }
.level-head { display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 18px; }
.level-number { font:normal clamp(40px,6vw,64px)/1 Georgia,serif; margin:0; }
.level-meta { color:var(--muted); }
.line { position:relative; height:26px; margin-top:14px; }
.line::before { content:''; position:absolute; left:0; right:0; top:12px; border-top:2px dashed #3d5a69; }
.line .cast { position:absolute; left:0; top:12px; height:2px; background:var(--sea); }
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
.rewards-row { grid-column:1 / -1; display:grid; grid-template-columns:minmax(200px,1fr) minmax(0,3.2fr);
  gap:22px 34px; }
.rewards-row .box.wide { grid-column:auto; }
.finds { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,1.7fr); gap:0 24px; }
.mini.charms th[scope=row] { white-space:nowrap; }
.mini.charms td + td, .mini.charms th + th { padding-left:10px; }
.finds h3 { font-size:15px; color:var(--ink); margin:10px 0 2px; }
.mini { width:100%; border-collapse:collapse; font-size:14px; font-variant-numeric:tabular-nums; }
.mini th, .mini td { padding:6px 0; border-bottom:1px solid #263d4a; text-align:right; font-weight:400; }
.mini th[scope=row], .mini thead th:first-child { text-align:left; padding-right:12px; }
.mini thead th { color:var(--muted); font-size:13px; } .mini td + td, .mini th + th { padding-left:14px; }
.tabs { display:flex; gap:4px; margin:0 0 22px; border-bottom:1px solid var(--line); }
.tabs a { padding:8px 14px; color:var(--muted); text-decoration:none; border-bottom:2px solid transparent; }
.tabs a.current { color:var(--ink); border-bottom-color:var(--sea); }
.model td.bad { color:#ffda8b; } .model td.good { color:var(--sea); }
.model .why { display:block; color:var(--muted); font-size:13px; }
.queue li { display:flex; align-items:center; gap:10px; } .queue form { margin:0; }
/* The Plan tab: where the account is, the boxes that act now, then the route as a line down the page. */
.plan-head { margin:0 0 22px; max-width:760px; }
.plan-now { font:normal clamp(26px,3.6vw,38px)/1.15 Georgia,serif; margin:0 0 6px; }
.plan-intro { color:var(--muted); margin:0 0 4px; } .plan-head .note { margin:0; }
.plan-boxes { margin:0 0 34px; } .plan-boxes .next { margin:0; overflow-wrap:anywhere; }
.timeline { position:relative; padding-left:34px; margin:0 0 30px; }
.timeline::before { content:''; position:absolute; left:10px; top:12px; bottom:14px; border-left:2px solid var(--line); }
.timeline .node { position:absolute; width:10px; height:10px; border-radius:50%; left:-28px; top:15px;
  background:var(--sea); }
.timeline .node.big { width:20px; height:20px; left:-33px; top:7px; background:#101e29;
  border:2px solid var(--muted); box-shadow:0 0 0 4px #101e29; }
.timeline .node.big.now { background:var(--sea); border-color:var(--sea); }
.timeline .node.big.reached { border-color:var(--sea); }
.timeline .node.ready { background:var(--sea); } .timeline .node.cannot_afford { background:var(--muted); }
.timeline .node.later { background:var(--line); }
.timeline .node.none { background:#101e29; border:2px solid var(--muted); }
.timeline details.phase { margin:0 0 18px; }
.timeline details.phase > summary { position:relative; display:flex; flex-wrap:wrap; align-items:baseline;
  gap:2px 14px; list-style:none; cursor:pointer; padding:2px 0 8px; }
.timeline details.phase > summary::-webkit-details-marker { display:none; }
.timeline .phase-title { font-size:19px; font-weight:650; color:var(--ink); margin:0; }
.timeline .phase-when { color:var(--sea); font-size:14px; }
.timeline .phase-count { color:var(--muted); font-size:13px; }
.timeline details.phase > summary .phase-count::after { content:' ▸'; }
.timeline details.phase[open] > summary .phase-count::after { content:' ▾'; }
.timeline .phase-intro { flex:1 0 100%; color:var(--muted); font-size:14px; }
.timeline .steps { list-style:none; margin:0; padding:0; }
.timeline .step { position:relative; display:flex; flex-wrap:wrap; align-items:center; gap:4px 16px; padding:7px 0;
  border-bottom:1px solid #263d4a; }
.timeline .step-text { flex:1 1 300px; min-width:0; overflow-wrap:anywhere; }
.timeline .step-text strong { display:block; font-weight:600; }
.timeline .meta { display:block; color:var(--muted); font-size:13px; margin:0; overflow-wrap:anywhere; }
.timeline .step-do { flex:0 0 auto; } .timeline .step-do form { margin:0; }
.timeline .step-do button { padding:4px 12px; font-size:14px; }
.timeline .cannot { color:var(--muted); font-size:13px; white-space:nowrap; }
.timeline .end { position:relative; padding:2px 0 0; }
.timeline .end::before { content:''; position:absolute; left:-34px; width:24px; top:27px; bottom:-30px;
  background:#101e29; }
/* The Progress tab: prestige bars and milestones, charts per time range, runs and actions. */
.progress { display:grid; gap:30px; } .progress-alone { margin-top:26px; }
.progress-alone > h2 { margin:0 0 16px; }
.prestige-when { font:normal clamp(22px,3vw,30px)/1.2 Georgia,serif; margin:4px 0 14px; overflow-wrap:anywhere; }
.goals { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(240px,100%),1fr)); gap:14px 34px;
  margin:0 0 14px; }
.goal-head { display:flex; flex-wrap:wrap; justify-content:space-between; gap:2px 12px; font-size:14px;
  margin:0 0 5px; }
.goal-count { color:var(--muted); font-variant-numeric:tabular-nums; }
.bar { height:8px; background:var(--line); border-radius:4px; overflow:hidden; }
.bar span { display:block; height:100%; background:var(--sea); }
.milestones { margin:0; padding-left:20px; font-size:14px; color:var(--muted); }
.progress .charts { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr)); gap:18px;
  margin:12px 0 18px; }
.range { border-bottom:1px solid #263d4a; padding:8px 0; }
.range > summary, .more > summary { cursor:pointer; color:var(--sea); font-weight:600; }
.more { margin-top:8px; } .more > summary { font-size:14px; font-weight:400; }
.progress .scroll { max-height:none; overflow-y:visible; overflow-x:auto; }
.progress-table th, .progress-table td { white-space:nowrap; }
.progress-table .text { text-align:left; white-space:normal; min-width:260px; }
.progress-table td.mark { text-align:center; } .progress-table td.done { color:var(--sea); }
.progress-table td.failed { color:#ffda8b; }
.toggles { display:grid; gap:10px; max-width:980px; }
.toggle { border-top:1px solid var(--line); padding-top:10px; }
.toggle > summary { cursor:pointer; color:var(--sea); font-size:17px; font-weight:650; }
.toggle .mini { margin-top:8px; } .toggle ul { font-size:14px; color:var(--muted); } .toggle form { margin:0; }
.toggle .cannot { color:var(--muted); font-size:13px; white-space:nowrap; }
.pets td:last-child { text-align:left; padding-left:24px; } .pets td:nth-child(2) { white-space:nowrap; }
.model .cannot { color:var(--muted); font-size:13px; white-space:nowrap; }
.model .where { display:block; color:var(--muted); font-size:12px; font-family:ui-monospace,Consolas,monospace; }
.visually-hidden { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); }
@media(max-width:900px) { .finds { grid-template-columns:minmax(0,1fr); } }
@media(max-width:900px) { .boxes, .rewards-row { grid-template-columns:minmax(0,1fr); } .box.wide { grid-column:auto; }
  .start .pair { grid-template-columns:minmax(0,1fr); } }
/* The page moves the bar (see the script); light runs along the line and the bobber floats. */
@keyframes flow { from { background-position:0 0; } to { background-position:160px 0; } }
@keyframes float { 0%, 100% { transform:translateY(0); } 50% { transform:translateY(-3px); } }
@media(prefers-reduced-motion:no-preference) {
  .line .cast { background:linear-gradient(90deg, var(--sea) 0, #c8f5ee 40px, var(--sea) 80px,
    var(--sea) 160px) 0 0 / 160px 2px repeat-x; animation:flow 1.6s linear infinite; }
  .line .bobber { animation:float 2.4s ease-in-out infinite; } }
</style>"""
SCRIPT = """<script nonce="{nonce}">
(function () {
  var panel = document.getElementById('panel');
  if (!panel) return;
  function showTab() {
    var wanted = (location.hash || '#run').slice(1);
    var tabs = panel.querySelectorAll('section.tab');
    var found = false;
    tabs.forEach(function (tab) { if (tab.getAttribute('data-tab') === wanted) found = true; });
    if (!found) wanted = 'run';
    tabs.forEach(function (tab) { tab.hidden = tab.getAttribute('data-tab') !== wanted; });
    panel.querySelectorAll('nav.tabs a').forEach(function (link) {
      link.className = link.getAttribute('href') === '#' + wanted ? 'current' : '';
    });
  }
  window.addEventListener('hashchange', showTab);
  showTab();
  // A button press reloads the page (post/redirect/get): it comes back to the tab it was pressed on (the redirect
  // keeps the form's #tab), scrolled where it was, with the same toggles open.
  panel.addEventListener('submit', function (event) {
    event.target.action = '/automation' + location.hash;
    var open = {};
    panel.querySelectorAll('details[data-key]').forEach(function (d) { open[d.getAttribute('data-key')] = d.open; });
    try { sessionStorage.setItem('back', JSON.stringify({y: window.scrollY, open: open})); } catch (e) {}
  });
  try {
    var back = JSON.parse(sessionStorage.getItem('back') || 'null');
    sessionStorage.removeItem('back');
    if (back) {
      panel.querySelectorAll('details[data-key]').forEach(function (d) {
        var key = d.getAttribute('data-key');
        if (key in back.open) d.open = back.open[key];
      });
      window.scrollTo(0, back.y);
    }
  } catch (e) {}
  // Next fish counts down here between refreshes. A refresh within half a second of the running count keeps it,
  // so the count never steps back and forth; a new cooldown starts a new count.
  var deadline = null;
  function syncCountdown() {
    var el = panel.querySelector('[data-left]');
    if (!el) { deadline = null; return; }
    var at = performance.now() + parseFloat(el.getAttribute('data-left')) * 1000;
    if (deadline === null || Math.abs(at - deadline) > 500) deadline = at;
  }
  // The XP line is moved here, never by the refresh: its place is level * 100 + percent, eased toward the newest
  // read every frame, so a big gain runs smoothly through the end of a level into the next. A lower level
  // (prestige) or reduced motion places it at once.
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var shown = null, last = null;
  function moveLine(now) {
    var line = panel.querySelector('.line[data-share]');
    if (!line) { shown = null; return; }
    var goal = Number(line.getAttribute('data-level')) * 100 + Number(line.getAttribute('data-share'));
    if (shown === null || still || goal < Math.floor(shown / 100) * 100) {
      shown = goal;
    } else {
      shown += (goal - shown) * (1 - Math.exp(-(now - last) / 450));
      if (Math.abs(goal - shown) < 0.02) shown = goal;
    }
    var share = shown - Math.floor(shown / 100) * 100;
    if (shown === goal) share = Number(line.getAttribute('data-share'));
    line.querySelector('.cast').style.width = share + '%';
    line.querySelector('.bobber').style.left = share + '%';
  }
  function tick(now) {
    var left = deadline === null ? null : Math.max(0, (deadline - performance.now()) / 1000);
    panel.querySelectorAll('[data-left]').forEach(function (el) {
      if (left !== null) el.textContent = left >= 0.05 ? 'in ' + left.toFixed(1) + ' s' : 'now';
    });
    moveLine(now);
    last = now;
    requestAnimationFrame(tick);
  }
  syncCountdown();
  requestAnimationFrame(function (now) { last = now; tick(now); });
  setInterval(function () {
    fetch('/automation/panel', {cache: 'no-store'}).then(function (r) { return r.text(); }).then(function (html) {
      var was = panel.getAttribute('data-running');
      // Keep where each scrolling list and the page were scrolled: the refresh replaces them all.
      var kept = {}, x = window.scrollX, y = window.scrollY;
      panel.querySelectorAll('.scroll').forEach(function (el, i) {
        kept[(el.getAttribute('aria-label') || '') + '#' + i] = [el.scrollTop, el.scrollLeft];
      });
      // ... and which toggles (phases, League, Assumptions) were open; one not seen before keeps its default.
      var open = {};
      panel.querySelectorAll('details[data-key]').forEach(function (d) {
        open[d.getAttribute('data-key')] = d.open;
      });
      panel.innerHTML = html;
      showTab();
      syncCountdown();
      moveLine(performance.now());
      // Toggles first: opening one moves what is below it, so scrolling is set back after.
      panel.querySelectorAll('details[data-key]').forEach(function (d) {
        var key = d.getAttribute('data-key');
        if (key in open) d.open = open[key];
      });
      panel.querySelectorAll('.scroll').forEach(function (el, i) {
        var at = kept[(el.getAttribute('aria-label') || '') + '#' + i];
        if (at) { el.scrollTop = at[0]; el.scrollLeft = at[1]; }
      });
      window.scrollTo(x, y);
      var now = panel.querySelector('[data-running]');
      // Load the page afresh, never reload: reloading a page a button press answered sends the press again.
      if (now && now.getAttribute('data-running') !== was) { location.replace('/automation' + location.hash); }
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


def _row(label, value, gain=None, left=None):
    """`left`: seconds the value counts down from; the page counts it down between refreshes."""
    counting = f' data-left="{left:.2f}"' if left is not None else ''
    shown = (f'<dd class="known"{counting}>{escape(str(value))}' if value is not None else '<dd class="unknown">not read')
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
        # The page moves the line itself from data-level and data-share (see SCRIPT); the inline widths are where
        # it stands without the script.
        line = (f'<div class="line" data-level="{level}" data-share="{share:.1f}" role="img" '
                f'aria-label="{escape(meta[0])}"><span class="cast" '
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
    if phase == 'WAITING_FOCUS':
        return 'Waiting for Discord'
    if phase == 'CAPTCHA':
        return 'Captcha: solve it, then Resume'
    if phase == 'READY_TO_PRESTIGE':
        return 'Ready to prestige'
    if status.get('paused') or phase in ('PAUSED', 'PAUSED_BY_YOU'):
        return 'Paused'
    if phase == 'NEEDS_REVIEW':
        return 'Needs you'
    if status.get('pending_kind') in ('refresh_profile', 'refresh_buffs', 'refresh_charms') or phase in (
            'RETRYING_STARTUP', 'BOOTSTRAPPING'):
        if status['trips']:
            return 'Refreshing stats'
        return {'refresh_buffs': 'Reading your buffs', 'refresh_charms': 'Reading your charms'}.get(
            status.get('pending_kind'), 'Reading your profile')
    kind = status.get('pending_kind') or ''
    if kind in ACCOUNT_READ_LABELS or kind.removesuffix('_page2') in ACCOUNT_READ_LABELS:
        return ACCOUNT_READ_LABELS[kind.removesuffix('_page2')]
    return 'Fishing'


def _button(action, label, secondary=False, extra=None):
    style = ' class="secondary"' if secondary else ''
    hidden = ''.join(f'<input type="hidden" name="{escape(name)}" value="{escape(str(value))}">'
                     for name, value in (extra or {}).items())
    return (f'<form method="post" action="/automation"><input type="hidden" name="action" value="{action}">'
            f'{hidden}<button type="submit"{style}>{escape(label)}</button></form>')


def _scroll(label, inner):
    return f'<div class="scroll" tabindex="0" role="region" aria-label="{escape(label)}">{inner}</div>'


def _charm_cells(charm):
    if charm is None:
        return '<td>?</td><td></td>'
    level = f'{"≥" if charm["at_least"] else ""}{charm["level"]}'
    if charm['needed'] is None:
        return f'<td>{level}</td><td>max</td>'
    return f'<td>{level}</td><td>{charm["into"]}/{charm["needed"]}</td>'


RARITIES = ('common', 'uncommon', 'rare', 'epic', 'legendary', 'mythic', 'super', 'artifact')


def _chest_rank(name):
    """Common chest first, up to legendary and super crates, then the artifact box; anything else last."""
    words = name.casefold().split()
    rarity = next((RARITIES.index(word) for word in words if word in RARITIES), len(RARITIES))
    return rarity, 'crate' in words, name.casefold()


def _finds_box(finds, charms=None):
    run, every = finds.get('run') or {}, finds.get('all') or {}
    charms = charms or {}

    def group(title, key, levels=False):
        names = sorted(set(every.get(key, {})) | set(run.get(key, {})),
                       key=lambda name: -every.get(key, {}).get(name, 0))
        if key == 'chests':
            names = sorted(names, key=_chest_rank)    # by rarity; the same rarity's chest before its crate
        if levels:
            # All eight charms, in /charms order, then anything else read as a charm.
            names = list(CHARM_NAMES) + [name for name in names + sorted(charms.get('charms', {}))
                                         if name not in CHARM_NAMES]
            names = list(dict.fromkeys(names))
        if not names:
            return f'<h3>{escape(title)}</h3><p class="empty">None yet.</p>'
        head = ('<th scope="col" title="This run">Run</th><th scope="col" title="All runs">All</th>' + (
            '<th scope="col" title="Level">Lvl</th><th scope="col" title="Charms to the next level">Next</th>'
            if levels else ''))
        rows = ''
        for name in names:
            rows += (f'<tr><th scope="row">{escape(name)}</th><td>{run.get(key, {}).get(name, 0):,}</td>'
                     f'<td>{every.get(key, {}).get(name, 0):,}</td>'
                     + (_charm_cells(charms.get('charms', {}).get(name)) if levels else '') + '</tr>')
        table = (f'<table class="mini{" charms" if levels else ""}"><thead><tr><th scope="col">'
                 f'<span class="visually-hidden">Name</span></th>{head}</tr></thead><tbody>{rows}</tbody></table>')
        # Eight charm rows always fit; other lists can grow long and scroll.
        return f'<h3>{escape(title)}</h3>' + (table if levels else _scroll(title, table))

    body = (f'<div class="finds"><div>{group("Chests", "chests")}</div>'
            f'<div>{group("Special fish", "special_fish")}</div>'
            f'<div>{group("Charms", "charms", levels=True)}</div></div>')
    note = 'Charms are a prestige requirement.'
    if charms:
        goal = (f'{charms["total"]:,} of {charms["goal"]:,}' if charms.get('goal') else
                f'{charms["total"]:,} (the goal shows once /charms or your prestige is read)')
        cap = f', each up to level {charms["max_level"]}' if charms.get('max_level') else ''
        note = (f'Charms toward prestige: {goal} ({charms["types"]} of {charms["type_count"]} kinds found{cap}). '
                'Each level needs as many charms as its number. ' +('From /charms, plus any found since it was read.' if charms.get('from_reply') else
                              '/charms not read yet: counted from the charms logged here; "≥" means the game '
                              'reported a higher level.'))
    return _box('Finds', body, wide=True, note=note)


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


def _levels(items):
    return ', '.join(f'{name} {item["level"]}/{item["max"]}' for name, item in items.items())


def _account_rows(sections):
    """(label, text, unread) per account reply, in reading order; text is None when never read."""
    def section(kind):
        found = sections.get(kind)
        return (found or {}).get('values'), (found or {}).get('unread') or []

    rows = []
    values, unread = section('shop_upgrades')
    rows.append(('Upgrades', _levels(values['items']) if values else None, unread))
    values, unread = section('rod')
    if values:
        owned = [name + (' (in use)' if name == values.get('current') else '') for name in values.get('owned') or []]
        text = ', '.join(owned) or 'none'
    rows.append(('Rods', text if values else None, unread))
    values, unread = section('shop_boats')
    if values:
        owned = [name for name, item in values['items'].items() if item['owned']]
        priced = [(item['price'], name) for name, item in values['items'].items() if item['price']]
        text = (', '.join(owned) or 'none') + (f'; {min(priced)[1]} next, ${min(priced)[0]:,}' if priced else '')
    rows.append(('Boats', text if values else None, unread))
    values, unread = section('bait')
    if values:
        owned = values.get('owned')
        text = f'{values["current"] or "None"} in use; ' + (
            'owned not read yet' if owned is None
            else ', '.join(f'{name} {count:,}' for name, count in owned.items()) or 'none owned')
    rows.append(('Bait', text if values else None, unread))
    values, unread = section('biome')
    rows.append(('Biome', ', '.join(values['unlocked'] or [values['current'] or '?']) if values else None, unread))
    values, unread = section('pet')
    if values:
        text = ', '.join(f'{name} {pet["level"]}' + (' (current)' if name == values['current'] else '')
                         for name, pet in values['owned'].items())
    rows.append(('Pets', text if values else None, unread))
    values, unread = section('prestige_shop')
    if values:
        owned = [name for name, bought in (values['upgrades'] or {}).items() if bought]
        text = (', '.join(owned) or 'none') + f'; Azure Fish: {values["azure_fish"]}'
    rows.append(('Prestige shop', text if values else None, unread))
    values, unread = section('shop_league')
    rows.append(('League', (_levels(values['items']) + f'; Hooks: {values["hooks"]}') if values else None, unread))
    values, unread = section('shop_special')
    if values:
        text = (_levels(values['items']) or 'nothing listed') + (
            f'; more at level {values["more_at_level"]}' if values.get('more_at_level') else '')
        if (values.get('pages') or 1) > 1 and '2' not in (sections['shop_special'].get('pages') or {}):
            text += '. Page 2 is not read yet; it is read with the next /shop special.'
    rows.append(('Special shop', text if values else None, unread))
    values, unread = section('boosts')
    rows.append(('Boosts', (f'global {values["global_duration"]}' if values['global_duration'] else 'none')
                 if values else None, unread))
    return rows


PET_STATS = (('fish_catch', 'Fish catch'), ('fish_quality', 'Fish quality'), ('treasure_chance', 'Treasure chance'),
             ('treasure_quality', 'Treasure quality'), ('xp', 'XP'))


@cache
def _pet_tables():
    """(each pet's stat weights from game_model.json, each pet's description from the wiki's pet.json); empty when
    either cannot be read. Read once."""
    from .game_model_data import load_game_data
    from .player_state import _pet_reference
    try:
        weights = load_game_data()['pets']
    except Exception:
        weights = {}
    reference = (_pet_reference() or {}).get('pets', {})
    return weights, {name: pet.get('description') for name, pet in reference.items()}


def _pets_box(account, profile):
    """Every pet owned (from /pet), its level against the max, its effects at that level and what it does. The
    effect is the game model's: weight × (0.1 + 0.0075 × level), checked against the game's pet screen (Dolphin 51:
    36.19%); Support Bait raises it further, which this leaves out."""
    sections = (account or {}).get('sections') or {}
    values = (sections.get('pet') or {}).get('values') or {}
    owned = values.get('owned') or {}
    if not owned:
        return _box('Pets', '<p class="empty">Not read yet: /pet is read at startup.</p>', wide=True)
    helper = (((sections.get('shop_league') or {}).get('values') or {}).get('items') or {}).get('Pet Helper') or {}
    top = 125 + 5 * (helper.get('level') or 0)
    in_use = values.get('current') or re.sub(r'\s*\(.*$', '', profile.get('pet') or '')
    weights, descriptions = _pet_tables()
    rows = ''
    for name, pet in sorted(owned.items(), key=lambda item: -(item[1].get('level') or 0)):
        level = pet.get('level') or 0
        effects = ', '.join(f'{label} +{weight * (0.1 + 0.0075 * level) * 100:.2f}%'
                            for stat, label in PET_STATS if (weight := (weights.get(name) or {}).get(stat)))
        xp = (f'{pet["xp_current"]:,} / {pet["xp_required"]:,} XP' if pet.get('xp_current') is not None
              and pet.get('xp_required') else '')
        rows += (f'<tr><th scope="row">{escape(name)}'
                 + (' <span class="gain">in use</span>' if name == in_use else '')
                 + (f'<span class="why">{escape(descriptions[name])}</span>' if descriptions.get(name) else '')
                 + f'</th><td>{level} / {top}' + (f'<span class="why">{escape(xp)}</span>' if xp else '')
                 + f'</td><td>{escape(effects or "not modelled")}</td></tr>')
    table = ('<table class="mini model pets"><thead><tr><th scope="col">Pet</th><th scope="col">Level</th>'
             f'<th scope="col">Effect at this level</th></tr></thead><tbody>{rows}</tbody></table>')
    return _box('Pets', table, wide=True,
                note='Max level 125, plus 5 per Pet Helper level. Effects are without Support Bait.')


def _account_box(account):
    """The account as replies last showed it (part 1's read-only check)."""
    sections = (account or {}).get('sections') or {}
    rows = ''
    for label, text, unread in _account_rows(sections):
        shown = escape(text) if text is not None else '<span class="unknown">Not read yet</span>'
        if unread:
            shown += f'<br><span class="note">Not read: {escape(", ".join(unread))}</span>'
        rows += f'<div class="stat"><dt>{escape(label)}</dt><dd class="known">{shown}</dd></div>'
    return _box('Account', f'<dl>{rows}</dl>', wide=True,
                note='As Virtual Fisher last showed it; read at startup and every re-read.')


def _fmt(value, digits=2):
    if value is None:
        return '—'
    return f'{value:,.{digits}f}' if abs(value) < 1000 else f'{value:,.0f}'


def render_model_tab(model) -> str:
    """The Model tab: setup, /buffs model vs game, per trip and hour, fish mix, estimates (progression part 2)."""
    if not model:
        return '<p class="empty">No model yet: it appears once the run has read your profile.</p>'
    if model.get('error'):
        return f'<p class="empty">{escape(model["error"])}</p>'
    setup = model['setup']
    boosts = ', '.join(setup['boosts']) or 'no boosts'
    read = f' · account read {escape(setup["read_at"][:16].replace("T", " "))} UTC' if setup.get('read_at') else ''
    head = (f'<p class="note">{escape(setup["rod"])} · {escape(setup["biome"])} · bait {escape(setup["bait"])} · '
            f'pet {escape(setup["pet"])} · {escape(boosts)}{read}</p>')
    buff_rows = ''
    for row in model['buffs']:
        mark = '' if row['ok'] is None else ('✓' if row['ok'] else '✗')
        tone = '' if row['ok'] is None else ('good' if row['ok'] else 'bad')
        note = ''
        if row['effect']:
            note = f'<span class="why">×{row["correction"]:.3f}: probably {escape(row["effect"])}</span>'
        elif row['ok'] is False:
            note = f'<span class="why">×{row["correction"]:.3f} applied to what-ifs</span>'
        note += ''.join(f'<span class="why">{escape(reason)}</span>' for reason in row['why'])
        buff_rows += (f'<tr><th scope="row">{escape(row["label"])}{note}</th><td>{_fmt(row["game"])}</td>'
                      f'<td>{_fmt(row["model"], 4)}</td><td class="{tone}">{mark}</td></tr>')
    buffs_box = _box('Buffs', '<table class="mini model"><thead><tr><th>Buff</th><th>Game</th><th>Model</th>'
                     f'<th></th></tr></thead><tbody>{buff_rows}</tbody></table>',
                     note='Not compared until /buffs has been read this run.' if all(
                         row['ok'] is None for row in model['buffs']) else '')
    trip_rows = ''
    for row in model['trip']:
        off = f' ⚠ {round_half_up(abs(row["factor"] - 1) * 100)}% off' if row['flag'] and row['factor'] else ''
        status = escape(row['status']) + off
        trip_rows += (f'<tr><th scope="row">{escape(row["label"])}</th><td>{_fmt(row["predicted"], 3)}</td>'
                      f'<td>{_fmt(row["measured"], 3)}</td><td>{_fmt(row["factor"], 3)}</td>'
                      f'<td>{_fmt(row["per_hour"])}</td><td>{status}</td></tr>')
    trip_box = _box('Per trip and per hour', '<table class="mini model"><thead><tr><th></th><th>Predicted</th>'
                    '<th>Measured</th><th>Factor</th><th>Per hour</th><th>Status</th></tr></thead>'
                    f'<tbody>{trip_rows}</tbody></table>', wide=True,
                    note='Per hour is the prediction × its factor. Measured is the mean over this setup\'s trips.')
    mix_rows = ''.join(f'<tr><th scope="row">{escape(row["fish"])}</th><td>{_fmt(row["predicted"] * 100, 1)}%</td>'
                       f'<td>{"—" if row["measured"] is None else _fmt(row["measured"] * 100, 1) + "%"}</td></tr>'
                       for row in model['mix']['rows'])
    mix_box = _box('Fish mix', '<table class="mini model"><thead><tr><th>Fish</th><th>Predicted</th>'
                   f'<th>Measured</th></tr></thead><tbody>{mix_rows}</tbody></table>',
                   note=model['mix']['source'])
    estimates = ''.join(f'<li>{escape(item)}</li>' for item in model['estimates'])
    estimates_box = _box('Estimates in use', f'<ul>{estimates}</ul>' if estimates else
                         '<p class="empty">None: every number comes from a formula or a measurement.</p>')
    return f'{head}<div class="boxes">{buffs_box}{mix_box}{trip_box}{estimates_box}</div>'


def _model_tab(model) -> str:
    """The Model tab, or a short error in its place: the Run tab never breaks over it."""
    try:
        return render_model_tab(model)
    except Exception as error:  # an unexpected value in the view
        return f'<p class="empty">The Model tab could not be shown: {escape(repr(error))}</p>'


def _duration(seconds) -> str:
    seconds = max(0, float(seconds or 0))
    if seconds < 3600:
        return f'{max(1, round_half_up(seconds / 60)):,} min'
    if seconds < 172800:
        return f'{seconds / 3600:.1f} h'
    return f'{seconds / 86400:.1f} days'


def _when(seconds) -> str:
    """When a step comes: "now" for one due at once, else its fishing time from now."""
    return 'now' if float(seconds or 0) < 1 else _duration(seconds)


def _trips(count) -> str:
    count = float(count or 0)
    return '' if count < 0.5 else f'{round_half_up(count):,}'


def _cost(step) -> str:
    if not step.get('cost'):
        return ''
    return f'${step["cost"]:,.0f}' if step.get('currency') == '$' else f'{step["cost"]:,.0f} {step["currency"]}'


def render_plan_tab(view) -> str:
    """The Plan tab: which phase the account is in, the boxes that act now (Ready to prestige, Queue, Next), the
    route as a timeline of phases down to prestige, and League and Assumptions as toggles under it."""
    view = view or {}
    plan = view.get('plan')
    # Queued steps stay in view even while there is no plan to show.
    queue = _queue_box(view) if view.get('queue') or view.get('action_log') else ''
    if plan is None:
        text = view.get('error') or ('Working out the plan…' if view.get('updating') else 'No plan yet.')
        return f'<p class="empty">{escape(text)}</p>' + (f'<div class="boxes">{queue}</div>' if queue else '')
    if plan.get('error'):
        return f'<p class="empty">{escape(plan["error"])}</p>' + (f'<div class="boxes">{queue}</div>' if queue else '')
    failed = ''
    if view.get('error'):
        # the plan below is the last one that worked: say so, and when it was made
        failed = (f'<p class="empty">{escape(str(view["error"]).rstrip("."))}. Showing the last good plan, made '
                  f'{escape(view.get("made_at") or "earlier")}.</p>')
    made = f'Made {view.get("made_at") or ""}' + (' · updating' if view.get('updating') else '')
    step = plan.get('next')
    next_box = _box('Next', '<p class="next">' + (escape(
        (f'Sell ${step["sell"]:,.0f} of fish, then ' if step.get('sell') else '') + step['action']
        + (f' in {step["where"]}' if step.get('where') else '')
        + (f' ({_cost(step)})' if _cost(step) else '') + f' — {_in_about(step)}')
        if step else 'Nothing to do.') + '</p>', note=(step.get('why') or '') if step else '')
    # The route by phase, in route order; a step from before the phase keys counts its label as its phase.
    phases = {}
    for item in plan.get('steps', []):
        phases.setdefault(item.get('phase') or item.get('phase_label'), []).append(item)
    names = list(phases)
    now = plan.get('phase_now')
    # At prestige no phase is current (the Prestige end node is); a plan from before phase_now: the first phase.
    at = names.index(now) if now in names else -1 if now == 'prestige' else 0
    if now in phases:
        label = phases[now][0]['phase_label']
    elif now == 'prestige':
        label = 'prestige phase'
    else:   # a plan from before phase_now: the first step's phase
        label = phases[names[0]][0]['phase_label'] if names and now is None else None
    head = ('<div class="plan-head">'
            + (f'<p class="plan-now">You are in the {escape(label)}</p>' if label else '')
            + (f'<p class="plan-intro">{escape(PHASE_INTRO[now])}</p>' if now in PHASE_INTRO else '')
            + f'<p class="note">{escape(made)}</p></div>')
    sections = ''.join(_phase_section(name, phases[name], index == at, index == at + 1)
                       for index, name in enumerate(names))
    timeline = f'<div class="timeline">{sections}{_prestige_node(plan)}</div>'
    notes = ''.join(f'<li>{escape(line)}</li>' for line in plan.get('assumptions', []))
    toggles = (f'<div class="toggles">{_league_box(plan)}<details class="toggle" data-key="assumptions">'
               f'<summary>Assumptions and estimates '
               f'({len(plan.get("assumptions", [])):,})</summary>'
               + (f'<ul>{notes}</ul>' if notes else '<p class="empty">None.</p>') + '</details></div>')
    return (f'{failed}{head}<div class="boxes plan-boxes">{_prestige_ready_box(plan)}{_queue_box(view)}{next_box}'
            f'</div>{timeline}{toggles}')


def _in_about(item) -> str:
    return 'now' if _when(item.get('seconds')) == 'now' else f'in about {_duration(item["seconds"])}'


def _phase_section(phase, steps, is_now, is_next):
    """One phase on the timeline: a heading node (filled for the phase the account is in), then its steps.
    The current phase and the next one start open."""
    ready = sum(1 for item in steps if item.get('do') and item.get('status', 'ready') == 'ready')
    count = f'{len(steps):,} step{"" if len(steps) == 1 else "s"} · {ready:,} ready'
    intro = PHASE_INTRO.get(phase)
    summary = (f'<summary><span class="node big{" now" if is_now else ""}"></span>'
               f'<span class="phase-title">{escape(steps[0]["phase_label"])}</span>'
               f'<span class="phase-when">{escape(_in_about(steps[0]))}</span>'
               f'<span class="phase-count">{escape(count)}</span>'
               + (f'<span class="phase-intro">{escape(intro)}</span>' if intro else '') + '</summary>')
    # data-key: the panel refresh keeps each phase open or closed as the person left it.
    return (f'<details class="phase"{" open" if is_now or is_next else ""} data-key="{escape(str(phase))}">'
            f'{summary}<ol class="steps">'
            + ''.join(_step_node(item) for item in steps) + '</ol></details>')


def _step_node(item):
    """A step: a small node coloured by its mark, the action, one muted line about it, and Do it (or why not)."""
    status = item.get('status', 'ready') if item.get('do') else 'none'
    when, trips = _when(item.get('seconds')), _trips(item.get('trips'))
    meta = [item.get('where') or '', _cost(item), f'sell ${item["sell"]:,.0f}' if item.get('sell') else '',
            f'{trips} trip{"" if trips == "1" else "s"}' if trips else '',
            when if when == 'now' else f'in {when}', item.get('why') or '']
    do = _do_cell(item)
    return (f'<li class="step"><span class="node {escape(status)}"></span><div class="step-text">'
            f'<strong>{escape(item["action"])}</strong>'
            f'<span class="meta">{escape(" · ".join(part for part in meta if part))}</span></div>'
            + (f'<div class="step-do">{do}</div>' if do else '') + '</li>')


def _prestige_node(plan):
    """The end of the line: when prestige comes and what to buy then, or what is still missing."""
    prestige = plan.get('prestige') or {}
    if prestige.get('reached'):
        text = f'Prestige in about {_duration(prestige["seconds"])}.'
        if prestige.get('shop'):
            text += ' Then buy ' + ', '.join(prestige['shop']) + ' in /prestige shop.'
    else:
        text = 'Not reached in this plan. Missing: ' + ', '.join(prestige.get('missing') or ['?']) + '.'
    if plan.get('cut_short'):
        text += ' Plan cut short.'
    now = ' now' if plan.get('phase_now') == 'prestige' else ''    # filled: the account is at prestige
    return (f'<div class="end"><span class="node big{" reached" if prestige.get("reached") else ""}{now}"></span>'
            f'<p class="phase-title">Prestige</p><p class="meta">{escape(text)}</p></div>')


def _do_cell(item):
    """A step's last cell: Do it when it is ready (part 5's mark; a step from before the marks counts as ready),
    else why not. A step with nothing to send has none."""
    if not item.get('do'):
        return ''
    status = item.get('status', 'ready')
    if status == 'ready':
        return _button('queue', 'Do it', secondary=True,
                       extra={'label': item['action'], 'actions': json.dumps(item['do'])})
    return f'<span class="cannot">{"Later phase" if status == "later" else "Cannot afford"}</span>'


def _league_box(plan):
    """The league track, as a toggle under the timeline: bought with Hooks, which come rarely, so it never holds
    the main route up. It starts open when a league step is ready."""
    league = plan.get('league') or []
    rows = ''.join(f'<tr><td>{escape(item["action"])}</td><td>{escape(_cost(item))}</td>'
                   f'<td>{escape(item.get("why") or "")}</td><td>{_do_cell(item)}</td></tr>'
                   for item in league)
    body = (f'<table class="mini model"><tbody>{rows}</tbody></table>' if rows
            else '<p class="empty">Nothing to buy in /shop league.</p>')
    ready = any(item.get('do') and item.get('status', 'ready') == 'ready' for item in league)
    return (f'<details class="toggle"{" open" if ready else ""} data-key="league">'
            f'<summary>League (Hooks: {plan.get("hooks", 0):,})</summary>{body}</details>')


def _prestige_ready_box(plan):
    """Shown once the account meets the prestige requirements; prestiging is done by hand."""
    if plan.get('phase_now') != 'prestige':
        return ''
    shop = (plan.get('prestige') or {}).get('shop') or []
    text = 'The requirements are met. Prestige by hand' + (
        f', then buy {", ".join(shop)} in /prestige shop.' if shop else '.')
    return _box('Ready to prestige', f'<p>{escape(text)}</p>')


def _queue_box(view):
    """The steps queued with Do it (each with remove) and the last results, newest first."""
    waiting = ''.join(f'<li>{escape(entry["label"])} '
                      + _button('unqueue', '✕', secondary=True, extra={'id': entry['id']}) + '</li>'
                      for entry in view.get('queue') or [])
    marks = {'done': '✓', 'failed': '✗', 'unknown': '?'}
    results = ''.join(f'<li>{escape(r["label"])} skipped: {escape(r["note"])}</li>' if r['result'] == 'skipped'
                      else f'<li>{marks.get(r["result"], "")} {escape(r["label"])}: {escape(r["note"])}</li>'
                      for r in view.get('action_log') or [])
    body = (f'<ul class="queue">{waiting}</ul>' if waiting
            else '<p class="empty">Nothing queued. Press Do it on a step to carry it out between trips.</p>')
    executor = view.get('executor') or {}
    following = executor.get('follow')
    toggle = (_button('follow', 'Stop following', secondary=True, extra={'on': '0'}) if following
              else _button('follow', 'Follow the plan', extra={'on': '1'}))
    held = ''.join(f'<li>{escape(entry["label"])} held back until trip {entry["until"]:,}</li>'
                   for entry in executor.get('held') or [])
    lead = ('<p class="note">Following the plan: ready steps are queued by themselves.</p>' if following else '')
    return _box('Queue', toggle + lead + body + (f'<ul class="note">{held}</ul>' if held else '')
                + (f'<ul class="note">{results}</ul>' if results else ''))


def _plan_tab(view) -> str:
    try:
        return render_plan_tab(view)
    except Exception as error:
        return f'<p class="empty">The Plan tab could not be shown: {escape(repr(error))}</p>'


PROGRESS_RANGES = (('7', 'Last 7 days'), ('30', 'Last 30 days'), ('all', 'All time'))
PROGRESS_CHARTS = (('level', 'Level', False), ('charms', 'Charms found', False), ('xp_per_hour', 'XP per hour', False),
                   ('trips', 'Trips per day', False), ('net_worth', 'Net worth', True))
RESULT_MARKS = {'done': '✓', 'failed': '✗'}
RUNS_SHOWN, ACTIONS_SHOWN = 20, 50


def _local_time(value) -> str:
    """A UTC ISO time (some end in Z) as local time, YYYY-MM-DD HH:MM; anything else as it came."""
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return str(value or '')
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime('%Y-%m-%d %H:%M')


def _amount(value, money=False) -> str:
    if not isinstance(value, (int, float)):
        return '?'
    return f'${value:,.0f}' if money else f'{value:,.0f}'


def _prestige_box(countdown) -> str:
    missing = [str(item) for item in countdown.get('missing') or []]
    if countdown.get('reached'):
        lead = f'Prestige in about {_duration(countdown.get("seconds"))}'
    elif missing == ['No plan yet.']:
        lead = missing[0]
    else:
        lead = 'Not reached in this plan.' + (f' Missing: {", ".join(missing)}' if missing else '')
    bars = ''
    for bar in countdown.get('bars') or []:
        now, goal, money = bar.get('now'), bar.get('goal'), bar.get('label') == 'Money'
        share = (max(0.0, min(100.0, now / goal * 100))
                 if isinstance(now, (int, float)) and isinstance(goal, (int, float)) and goal > 0 else 0.0)
        bars += (f'<div class="goal"><div class="goal-head"><span>{escape(str(bar.get("label") or ""))}</span>'
                 f'<span class="goal-count">{escape(_amount(now, money))} / {escape(_amount(goal, money))}</span></div>'
                 f'<div class="bar"><span style="width:{share:.0f}%"></span></div></div>')
    milestones = ''.join(
        f'<li>{escape(str(item.get("label") or ""))}'
        + (f' — in about {escape(_duration(item["seconds"]))}' if isinstance(item.get('seconds'), (int, float))
           else '') + '</li>'
        for item in countdown.get('milestones') or [])
    return _box('Prestige', f'<p class="prestige-when">{escape(lead)}</p>'
                + (f'<div class="goals">{bars}</div>' if bars else '')
                + (f'<ul class="milestones">{milestones}</ul>' if milestones else ''))


def _progress_charts(charts) -> str:
    sections = ''
    for key, title in PROGRESS_RANGES:
        series = charts.get(key) or {}
        figures = ''.join(
            line_chart_svg(label, list(series.get(name) or []),
                           (lambda v: f'${v:,.0f}') if money else (lambda v: f'{v:,.0f}'))
            for name, label, money in PROGRESS_CHARTS)
        sections += (f'<details class="range" data-key="range-{key}"{" open" if key == "7" else ""}>'
                     f'<summary>{escape(title)}</summary><div class="charts">{figures}</div></details>')
    return f'<section class="box progress-charts"><h2>Charts</h2>{sections}</section>'


def _progress_table(label, head, rows, shown, key, more) -> str:
    """A table of the first `shown` rows, the rest behind a toggle; each scrolls sideways on a narrow screen."""
    def table(part):
        return _scroll(label, f'<table class="mini progress-table"><thead><tr>{head}</tr></thead>'
                              f'<tbody>{"".join(part)}</tbody></table>')
    html = table(rows[:shown])
    rest = rows[shown:]
    if rest:
        html += (f'<details class="more" data-key="{key}"><summary>{len(rest):,} more {more}</summary>'
                 f'{table(rest)}</details>')
    return html


def _runs_box(runs) -> str:
    if not runs:
        return _box('Runs', '<p class="empty">No runs recorded yet.</p>')
    head = (''.join(f'<th scope="col">{name}</th>' for name in
                    ('Started', 'Length', 'Trips', 'XP', 'Chests', 'Charms'))
            + '<th scope="col" class="text">Stopped</th>')
    rows = [f'<tr><th scope="row">{escape(_local_time(run.get("start")))}</th>'
            f'<td>{escape(_duration(run["seconds"])) if isinstance(run.get("seconds"), (int, float)) else ""}</td>'
            + ''.join(f'<td>{escape(_amount(run.get(name)))}</td>' for name in ('trips', 'xp', 'chests', 'charms'))
            + f'<td class="text">{escape(str(run.get("stopped") or ""))}</td></tr>' for run in runs]
    return _box('Runs', _progress_table('Runs', head, rows, RUNS_SHOWN, 'runs-more', 'runs'))


def _actions_box(actions) -> str:
    if not actions:
        return _box('Actions', '<p class="empty">No actions carried out yet.</p>')
    head = ('<th scope="col">When</th><th scope="col" class="text">Step</th><th scope="col">Result</th>'
            '<th scope="col" class="text">Note</th><th scope="col">Cost</th>')
    rows = []
    for action in actions:
        result = str(action.get('result') or 'unknown')
        rows.append(f'<tr><th scope="row">{escape(_local_time(action.get("at")))}</th>'
                    f'<td class="text">{escape(str(action.get("label") or ""))}</td>'
                    f'<td class="mark {escape(result)}" title="{escape(result)}">'
                    f'{RESULT_MARKS.get(result, "?")}</td>'
                    f'<td class="text">{escape(str(action.get("note") or ""))}</td>'
                    f'<td>{escape(str(action.get("cost") or ""))}</td></tr>')
    return _box('Actions', _progress_table('Actions', head, rows, ACTIONS_SHOWN, 'actions-more', 'actions'))


def render_progress_tab(progress) -> str:
    """Part 6: how far prestige is (bars and milestones), charts over 7 days / 30 days / all time, past runs and
    the actions carried out."""
    progress = progress or {}
    view = progress.get('view')
    if view is None:
        if progress.get('error'):
            return f'<p class="empty">{escape(str(progress["error"]))}</p>'
        return '<p class="empty">Working out progress…</p>'
    notes = ' '.join(str(note) for note in view.get('notes') or [])
    return ('<div class="progress">'
            f'{_prestige_box(view.get("countdown") or {})}{_progress_charts(view.get("charts") or {})}'
            f'{_runs_box(view.get("runs") or [])}{_actions_box(view.get("actions") or [])}'
            + (f'<p class="note">{escape(notes)}</p>' if notes else '') + '</div>')


def _progress_tab(progress) -> str:
    try:
        return render_progress_tab(progress)
    except Exception as error:
        return f'<p class="empty">The Progress tab could not be shown: {escape(repr(error))}</p>'


def render_panel(status, mode=None) -> str:
    """The live part of the page: run status, level line, and the stat boxes. With no run since the server started,
    the Progress view from the account's files (when there is one) under the "No run yet" line."""
    if not status or 'running' not in status:
        progress = (f'<section class="progress-alone"><h2>Progress</h2>{_progress_tab(status.get("progress"))}'
                    '</section>') if status else ''
        return ('<div data-running="0"><p class="empty">No run yet. Start one above; your profile, buffs and '
                f'inventory appear here once Virtual Fisher replies.</p>{progress}</div>')
    running = status['running']
    label = state_label(status)
    buttons = ''
    if running:
        toggle = (_button('resume', 'Resume', extra={'pause_id': status.get('pause_id', 0)}) if status.get('paused')
                  else _button('pause', 'Pause', secondary=True))
        buttons = f'<div class="actions">{toggle}{_button("stop", "Stop now")}</div>'
    active = label in ('Fishing', 'Refreshing stats') or label.startswith('Reading ')
    tone = 'on' if active else ('halted' if running else '')
    reason = escape(status['reason'])
    next_fish = status.get('next_eligible_in')
    if running and next_fish is not None and status['reason'].startswith('Next fish in '):
        # Counted down in the page, from the same value as the Next fish row.
        reason = (f'Next fish <span data-left="{next_fish:.2f}">'
                  + (f'in {next_fish:.1f} s' if next_fish else 'now') + '</span>.')
    strip = (f'<div class="strip {tone}"><span class="phase">{escape(label)}</span>'
             f'<span class="why">{reason}</span>{buttons}</div>')
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
    sync_note = ''
    if dashboard.get('next_sync_in') is not None and dashboard.get('synced_at'):
        sync_note = (f'Profile and buffs read {dashboard["trips_since_sync"]:,} trips ago; read again in '
                     f'{dashboard["next_sync_in"]:,} trips. Between reads, numbers are worked out from each trip.')
        uncounted = dashboard.get('uncounted_since_sync') or 0
        if uncounted:
            sync_note += (f' {uncounted:,} trip{"s" if uncounted != 1 else ""} since then had no confirmed reply, so '
                          'their XP and money are not counted until /profile is read, next.')
    for kind, unread in (dashboard.get('unread') or {}).items():
        sync_note += (f' Not read from /{kind}: {", ".join(unread["fields"])}; reading /{kind} again in '
                      f'{unread["retry_in"]:,} trips.')
    run_box = _box('This run', '<dl>' + ''.join((
        _row('Mode', 'Live' if mode == 'live' else 'Simulation'),
        _row('Trips', f'{status["trips"]:,}' + (f' of {limit:,}' if limit else '')),
        _row('Fish caught', _num(run.get('catches'))),
        _row('XP gained', _num(run.get('xp'))),
        _row('Trips per hour', _num(per_hour.get('trips'))),
        _row('XP per hour', _num(per_hour.get('xp'))),
        _row('Fish value per hour', _num(per_hour.get('fish_value'), '$')),
        _row('Next fish', (f'in {next_fish:.1f} s' if next_fish else 'now') if running else 'not running',
             left=next_fish if running and next_fish is not None else None),
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
                         note=' '.join(filter(None, (
                             'Green numbers are added since your profile was last read.' if changes else '',
                             (', '.join(dashboard['unlisted']) + ' are counted from the finds logged here: your '
                              'profile does not list them until level 50.') if dashboard.get('unlisted') else ''))))

    recent = dashboard.get('recent') or []
    items = ''.join(
        f'<li><span>{escape(_catch_text(trip["catches"]))}'
        + ''.join(f'<br><span class="note">{escape(event)}</span>' for event in trip['events'])
        + f'</span><span>+{trip["xp"]:,} XP</span></li>' for trip in recent)
    trips_box = _box('Recent trips', _scroll('Recent trips', f'<ul class="trips-list">{items}</ul>')
                     if recent else '<p class="empty">No trips yet.</p>')

    run_tab = (f'{_level_line(profile, dashboard)}'
               f'<div class="boxes">{profile_box}{buffs_box}{run_box}'
               # Rewards takes a narrow share of its row so the finds tables (charms most of all) fit without scrolling.
               f'<div class="rewards-row">{rewards_box}{_finds_box(dashboard.get("finds") or {}, dashboard.get("charms"))}'
               f'</div>{_pets_box(status.get("account"), profile)}{inventory_box}{trips_box}'
               f'{_account_box(status.get("account"))}</div>')
    return (f'<div data-running="{1 if running else 0}">{strip}'
            '<nav class="tabs"><a href="#run">Run</a><a href="#model">Model</a><a href="#plan">Plan</a>'
            '<a href="#progress">Progress</a></nav>'
            f'<section class="tab" data-tab="run">{run_tab}</section>'
            f'<section class="tab" data-tab="model" hidden>{_model_tab(status.get("model"))}</section>'
            f'<section class="tab" data-tab="plan" hidden>{_plan_tab({**(status.get('plan') or {}), 'queue': status.get('queue'), 'executor': status.get('executor'), 'action_log': status.get('action_log')})}</section>'
            f'<section class="tab" data-tab="progress" hidden>{_progress_tab(status.get("progress"))}</section></div>')


def _field(name, label, value, hint=''):
    return (f'<div><label for="{name}">{escape(label)}</label>'
            f'<input id="{name}" name="{name}" type="text" value="{escape(value or "")}" autocomplete="off">'
            + (f'<p class="note">{escape(hint)}</p>' if hint else '') + '</div>')


def render_automation(head, status, notice='', live_problem='', settings=None, mode=None, nonce=''):
    """Render the page. `status` is AutomationController.status(), or None before the first run; `mode` is the
    current run's mode; `settings` holds the last live account, username and channel."""
    settings = settings or {}
    running = bool(status and status.get('running'))
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
