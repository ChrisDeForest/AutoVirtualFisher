"""HTML for the live capture page: start/stop, status, activity, and the review queue."""
from html import escape


def _options(names, selected=''):
    return ''.join(f'<option value="{escape(name)}"{" selected" if name == selected else ""}>{escape(name)}</option>'
                   for name in names)


def _review(items):
    if not items:
        return '<p class="status">Nothing waiting for review.</p>'
    html = ''
    for item in items:
        reasons = ''.join(f'<li>{escape(reason)}</li>' for reason in item['reasons'])
        html += (f'<article class="review"><p class="note">{escape(item["captured_at"])} · '
                 f'{escape(item["kind"])} · {escape(str(item.get("account") or ""))}</p>'
                 f'<ul>{reasons}</ul><pre>{escape(item["text"])}</pre><div class="actions">'
                 '<form method="post" action="/"><input type="hidden" name="action" value="sort">'
                 f'<input type="hidden" name="everything" value="{escape(item["text"])}">'
                 '<button class="secondary" type="submit">Load into boxes</button></form>'
                 '<form method="post" action="/live"><input type="hidden" name="action" value="dismiss">'
                 f'<input type="hidden" name="id" value="{escape(item["id"])}">'
                 '<button class="secondary" type="submit">Dismiss</button></form></div></article>')
    return html


def render_live(head, status, accounts, bait_options, review_items, notice='', keeping=None):
    """Render the page. `head` is the main page's <head> markup, reused for identical styling."""
    running = status['running']
    refresh = '<meta http-equiv="refresh" content="5;url=/live">' if running else ''
    head = head.replace('<title>Player logbook · AutoVirtualFisher</title>',
                        f'<title>Live capture · AutoVirtualFisher</title>{refresh}')
    if running:
        watching = (f'Watching {status["account"]}' + (f' ({status["username"]})' if status.get('username') else '')
                    + (f' with {status["bait"]}' if status['bait'] else '') + '.')
        control = (f'<p class="save-status">{escape(watching)}</p>'
                   '<form method="post" action="/live"><input type="hidden" name="action" value="stop">'
                   '<div class="actions"><button type="submit">Stop</button></div></form>')
    else:
        control = ('<form method="post" action="/live"><input type="hidden" name="action" value="start">'
                   '<label for="live_account">Account you are playing</label>'
                   f'<select id="live_account" name="account"><option value="">Choose an account…</option>'
                   f'{_options(accounts)}</select>'
                   '<label for="live_username">Discord username</label>'
                   '<input id="live_username" name="username" autocomplete="off" '
                   'placeholder="Leave blank to use the one saved for this account">'
                   '<p class="note">The name above "You caught:" in your fishing results. Only replies naming your '
                   'account or this username are saved; anything else goes to Needs review.</p>'
                   f'<label for="live_bait">Bait in use</label><select id="live_bait" name="bait">{bait_options}</select>'
                   '<div class="actions"><button type="submit">Start</button></div></form>')
    counts = status['counts']
    count_rows = ''.join(f'<div class="stat"><dt>{label}</dt><dd class="known">{counts[key]:,}</dd></div>'
                         for key, label in (('trips', 'Fishing trips saved'), ('sessions', 'Fishing sessions saved'),
                                            ('snapshots', 'Snapshots saved'), ('flagged', 'Sent to review'),
                                            ('ignored', 'Ignored (shop, boosts, …)')))
    log = ''.join(f'<li><span>{escape(when)}</span> {escape(text)}</li>' for when, text in status['log'])
    notice_html = f'<p class="save-status">{escape(notice)}</p>' if notice else ''
    if keeping:
        notice_html += (f'<p class="status">Keeping full-window captures in {escape(str(keeping))} (newest 200). '
                        'They show everything visible in Discord, including unrelated conversations; delete them '
                        'when you are done.</p>')
    if running and status.get('interval'):
        notice_html += (f'<p class="note">Capturing every {status["interval"]:g} s. '
                        f'{escape(status.get("interval_reason") or "")}</p>')
    if running:
        context = status.get('context') or {}
        channel = {'verified': f'Channel confirmed in the capture: {context.get("channel")}.',
                   'changed': 'Discord is showing a different channel; nothing is read until you return.'}.get(
            context.get('status'), 'Channel not confirmed in the latest capture (the search box was not read).')
        notice_html += f'<p class="note">{escape(channel)}</p>'
    if status.get('unresolved'):
        notice_html += (f'<p class="status">{status["unresolved"]:,} review item'
                        f'{"" if status["unresolved"] == 1 else "s"} about your replies '
                        f'{"is" if status["unresolved"] == 1 else "are"} waiting (unreadable results, replies that '
                        'may be repeats, or replies that may be yours). Until each is fixed or dismissed, what '
                        'happened is unknown: trip counts may be incomplete and nothing automatic should rely on '
                        'them.</p>')
    return (f'<!doctype html><html lang="en">{head}<body>'
            '<header><a class="brand" href="/">AutoVirtualFisher</a><span class="local">Live capture · Read-only</span></header>'
            '<main><h1>Live capture</h1>'
            '<p class="intro">Every few seconds this reads new Virtual Fisher replies from your Discord window with '
            'Windows OCR. It only reads the screen: nothing is typed, clicked, or sent to Discord. Clean results '
            'are saved automatically; anything it cannot read properly goes to Needs review below.</p>'
            f'{notice_html}{control}<p class="status">{escape(status["state"])}</p>'
            '<p class="note">Keep the Virtual Fisher chat scrolled to the newest messages. Capture works while '
            'Discord is covered by other windows, but not while it is minimized. Messages already on screen when '
            'you start are treated as seen.</p>'
            f'<div class="layout"><section><h2>This run</h2><dl>{count_rows}</dl></section>'
            f'<section><h2>Activity</h2><ul class="history">{log or "<li>No activity yet.</li>"}</ul></section></div>'
            f'<section class="dashboard"><h2>Needs review ({len(review_items)})</h2>'
            '<p class="note">Messages kept here could not be processed cleanly. Load one into the logbook\'s boxes to '
            'fix and save it by hand, or keep it as an example for improving the parser.</p>'
            f'{_review(review_items)}</section></main></body></html>')
