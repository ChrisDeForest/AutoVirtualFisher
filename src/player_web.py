"""Local web interface with snapshot history. Run with python -m src.player_web."""
import argparse
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from pathlib import Path
from string import Template
from urllib.parse import parse_qs, urlsplit

from .fishing_sessions import DEFAULT_SESSION_ROOT
from .fishing_web import bait_card, bait_choice, bait_options, fishing_card, fishing_panel, handle_fishing
from .paste_detect import detect
from .fish_value import latest_sell_multiplier
from .live_capture import LiveCapture, capture_discord, make_capture
from .automation_controller import AutomationController
from .automation_journal import AutomationJournal
from .automation_models import AutomationConfig
from .automation_transport import SimulationTransport
from .automation_live import DEFAULT_LIVE_ROOT, build_live, live_problem, save_diagnostics
from .automation_web import DEFAULT_CHANNEL, parse_limits, render_automation
from .live_web import render_live
from .observation import DEFAULT_IDENTITY_PATH, Identity, load_identity, save_identity
from .planner_web import planner_card
from .player_charts import line_chart_svg
from .player_snapshots import (DEFAULT_SNAPSHOT_ROOT, compare_latest, describe_changes, growth_series,
                               latest_full_snapshots, list_accounts, load_history, save_snapshot)
from .player_state import display_value, parse_responses, quest_summary
from .screenshot_ocr import ocr_available, read_image
from .review_queue import DEFAULT_REVIEW_ROOT, dismiss, list_items
from .screenshot_web import INSTALL_COMMAND, MAX_UPLOAD_BYTES, read_screenshots, uploaded_files

TEMPLATE = Path(__file__).with_name('player_web.html')
HISTORY_LIMIT = 10
# Large enough for several hundred pasted fishing results.
MAX_FORM_BYTES = 1_048_576
GROWTH_CHARTS = (
    ('balance', 'Balance', lambda value: '$' + display_value(value)),
    ('level_progress', 'Level progress', lambda value: f'{value:,.2f}'),
    ('fish_value', 'Fish value', lambda value: '$' + display_value(value)),
    ('exotic_total', 'Exotic fish total', display_value),
)


def _sections(state):
    sections = []
    for name in ('profile', 'buffs', 'prestige', 'pets', 'inventory', 'quests'):
        values = state.get(name, {}).copy()
        if name == 'pets' and values['owned'] is not None:
            values['owned'] = ', '.join(values['owned'])
        if name == 'quests':
            values = {field: quest_summary(value) if field in ('daily', 'special') else value
                      for field, value in values.items()}
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


def _sell(account, root):
    try:
        sell = latest_sell_multiplier(load_history(account, root)[0])
    except OSError:
        return None
    return sell[0] if sell else None


def _dashboard(account, root, fishing_root=DEFAULT_SESSION_ROOT, target=None, money_target=None):
    """Return (html, latest full snapshot or None) for one account's saved history."""
    if not account:
        return '', None
    try:
        history, history_warnings = load_history(account, root, limit=HISTORY_LIMIT)
        comparison = compare_latest(account, root)
        growth = growth_series(account, root)
    except OSError as error:
        return _warning_list('Snapshot history unavailable', [str(error)]), None
    if not history:
        return f'<section class="dashboard"><h2>History for {escape(account)}</h2><p class="status">No snapshots saved for this account yet.</p></section>', None
    items = ''.join(f'<li><span>{escape(item["captured_at"])}</span>'
                    f'<span class="tag">{escape(item.get("save_mode", "unknown"))}</span>'
                    f'<span class="tag">{escape(item.get("validity", "unknown"))}</span></li>'
                    for item in history)
    warnings = sorted(set(history_warnings) | set(comparison['warnings']) | set(growth['warnings']))
    charts = ''.join(line_chart_svg(title, growth['series'][metric], format_value)
                     for metric, title, format_value in GROWTH_CHARTS)
    html = (f'<section class="dashboard"><h2>History for {escape(account)}</h2>'
            + _warning_list('Some snapshots could not be read', warnings)
            + planner_card(account, root, fishing_root, target, money_target)
            + f'<h3>Growth</h3><div class="charts">{charts}</div>'
            + '<div class="layout"><div><h3>Progress between the latest full snapshots</h3>'
            + _progress(comparison)
            + f'</div><div><h3>Recent snapshots</h3><ul class="history">{items}</ul></div></div>'
            + fishing_card(account, fishing_root) + bait_card(account, fishing_root, _sell(account, root))
            + '</section>')
    return html, comparison['newer']


SORTED_NAMES = {'profile': ('profile', 'profiles'), 'buffs': ('buffs response', 'buffs responses'),
                'prestige_shop': ('prestige shop', 'prestige shops'), 'pet': ('pet response', 'pet responses'),
                'quests': ('quest list', 'quest lists'), 'fishing': ('fishing result', 'fishing results')}


def _plural(count, singular, plural):
    return f'{count} {singular if count == 1 else plural}'


def sort_summary(result):
    """Describe what detect() found, for the message shown above the boxes."""
    parts = []
    for kind, (singular, plural) in SORTED_NAMES.items():
        count = result['counts'].get(kind)
        if not count:
            continue
        part = _plural(count, singular, plural)
        if count > 1 and kind != 'fishing':
            when = result['times'].get(kind)
            part += ' (using the newest' + (f', from {when}' if when else '') + ')'
        parts.append(part)
    skipped = ([_plural(result['skipped_bot'], 'other bot message', 'other bot messages')] if result['skipped_bot'] else []) + \
              ([_plural(result['skipped_user'], 'of your own messages', 'of your own messages')] if result['skipped_user'] else [])
    message = ('Found ' + ', '.join(parts) + '.') if parts else 'Nothing recognizable was found in that paste.'
    if skipped:
        message += ' Skipped ' + ' and '.join(skipped) + '.'
    if parts:
        message += ' Check the boxes below, then select Read player state and Read fishing results.'
    return message


def _screenshot_form(ocr_ready):
    if not ocr_ready:
        return ('<p class="note">To read screenshots instead of pasting, install Windows OCR support: '
                f'<code>{escape(INSTALL_COMMAND)}</code></p>')
    return ('<form class="upload" method="post" action="/screenshots" enctype="multipart/form-data">'
            '<label for="screenshots">Or read screenshots</label>'
            '<input id="screenshots" type="file" name="screenshots" accept=".png,.jpg,.jpeg" multiple>'
            '<div class="actions"><button class="secondary" type="submit">Read screenshots</button></div>'
            '<p class="note">Text is read on this computer with Windows OCR, and the images are not kept. Choose '
            'screenshots in time order: when several show the same kind of response, the last one is used. '
            'Check the numbers in the boxes before saving; OCR can miss bold numbers such as pet XP.</p></form>')


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
           account='', action='parse', fishing=None, fishing_root=DEFAULT_SESSION_ROOT, quests='',
           target=None, everything=None, ocr_ready=False, smart_prefix='', money_target=None, bait=''):
    """Render the page. `submitted` means the player form was posted; `fishing` holds a posted fishing batch."""
    page_warnings = []
    try:
        accounts, account_warnings = list_accounts(root)
    except OSError as error:
        accounts, account_warnings = [], [str(error)]
    fishing_tally, fishing_message, fishing_biome, fishing_sell = None, '', None, None
    fishing_box, fishing_account = (fishing, account) if fishing is not None else ('', '')
    smart_message = ''
    if everything is not None:
        sorted_paste = detect(everything)
        responses = sorted_paste['responses']
        profile, buffs, prestige_shop, pet, quests = (responses[kind] for kind in
                                                      ('profile', 'buffs', 'prestige_shop', 'pet', 'quests'))
        fishing_box = sorted_paste['fishing']
        detected = parse_responses(profile=profile)['profile']
        fishing_account = detected['account_name'] if detected['account_name'] in accounts else ''
        detected_bait = bait_choice(detected['bait']) if detected['bait'] else ''
        bait = detected_bait if detected_bait not in (None, '') else ''
        smart_message = sort_summary(sorted_paste)
    if smart_prefix:
        smart_message = f'{smart_prefix} {smart_message}'.strip()
    if fishing is not None:
        fishing_tally, fishing_message = handle_fishing(fishing, action, account, accounts, fishing_root, bait)
        if account in accounts:
            try:
                latest_full, _ = latest_full_snapshots(account, root, count=1)
            except OSError:
                latest_full = []
            if latest_full:
                fishing_biome = latest_full[0]['state'].get('profile', {}).get('biome')
            fishing_sell = _sell(account, root)
    save_message = ''
    shown_account = account
    if submitted:
        state = parse_responses(profile, buffs, prestige_shop, pet, quests)
        save_message, saved_account = handle_save(state, action, account, root)
        shown_account = saved_account or account
        try:
            accounts, account_warnings = list_accounts(root)
        except OSError:
            pass
        page_warnings = state['warnings']
        dashboard, _ = _dashboard(shown_account, root, fishing_root)
        status = 'Parsed from your pasted responses'
        sections = _sections(state)
    else:
        dashboard, latest = _dashboard(account, root, fishing_root, target, money_target)
        if latest:
            status = f'Latest full snapshot for {account}, captured {latest["captured_at"]}'
            sections = _sections(latest['state'])
        else:
            status = 'Waiting for your first response'
            sections = _sections(parse_responses())
    return Template(TEMPLATE.read_text(encoding='utf-8')).substitute(
        profile=escape(profile), buffs=escape(buffs), prestige_shop=escape(prestige_shop), pet=escape(pet),
        quests=escape(quests),
        sections=sections, warnings=_warning_list('Needs a look', page_warnings),
        status=escape(status), save_status=f'<p class="save-status">{escape(save_message)}</p>' if save_message else '',
        save_account_options=_options(accounts, account if submitted else ''),
        view_account_options=_options(accounts, shown_account),
        account_warnings=_warning_list('Some snapshots could not be read', account_warnings),
        target_value=target or '',
        money_value=money_target or '',
        fishing=fishing_panel(fishing_box, fishing_tally, fishing_message,
                              _options(accounts, fishing_account), fishing_biome, fishing_sell, bait),
        smart_status=f'<p class="save-status">{escape(smart_message)}</p>' if smart_message else '',
        screenshot_form=_screenshot_form(ocr_ready),
        dashboard=dashboard)


LIVE_LOCK = threading.Lock()
AUTOMATION_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    snapshot_root = DEFAULT_SNAPSHOT_ROOT
    fishing_root = DEFAULT_SESSION_ROOT
    review_root = DEFAULT_REVIEW_ROOT
    identity_path = DEFAULT_IDENTITY_PATH
    # Simulated automation keeps its journal, trips, and review items here, apart from the logbook.
    automation_root = Path(__file__).parent / 'data' / 'automation_sim'
    automation = None
    # Builds the simulated game from (monotonic clock, UTC clock); one game is shared by every run.
    automation_game_factory = staticmethod(lambda monotonic, utc: SimulationTransport(monotonic, utc))
    automation_game = None
    automation_tick = 0.2
    # The current run's mode ('live' or 'simulation') and, for live runs, its Discord transport.
    automation_mode = None
    automation_transport = None
    # Live runs keep their journal, trips, review items, diagnostics and last settings here.
    automation_live_root = DEFAULT_LIVE_ROOT
    automation_live_factory = staticmethod(build_live)
    automation_live_problem = staticmethod(live_problem)
    # One live capture per server, shared by every request.
    live = None
    live_capture_function = staticmethod(capture_discord)
    # Set by --keep-captures; shown on the live page because kept images may include unrelated conversations.
    keep_captures_dir = None
    ocr_reader = staticmethod(read_image)
    # None means "check whether the Windows OCR packages are installed".
    ocr_ready = None

    def screenshots_supported(self):
        return ocr_available() if self.ocr_ready is None else self.ocr_ready

    def live_capture(self):
        cls = type(self)
        with LIVE_LOCK:
            if cls.live is None:
                cls.live = LiveCapture(self.snapshot_root, self.fishing_root, self.review_root,
                                       capture=self.live_capture_function)
        return cls.live

    def live_page(self, notice=''):
        try:
            accounts, _ = list_accounts(self.snapshot_root)
        except OSError:
            accounts = []
        try:
            items, warnings = list_items(self.review_root)
        except OSError as error:
            items, warnings = [], [str(error)]
        template = TEMPLATE.read_text(encoding='utf-8')
        head = template[template.index('<head>'):template.index('</head>') + len('</head>')]
        notice = ' '.join([notice] + warnings).strip()
        self.send_content(render_live(head, self.live_capture().status(), accounts, bait_options(''), items, notice,
                                      keeping=self.keep_captures_dir))

    def live_action(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 4096:
                self.send_error(413, 'Request is too large')
                return
            fields = parse_qs(self.rfile.read(length).decode('utf-8'), max_num_fields=5)
        except (ValueError, UnicodeDecodeError):
            self.send_error(400, 'Invalid form input')
            return
        action = fields.get('action', [''])[0]
        live, notice = self.live_capture(), ''
        if action == 'start':
            account = fields.get('account', [''])[0].strip()
            try:
                accounts, _ = list_accounts(self.snapshot_root)
            except OSError:
                accounts = []
            saved = load_identity(account, self.identity_path) if account else None
            username = fields.get('username', [''])[0].strip() or (saved.username if saved else '')
            if account not in accounts:
                notice = 'Choose a saved account to start. Save a snapshot for a new account first.'
            elif not username:
                notice = ('Enter your Discord username (the name above "You caught:" in your fishing results) so '
                          'only your own replies are saved.')
            else:
                try:
                    save_identity(Identity(account, username), self.identity_path)
                except OSError as error:
                    notice = f'Your Discord username could not be remembered: {error}'
                choice = bait_choice(fields.get('bait', [''])[0])
                live.start(account, choice, username=username)
        elif action == 'stop':
            live.stop()
        elif action == 'dismiss':
            if not dismiss(self.review_root, fields.get('id', [''])[0]):
                notice = 'That review item was already gone.'
        self.live_page(notice)

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

    def automation_page(self, notice=''):
        template = TEMPLATE.read_text(encoding='utf-8')
        head = template[template.index('<head>'):template.index('</head>') + len('</head>')]
        cls = type(self)
        status = cls.automation.status() if cls.automation else None
        self.send_content(render_automation(head, status, notice, cls.automation_live_problem(),
                                            self.live_settings(), cls.automation_mode))

    def live_settings(self):
        """The last live account, username and channel, with the username from the saved identity."""
        try:
            settings = json.loads((Path(self.automation_live_root) / 'settings.json').read_text(encoding='utf-8'))
            if not isinstance(settings, dict):
                settings = {}
        except (OSError, json.JSONDecodeError):
            settings = {}
        if not settings.get('account'):
            try:
                saved = json.loads(Path(self.identity_path).read_text(encoding='utf-8'))['identities']
                entry = next(iter(saved.values()))
                settings = {'account': entry['display_name'], 'username': entry.get('username')}
            except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError, StopIteration):
                pass
        if settings.get('account') and not settings.get('username'):
            identity = load_identity(settings['account'], self.identity_path)
            settings['username'] = identity.username if identity else None
        return settings

    def stop_automation(self):
        """Stop the current run; a live run also stops input and saves what it last saw."""
        cls = type(self)
        if cls.automation:
            cls.automation.stop()
        if cls.automation_transport is not None:
            cls.automation_transport.stop()
            save_diagnostics(cls.automation, cls.automation_transport, cls.automation_live_root)
            cls.automation_transport = None

    def automation_action(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 4096:
                self.send_error(413, 'Request is too large')
                return
            fields = parse_qs(self.rfile.read(length).decode('utf-8'), max_num_fields=10)
        except (ValueError, UnicodeDecodeError):
            self.send_error(400, 'Invalid form input')
            return
        cls, action, notice = type(self), fields.get('action', [''])[0], ''
        with AUTOMATION_LOCK:
            current = cls.automation
            if action == 'start':
                trips, minutes, notice = parse_limits(fields)
                mode = fields.get('mode', ['simulation'])[0]
                if current and current.status()['running']:
                    notice = 'A run is already running; stop it first.'
                elif not notice and mode == 'live':
                    notice = self.start_live(fields, trips, minutes)
                elif not notice:
                    self.stop_automation()
                    root = Path(cls.automation_root)
                    clock = time.monotonic
                    utc = lambda: datetime.now(timezone.utc)
                    if cls.automation_game is None:
                        cls.automation_game = cls.automation_game_factory(clock, utc)
                    game = cls.automation_game
                    config = AutomationConfig(account='sim', username='sim0001', context_id='sim:dm',
                                              max_trips=trips, max_seconds=minutes * 60 if minutes else None)
                    cls.automation = AutomationController(game, config, AutomationJournal(root / 'journal'),
                                                          root / 'fishing', root / 'review', monotonic=clock,
                                                          utc=utc, interval=cls.automation_tick)
                    cls.automation_mode = 'simulation'
                    cls.automation.start(thread=True)
            elif action == 'stop' and current:
                self.stop_automation()
            elif action == 'resolve' and current:
                outcome = fields.get('outcome', [''])[0]
                if outcome not in ('happened', 'not_sent') or not current.resolve(fields.get('id', [''])[0], outcome):
                    notice = 'That command was already resolved.'
        self.automation_page(notice)

    def start_live(self, fields, trips, minutes):
        """Start a live run; returns a problem to show, or '' once it is running (paused until Discord is in
        front). Called with AUTOMATION_LOCK held."""
        cls = type(self)
        problem = cls.automation_live_problem()
        if problem:
            return problem
        account, username, channel = (fields.get(name, [''])[0].strip() for name in ('account', 'username', 'channel'))
        channel = channel or DEFAULT_CHANNEL
        if not account or not username:
            return 'Enter your Virtual Fisher name and Discord username.'
        self.stop_automation()
        try:
            controller, transport = cls.automation_live_factory(channel, account, username, Path(cls.automation_live_root),
                                                                trips, minutes * 60 if minutes else None, 0.5)
        except RuntimeError as error:
            return str(error)
        try:
            root = Path(cls.automation_live_root)
            root.mkdir(parents=True, exist_ok=True)
            (root / 'settings.json').write_text(json.dumps({'account': account, 'username': username,
                                                            'channel': channel}), encoding='utf-8')
            save_identity(Identity(account, username), self.identity_path)
        except OSError:
            pass  # remembering the names is a convenience only
        cls.automation, cls.automation_transport, cls.automation_mode = controller, transport, 'live'
        controller.start(thread=True)
        transport.enable()
        return ''

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == '/live':
            self.live_page()
            return
        if url.path == '/automation':
            self.automation_page()
            return
        if url.path != '/':
            self.send_error(404)
            return
        try:
            query = parse_qs(url.query, max_num_fields=5)
        except ValueError:
            self.send_error(400, 'Invalid query')
            return
        account = query.get('account', [''])[0].strip()
        target = query.get('target', [''])[0].strip()
        target = int(target) if target.isdigit() and 0 < int(target) <= 10_000 else None
        money = query.get('money', [''])[0].strip()
        money = int(money) if money.isdigit() and 0 < int(money) <= 10 ** 15 else None
        self.send_content(render(root=self.snapshot_root, account=account, fishing_root=self.fishing_root,
                                 target=target, ocr_ready=self.screenshots_supported(), money_target=money))

    def read_uploaded_screenshots(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self.send_error(400, 'Invalid upload')
            return
        if length < 0 or length > MAX_UPLOAD_BYTES:
            self.send_error(413, 'Screenshots are too large')
            return
        if self.headers.get_content_type() != 'multipart/form-data':
            self.send_error(415, 'Expected a file upload')
            return
        body = self.rfile.read(length)
        ready = self.screenshots_supported()
        if not ready:
            text, summary = '', f'Screenshot reading needs Windows OCR support: {INSTALL_COMMAND}'
        else:
            text, summary = read_screenshots(uploaded_files(self.headers['Content-Type'], body), self.ocr_reader)
        self.send_content(render(root=self.snapshot_root, fishing_root=self.fishing_root, everything=text,
                                 smart_prefix=summary, ocr_ready=ready))

    def do_POST(self):
        if self.path == '/screenshots':
            self.read_uploaded_screenshots()
            return
        if self.path == '/live':
            self.live_action()
            return
        if self.path == '/automation':
            self.automation_action()
            return
        if self.path != '/':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > MAX_FORM_BYTES:
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
        quests = fields.get('quests', [''])[0]
        account = fields.get('account', [''])[0].strip()
        action = fields.get('action', [''])[0] or ('json' if fields.get('format', [''])[0] == 'json' else 'parse')
        if action == 'sort':
            self.send_content(render(root=self.snapshot_root, fishing_root=self.fishing_root,
                                     everything=fields.get('everything', [''])[0],
                                     ocr_ready=self.screenshots_supported()))
        elif action in ('fishing_read', 'fishing_save'):
            self.send_content(render(root=self.snapshot_root, account=account, action=action,
                                     fishing=fields.get('fishing', [''])[0], fishing_root=self.fishing_root,
                                     ocr_ready=self.screenshots_supported(), bait=fields.get('bait', [''])[0]))
        elif action == 'json':
            self.send_content(json.dumps(parse_responses(profile, buffs, prestige_shop, pet, quests), indent=2, ensure_ascii=False),
                              'application/json; charset=utf-8', download=True)
        else:
            self.send_content(render(profile, buffs, prestige_shop, pet, submitted=True, root=self.snapshot_root,
                                     account=account, action='save' if action == 'save' else 'parse',
                                     fishing_root=self.fishing_root, quests=quests,
                                     ocr_ready=self.screenshots_supported()))


def main():
    parser = argparse.ArgumentParser(description='Open the local player logbook')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--keep-captures', type=Path, metavar='DIR',
                        help='Keep live-capture images whose chat text changed, with their OCR results, in DIR '
                             '(for analysing misreads; e.g. docs/screenshots/ocr)')
    args = parser.parse_args()
    if args.keep_captures:
        Handler.live_capture_function = staticmethod(make_capture(args.keep_captures))
        Handler.keep_captures_dir = args.keep_captures.resolve()
        print(f'Keeping live captures in {args.keep_captures.resolve()}', flush=True)
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        print(f'Player logbook: http://127.0.0.1:{server.server_port}', flush=True)
        print(f'Press Ctrl+C to stop. Parsed snapshots are kept in {Handler.snapshot_root} and fishing '
              f'sessions in {Handler.fishing_root}; '
              'raw pasted responses are not stored.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if Handler.live:
                Handler.live.stop()
            if Handler.automation:
                Handler.automation.stop()
            if Handler.automation_transport is not None:
                Handler.automation_transport.stop()


if __name__ == '__main__':
    main()
