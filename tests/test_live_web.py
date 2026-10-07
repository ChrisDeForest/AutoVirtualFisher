from html import unescape
from http.server import ThreadingHTTPServer
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from urllib.parse import urlencode
from urllib.request import urlopen

from src.player_snapshots import save_snapshot
from src.player_state import parse_responses
from src.player_web import Handler
from src.review_queue import add_item, list_items


def no_window():
    return {'status': 'no_window', 'lines': []}


class LiveWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = Path(tempfile.mkdtemp())
        cls.handler = type('LiveHandler', (Handler,), {
            'snapshot_root': cls.temporary / 'snapshots', 'fishing_root': cls.temporary / 'fishing',
            'review_root': cls.temporary / 'review', 'live_capture_function': staticmethod(no_window),
            'identity_path': cls.temporary / 'identities' / 'identities.json', 'live': None})
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), cls.handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        save_snapshot(parse_responses(profile='Inventory of epicmouse\nBalance: $5\nLevel: 3'),
                      cls.temporary / 'snapshots')

    @classmethod
    def tearDownClass(cls):
        if cls.handler.live:
            cls.handler.live.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()
        shutil.rmtree(cls.temporary, ignore_errors=True)

    def tearDown(self):
        if self.handler.live:
            self.handler.live.stop()
        shutil.rmtree(self.temporary / 'review', ignore_errors=True)

    def get(self):
        with urlopen(self.url + '/live') as response:
            return response.read().decode()

    def post(self, **fields):
        with urlopen(self.url + '/live', data=urlencode(fields).encode()) as response:
            return response.read().decode()

    def test_page_offers_start_with_accounts_and_baits(self):
        html = self.get()
        self.assertIn('<h1>Live capture</h1>', html)
        self.assertIn('<option value="epicmouse">epicmouse</option>', html)
        self.assertIn('<option value="Worms">Worms</option>', html)
        self.assertNotIn('http-equiv="refresh"', html)
        self.assertIn('href="/live"', urlopen(self.url).read().decode(), 'main page links to live capture')

    def test_start_and_stop(self):
        html = self.post(action='start', account='epicmouse', bait='Worms', username='epicmouse9925')
        self.assertIn('http-equiv="refresh"', html)
        self.assertIn('Watching epicmouse (epicmouse9925) with Worms', html)
        self.assertIn('Discord window not found', unescape(self.get()))
        html = self.post(action='stop')
        self.assertNotIn('http-equiv="refresh"', html)
        self.assertIn('Stopped', html)

    def test_start_needs_a_discord_username_once(self):
        shutil.rmtree(self.temporary / 'identities', ignore_errors=True)
        html = self.post(action='start', account='epicmouse', bait='', username='')
        self.assertIn('Enter your Discord username', html)
        self.assertFalse(self.handler.live and self.handler.live.status()['running'])
        self.post(action='start', account='epicmouse', bait='', username='epicmouse9925')
        self.post(action='stop')
        html = self.post(action='start', account='epicmouse', bait='', username='')
        self.assertIn('Watching epicmouse (epicmouse9925)', html, 'the saved username is used')

    def test_start_needs_an_existing_account(self):
        html = self.post(action='start', account='Stranger', bait='')
        self.assertIn('Choose a saved account', html)
        self.assertFalse(self.handler.live and self.handler.live.status()['running'])

    def test_review_queue_lists_loads_and_dismisses(self):
        item = add_item(self.temporary / 'review', 'epicmouse', 'pet', 'Puffer(Lvl 30)\n<b>XP</b> to next level.',
                        ['Pet XP could not be read for Puffer.'])
        html = self.get()
        self.assertIn('Needs review (1)', html)
        self.assertIn('Pet XP could not be read for Puffer.', html)
        self.assertIn('&lt;b&gt;XP&lt;/b&gt;', html)
        self.assertNotIn('<b>XP</b>', html)
        self.assertIn('<input type="hidden" name="action" value="sort">', html)
        self.assertIn('Load into boxes', html)
        html = self.post(action='dismiss', id=item['id'])
        self.assertIn('Needs review (0)', html)
        self.assertEqual(list_items(self.temporary / 'review')[0], [])


if __name__ == '__main__':
    unittest.main()
