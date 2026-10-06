import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from src.player_web import Handler

FIXTURES = Path(__file__).parent / 'fixtures'


def named_profile(balance=100, level=2, name='BugParticle', extra=''):
    return f'Inventory of {name}\nBalance: ${balance}\nLevel: {level}\n{extra}'


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.mkdtemp()
        cls.root = Path(cls.temporary) / 'snapshots'
        cls.handler = type('IsolatedHandler', (Handler,), {'snapshot_root': cls.root})
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), cls.handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()
        shutil.rmtree(cls.temporary, ignore_errors=True)

    def setUp(self):
        shutil.rmtree(self.root, ignore_errors=True)
        self.handler.snapshot_root = self.root

    def post(self, **fields):
        with urlopen(self.url, data=urlencode(fields).encode()) as response:
            return response.read().decode()

    def get(self, query=''):
        with urlopen(self.url + query) as response:
            return response.read().decode()

    def snapshot_files(self):
        return list(self.root.rglob('*.json')) if self.root.exists() else []

    def test_form_and_parsed_result(self):
        with urlopen(self.url) as response:
            self.assertIn('Paste your bot responses', response.read().decode())
        data = urlencode({'profile': 'Balance: $123\nRod: <script>alert(1)</script>', 'buffs': ''}).encode()
        with urlopen(self.url, data=data) as response:
            html = response.read().decode()
        self.assertIn('123', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>alert(1)</script>', html)

    def test_prestige_shop_form_uses_the_shared_parser(self):
        data = urlencode({'prestige_shop': 'You have: 0 Azure Fish.\n'
                          'International Ties - MAXED\nBusiness Education - MAXED\n'
                          'Fish Whisperer - MAXED\nAncient One - MAXED\n'
                          'UNLOCKED AT PRESTIGE 5: Virtual Fisher - 0/1'}).encode()
        with urlopen(self.url, data=data) as response:
            html = response.read().decode()
        self.assertIn('Azure fish', html)
        self.assertIn('Inferred prestige minimum', html)
        self.assertIn('4', html)

    def test_pet_form_records_the_confirmed_no_pet_response(self):
        data = urlencode({'pet': '+14 pet XP per fishing trip (average)\nYou have no pets! Find pets by fishing.'}).encode()
        with urlopen(self.url, data=data) as response:
            html = response.read().decode()
        self.assertIn('Xp per fishing trip average', html)
        self.assertIn('No pets', html)

    def test_pet_collection_shows_owned_names_without_raw_nested_data(self):
        data = urlencode({'pet': '''Current pet: Dolphin.
Puffer(Lvl 30)
Increases your fish catch.
1,493/2,600 XP to next level.
Dolphin(Lvl 51)
Gives you bonus XP and leads you to more treasure.
13,986/18,000 XP to next level.'''}).encode()
        with urlopen(self.url, data=data) as response:
            html = response.read().decode()
        self.assertIn('Puffer, Dolphin', html)
        self.assertNotIn('description:', html)

    def test_json_download_uses_same_state(self):
        data = urlencode({'profile': 'Level: 149', 'format': 'json'}).encode()
        with urlopen(self.url, data=data) as response:
            self.assertIn('attachment', response.headers['Content-Disposition'])
            state = json.loads(response.read())
        self.assertEqual(state['profile']['level'], 149)
        self.assertIsNone(state['profile']['balance'])

    def test_unknown_route_and_oversized_input_rejected(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + '/missing')
        self.assertEqual(error.exception.code, 404)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.url, data=b'x' * 70000))
        self.assertEqual(error.exception.code, 413)

    def test_valid_full_profile_is_saved_automatically_once(self):
        html = self.post(profile=named_profile())
        self.assertIn('Snapshot saved for BugParticle', html)
        self.assertEqual(len(self.snapshot_files()), 1)
        html = self.post(profile=named_profile())
        self.assertIn('unchanged', html)
        self.assertEqual(len(self.snapshot_files()), 1)

    def test_partial_state_renders_without_autosaving(self):
        html = self.post(profile='Balance: $555')
        self.assertIn('555', html)
        self.assertIn('Not saved automatically', html)
        self.assertEqual(self.snapshot_files(), [])

    def test_manual_partial_save_requires_an_account(self):
        html = self.post(pet='You have no pets! Find pets by fishing.', action='save')
        self.assertIn('Choose an account', html)
        self.assertEqual(self.snapshot_files(), [])

    def test_manual_partial_save_uses_selected_account_without_becoming_latest_full(self):
        self.post(profile=named_profile(balance=100))
        html = self.post(pet='You have no pets! Find pets by fishing.', action='save',
                         account='BugParticle')
        self.assertIn('Partial snapshot saved for BugParticle', html)
        self.assertEqual(len(self.snapshot_files()), 2)
        html = self.get('/?account=BugParticle')
        self.assertIn('manual', html)
        self.assertIn('partial', html)
        self.assertIn('another full snapshot', html.lower())

    def test_manual_save_rejects_unknown_selected_account(self):
        html = self.post(pet='You have no pets! Find pets by fishing.', action='save',
                         account='Stranger')
        self.assertIn('Choose an existing account', html)
        self.assertEqual(self.snapshot_files(), [])

    def test_dashboard_lists_accounts_and_compares_latest_full_snapshots(self):
        self.post(profile=named_profile(balance=100, level=2, extra='Fish Value: $50'))
        self.post(profile=named_profile(balance=1600, level=4, extra='Fish Value: $80'))
        self.post(profile=named_profile(name='Other <b>Fisher</b>'))
        html = self.get()
        self.assertIn('<option value="BugParticle">BugParticle</option>', html)
        self.assertIn('Other &lt;b&gt;Fisher&lt;/b&gt;', html)
        self.assertNotIn('<b>Fisher</b>', html)
        html = self.get('/?account=BugParticle')
        self.assertIn('+1,500', html)
        self.assertIn('+2', html)
        self.assertIn('+30', html)
        self.assertIn('automatic', html)
        self.assertIn('<option value="BugParticle" selected>', html)

    def test_dashboard_does_not_restore_raw_responses(self):
        self.post(profile=FIXTURES.joinpath('live_profile.txt').read_text(encoding='utf-8'))
        html = self.get('/?account=BugParticle')
        self.assertIn('2,343,033', html)
        self.assertNotIn('Currently using', html)
        self.assertNotIn('Inventory of BugParticle', html)

    def test_dashboard_warns_about_malformed_history(self):
        self.post(profile=named_profile())
        folder = next(self.root.iterdir())
        folder.joinpath('broken.json').write_text('{not json', encoding='utf-8')
        html = self.get('/?account=BugParticle')
        self.assertIn('broken.json', html)
        self.assertIn('100', html)

    def test_failed_save_still_renders_parsed_state(self):
        blocker = Path(self.temporary) / 'not-a-directory'
        blocker.write_text('occupied', encoding='utf-8')
        self.handler.snapshot_root = blocker
        html = self.post(profile=named_profile(balance=4321))
        self.assertIn('4,321', html)
        self.assertIn('could not be saved', html)

    def test_json_action_downloads_without_saving(self):
        with urlopen(self.url, data=urlencode({'profile': named_profile(), 'action': 'json'}).encode()) as response:
            self.assertIn('attachment', response.headers['Content-Disposition'])
            json.loads(response.read())
        self.assertEqual(self.snapshot_files(), [])
