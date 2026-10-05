import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from src.player_web import Handler


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()

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
