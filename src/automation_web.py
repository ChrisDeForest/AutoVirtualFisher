"""The /automation page: start a live or simulated fishing run, watch it, stop it, and resolve anything it could
not settle on its own. Live runs type into the Discord window; simulated runs never touch Discord."""
from html import escape

MAX_TRIPS, MAX_MINUTES = 1000, 600
DEFAULT_CHANNEL = 'Virtual Fisher'


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


def _row(label, value):
    return f'<div class="stat"><dt>{escape(label)}</dt><dd class="known">{escape(str(value))}</dd></div>'


def _field(name, label, value, hint=''):
    return (f'<label for="{name}">{escape(label)}</label>'
            f'<input id="{name}" name="{name}" type="text" value="{escape(value or "")}" autocomplete="off">'
            + (f'<p class="note">{escape(hint)}</p>' if hint else ''))


def render_automation(head, status, notice='', live_problem='', settings=None, mode=None):
    """Render the page. `status` is AutomationController.status(), or None before the first run; `mode` is the
    current run's mode; `settings` holds the last live account, username and channel."""
    settings = settings or {}
    running = bool(status and status['running'])
    refresh = '<meta http-equiv="refresh" content="2;url=/automation">' if running else ''
    head = head.replace('<title>Player logbook · AutoVirtualFisher</title>',
                        f'<title>Automation · AutoVirtualFisher</title>{refresh}')
    notice_html = f'<p class="save-status">{escape(notice)}</p>' if notice else ''
    live = mode == 'live'
    if running:
        control = ('<form method="post" action="/automation"><input type="hidden" name="action" value="stop">'
                   '<div class="actions"><button type="submit">Stop now</button></div></form>'
                   '<p class="note">Stop sends nothing further. A command already handed over is not undone.</p>')
        if live:
            control = ('<p class="status"><strong>Switch to Discord</strong> to fish. The run types into the '
                       'Discord window only while it is in front; switching away pauses it until you come back.'
                       '</p>') + control
    else:
        live_attrs = ' disabled' if live_problem else ' checked'
        sim_attrs = ' checked' if live_problem else ''
        control = ('<form method="post" action="/automation"><input type="hidden" name="action" value="start">'
                   '<fieldset><legend>Mode</legend>'
                   f'<label><input type="radio" name="mode" value="live"{live_attrs}> Live: fish in Discord</label>'
                   f'<label><input type="radio" name="mode" value="simulation"{sim_attrs}> Simulation: '
                   'nothing is sent</label>'
                   + (f'<p class="note">{escape(live_problem)}</p>' if live_problem else '') + '</fieldset>'
                   + _field('account', 'Virtual Fisher name (as in "Inventory of …")', settings.get('account'))
                   + _field('username', 'Discord username (as in "…\'s current multipliers")', settings.get('username'))
                   + _field('channel', 'Channel', settings.get('channel') or DEFAULT_CHANNEL,
                            'The DM with Virtual Fisher, as Discord\'s search box names it.')
                   + f'<label for="max_trips">Stop after this many trips (blank: no limit, up to {MAX_TRIPS:,})</label>'
                   f'<input id="max_trips" name="max_trips" type="number" min="1" max="{MAX_TRIPS}" value="">'
                   f'<label for="max_minutes">…or this many minutes (blank: no limit, up to {MAX_MINUTES})</label>'
                   f'<input id="max_minutes" name="max_minutes" type="number" min="1" max="{MAX_MINUTES}" value="">'
                   '<div class="actions"><button type="submit">Start</button></div></form>'
                   '<p class="note">Names are used only for live runs. Simulation uses a simulated player.</p>')
    details = ''
    if status:
        last = status['last_result']
        limit = status['config']['max_trips']
        details = '<dl>' + ''.join(_row(label, value) for label, value in (
            ('Mode', 'Live' if live else 'Simulation'),
            ('State', status['phase']), ('Why', status['reason']),
            ('Trips this run', f'{status["trips"]:,}' + (f' of {limit:,}' if limit else '')),
            ('Confirmed trips (all runs)', f'{status["total_trips"]:,}'),
            ('Waiting for reply to', status['pending'] or 'nothing'),
            ('Last result', f'{last["result"]} ({last["action_id"]})' if last else 'none yet'),
            ('Next fish in', f'{status["next_eligible_in"]:.1f} s' if status['next_eligible_in'] else 'now'),
        )) + '</dl>'
        for action in status['unresolved']:
            details += ('<form method="post" action="/automation" class="actions">'
                        '<input type="hidden" name="action" value="resolve">'
                        f'<input type="hidden" name="id" value="{escape(action)}">'
                        f'<p class="status">Outcome unknown: {escape(action)}. Check Discord, then say what '
                        'happened.</p>'
                        '<button class="secondary" type="submit" name="outcome" value="happened">It happened</button>'
                        '<button class="secondary" type="submit" name="outcome" value="not_sent">It was not sent'
                        '</button></form>')
    return (f'<!doctype html><html lang="en">{head}<body>'
            '<header><a class="brand" href="/">AutoVirtualFisher</a><span class="local">Automation</span></header>'
            '<main><h1>Automation</h1>'
            '<p class="intro">The fishing loop refreshes your profile and cooldown, fishes, reads each reply, and '
            'waits out the cooldown. Anything it cannot read is noted in Needs review and the run carries on; '
            'three unknown outcomes in a row stop it. Live trips go to your automation logbook; simulated trips are '
            'kept apart.</p>'
            f'{notice_html}{control}<section class="dashboard"><h2>Run</h2>'
            f'{details or "<p class=status>No run yet.</p>"}</section></main></body></html>')
