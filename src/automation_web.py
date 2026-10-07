"""The /automation page: run the fishing loop against the simulation, watch it, stop it, and resolve uncertain
commands. Live mode is not implemented (there is no live transport)."""
from html import escape

MAX_TRIPS, MAX_MINUTES = 1000, 120


def parse_limits(fields: dict) -> tuple[int | None, int | None, str]:
    """(trip limit, minute limit, problem) from the start form; the problem is '' when both are valid."""
    try:
        trips, minutes = int(fields.get('max_trips', [''])[0]), int(fields.get('max_minutes', [''])[0])
    except ValueError:
        return None, None, f'Enter whole numbers: trips between 1 and {MAX_TRIPS}, minutes between 1 and {MAX_MINUTES}.'
    if not 1 <= trips <= MAX_TRIPS or not 1 <= minutes <= MAX_MINUTES:
        return None, None, f'Trips must be between 1 and {MAX_TRIPS} and minutes between 1 and {MAX_MINUTES}.'
    return trips, minutes, ''


def _row(label, value):
    return f'<div class="stat"><dt>{escape(label)}</dt><dd class="known">{escape(str(value))}</dd></div>'


def render_automation(head, status, notice=''):
    """Render the page. `status` is AutomationController.status(), or None before the first run."""
    running = bool(status and status['running'])
    refresh = '<meta http-equiv="refresh" content="2;url=/automation">' if running else ''
    head = head.replace('<title>Player logbook · AutoVirtualFisher</title>',
                        f'<title>Automation · AutoVirtualFisher</title>{refresh}')
    notice_html = f'<p class="save-status">{escape(notice)}</p>' if notice else ''
    if running:
        control = ('<form method="post" action="/automation"><input type="hidden" name="action" value="stop">'
                   '<div class="actions"><button type="submit">Stop now</button></div></form>'
                   '<p class="note">Stop sends nothing further. A command already handed over is not undone; its '
                   'outcome stays in the journal.</p>')
    else:
        control = ('<form method="post" action="/automation"><input type="hidden" name="action" value="start">'
                   f'<label for="max_trips">Stop after this many trips (1–{MAX_TRIPS})</label>'
                   '<input id="max_trips" name="max_trips" type="number" min="1" max="1000" value="25">'
                   f'<label for="max_minutes">…or this many minutes (1–{MAX_MINUTES})</label>'
                   '<input id="max_minutes" name="max_minutes" type="number" min="1" max="120" value="15">'
                   '<div class="actions"><button type="submit">Start simulated run</button></div></form>')
    details = ''
    if status:
        last = status['last_result']
        details = '<dl>' + ''.join(_row(label, value) for label, value in (
            ('State', status['phase']), ('Why', status['reason']),
            ('Trips this run', f'{status["trips"]:,}' + (f' of {status["config"]["max_trips"]:,}'
                                                        if status['config']['max_trips'] else '')),
            ('Confirmed trips (all runs)', f'{status["total_trips"]:,}'),
            ('Waiting for reply to', status['pending'] or 'nothing'),
            ('Last confirmed result', f'{last["result"]} ({last["action_id"]})' if last else 'none yet'),
            ('Next fish in', f'{status["next_eligible_in"]:.1f} s' if status['next_eligible_in'] else 'now'),
        )) + '</dl>'
        for action in status['unresolved']:
            details += ('<form method="post" action="/automation" class="actions">'
                        '<input type="hidden" name="action" value="resolve">'
                        f'<input type="hidden" name="id" value="{escape(action)}">'
                        f'<p class="status">Outcome unknown: {escape(action)}. Check Discord, then say what '
                        'happened. It will not be resent.</p>'
                        '<button class="secondary" type="submit" name="outcome" value="happened">It happened</button>'
                        '<button class="secondary" type="submit" name="outcome" value="not_sent">It was not sent'
                        '</button></form>')
    return (f'<!doctype html><html lang="en">{head}<body>'
            '<header><a class="brand" href="/">AutoVirtualFisher</a><span class="local">Automation · '
            'Simulation</span></header><main><h1>Automation</h1>'
            '<p class="intro">Mode: <strong>Simulation</strong>. The fishing loop runs against a simulated '
            'Virtual Fisher: it refreshes your profile and cooldown, fishes, confirms each reply, and waits out '
            'the cooldown. Every command is journaled before it is sent; anything uncertain stops the loop until '
            'you resolve it. Simulated trips are saved apart from your logbook.</p>'
            '<p class="note">Live mode: not implemented. No live transport exists, so nothing here sends '
            'anything to Discord. Project direction: docs/planning/interaction-feasibility.md.</p>'
            f'{notice_html}{control}<section class="dashboard"><h2>Run</h2>'
            f'{details or "<p class=status>No run yet.</p>"}</section></main></body></html>')
