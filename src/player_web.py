"""Local web interface with snapshot history. Run with python -m src.player_web."""
import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from string import Template
from urllib.parse import parse_qs, urlsplit

from .player_snapshots import (DEFAULT_SNAPSHOT_ROOT, compare_latest, describe_changes,
                               list_accounts, load_history, save_snapshot)
from .player_state import display_value, parse_responses

TEMPLATE = Path(__file__).with_name('player_web.html')
HISTORY_LIMIT = 10


def _sections(state):
    sections = []
    for name in ('profile', 'buffs', 'prestige', 'pets', 'inventory'):
        values = state[name].copy()
        if name == 'pets' and values['owned'] is not None:
            values['owned'] = ', '.join(values['owned'])
        rows = ''.join(f'<div class="stat"><dt>{escape(field.replace("_", " ").capitalize())}</dt>'
                       f'<dd class="{"unknown" if value is None else "known"}">{escape(display_value(value))}</dd></div>'
                       for field, value in values.items())
        sections.append(f'<section><h3>{name.title()}</h3><dl>{rows}</dl></section>')
    return ''.join(sections)


def _warning_list(title, warnings):
    if not warnings:
        return ''
    return (f'<aside class="warnings"><h3>{escape(title)}</h3><ul>'
            + ''.join('<li>' + escape(warning) + '</li>' for warning in warnings) + '</ul></aside>')


def _options(accounts, selected):
    return ''.join(f'<option value="{escape(name)}"{" selected" if name == selected else ""}>{escape(name)}</option>'
                   for name in accounts)


def _progress(comparison):
    if comparison['status'] != 'ok':
        return '<p class="status">Another full snapshot is needed before progress can be compared.</p>'
    rows = ''.join(f'<div class="stat"><dt>{escape(label)}</dt>'
                   f'<dd class="{"unknown" if text is None else "known"}">{escape(text or "Unknown")}</dd></div>'
                   for label, text in describe_changes(comparison['changes']))
    span = (f'<p class="note">From {escape(comparison["older"]["captured_at"])} '
            f'to {escape(comparison["newer"]["captured_at"])}</p>')
    return span + '<dl>' + rows + '</dl>'


def _dashboard(account, root):
    """Return (html, latest full snapshot or None) for one account's saved history."""
    if not account:
        return '', None
    try:
        history, history_warnings = load_history(account, root, limit=HISTORY_LIMIT)
        comparison = compare_latest(account, root)
    except OSError as error:
        return _warning_list('Snapshot history unavailable', [str(error)]), None
    if not history:
        return f'<section class="dashboard"><h2>History for {escape(account)}</h2><p class="status">No snapshots saved for this account yet.</p></section>', None
    items = ''.join(f'<li><span>{escape(item["captured_at"])}</span>'
                    f'<span class="tag">{escape(item.get("save_mode", "unknown"))}</span>'
                    f'<span class="tag">{escape(item.get("validity", "unknown"))}</span></li>'
                    for item in history)
    warnings = sorted(set(history_warnings) | set(comparison['warnings']))
    html = (f'<section class="dashboard"><h2>History for {escape(account)}</h2>'
            + _warning_list('Some snapshots could not be read', warnings)
            + '<div class="layout"><div><h3>Progress between the latest full snapshots</h3>'
            + _progress(comparison)
            + f'</div><div><h3>Recent snapshots</h3><ul class="history">{items}</ul></div></div></section>')
    return html, comparison['newer']


def handle_save(state, action, selected_account, root):
    """Save according to the submitted action. Return (status message, account to show)."""
    parsed_account = state['profile'].get('account_name')
    try:
        accounts, _ = list_accounts(root)
        if action == 'save':
            if not parsed_account and selected_account and selected_account not in accounts:
                return 'Choose an existing account before saving a partial snapshot.', None
            result = save_snapshot(state, root, mode='manual', account_name=selected_account or None)
        else:
            result = save_snapshot(state, root, mode='automatic')
    except OSError as error:
        return f'Snapshot could not be saved: {error}', parsed_account or selected_account or None
    snapshot = result.get('snapshot')
    account = snapshot['account_name'] if snapshot else parsed_account or selected_account or None
    if result['status'] == 'saved':
        kind = 'Snapshot' if snapshot['validity'] == 'full' else 'Partial snapshot'
        return f'{kind} saved for {account}.', account
    if result['status'] == 'unchanged':
        return f'Latest snapshot for {account} is unchanged; nothing new was saved.', account
    if action == 'save':
        return f'Not saved: {result["reason"]}', account
    return f'Not saved automatically: {result["reason"]} Use Save snapshot to keep a partial record.', account


def render(profile='', buffs='', prestige_shop='', pet='', submitted=False, root=DEFAULT_SNAPSHOT_ROOT,
           account='', action='parse'):
    page_warnings = []
    try:
        accounts, account_warnings = list_accounts(root)
    except OSError as error:
        accounts, account_warnings = [], [str(error)]
    save_message = ''
    shown_account = account
    if submitted:
        state = parse_responses(profile, buffs, prestige_shop, pet)
        save_message, saved_account = handle_save(state, action, account, root)
        shown_account = saved_account or account
        try:
            accounts, account_warnings = list_accounts(root)
        except OSError:
            pass
        page_warnings = state['warnings']
        dashboard, _ = _dashboard(shown_account, root)
        status = 'Parsed from your pasted responses'
        sections = _sections(state)
    else:
        dashboard, latest = _dashboard(account, root)
        if latest:
            status = f'Latest full snapshot for {account}, captured {latest["captured_at"]}'
            sections = _sections(latest['state'])
        else:
            status = 'Waiting for your first response'
            sections = _sections(parse_responses())
    return Template(TEMPLATE.read_text(encoding='utf-8')).substitute(
        profile=escape(profile), buffs=escape(buffs), prestige_shop=escape(prestige_shop), pet=escape(pet),
        sections=sections, warnings=_warning_list('Needs a look', page_warnings),
        status=escape(status), save_status=f'<p class="save-status">{escape(save_message)}</p>' if save_message else '',
        save_account_options=_options(accounts, account if submitted else ''),
        view_account_options=_options(accounts, shown_account),
        account_warnings=_warning_list('Some snapshots could not be read', account_warnings),
        dashboard=dashboard)


class Handler(BaseHTTPRequestHandler):
    snapshot_root = DEFAULT_SNAPSHOT_ROOT

    def log_message(self, format, *args):
        # Pasted account data should not appear in request logs.
        pass

    def send_content(self, content, content_type='text/html; charset=utf-8', download=False):
        body = content.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="player-state.json"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path != '/':
            self.send_error(404)
            return
        try:
            query = parse_qs(url.query, max_num_fields=5)
        except ValueError:
            self.send_error(400, 'Invalid query')
            return
        account = query.get('account', [''])[0].strip()
        self.send_content(render(root=self.snapshot_root, account=account))

    def do_POST(self):
        if self.path != '/':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 65536:
                self.send_error(413, 'Response text is too large')
                return
            if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                self.send_error(415, 'Expected form input')
                return
            fields = parse_qs(self.rfile.read(length).decode('utf-8'), max_num_fields=10)
        except (ValueError, UnicodeDecodeError):
            self.send_error(400, 'Invalid form input')
            return
        profile = fields.get('profile', [''])[0]
        buffs = fields.get('buffs', [''])[0]
        prestige_shop = fields.get('prestige_shop', [''])[0]
        pet = fields.get('pet', [''])[0]
        account = fields.get('account', [''])[0].strip()
        action = fields.get('action', [''])[0] or ('json' if fields.get('format', [''])[0] == 'json' else 'parse')
        if action == 'json':
            self.send_content(json.dumps(parse_responses(profile, buffs, prestige_shop, pet), indent=2, ensure_ascii=False),
                              'application/json; charset=utf-8', download=True)
        else:
            self.send_content(render(profile, buffs, prestige_shop, pet, submitted=True, root=self.snapshot_root,
                                     account=account, action='save' if action == 'save' else 'parse'))


def main():
    parser = argparse.ArgumentParser(description='Open the local player logbook')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        print(f'Player logbook: http://127.0.0.1:{server.server_port}', flush=True)
        print(f'Press Ctrl+C to stop. Parsed snapshots are kept in {Handler.snapshot_root}; '
              'raw pasted responses are not stored.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
