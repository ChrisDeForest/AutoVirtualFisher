import unittest

from src import account_samples as sample
from src.account_replies import PARSERS


class SpecialShopTests(unittest.TestCase):
    def test_special_shop(self):
        from src.paste_detect import _kind
        self.assertEqual(_kind('Special Shop'), 'shop_special')
        parsed = PARSERS['shop_special'](sample.SPECIAL)
        self.assertEqual(parsed['values']['items'], {'Fish Ovens': {'level': 2, 'max': 20, 'price': None}})
        self.assertEqual((parsed['values']['inventory'], parsed['values']['more_at_level']), (49, 100))
        self.assertEqual(parsed['unread'], [])

    def test_a_second_special_shop_page_shows_a_reminder(self):
        # Only page 1 is read: when the shop grows a page 2 (expected from level 100), the Account box says so.
        from src.automation_web import _account_rows
        parsed = PARSERS['shop_special'](sample.SPECIAL + '\nPage 1/2')
        self.assertEqual(parsed['values']['pages'], 2)
        rows = {label: (text, unread) for label, text, unread in
                _account_rows({'shop_special': {'values': parsed['values'], 'unread': []}})}
        self.assertIn('page 2 is not read yet', rows['Special shop'][0])
        one = PARSERS['shop_special'](sample.SPECIAL)['values']
        rows = {label: text for label, text, _ in _account_rows({'shop_special': {'values': one, 'unread': []}})}
        self.assertNotIn('page 2', rows['Special shop'])

    def test_a_league_price_with_its_hook_icon_misread(self):
        parsed = PARSERS['shop_league']("League Shop\nBalance: 11\nSuper Crates (2/5)\nt.' 150\nFind super crates.")
        self.assertEqual(parsed['values']['items']['Super Crates']['price'], 150)

    def test_special_shop_is_an_account_read(self):
        from src.account_reads import ACCOUNT_READS
        self.assertEqual(ACCOUNT_READS['refresh_special'].command, '/shop special')


class ShopTests(unittest.TestCase):
    def test_upgrades_page_1_levels_and_next_prices(self):
        parsed = PARSERS['shop_upgrades'](sample.UPGRADES_PAGE_1)
        self.assertEqual(parsed['page'], (1, 2))
        self.assertEqual(parsed['values']['balance'], 266418)
        self.assertEqual(parsed['values']['items']['Better Fish'], {'level': 7, 'max': 21, 'price': 25000})
        self.assertEqual(parsed['values']['items']['Worker Motivation'], {'level': 1, 'max': 12, 'price': 50000})
        self.assertEqual(len(parsed['values']['items']), 5)
        self.assertEqual(parsed['unread'], [])

    def test_page_slash_read_as_one(self):
        parsed = PARSERS['shop_upgrades'](sample.UPGRADES_PAGE_1.replace('Page 1/2', 'Page 112'))
        self.assertEqual(parsed['page'], (1, 2))
        parsed = PARSERS['shop_upgrades'](sample.UPGRADES_PAGE_2.replace('Page 2/2', 'Page 212'))
        self.assertEqual(parsed['page'], (2, 2))

    def test_upgrades_page_2(self):
        parsed = PARSERS['shop_upgrades'](sample.UPGRADES_PAGE_2)
        self.assertEqual(parsed['page'], (2, 2))
        self.assertEqual(parsed['values']['items']['Better Chests'], {'level': 0, 'max': 5, 'price': 100000})

    def test_an_ocr_garbled_item_is_unread(self):
        parsed = PARSERS['shop_upgrades'](sample.UPGRADES_PAGE_1.replace('Salesman (7/20)', 'Sa1esrnan (7/2O)'))
        self.assertNotIn('Salesman', parsed['values']['items'])
        self.assertEqual(parsed['unread'], ['Sa1esrnan (7/2O)'])

    def test_a_price_read_just_before_its_item_belongs_to_it(self):
        # The button sits level with the item, so OCR may read the price first.
        text = '\n'.join(('Your balance: $10', '$25,000', 'Better Fish (7/21)', 'Causes slightly better fish to bite.',
                          '$25,000', 'Salesman (7/20)', 'Sell fish for 5% more.', 'Page 1/2'))
        items = PARSERS['shop_upgrades'](text)['values']['items']
        self.assertEqual((items['Better Fish']['price'], items['Salesman']['price']), (25000, 25000))

    def test_a_maxed_upgrade_has_no_price(self):
        parsed = PARSERS['shop_upgrades'](sample.UPGRADES_PAGE_1.replace('Bait Efficiency (5/9)\n$20,000',
                                                                          'Bait Efficiency (9/9)\nMAXED'))
        self.assertEqual(parsed['values']['items']['Bait Efficiency'], {'level': 9, 'max': 9, 'price': None})

    def test_league_shop_hooks_and_prices(self):
        parsed = PARSERS['shop_league'](sample.LEAGUE_PAGE_1)
        self.assertEqual(parsed['values']['hooks'], 11)
        self.assertEqual(parsed['values']['items']['Super Crates'], {'level': 2, 'max': 5, 'price': 150})
        second = PARSERS['shop_league'](sample.LEAGUE_PAGE_2)
        self.assertEqual(second['values']['items']['Duplicator 2.0'], {'level': 0, 'max': 10, 'price': 25})

    def test_the_rod_shop_is_not_read(self):
        # /rod lists every owned rod; prices and unlock levels come from game_model.json.
        from src.account_reads import ACCOUNT_READS
        self.assertNotIn('refresh_shop_rods', ACCOUNT_READS)
        self.assertNotIn('shop_rods', PARSERS)

    def test_boat_shop(self):
        parsed = PARSERS['shop_boats'](sample.BOATS)
        self.assertEqual(parsed['values']['items']['Rowboat'], {'owned': True, 'price': None})
        self.assertEqual(parsed['values']['items']['Yacht'], {'owned': False, 'price': 20000000})
        self.assertEqual(parsed['values']['more_at_level'], 50)
        self.assertEqual(parsed['unread'], [])


class SetupTests(unittest.TestCase):
    def test_rod(self):
        parsed = PARSERS['rod'](sample.ROD)
        self.assertEqual(parsed['values'], {'current': 'Fiberglass Rod',
                                            'owned': ['Supporter Rod', 'Plastic Rod', 'Steel Rod', 'Fiberglass Rod']})
        self.assertEqual(PARSERS['rod']('/rod <rod name>')['unread'], ['Rod in use'])

    def test_bait_none(self):
        parsed = PARSERS['bait'](sample.BAIT)
        self.assertEqual(parsed['values'], {'current': None, 'owned': {}, 'other_lines': []})
        self.assertEqual(parsed['unread'], [])

    def test_bait_owned_counts(self):
        parsed = PARSERS['bait'](sample.BAIT_OWNED)
        self.assertEqual(parsed['values']['current'], 'Support Bait')
        self.assertEqual(parsed['values']['owned'], {
            'Worms': 68, 'Leeches': 1006, 'Magnet': 212, 'Wise Bait': 1479, 'Fish': 909, 'Artifact Magnet': 1727,
            'Magic Bait': 1485, 'Support Bait': 982})
        self.assertEqual(parsed['values']['other_lines'], [])

    def test_bait_owned_counts_with_ocr_noise(self):
        # Icons read as stray letters or symbols, the dash as an en dash or tilde, the comma as a period.
        text = '\n'.join(('Current bait: Magic Bait', 'J Worms - 68', '% Leeches – 1.006', '@ Artifact Magnet ~ 1,727',
                          'Magnet - 212', 'Magic Bait—1 485', 'Fishy line'))
        parsed = PARSERS['bait'](text)
        self.assertEqual(parsed['values']['owned'], {'Worms': 68, 'Leeches': 1006, 'Artifact Magnet': 1727,
                                                     'Magnet': 212, 'Magic Bait': 1485})
        self.assertEqual(parsed['values']['other_lines'], ['Fishy line'])
        self.assertEqual(parsed['unread'], [])

    def test_bait_in_use_with_its_icon_misread(self):
        # Live: the Fish icon read as "r'", and the model stopped on an unknown bait "r' Fish".
        parsed = PARSERS['bait']("Current bait: r' Fish\nr' Fish - 120")
        self.assertEqual(parsed['values']['current'], 'Fish')
        self.assertEqual(parsed['values']['owned'], {'Fish': 120})
        from src.player_state import parse_responses
        profile = parse_responses(profile="Bait: r' Fish (120)")['profile']
        self.assertEqual((profile['bait'], profile['bait_quantity']), ('Fish', 120))
        self.assertEqual(parse_responses(profile='Bait: Goldfish')['profile']['bait'], 'Goldfish')   # not a bait

    def test_current_pet_with_a_stray_mark(self):
        # Live: /pet saved the current pet as "Puffer _".
        from src.player_state import parse_responses
        self.assertEqual(parse_responses(pet='Current pet: Puffer _')['pets']['current_pet'], 'Puffer')
        self.assertEqual(parse_responses(pet='Current pet: V Puffer')['pets']['current_pet'], 'Puffer')

    def test_bait_owned_unknown_without_the_current_line(self):
        parsed = PARSERS['bait']('Worms - 68')
        self.assertIsNone(parsed['values']['owned'])                  # not a whole reply: keep the earlier read
        self.assertEqual(parsed['unread'], ['Bait in use'])

    def test_rod_and_bait_with_the_slash_misread(self):
        # Live OCR: the leading slash read as I or [, and a rod icon as J.
        from src.paste_detect import _kind
        rod = ['Irod <rod name>', 'Currently using: Fiberglass Rod', 'J Supporter Rod', 'Plastic Rod', 'Steel Rod',
               'Fiberglass Rod']
        bait = ['Ibait <bait type> to use bait.', '[bait none is valid.', 'Current bait: NONE']
        self.assertEqual((_kind(rod[0]), _kind(bait[0])), ('rod', 'bait'))
        self.assertEqual(PARSERS['rod']('\n'.join(rod))['values']['owned'],
                         ['Supporter Rod', 'Plastic Rod', 'Steel Rod', 'Fiberglass Rod'])
        self.assertEqual(PARSERS['bait']('\n'.join(bait))['values'], {'current': None, 'owned': {}, 'other_lines': []})

    def test_biome(self):
        parsed = PARSERS['biome'](sample.BIOME)
        self.assertEqual(parsed['values'], {'current': 'River', 'unlocked': ['River'], 'next': 'Volcanic',
                                            'next_level': 50})

    def test_pets_and_prestige_shop_reuse_the_logbook_parsers(self):
        pets = PARSERS['pet'](sample.PETS)['values']
        self.assertEqual(pets['current'], 'Dolphin')
        self.assertEqual(pets['owned']['Axolotl']['level'], 60)
        self.assertEqual(pets['xp_per_trip'], 14)
        shop = PARSERS['prestige_shop'](sample.PRESTIGE_SHOP)
        self.assertEqual(shop['values']['azure_fish'], 0)
        self.assertTrue(shop['values']['upgrades']['International Ties'])
        self.assertEqual(shop['unread'], [])

    def test_boosts(self):
        parsed = PARSERS['boosts'](sample.BOOSTS)
        self.assertEqual(parsed['values'], {'global_duration': '3h 3m 30s', 'booster': 'Colby', 'active': {}})


class KindTests(unittest.TestCase):
    def test_each_reply_is_recognised_by_its_first_line(self):
        from src.paste_detect import _kind
        from src.player_state import _clean
        expected = {'shop_upgrades': sample.UPGRADES_PAGE_1, 'rod': sample.ROD,
                    'bait': sample.BAIT, 'biome': sample.BIOME, 'shop_boats': sample.BOATS, 'pet': sample.PETS,
                    'prestige_shop': sample.PRESTIGE_SHOP, 'shop_league': sample.LEAGUE_PAGE_1,
                    'boosts': sample.BOOSTS}
        for kind, text in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(_kind(_clean(text.splitlines()[0])), kind)

    def test_a_captured_account_reply_is_classified(self):
        from src.live_capture import capture_frame
        frame = capture_frame(['epicmouse used', '% shop upgrades', 'Virtual Fisher 11:16 PM',
                               *sample.UPGRADES_PAGE_1.splitlines()])
        self.assertEqual([message['kind'] for message in frame['messages']], ['shop_upgrades'])


if __name__ == '__main__':
    unittest.main()
