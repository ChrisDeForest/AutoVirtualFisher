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

from src.player_web import MAX_FORM_BYTES, Handler

FIXTURES = Path(__file__).parent / 'fixtures'
LIVE_FISHING = (FIXTURES / 'live_fishing.txt').read_text(encoding='utf-8')


def named_profile(balance=100, level=2, name='BugParticle', extra=''):
    return f'Inventory of {name}\nBalance: ${balance}\nLevel: {level}\n{extra}'


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.mkdtemp()
        cls.root = Path(cls.temporary) / 'snapshots'
        cls.fishing_root = Path(cls.temporary) / 'fishing'
        cls.handler = type('IsolatedHandler', (Handler,), {'snapshot_root': cls.root,
                                                           'fishing_root': cls.fishing_root})
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
        shutil.rmtree(self.fishing_root, ignore_errors=True)
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
            # Declare an oversized body but send one byte: the server rejects on the header alone, and a
            # real 1 MB upload races the early 413 on Windows (WinError 10053).
            urlopen(Request(self.url, data=b'x', headers={'Content-Length': str(MAX_FORM_BYTES + 1)}))
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

    def test_history_view_shows_four_growth_charts(self):
        for balance, level in ((100, 2), (400, 3), (900, 5)):
            self.post(profile=named_profile(balance=balance, level=level, extra='Fish Value: $10'))
        html = self.get('/?account=BugParticle')
        self.assertIn('<h3>Growth</h3>', html)
        self.assertEqual(html.count('<figure class="chart">'), 4)
        self.assertEqual(html.count('<svg'), 2)
        self.assertIn('Balance: $100 → $900 (+$800)', html)
        self.assertIn('Fish value: $10 → $10 (no change)', html)
        self.assertEqual(html.count('Needs two full snapshots with this value.'), 2)

    def test_single_snapshot_asks_for_another(self):
        self.post(profile=named_profile())
        html = self.get('/?account=BugParticle')
        self.assertEqual(html.count('Needs two full snapshots with this value.'), 4)
        self.assertNotIn('<svg', html)

    def session_files(self):
        return list(self.fishing_root.rglob('*.json')) if self.fishing_root.exists() else []

    def test_reading_fishing_results_shows_tally_without_saving(self):
        html = self.post(fishing=LIVE_FISHING, action='fishing_read')
        self.assertIn('Fishing results', html)
        self.assertIn('<dt>Trips</dt><dd class="known">6</dd>', html)
        self.assertIn('Salmon 48', html)
        self.assertIn('631 (105.17 per trip)', html)
        self.assertIn('Daily Level-ups tier 1', html)
        self.assertEqual(self.session_files(), [])

    def test_unrecognized_fishing_lines_are_shown_escaped(self):
        html = self.post(fishing='You caught:\n3 Cod\n<b>odd</b>', action='fishing_read')
        self.assertIn('Unrecognized line: &lt;b&gt;odd&lt;/b&gt;', html)
        self.assertNotIn('<b>odd</b>', html)

    def test_saving_fishing_session_requires_an_existing_account(self):
        html = self.post(fishing=LIVE_FISHING, action='fishing_save')
        self.assertIn('Choose an account', html)
        html = self.post(fishing=LIVE_FISHING, action='fishing_save', account='Stranger')
        self.assertIn('Choose an existing account', html)
        self.assertEqual(self.session_files(), [])

    def test_saving_fishing_session_once_and_showing_totals(self):
        self.post(profile=named_profile())
        html = self.post(fishing=LIVE_FISHING, action='fishing_save', account='BugParticle')
        self.assertIn('Fishing session saved for BugParticle', html)
        html = self.post(fishing=LIVE_FISHING, action='fishing_save', account='BugParticle')
        self.assertIn('already saved', html)
        self.assertEqual(len(self.session_files()), 1)
        html = self.get('/?account=BugParticle')
        self.assertIn('Fishing (all time)', html)
        self.assertIn('16.83', html)
        self.assertIn('artifact box 16.67', html)
        self.assertNotIn('epicmouse9925', html)

    def test_large_fishing_batches_are_accepted(self):
        html = self.post(fishing=LIVE_FISHING * 80, action='fishing_read')
        self.assertIn('<dt>Trips</dt><dd class="known">480</dd>', html)

    def test_quests_response_is_parsed_and_saved_with_snapshot(self):
        quests = (FIXTURES / 'live_quests.txt').read_text(encoding='utf-8')
        html = self.post(profile=named_profile(), quests=quests)
        self.assertIn('Daily Level-ups 1/2, Daily Fishing 101/500, Daily Artifact Hunter 1/5', html)
        self.assertIn('Pet Locator 0/1', html)
        self.assertIn('Snapshot saved for BugParticle', html)
        html = self.get('/?account=BugParticle')
        self.assertIn('Daily Fishing 101/500', html)
        self.assertNotIn('Quests have multiple tiers', html)

    def test_snapshots_saved_before_quests_existed_still_render(self):
        self.post(profile=named_profile(balance=777))
        path = next(self.root.rglob('*.json'))
        stored = json.loads(path.read_text(encoding='utf-8'))
        del stored['state']['quests']
        path.write_text(json.dumps(stored), encoding='utf-8')
        html = self.get('/?account=BugParticle')
        self.assertIn('777', html)
        self.assertIn('<h3>Quests</h3>', html)

    def test_planner_card_uses_live_profile_and_fishing(self):
        self.post(profile=(FIXTURES / 'live_profile.txt').read_text(encoding='utf-8'))
        self.post(fishing=LIVE_FISHING, action='fishing_save', account='BugParticle')
        html = self.get('/?account=BugParticle&target=135')
        self.assertIn('<h3>Planner</h3>', html)
        self.assertIn('XP model matches 1 of your snapshots', html)
        self.assertIn('<dt>Level 132</dt><dd class="known">206,638 XP · about 1,965 trips</dd>', html)
        self.assertIn('<dt>Level 135</dt>', html)
        self.assertIn('<dt>Level 136</dt>', html)
        self.assertIn('name="target" min="1" value="135"', html)

    def test_planner_ignores_invalid_target_and_explains_missing_fishing(self):
        self.post(profile=(FIXTURES / 'live_profile.txt').read_text(encoding='utf-8'))
        html = self.get('/?account=BugParticle&target=abc')
        self.assertIn('<dt>Level 132</dt><dd class="known">206,638 XP · save a fishing session to estimate trips</dd>', html)
        self.assertIn('value=""', html)

    def test_planner_shows_quest_estimates(self):
        quests = (FIXTURES / 'live_quests.txt').read_text(encoding='utf-8')
        self.post(profile=(FIXTURES / 'live_profile.txt').read_text(encoding='utf-8'), quests=quests)
        self.post(fishing=LIVE_FISHING, action='fishing_save', account='BugParticle')
        html = self.get('/?account=BugParticle')
        self.assertIn('<dt>Daily Fishing (101/500)</dt><dd class="known">about 24 trips (399 fish at 16.83 per trip)</dd>', html)
        self.assertIn('Pet Locator (0/1)', html)
        self.assertIn('No pet finds in your fishing sessions yet', html)

    def test_fishing_batch_shows_xp_check(self):
        html = self.post(fishing=LIVE_FISHING, action='fishing_read')
        self.assertIn('<h3>XP check</h3>', html)
        self.assertIn('<td>1</td><td>76</td><td>140</td><td>1.84×</td>', html)
        self.assertIn('▲ ratio changed from 1.84× to 1.94×', html)
        self.assertIn('Found Experience Charm', html)
        self.assertIn('Base sell value of this batch: $620', html)
        self.assertIn('Choose an account to check catches against its biome.', html)

    def test_xp_check_flags_fish_outside_the_accounts_biome(self):
        self.post(profile=(FIXTURES / 'live_profile.txt').read_text(encoding='utf-8'))
        html = self.post(fishing=LIVE_FISHING, action='fishing_read', account='BugParticle')
        self.assertIn('Biome: Ocean, from the latest snapshot.', html)
        self.assertIn('Wiki says not caught in Ocean: Salmon, Cod', html)

    def test_paste_anything_sorts_into_boxes_without_saving(self):
        copy = (FIXTURES / 'live_discord_copy.txt').read_text(encoding='utf-8')
        html = self.post(everything=copy, action='sort')
        self.assertIn('Found 4 profiles (using the newest, from Yesterday at 5:35 PM)', html)
        self.assertIn('26 fishing results', html)
        self.assertIn('Skipped 15 other bot messages and 1 of your own messages.', html)
        profile_box = html[html.index('id="profile"'):html.index('</textarea>', html.index('id="profile"'))]
        self.assertIn('Inventory of epicmouse', profile_box)
        self.assertIn('Bait: :bait_worms: Worms (10)', profile_box)
        fishing_box = html[html.index('id="fishing"'):html.index('</textarea>', html.index('id="fishing"'))]
        self.assertEqual(fishing_box.count('You caught:'), 26)
        self.assertEqual(self.snapshot_files(), [])
        self.assertEqual(self.session_files(), [])

    def test_paste_anything_preselects_a_known_account_for_fishing(self):
        self.post(profile=named_profile(name='epicmouse'))
        html = self.post(everything='Inventory of epicmouse\nBalance: $5\nLevel: 3\n\nepicmouse9925\nYou caught:\n2 Cod\n+5 XP',
                         action='sort')
        fishing_select = html[html.index('id="fishing_account"'):html.index('</select>', html.index('id="fishing_account"'))]
        self.assertIn('<option value="epicmouse" selected>', fishing_select)

    def test_paste_anything_with_nothing_recognized(self):
        html = self.post(everything='just chatting', action='sort')
        self.assertIn('Nothing recognizable was found', html)

    def upload(self, files):
        boundary = 'testboundary'
        body = b''
        for name, content in files:
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="screenshots"; filename="{name}"\r\n'
                     f'Content-Type: image/png\r\n\r\n').encode() + content + b'\r\n'
        body += f'--{boundary}--\r\n'.encode()
        request = Request(self.url + '/screenshots', data=body,
                          headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
        with urlopen(request) as response:
            return response.read().decode()

    def test_screenshots_are_read_into_the_boxes(self):
        def fake_reader(path):
            fixture = Path(path).read_bytes().decode()
            return (FIXTURES / f'ocr_{fixture}.txt').read_text(encoding='utf-8').splitlines()
        self.handler.ocr_reader = staticmethod(fake_reader)
        self.handler.ocr_ready = True
        try:
            html = self.upload([('a.png', b'profile_msg'), ('b.png', b'2x_fish_msgs'), ('notes.txt', b'x')])
        finally:
            del self.handler.ocr_reader, self.handler.ocr_ready
        self.assertIn('Read 2 screenshots; skipped notes.txt (not a PNG or JPEG image).', html)
        self.assertIn('Found 1 profile, 2 fishing results.', html)
        profile_box = html[html.index('id="profile"'):html.index('</textarea>', html.index('id="profile"'))]
        self.assertIn('Inventory of epicmouse', profile_box)
        self.assertEqual(self.snapshot_files(), [])
        self.assertEqual(list(Path(self.temporary).rglob('*.png')), [], 'uploaded images are not kept')

    def test_screenshots_need_the_ocr_packages(self):
        self.handler.ocr_ready = False
        try:
            html = self.get()
            self.assertIn('requirements-ocr.txt', html)
            html = self.upload([('a.png', b'x')])
        finally:
            del self.handler.ocr_ready
        self.assertIn('Screenshot reading needs', html)

    def test_screenshot_form_stays_after_other_posts(self):
        self.handler.ocr_ready = True
        try:
            for fields in ({'profile': named_profile()}, {'fishing': 'You caught:\n1 Cod', 'action': 'fishing_read'},
                           {'everything': 'x', 'action': 'sort'}):
                with self.subTest(fields=fields):
                    self.assertIn('Read screenshots', self.post(**fields))
        finally:
            del self.handler.ocr_ready

    def test_planner_shows_money_per_trip_and_target_balance(self):
        self.post(profile=(FIXTURES / 'live_profile.txt').read_text(encoding='utf-8'),
                  buffs=(FIXTURES / 'live_buffs.txt').read_text(encoding='utf-8'))
        self.post(fishing=LIVE_FISHING, action='fishing_save', account='BugParticle')
        html = self.get('/?account=BugParticle&money=3000000')
        self.assertIn('<dt>Gross catch value per trip</dt><dd class="known">$499.10 ($103.33 at wiki base prices × 4.83 sell price)</dd>', html)
        self.assertIn('<dt>Reach $3,000,000</dt><dd class="known">$519,697 more (balance + unsold fish: $2,480,303) · about 1,042 trips</dd>', html)
        self.assertIn('Fish Value matches wiki base prices × your sell price in 1 of your snapshots.', html)
        self.assertIn('name="money" min="1" value="3000000"', html)

    def test_planner_money_needs_buffs(self):
        self.post(profile=(FIXTURES / 'live_profile.txt').read_text(encoding='utf-8'))
        html = self.get('/?account=BugParticle')
        self.assertIn('Paste /buffs to include your sell price', html)

    def test_xp_check_values_catches_at_the_accounts_sell_price(self):
        self.post(profile=(FIXTURES / 'live_profile.txt').read_text(encoding='utf-8'),
                  buffs=(FIXTURES / 'live_buffs.txt').read_text(encoding='utf-8'))
        html = self.post(fishing=LIVE_FISHING, action='fishing_read', account='BugParticle')
        self.assertIn('<td>1</td><td>76</td><td>140</td><td>1.84×</td><td>$691</td>', html)
        self.assertIn('Sell value of this batch: $2,995 at your sell price of 4.83× ($620 at wiki base prices).', html)

    def test_fishing_session_records_the_chosen_bait(self):
        self.post(profile=named_profile())
        html = self.post(fishing=LIVE_FISHING, action='fishing_save', account='BugParticle', bait='Worms')
        self.assertIn('<option value="Worms" selected>Worms</option>', html)
        self.assertIn('<dt>Bait</dt><dd class="known">Worms</dd>', html)
        stored = json.loads(self.session_files()[0].read_text(encoding='utf-8'))
        self.assertEqual([trip['bait'] for trip in stored['tally']['trip_details']], ['Worms'] * 6)

    def test_paste_anything_preselects_bait_from_the_profile(self):
        html = self.post(everything=(FIXTURES / 'live_discord_copy.txt').read_text(encoding='utf-8'), action='sort')
        bait_select = html[html.index('id="fishing_bait"'):html.index('</select>', html.index('id="fishing_bait"'))]
        self.assertIn('<option value="Worms" selected>Worms</option>', bait_select)

    def test_history_view_compares_baits(self):
        self.post(profile=(FIXTURES / 'live_profile.txt').read_text(encoding='utf-8'),
                  buffs=(FIXTURES / 'live_buffs.txt').read_text(encoding='utf-8'))
        self.post(fishing=LIVE_FISHING, action='fishing_save', account='BugParticle', bait='Worms')
        self.post(fishing='You caught:\n4 Cod\n+40 XP', action='fishing_save', account='BugParticle', bait='none')
        self.post(fishing='You caught:\n9 Cod\n+90 XP', action='fishing_save', account='BugParticle', bait='')
        html = self.get('/?account=BugParticle')
        self.assertIn('<h3>Bait comparison</h3>', html)
        self.assertIn('<td>Worms *</td><td>6 (6 boosted)</td><td>16.83</td><td>1.85×</td><td>$499</td><td>$4</td><td>$495</td>'
                      '<td>66.67</td>'
                      '<td>+2 fish, XP −10%</td>', html)
        self.assertIn('<td>No bait *</td><td>1 (0 boosted)</td><td>4.00</td><td>2.00×</td>', html)
        self.assertIn('1 trip from sessions saved without a bait is left out.', html)
