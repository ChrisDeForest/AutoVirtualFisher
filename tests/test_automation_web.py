from html import unescape
from http.server import ThreadingHTTPServer
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode
from urllib.request import urlopen

from src.automation_transport import SimulationTransport
from src.fishing_sessions import load_sessions
from src.player_web import Handler


def fast_game(monotonic, utc):
    return SimulationTransport(monotonic, utc, cooldown=0.05, latency=0.01)


class AutomationWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = Path(tempfile.mkdtemp())
        cls.handler = type('AutomationHandler', (Handler,), {
            'snapshot_root': cls.temporary / 'snapshots', 'fishing_root': cls.temporary / 'fishing',
            'review_root': cls.temporary / 'review', 'automation_root': cls.temporary / 'simulation',
            'automation_game_factory': staticmethod(fast_game), 'automation_game': None,
            'automation_tick': 0.005, 'automation': None})
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

    def test_page_defaults_to_simulation_and_explains_live_mode(self):
        html = self.get()
        self.assertIn('<h1>Automation</h1>', html)
        self.assertIn('Simulation', html)
        self.assertIn('interaction-feasibility.md', html)
        self.assertNotIn('name="mode" value="live"', html, 'there is no live option to select')
        self.assertIn('href="/automation"', self.get('/'), 'the main page links here')

    def test_limits_are_validated(self):
        for fields in ({'max_trips': '0'}, {'max_trips': '5000'}, {'max_trips': 'lots'}, {'max_minutes': '0'},
                       {'max_minutes': '600'}):
            with self.subTest(fields=fields):
                html = self.post(action='start', **{'max_trips': '25', 'max_minutes': '15', **fields})
                self.assertIn('between', html)
                self.assertFalse(self.handler.automation and self.handler.automation.status()['running'])

    def test_bounded_simulated_run_saves_to_the_simulation_logbook_only(self):
        self.post(action='start', max_trips='3', max_minutes='5')
        self.wait_until_stopped()
        html = self.get()
        self.assertIn('trip limit reached', html)
        self.assertEqual(sum(s['tally']['trips'] for s in load_sessions('sim', self.temporary / 'simulation' /
                                                                       'fishing')[0]), 3)
        self.assertEqual(load_sessions('sim', self.temporary / 'fishing')[0], [], 'your real logbook is untouched')

    def test_repeated_start_and_stop(self):
        self.post(action='start', max_trips='1000', max_minutes='5')
        html = self.post(action='start', max_trips='1000', max_minutes='5')
        self.assertIn('already running', html)
        html = self.post(action='stop')
        self.assertIn('STOPPED', html)
        self.assertFalse(self.handler.automation.status()['running'])


if __name__ == '__main__':
    unittest.main()
