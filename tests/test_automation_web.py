from html import unescape
from http.server import ThreadingHTTPServer
from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode
from urllib.request import urlopen

from src.automation_transport import SimulationTransport
from src.automation_web import render_panel
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
        self.assertIn('Switch to Discord', html)
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
        self.post(action='resume')
        self.assertFalse(self.handler.automation.status()['paused'])

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
