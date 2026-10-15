from html import unescape
from http.server import ThreadingHTTPServer
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode
from urllib.request import urlopen

from src.automation_transport import SimulationTransport
from src.automation_web import PHASE_INTRO, render_panel
from src.fishing_sessions import load_sessions
from src.player_web import Handler


def fast_game(monotonic, utc):
    return SimulationTransport(monotonic, utc, cooldown=0.05, latency=0.01)


class FakeLive(SimulationTransport):
    """Stands in for the Discord transport: the same contract, plus the live controls the page uses."""
    def __init__(self, monotonic, utc, display_name, username):
        super().__init__(monotonic, utc, display_name=display_name, username=username,
                         context_id='discord:1:virtual fisher', cooldown=0.05, latency=0.01)
        self.context_id = self.context.context_id
        self.enabled = self.stopped = False
        self.baseline = self.last_frame = self.last_lines = self.blocked = self.last_issue = None

    def enable(self):
        self.enabled = True

    def stop(self):
        self.stopped = True


def fake_live_factory(calls):
    def build(channel, account, username, root, max_trips, max_seconds, interval):
        from src.automation_controller import AutomationController
        from src.automation_journal import AutomationJournal
        from src.automation_models import AutomationConfig
        clock = time.monotonic
        utc = lambda: datetime.now(timezone.utc)
        transport = FakeLive(clock, utc, account, username)
        calls.append(dict(channel=channel, account=account, username=username, root=root,
                          max_trips=max_trips, max_seconds=max_seconds, transport=transport))
        config = AutomationConfig(account, username, transport.context_id, max_trips=max_trips,
                                  max_seconds=max_seconds, mode='live')
        controller = AutomationController(transport, config, AutomationJournal(root / 'journal'), root / 'fishing',
                                          root / 'review', monotonic=clock, utc=utc, interval=interval)
        return controller, transport
    return build


class AutomationWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = Path(tempfile.mkdtemp())
        cls.handler = type('AutomationHandler', (Handler,), {
            'snapshot_root': cls.temporary / 'snapshots', 'fishing_root': cls.temporary / 'fishing',
            'review_root': cls.temporary / 'review', 'automation_root': cls.temporary / 'simulation',
            'automation_game_factory': staticmethod(fast_game), 'automation_game': None,
            'automation_tick': 0.005, 'automation': None, 'automation_transport': None,
            'automation_live_root': cls.temporary / 'live', 'identity_path': cls.temporary / 'identities.json',
            'automation_live_problem': staticmethod(lambda: '')})
        cls.calls = []
        cls.handler.automation_live_factory = staticmethod(fake_live_factory(cls.calls))
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), cls.handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        if cls.handler.automation:
            cls.handler.automation.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.temporary, ignore_errors=True)

    def tearDown(self):
        if self.handler.automation:
            self.handler.automation.stop()
        # A run stopped mid-command leaves its reply in the simulated game: the next test gets a fresh one.
        self.handler.automation_game = None

    def get(self, path='/automation'):
        with urlopen(self.url + path) as response:
            return unescape(response.read().decode())

    def post(self, **fields):
        with urlopen(self.url + '/automation', data=urlencode(fields).encode()) as response:
            return unescape(response.read().decode())

    def wait_until_stopped(self):
        for _ in range(400):
            if not self.handler.automation.status()['running']:
                return
            time.sleep(0.01)
        self.fail('the bounded run did not finish')

    def test_page_offers_live_mode_with_saved_identity(self):
        from src.observation import Identity, save_identity
        save_identity(Identity('epicmouse', 'epicmouse9925'), self.handler.identity_path)
        html = self.get()
        self.assertIn('<h1>Automation</h1>', html)
        self.assertIn('name="mode" value="live" checked', html)
        self.assertIn('value="epicmouse"', html)
        self.assertIn('value="epicmouse9925"', html)
        self.assertIn('value="Virtual Fisher"', html)
        self.assertIn('href="/automation"', self.get('/'), 'the main page links here')

    def test_live_mode_unavailable_explains_why(self):
        self.handler.automation_live_problem = staticmethod(lambda: 'Live mode needs the Windows OCR packages.')
        try:
            html = self.get()
            self.assertIn('Live mode needs the Windows OCR packages.', html)
            self.assertIn('name="mode" value="live" disabled', html)
            html = self.post(action='start', mode='live', account='epicmouse', username='epicmouse9925',
                             channel='Virtual Fisher', max_trips='', max_minutes='')
            self.assertIn('Live mode needs the Windows OCR packages.', html)
            self.assertFalse(self.handler.automation and self.handler.automation.status()['running'])
        finally:
            self.handler.automation_live_problem = staticmethod(lambda: '')

    def test_live_run_starts_without_limits_and_stop_stops_input(self):
        html = self.post(action='start', mode='live', account='epicmouse', username='epicmouse9925',
                         channel='Virtual Fisher', max_trips='', max_minutes='')
        call = self.calls[-1]
        self.assertEqual((call['account'], call['username'], call['channel']),
                         ('epicmouse', 'epicmouse9925', 'Virtual Fisher'))
        self.assertEqual((call['max_trips'], call['max_seconds']), (None, None))
        self.assertTrue(call['transport'].enabled)
        self.assertIn('not minimized', html)
        self.assertIn('use other applications', html)
        self.assertIn('Live', html)
        self.assertTrue(self.handler.automation.status()['running'])
        html = self.post(action='stop')
        self.assertIn('Stopped', html)
        self.assertTrue(call['transport'].stopped)
        self.assertIn('value="epicmouse"', self.get(), 'the last settings are remembered')

    def test_live_run_needs_names(self):
        html = self.post(action='start', mode='live', account='', username='epicmouse9925',
                         channel='Virtual Fisher', max_trips='', max_minutes='')
        self.assertIn('Enter your Virtual Fisher name', html)
        self.assertFalse(self.handler.automation and self.handler.automation.status()['running'])

    def test_limits_are_validated(self):
        for fields in ({'max_trips': '0'}, {'max_trips': '5000'}, {'max_trips': 'lots'}, {'max_minutes': '0'},
                       {'max_minutes': '601'}):
            with self.subTest(fields=fields):
                html = self.post(action='start', mode='simulation', **{'max_trips': '25', 'max_minutes': '15', **fields})
                self.assertIn('between', html)
                self.assertFalse(self.handler.automation and self.handler.automation.status()['running'])

    def test_bounded_simulated_run_saves_to_the_simulation_logbook_only(self):
        self.post(action='start', mode='simulation', max_trips='3', max_minutes='5')
        self.wait_until_stopped()
        html = self.get()
        self.assertIn('trip limit reached', html)
        self.assertEqual(sum(s['tally']['trips'] for s in load_sessions('sim', self.temporary / 'simulation' /
                                                                       'fishing')[0]), 3)
        self.assertEqual(load_sessions('sim', self.temporary / 'fishing')[0], [], 'your real logbook is untouched')

    def test_dashboard_boxes_show_synced_and_projected_stats(self):
        self.post(action='start', mode='simulation', max_trips='3', max_minutes='5')
        self.wait_until_stopped()
        panel = self.get('/automation/panel')
        self.assertNotIn('<html', panel, 'a fragment the page swaps in place')
        for heading in ('Profile', 'Buffs', 'This run', 'Inventory', 'Recent trips'):
            self.assertIn(f'<h2>{heading}</h2>', panel)
        self.assertIn('$1,000', panel)
        self.assertIn('Level 10', panel)
        self.assertIn('0.05 s', panel)
        self.assertIn('/automation/panel', self.get(), 'the page refreshes its boxes')

    def test_level_line_without_xp_and_pet_with_level(self):
        status = {'running': False, 'reason': 'done', 'phase': 'STOPPED', 'trips': 0, 'config': {'max_trips': 0},
                  'dashboard': {'profile': {'level': 31, 'prestige': 4, 'xp_current': None, 'xp_required': None,
                                            'pet': None, 'pet_level': 51}}}
        panel = render_panel(status)
        self.assertIn('Level 31 (prestige 4)', panel)
        self.assertIn('XP not read yet', panel)
        self.assertIn('level 51 (name not read)', panel)
        self.assertNotIn('Level 51)', panel)
        status['dashboard']['profile'].update(pet='Dolphin', xp_current=1677, xp_required=6000)
        panel = render_panel(status)
        self.assertIn('Dolphin, level 51', panel)
        self.assertIn('class="line"', panel)

    def test_pause_and_resume_buttons(self):
        self.post(action='start', mode='simulation', max_trips='1000', max_minutes='5')
        html = self.get('/automation/panel')
        self.assertIn('value="pause"', html)
        self.assertIn('value="stop"', html)
        self.post(action='pause')
        self.assertTrue(self.handler.automation.status()['paused'])
        html = self.get('/automation/panel')
        self.assertIn('<span class="phase">Paused</span>', html)
        self.assertIn('value="resume"', html)
        token = re.search(r'name="pause_id" value="(\d+)"', html)[1]
        self.post(action='resume', pause_id=token)
        self.assertFalse(self.handler.automation.status()['paused'])

    def test_do_it_queues_a_step_and_remove_takes_it_off(self):
        import json
        self.post(action='start', mode='simulation', max_trips='1000', max_minutes='5')
        self.handler.automation.pause()               # nothing runs while queued steps are checked
        self.post(action='queue', label='Switch: rod Steel Rod', actions=json.dumps([{'act': 'rod', 'name': 'Steel Rod'}]))
        queue = self.handler.automation.status()['queue']
        self.assertEqual([entry['label'] for entry in queue], ['Switch: rod Steel Rod'])
        page = self.post(action='queue', label='bad', actions=json.dumps([{'act': 'prestige'}]))   # never an action
        self.assertIn('That step has nothing the run can do.', page)
        self.post(action='queue', label='worse', actions='not json')
        self.assertEqual(len(self.handler.automation.status()['queue']), 1)
        self.post(action='unqueue', id=str(queue[0]['id']))
        self.assertEqual(self.handler.automation.status()['queue'], [])

    def test_follow_the_plan_toggle(self):
        self.post(action='start', mode='simulation', max_trips='1000', max_minutes='5')
        self.handler.automation.pause()
        self.post(action='follow', on='1')
        self.assertTrue(self.handler.automation.status()['executor']['follow'])
        self.post(action='follow', on='0')
        self.assertFalse(self.handler.automation.status()['executor']['follow'])

    def test_queue_box_has_the_follow_toggle_and_held_steps(self):
        from src.automation_web import render_plan_tab
        plan = {'error': None, 'cut_short': False, 'assumptions': [], 'next': None, 'steps': [], 'league': [],
                'prestige': {'reached': False, 'missing': []}}
        off = render_plan_tab({'plan': plan, 'executor': {'follow': False, 'held': [], 'prestige_ready': False}})
        self.assertIn('value="follow"', off)
        self.assertIn('Follow the plan', off)
        on = render_plan_tab({'plan': plan, 'executor': {
            'follow': True, 'prestige_ready': False, 'held': [{'label': 'Salesman 8', 'until': 140}]}})
        self.assertIn('Stop following', on)
        self.assertIn('Salesman 8 held back until trip 140', on)

    def test_a_stale_resume_never_ends_a_newer_pause(self):
        # A resume sent again later (a reload, a restored tab) belongs to an earlier pause: the run stays paused.
        self.post(action='start', mode='simulation', max_trips='1000', max_minutes='5')
        self.post(action='pause')
        old = re.search(r'name="pause_id" value="(\d+)"', self.get('/automation/panel'))[1]
        self.post(action='resume', pause_id=old)
        self.post(action='pause')
        self.post(action='resume', pause_id=old)
        self.assertTrue(self.handler.automation.status()['paused'])
        self.post(action='resume')                                   # no token: not from the Resume button
        self.assertTrue(self.handler.automation.status()['paused'])

    def test_button_presses_redirect_so_a_reload_cannot_send_them_again(self):
        import http.client
        from urllib.parse import urlsplit
        self.post(action='start', mode='simulation', max_trips='1000', max_minutes='5')
        parts = urlsplit(self.url)
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
        connection.request('POST', '/automation', body='action=pause',
                           headers={'Content-Type': 'application/x-www-form-urlencoded'})
        response = connection.getresponse()
        self.assertEqual(response.status, 303)
        self.assertEqual(response.getheader('Location'), '/automation')
        connection.close()
        self.assertTrue(self.handler.automation.status()['paused'])

    def test_a_notice_survives_the_redirect_once(self):
        self.post(action='start', mode='simulation', max_trips='1000', max_minutes='5')
        page = self.post(action='start', mode='simulation', max_trips='1000', max_minutes='5')
        self.assertIn('A run is already running; stop it first.', page)
        self.assertNotIn('A run is already running; stop it first.', unescape(self.get('/automation')))

    def test_panel_has_run_and_model_tabs(self):
        from src.automation_web import render_panel
        status = {'running': True, 'phase': 'READY', 'reason': 'Fishing.', 'trips': 3, 'paused': False,
                  'config': {'max_trips': None}, 'dashboard': {}, 'account': None,
                  'model': {'error': None, 'setup': {'rod': 'Fiberglass Rod', 'biome': 'River', 'bait': 'none',
                                                     'pet': 'Dolphin', 'boosts': []},
                            'buffs': [{'name': 'sell_price', 'label': 'Sell price', 'game': 2.91, 'model': 2.9106,
                                       'ok': True, 'correction': 1.0, 'effect': None, 'source': 'formula', 'why': []},
                                      {'name': 'fish_catch', 'label': 'Fish catch', 'game': 1.41, 'model': 1.3389,
                                       'ok': False, 'correction': 1.0531, 'effect': 'the official server bonus',
                                       'source': 'formula', 'why': []}],
                            'trip': [{'name': 'fish', 'label': 'Fish', 'predicted': 11.2, 'corrected': 11.2,
                                      'measured': None, 'factor': 1.0, 'status': 'measuring 3/50 trips',
                                      'flag': False, 'per_hour': 10080.0, 'source': 'formula'}],
                            'mix': {'source': 'fish mix of Fiberglass Rod in River estimated from the calculator '
                                              'example',
                                    'rows': [{'fish': 'Fish', 'predicted': 0.84, 'measured': None}]},
                            'estimates': ['trips per hour: cooldown + 0.5 s until this run has a rate']}}
        html = render_panel(status, 'live')
        self.assertIn('<nav class="tabs"><a href="#run">Run</a><a href="#model">Model</a><a href="#plan">Plan</a>'
                      '<a href="#progress">Progress</a></nav>', html)
        self.assertIn('data-tab="run"', html)
        self.assertIn('<section class="tab" data-tab="model" hidden>', html)
        self.assertIn('probably the official server bonus', html)
        self.assertIn('measuring 3/50 trips', html)
        self.assertIn('estimated from the calculator example', html)
        self.assertLess(html.index('class="strip'), html.index('class="tabs"'), 'Pause and Stop stay above the tabs')

    def test_plan_tab_renders_next_action_route_and_prestige(self):
        from src.automation_web import render_plan_tab
        view = {'updating': False, 'made_at': '2026-10-10T20:00:00+00:00', 'error': None, 'plan': {
            'error': None, 'cut_short': False, 'assumptions': ['640 is used <P4>'],
            'next': {'phase_label': '1st XP grind', 'kind': 'buy', 'action': 'Salesman 8', 'cost': 25000,
                     'currency': '$', 'sell': 4000, 'trips': 12.4, 'seconds': 840, 'why': 'Salesman 6 before Alloy Rod'},
            'steps': [{'phase_label': '1st XP grind', 'kind': 'buy', 'action': 'Salesman 8', 'cost': 25000,
                       'currency': '$', 'sell': 4000, 'trips': 12.4, 'seconds': 840, 'why': 'Salesman 6 before Alloy Rod'},
                      {'phase_label': '1st charm grind', 'kind': 'switch', 'action': 'Switch: rod Golden Rod',
                       'cost': 0, 'currency': '$', 'sell': 0, 'trips': 0, 'seconds': 90000, 'why': 'best setup'}],
            'prestige': {'reached': True, 'trips': 400000, 'seconds': 1.6e6, 'missing': [], 'shop': ['International Ties']}}}
        html = render_plan_tab(view)
        for text in ('Salesman 8', '$25,000', 'sell $4,000', '1st charm grind', 'International Ties', '&lt;P4&gt;',
                     '14 min'):
            self.assertIn(text, html)

    def test_plan_tab_says_where_each_purchase_is_made(self):
        from src.automation_web import render_plan_tab
        step = {'phase_label': '1st XP grind', 'kind': 'buy', 'action': 'Salesman 8', 'cost': 25000, 'currency': '$',
                'sell': 0, 'trips': 0, 'seconds': 0, 'why': '', 'where': '/shop upgrades'}
        view = {'plan': {'error': None, 'cut_short': False, 'assumptions': [], 'next': step,
                         'steps': [step, {**step, 'kind': 'switch', 'action': 'Switch: rod Golden Rod', 'where': ''}],
                         'prestige': {'reached': True, 'seconds': 10, 'shop': ['International Ties']}}}
        html = render_plan_tab(view)
        self.assertEqual(html.count('/shop upgrades'), 2)                       # the Next box and the step
        self.assertIn('Salesman 8 in /shop upgrades', html)                     # the Next box
        self.assertIn('Then buy International Ties in /prestige shop.', html)

    def test_plan_steps_have_do_it_buttons_and_a_queue_box(self):
        import json
        from html import escape
        from src.automation_web import render_plan_tab
        action = {'act': 'buy', 'name': 'Salesman', 'amount': None, 'level': 8, 'shop': 'upgrades', 'price': 25000}
        step = {'phase_label': '1st XP grind', 'kind': 'buy', 'action': 'Salesman 8', 'cost': 25000, 'currency': '$',
                'sell': 0, 'trips': 0, 'seconds': 0, 'why': '', 'where': '/shop upgrades', 'do': [action]}
        view = {'plan': {'error': None, 'cut_short': False, 'assumptions': [], 'next': step,
                         'steps': [step, {**step, 'action': 'price not read', 'do': []}],
                         'prestige': {'reached': False, 'missing': []}},
                'queue': [{'id': 3, 'label': 'Better Fish 9', 'left': 1}],
                'action_log': [{'label': 'Rod', 'result': 'skipped', 'note': 'already using Lava Rod'},
                               {'label': 'Salesman 7', 'result': 'done', 'note': 'bought Salesman level 7'},
                               {'label': 'Bait Helper 2', 'result': 'failed', 'note': "can't afford it"}]}
        html = render_plan_tab(view)
        self.assertEqual(html.count('name="action" value="queue"'), 1)       # only the step with actions
        self.assertIn(escape(json.dumps([action])), html)
        self.assertIn('Better Fish 9', html)
        self.assertIn('name="action" value="unqueue"', html)
        self.assertIn('Rod skipped: already using Lava Rod', html)
        self.assertIn('✓ Salesman 7: bought Salesman level 7', html)
        self.assertIn('✗ Bait Helper 2: can&#x27;t afford it', html)
        self.assertIn('<h2>Queue</h2>', render_plan_tab({'plan': view['plan']}))   # shown empty too

    def test_plan_marks_league_box_and_ready_to_prestige(self):
        from src.automation_web import render_plan_tab
        action = {'act': 'buy', 'name': 'Bait Lover', 'level': 3, 'shop': 'special', 'price': 25}
        step = {'phase_label': '1st XP grind', 'kind': 'special', 'action': 'Bait Lover 3 (25 Diamond Fish)',
                'cost': 25, 'currency': 'Diamond Fish', 'sell': 0, 'trips': 0, 'seconds': 0, 'why': '',
                'where': '/shop special', 'do': [action], 'status': 'cannot_afford'}
        later = {**step, 'action': 'Bait Lover 4 (50 Diamond Fish)', 'status': 'later'}
        crates = {**step, 'kind': 'league', 'action': 'Super Crates 3', 'cost': 150, 'currency': 'Hooks',
                  'where': '/shop league', 'status': 'ready'}
        plan = {'error': None, 'cut_short': False, 'assumptions': [], 'next': step, 'steps': [step, later],
                'league': [crates], 'hooks': 160, 'phase_now': 'xp_grind_1',
                'prestige': {'reached': False, 'missing': []}}
        html = render_plan_tab({'plan': plan})
        self.assertIn('Cannot afford', html)
        self.assertIn('Later phase', html)
        self.assertIn('<details class="toggle" open data-key="league"><summary>League (Hooks: 160)</summary>',
                      html)  # a step is ready
        self.assertIn('Super Crates 3', html)
        self.assertEqual(html.count('value="queue"'), 1)                 # only the ready league step
        self.assertNotIn('Ready to prestige', html)
        ready = render_plan_tab({'plan': {**plan, 'phase_now': 'prestige',
                                          'prestige': {'reached': True, 'seconds': 0, 'shop': ['International Ties']}}})
        self.assertIn('<h2>Ready to prestige</h2>', ready)
        self.assertIn('International Ties', ready)

    def test_plan_tab_is_a_timeline_by_phase(self):
        from src.automation_web import render_plan_tab
        step = lambda phase, label, action, status, seconds: {
            'phase': phase, 'phase_label': label, 'kind': 'buy', 'action': action, 'cost': 100, 'currency': '$',
            'sell': 0, 'trips': 3, 'seconds': seconds, 'why': 'because', 'where': '/shop upgrades',
            'do': [{'act': 'buy', 'name': action}], 'status': status}
        steps = [step('xp_grind_1', '1st XP grind', 'Salesman 8', 'ready', 0),
                 step('xp_grind_1', '1st XP grind', 'Golden Rod', 'cannot_afford', 600),
                 step('charm_grind_1', '1st charm grind', 'Better Chests 5', 'later', 7200),
                 step('xp_grind_2', '2nd XP grind', 'Yacht', 'later', 90000)]
        plan = {'error': None, 'cut_short': False, 'assumptions': ['an estimate'], 'next': steps[0], 'steps': steps,
                'league': [], 'hooks': 3, 'phase_now': 'xp_grind_1',
                'prestige': {'reached': True, 'seconds': 200000, 'shop': ['International Ties']}}
        html = render_plan_tab({'plan': plan, 'made_at': 'x'})
        self.assertIn('You are in the 1st XP grind', html)
        self.assertIn(PHASE_INTRO['xp_grind_1'], html)
        self.assertEqual(html.count('<details class="phase"'), 3)
        self.assertEqual(html.count('<details class="phase" open'), 2)     # this phase and the next
        self.assertIn('2 steps · 1 ready', html)
        self.assertLess(html.index('Salesman 8'), html.index('Better Chests 5'))
        self.assertLess(html.index('Better Chests 5'), html.index('Prestige in about'))
        self.assertIn('Assumptions and estimates (1)', html)
        self.assertIn('League (Hooks: 3)', html)
        # Each toggle has a key, so the 2-second refresh keeps it open or closed as the person left it.
        for key in ('xp_grind_1', 'charm_grind_1', 'xp_grind_2', 'league', 'assumptions'):
            self.assertIn(f'data-key="{key}"', html)
        self.assertNotIn('class="node big reached now"', html)

    def test_at_prestige_the_prestige_node_is_current_not_a_phase(self):
        from src.automation_web import render_plan_tab
        step = {'phase': 'xp_grind_2', 'phase_label': '2nd XP grind', 'kind': 'buy', 'action': 'Yacht', 'cost': 1,
                'currency': '$', 'sell': 0, 'trips': 3, 'seconds': 10, 'why': '', 'where': '', 'do': [],
                'status': 'later'}
        plan = {'error': None, 'cut_short': False, 'assumptions': [], 'next': None, 'steps': [step], 'league': [],
                'hooks': 0, 'phase_now': 'prestige', 'prestige': {'reached': True, 'seconds': 0, 'shop': []}}
        html = render_plan_tab({'plan': plan})
        self.assertNotIn('class="node big now"', html)
        self.assertIn('<span class="node big reached now"></span><p class="phase-title">Prestige</p>', html)

    def test_plan_tab_trips_column_and_now(self):
        from src.automation_web import render_plan_tab
        step = {'phase_label': '1st XP grind', 'kind': 'switch', 'action': 'Switch: rod Supporter Rod', 'cost': 0,
                'currency': '$', 'sell': 0, 'trips': 0, 'seconds': 0, 'why': 'best setup for money'}
        later = dict(step, kind='buy', action='Salesman 8', cost=25000, trips=311.6, seconds=4800)
        html = render_plan_tab({'updating': False, 'made_at': 'x', 'error': None, 'plan': {
            'error': None, 'cut_short': False, 'assumptions': [], 'next': step, 'steps': [step, later],
            'prestige': {'reached': False, 'missing': ['level 500 (the plan reaches 52)', '829 more charms']}}})
        self.assertIn('Switch: rod Supporter Rod — now', html)
        self.assertNotIn('about 1 min', html)
        self.assertIn('312 trips · in 1.3 h', html)
        self.assertIn('now · best setup for money', html)                  # no trips before it
        self.assertNotIn('0 trips', html)
        self.assertIn('Missing: level 500 (the plan reaches 52), 829 more charms.', html)

    def test_plan_tab_shows_a_later_error_above_the_last_good_plan(self):
        from src.automation_web import render_plan_tab
        step = {'phase_label': '1st XP grind', 'kind': 'buy', 'action': 'Salesman 8', 'cost': 25000,
                'currency': '$', 'sell': 0, 'trips': 3, 'seconds': 840, 'why': ''}
        html = render_plan_tab({'updating': False, 'made_at': '2026-10-10T20:00:00+00:00',
                                'error': 'The planner failed: unknown rod', 'plan': {
                                    'error': None, 'cut_short': False, 'assumptions': [], 'next': step,
                                    'steps': [step], 'prestige': {'reached': True, 'seconds': 60, 'shop': []}}})
        self.assertIn('The planner failed: unknown rod. Showing the last good plan, made 2026-10-10T20:00:00+00:00',
                      html)
        self.assertLess(html.index('The planner failed'), html.index('Salesman 8'))

    def test_a_broken_plan_leaves_the_page_rendering(self):
        status = {'running': True, 'phase': 'READY', 'reason': 'Fishing.', 'trips': 3, 'paused': False,
                  'config': {'max_trips': None}, 'dashboard': {}, 'account': None, 'model': None,
                  'plan': {'plan': {'steps': [{'action': 'x'}], 'next': 5}, 'updating': False, 'made_at': None,
                           'error': None}}
        html = render_panel(status)
        self.assertIn('The Plan tab could not be shown', html)
        self.assertIn('data-tab="run"', html)
        self.assertIn('Fishing.', html)

    def test_plan_tab_error_and_updating(self):
        from src.automation_web import render_plan_tab
        self.assertIn('not planned: x', render_plan_tab({'plan': {'error': 'not planned: x', 'steps': []},
                                                        'updating': False, 'made_at': None, 'error': None}))
        self.assertIn('Working out', render_plan_tab({'plan': None, 'updating': True, 'made_at': None, 'error': None}))

    def test_panel_has_three_tabs(self):
        status = {'running': True, 'phase': 'READY', 'reason': 'Fishing.', 'trips': 3, 'paused': False,
                  'config': {'max_trips': None}, 'dashboard': {}, 'account': None, 'model': None,
                  'plan': {'plan': None, 'updating': True, 'made_at': None, 'error': None}}
        html = render_panel(status)
        self.assertIn('href="#plan"', html)
        self.assertIn('data-tab="plan"', html)

    @staticmethod
    def minimal_status():
        return {'running': True, 'phase': 'READY', 'reason': 'Fishing.', 'trips': 3, 'paused': False,
                'config': {'max_trips': None}, 'dashboard': {}, 'account': None, 'model': None,
                'plan': {'plan': None, 'updating': True, 'made_at': None, 'error': None}}

    @staticmethod
    def progress_view():
        series = {'level': [('2026-10-13T12:00:00+00:00', 120), ('2026-10-14T12:00:00+00:00', 128)],
                  'charms': [], 'xp_per_hour': [], 'trips': [], 'net_worth': []}
        runs = [{'run_id': f'r{i}', 'start': '2026-10-14T10:00:00+00:00', 'end': '2026-10-14T11:00:00+00:00',
                 'seconds': 3600, 'trips': 100, 'xp': 5000, 'chests': 3, 'charms': 1, 'stopped': 'Stopped by you.'}
                for i in range(22)]
        actions = [{'at': '2026-10-14T10:00:00+00:00', 'label': 'Buy Salesman 8 (Salesman 8)', 'result': 'done',
                    'note': 'bought Salesman level 8', 'cost': '$25,000'}]
        return {'countdown': {'reached': True, 'seconds': 90000, 'missing': [],
                              'bars': [{'label': 'Level', 'now': 128, 'goal': 500},
                                       {'label': 'Money', 'now': 1.5e6, 'goal': 1e9}],
                              'milestones': [{'label': '2nd XP grind', 'seconds': 3600}]},
                'charts': {'7': series, '30': series, 'all': series}, 'runs': runs, 'actions': actions,
                'notes': ['1 trip records could not be read.']}

    def test_progress_tab_sections(self):
        from src.automation_web import render_progress_tab
        view = self.progress_view()
        html = render_progress_tab({'view': view, 'made_at': None, 'error': None})
        for text in ('Prestige in about', '128 / 500', '$1,500,000 / $1,000,000,000', '2nd XP grind',
                     'data-key="range-7"', 'Last 30 days', 'All time', 'Level', '2 more runs', '✓',
                     'bought Salesman level 8', '$25,000', '1 trip records could not be read.'):
            self.assertIn(text, html)
        self.assertEqual(html.count('<details class="range"'), 3)
        self.assertIn('Working out progress', render_progress_tab({'view': None, 'made_at': None, 'error': None}))
        self.assertIn('boom', render_progress_tab({'view': None, 'made_at': None, 'error': 'boom'}))

    def test_progress_tab_details(self):
        from src.automation_web import render_progress_tab
        view = self.progress_view()
        view['countdown'] = {'reached': False, 'seconds': None, 'missing': ['level 500', '<b>money</b>'],
                             'bars': [{'label': 'Level', 'now': 900, 'goal': 500}], 'milestones': []}
        view['actions'] = [dict(view['actions'][0], result=result) for result in ('failed', 'unknown')] * 30
        html = render_progress_tab({'view': view, 'made_at': None, 'error': None})
        self.assertIn('Not reached in this plan. Missing: level 500, &lt;b&gt;money&lt;/b&gt;', html)
        self.assertIn('style="width:100%"', html)          # a bar never runs past its end
        self.assertIn('✗', html)
        self.assertIn('?', html)
        self.assertIn('data-key="actions-more"', html)
        self.assertIn('10 more actions', html)
        self.assertIn('data-key="runs-more"', html)
        self.assertIn('<details class="range" data-key="range-7" open>', html)
        self.assertNotIn('data-key="range-30" open', html)
        self.assertEqual(html.count('Needs two full snapshots'), 12)  # four empty series in each of three ranges
        # Times are shown in local time, YYYY-MM-DD HH:MM.
        local = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')
        self.assertIn(local, html)
        z = render_progress_tab({'view': dict(view, runs=[dict(view['runs'][0], start='2026-10-14T10:00:00Z')]),
                                 'made_at': None, 'error': None})
        self.assertIn(local, z)

    def test_a_broken_progress_view_never_breaks_the_page(self):
        status = dict(self.minimal_status(), progress={'view': {'countdown': 5}, 'made_at': None, 'error': None})
        html = render_panel(status)
        self.assertIn('The Progress tab could not be shown', html)
        self.assertIn('data-tab="run"', html)

    def test_the_progress_tab_is_in_the_tab_row(self):
        html = render_panel(self.minimal_status())
        self.assertIn('<a href="#progress">Progress</a>', html)
        self.assertIn('data-tab="progress"', html)
        self.assertIn('Working out progress', html)       # no 'progress' key yet: its view is on its way

    def test_progress_shows_before_any_run(self):
        # No run since the server started: the page shows the account's progress from its files.
        html = render_panel({'progress': {'view': self.progress_view(), 'made_at': None, 'error': None}})
        self.assertIn('No run yet', html)
        self.assertIn('<h2>Progress</h2>', html)
        self.assertIn('Prestige in about', html)
        self.assertLess(html.index('No run yet'), html.index('Prestige in about'))
        from src.automation_web import render_automation
        page = render_automation('<head></head>', {'progress': {'view': None, 'made_at': None, 'error': None}})
        self.assertIn('Working out progress', page)
        self.assertIn('name="action" value="start"', page)    # the start form, as before any run
        self.assertNotIn('<h2>Progress</h2>', render_panel(None))
        self.assertIn('No run yet', render_panel(None))

    def test_model_tab_shows_a_problem(self):
        from src.automation_web import render_model_tab
        self.assertIn('Not modelled: unknown rod Banana Rod.',
                      render_model_tab({'error': 'Not modelled: unknown rod Banana Rod.'}))
        self.assertIn('No model yet', render_model_tab(None))
        self.assertIn('&lt;b&gt;', render_model_tab({'error': '<b>'}))

    def test_a_broken_model_view_never_breaks_the_run_tab(self):
        from src.automation_web import render_panel
        status = {'running': True, 'phase': 'READY', 'reason': 'Fishing.', 'trips': 3, 'paused': False,
                  'config': {'max_trips': None}, 'dashboard': {}, 'account': None,
                  'model': {'error': None, 'setup': None}}
        html = render_panel(status, 'live')
        self.assertIn('data-tab="run"', html)
        self.assertIn('The Model tab could not be shown', html)

    def test_a_flagged_factor_shows_how_far_off_it_is(self):
        from src.automation_web import render_model_tab
        model = {'error': None, 'setup': {'rod': 'Steel Rod', 'biome': 'River', 'bait': 'none', 'pet': 'none',
                                          'boosts': []}, 'buffs': [],
                 'trip': [{'name': 'fish', 'label': 'Fish', 'predicted': 10.0, 'corrected': 11.4, 'measured': 11.4,
                           'factor': 1.125, 'status': 'applied', 'flag': True, 'per_hour': 10260.0,
                           'source': 'formula'}],
                 'mix': {'source': '', 'rows': []}, 'estimates': []}
        self.assertIn('applied ⚠ 13% off', render_model_tab(model))

    def test_next_fish_counts_down_in_the_page(self):
        # The panel is replaced every 2 s; the page counts down between refreshes instead of jumping.
        from src.automation_web import SCRIPT
        status = {'running': True, 'reason': '', 'phase': 'WAITING_COOLDOWN', 'trips': 3, 'config': {'max_trips': 0},
                  'next_eligible_in': 2.46, 'dashboard': {}}
        self.assertIn('<dd class="known" data-left="2.46">in 2.5 s', render_panel(status))
        self.assertIn('[data-left]', SCRIPT)
        self.assertIn('requestAnimationFrame', SCRIPT)
        # The status strip counts down too, from the same value.
        status['reason'] = 'Next fish in 3.4 s.'
        self.assertIn('<span class="why">Next fish <span data-left="2.46">in 2.5 s</span>.</span>', render_panel(status))

    def test_the_xp_line_is_moved_by_the_page_across_levels(self):
        # Live: a big XP gain moved the bar a little, then it snapped (a level-up made the new bar shorter, and a
        # refresh mid-glide restarted it). The page keeps its own position and eases it through each level's end.
        from src.automation_web import SCRIPT, STYLE
        status = {'running': False, 'reason': 'done', 'phase': 'STOPPED', 'trips': 0, 'config': {'max_trips': 0},
                  'dashboard': {'profile': {'level': 31, 'xp_current': 1500, 'xp_required': 6000}}}
        self.assertIn('class="line" data-level="31" data-share="25.0"', render_panel(status))
        self.assertIn('data-share', SCRIPT)
        self.assertIn('@keyframes', STYLE)
        self.assertNotIn('transition:width', STYLE)

    def test_a_button_press_comes_back_to_its_tab(self):
        # Pressing Do it on the Plan tab used to land back on the Run tab, scrolled to the top.
        from src.automation_web import SCRIPT
        self.assertIn("panel.addEventListener('submit'", SCRIPT)
        self.assertIn("event.target.action = '/automation' + location.hash", SCRIPT)
        self.assertIn("sessionStorage.setItem('back'", SCRIPT)
        self.assertIn('window.scrollTo(0, back.y)', SCRIPT)

    def test_pets_box_lists_each_pet_with_its_effects(self):
        from src.automation_web import _pets_box
        account = {'sections': {
            'pet': {'values': {'current': 'Zebrafish', 'owned': {
                'Dolphin': {'level': 51, 'xp_current': 1493, 'xp_required': 2600}, 'Zebrafish': {'level': 61}}}},
            'shop_league': {'values': {'items': {'Pet Helper': {'level': 2}}}}}}
        html = _pets_box(account, {})
        # The game's own numbers: Dolphin 51 is 36.19% (its pet screen), Zebrafish 61 fish catch 13.94% (/pet).
        self.assertIn('Treasure chance +36.19%, XP +36.19%', html)
        self.assertIn('Fish catch +13.94%, Fish quality +55.75%', html)
        self.assertIn('Zebrafish <span class="gain">in use</span>', html)
        self.assertIn('51 / 135', html)                                 # 125 + 2 Pet Helper levels
        self.assertIn('1,493 / 2,600 XP', html)
        self.assertIn('Gives you bonus XP and leads you to more treasure.', html)   # from pet.json, not OCR
        self.assertLess(html.index('Zebrafish'), html.index('Dolphin'))           # highest level first
        self.assertIn('Not read yet', _pets_box({}, {}))

    def test_chests_are_listed_by_rarity(self):
        from src.automation_web import _finds_box
        chests = {'legendary crate': 1, 'uncommon chest': 90, 'rare chest': 50, 'common chest': 40, 'epic crate': 9,
                  'super crate': 2}
        html = _finds_box({'run': {}, 'all': {'chests': chests}})
        order = ['common chest', 'uncommon chest', 'rare chest', 'epic crate', 'legendary crate', 'super crate']
        self.assertEqual(sorted(order, key=html.index), order)

    def test_tab_script_keeps_the_hash_across_refreshes(self):
        from src.automation_web import SCRIPT
        self.assertIn('location.hash', SCRIPT)
        self.assertIn('showTab()', SCRIPT)
        # the 2-second refresh keeps each scrolling list's and the page's position
        self.assertIn('el.scrollTop = at[0]', SCRIPT)
        self.assertIn('window.scrollTo(x, y)', SCRIPT)
        # ... and which toggles were open: each details[data-key] is recorded before and set back after.
        self.assertIn("querySelectorAll('details[data-key]')", SCRIPT)
        self.assertIn('open[d.getAttribute(\'data-key\')] = d.open', SCRIPT)
        self.assertIn('d.open = open[key]', SCRIPT)
        self.assertLess(SCRIPT.index('= d.open'), SCRIPT.index('if (current) updateSection(current, fresh, key)'))
        self.assertLess(SCRIPT.index('if (current) updateSection(current, fresh, key)'), SCRIPT.index('d.open = open[key]'))
        self.assertLess(SCRIPT.index('d.open = open[key]'), SCRIPT.index('window.scrollTo(x, y)'))
        # A run starting or stopping loads the page afresh: reloading a page a press answered would resend it (a
        # tab left on an old Stop page stopped the next run a second after it started).
        self.assertNotIn('location.reload', SCRIPT)
        self.assertIn("location.replace('/automation' + location.hash)", SCRIPT)

    def test_waiting_for_discord_is_its_own_state(self):
        from src.automation_web import state_label
        status = {'running': True, 'phase': 'WAITING_FOCUS', 'trips': 3, 'paused': False}
        self.assertEqual(state_label(status), 'Waiting for Discord')

    def test_account_reads_are_not_fishing(self):
        from src.account_reads import ACCOUNT_READS, PAGE_TWO
        from src.automation_web import state_label
        for kind in (*ACCOUNT_READS, *PAGE_TWO):
            for trips in (0, 12):
                status = {'running': True, 'phase': 'WAITING_REPLY', 'trips': trips, 'paused': False,
                          'pending_kind': kind}
                self.assertTrue(state_label(status).startswith('Reading '), (kind, state_label(status)))
        status = {'running': True, 'phase': 'WAITING_REPLY', 'trips': 0, 'paused': False,
                  'pending_kind': 'refresh_upgrades_page2'}
        self.assertEqual(state_label(status), 'Reading the upgrade shop')
        self.assertEqual(state_label(dict(status, pending_kind='fish')), 'Fishing')

    def test_account_box_lists_what_replies_showed(self):
        from src import account_samples as sample
        from src.account_replies import PARSERS
        from src.account_state import apply_read, empty_state
        account = empty_state()
        for kind, text in (('shop_upgrades', sample.UPGRADES_PAGE_1), ('shop_upgrades', sample.UPGRADES_PAGE_2),
                           ('rod', sample.ROD), ('shop_boats', sample.BOATS), ('pet', sample.PETS),
                           ('shop_league', sample.LEAGUE_PAGE_1), ('bait', sample.BAIT), ('biome', sample.BIOME)):
            account = apply_read(account, kind, PARSERS[kind](text), '2026-10-10T00:00:00+00:00')
        status = {'running': False, 'reason': 'done', 'phase': 'STOPPED', 'trips': 0, 'config': {'max_trips': 0},
                  'dashboard': {}, 'account': account}
        panel = unescape(render_panel(status))
        self.assertIn('<h2>Account</h2>', panel)
        for text in ('Better Fish 7/21', 'Better Chests 0/5', 'Fiberglass Rod (in use)', 'Rowboat',
                     'Fishing Boat next, $25,000', 'Dolphin 52 (current)', 'Super Crates 2/5', 'None in use; none owned',
                     'River', 'Hooks: 11'):
            self.assertIn(text, panel)
        self.assertIn('Not read: Worker Crates, Fishing Frenzy, Duplicator 2.0', panel, 'league page 2 missing')
        self.assertIn('Prestige shop', panel)
        self.assertIn('Not read yet', panel)

    def test_account_box_lists_bait_owned(self):
        from src import account_samples as sample
        from src.account_replies import PARSERS
        from src.account_state import apply_read, empty_state
        account = apply_read(empty_state(), 'bait', PARSERS['bait'](sample.BAIT_OWNED), '2026-10-10T00:00:00+00:00')
        status = {'running': False, 'reason': 'done', 'phase': 'STOPPED', 'trips': 0, 'config': {'max_trips': 0},
                  'dashboard': {}, 'account': account}
        panel = unescape(render_panel(status))
        self.assertIn('Support Bait in use; Worms 68, Leeches 1,006, Magnet 212, Wise Bait 1,479, Fish 909, '
                      'Artifact Magnet 1,727, Magic Bait 1,485, Support Bait 982', panel)
        # A later whole reply showing no bait lines means none are left.
        account = apply_read(account, 'bait', PARSERS['bait'](sample.BAIT), '2026-10-11T00:00:00+00:00')
        panel = unescape(render_panel(dict(status, account=account)))
        self.assertIn('None in use; none owned', panel)

    def test_all_eight_charms_are_listed_without_scrolling(self):
        from src.automation_web import _finds_box
        from src.fishing_log import CHARM_NAMES
        html = _finds_box({'run': {}, 'all': {'charms': {'Quality Charm': 3}}})
        charms = html[html.index('<h3>Charms</h3>'):]
        for name in CHARM_NAMES:
            self.assertIn(f'<th scope="row">{name}</th>', charms)
        self.assertNotIn('class="scroll"', charms, 'eight rows fit without scrolling')
        self.assertLess(charms.index('Marketing Charm'), charms.index('Experience Charm'), 'in /charms order')

    def test_finds_rewards_and_scrolling_lists(self):
        self.post(action='start', mode='simulation', max_trips='2', max_minutes='5')
        self.wait_until_stopped()
        panel = self.get('/automation/panel')
        self.assertIn('<h2>Finds</h2>', panel)
        for label in ('Chests', 'Special fish', 'Charms', 'Quests', 'Level-ups'):
            self.assertIn(label, panel)
        self.assertIn('class="scroll"', panel, 'long lists scroll inside their box')

    def test_repeated_start_and_stop(self):
        self.post(action='start', mode='simulation', max_trips='1000', max_minutes='5')
        html = self.post(action='start', mode='simulation', max_trips='1000', max_minutes='5')
        self.assertIn('already running', html)
        html = self.post(action='stop')
        self.assertIn('Stopped', html)
        self.assertFalse(self.handler.automation.status()['running'])


if __name__ == '__main__':
    unittest.main()


class AhkWebLifecycleTests(unittest.TestCase):
    def test_override_and_input_start_precede_controller_thread(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            cls=type('AhkHandler',(Handler,),{})
            handler=cls.__new__(cls)
            cls.automation=None; cls.automation_transport=None
            cls.automation_live_root=directory; cls.identity_path=Path(directory)/'identity.json'
            cls.automation_ahk_exe=Path('C:/AHK/AutoHotkey64.exe')
            order=[]; controller=Mock(); transport=Mock()
            transport.enable.side_effect=lambda:order.append('enable')
            controller.start.side_effect=lambda **kw:order.append('start')
            readiness=Mock(return_value=''); factory=Mock(return_value=(controller,transport))
            cls.automation_live_problem=staticmethod(readiness); cls.automation_live_factory=staticmethod(factory)
            result=handler.start_live({'account':['epicmouse'],'username':['epicmouse9925']},1,None)
            self.assertEqual(result,''); self.assertEqual(order,['enable','start'])
            self.assertEqual(factory.call_args.kwargs,{'ahk_exe':cls.automation_ahk_exe})
            readiness.assert_called_once_with(ahk_exe=cls.automation_ahk_exe)
            from unittest.mock import patch
            with patch('src.player_web.save_diagnostics',return_value=''):
                handler.stop_automation()
            transport.close.assert_called_once()

    def test_disposal_closes_input_even_when_diagnostics_fail(self):
        from unittest.mock import Mock, patch
        cls=type('AhkFailureHandler',(Handler,),{})
        handler=cls.__new__(cls)
        cls.automation=Mock(); cls.automation_transport=Mock()
        transport=cls.automation_transport
        with patch('src.player_web.save_diagnostics',side_effect=RuntimeError('bad diagnostic')):
            with self.assertRaises(RuntimeError): handler.stop_automation()
        transport.close.assert_called_once()
        self.assertIsNone(cls.automation_transport)
