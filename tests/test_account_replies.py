import unittest

from src import account_samples as sample
from src.account_replies import PARSERS


class ShopTests(unittest.TestCase):
    def test_upgrades_page_1_levels_and_next_prices(self):
        parsed = PARSERS['shop_upgrades'](sample.UPGRADES_PAGE_1)
        self.assertEqual(parsed['page'], (1, 2))
        self.assertEqual(parsed['values']['balance'], 266418)
        self.assertEqual(parsed['values']['items']['Better Fish'], {'level': 7, 'max': 21, 'price': 25000})
        self.assertEqual(parsed['values']['items']['Worker Motivation'], {'level': 1, 'max': 12, 'price': 50000})
        self.assertEqual(len(parsed['values']['items']), 5)
        self.assertEqual(parsed['unread'], [])

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

    def test_rod_shop_owned_selected_and_prices(self):
        first = PARSERS['shop_rods'](sample.RODS_PAGE_1)['values']
        self.assertEqual(first['items']['Fiberglass Rod'], {'owned': True, 'selected': True, 'price': None})
        self.assertEqual(first['items']['Improved Rod'], {'owned': False, 'selected': False, 'price': 500})
        second = PARSERS['shop_rods'](sample.RODS_PAGE_2)['values']
        self.assertEqual(second['items']['Magma Rod']['price'], 10000000)
        self.assertEqual(second['more_at_level'], 100)

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
        self.assertEqual(parsed['values'], {'current': None, 'other_lines': []})
        self.assertEqual(parsed['unread'], [])

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
        expected = {'shop_upgrades': sample.UPGRADES_PAGE_1, 'shop_rods': sample.RODS_PAGE_1, 'rod': sample.ROD,
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
