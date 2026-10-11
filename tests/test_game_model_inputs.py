import unittest

from src.game_model_inputs import build_inputs

STATE = {'sections': {
    'shop_upgrades': {'values': {'items': {'Better Fish': {'level': 7}, 'Salesman': {'level': 7},
                                           'More Chests': {'level': 3}}}, 'unread': ['Experienced']},
    'shop_league': {'values': {'items': {'Bait Helper': {'level': 1}, 'Super Crates': {'level': 2}}}, 'unread': []},
    'prestige_shop': {'values': {'azure_fish': 0, 'upgrades': {'International Ties': True, 'Business Education': True,
                                                               'Fish Whisperer': True, 'Ancient One': True,
                                                               'Virtual Fisher': False}}, 'unread': []},
    'shop_boats': {'values': {'items': {'Rowboat': {'owned': True}, 'Fishing Boat': {'owned': False}}}, 'unread': []},
    'pet': {'values': {'current': 'Dolphin', 'owned': {'Dolphin': {'level': 52}, 'Puffer': {'level': 30}}},
            'unread': []},
    'rod': {'values': {'current': 'Fiberglass Rod'}, 'unread': []},
    'biome': {'values': {'current': 'River'}, 'unread': []},
    'bait': {'values': {'current': None}, 'unread': []},
    'boosts': {'values': {'global_duration': '3h 50m', 'active': {}}, 'unread': []},
}}
PROFILE = {'level': 49, 'prestige': 4, 'rod': 'Fiberglass Rod', 'biome': 'River', 'pet': 'Dolphin', 'pet_level': 52}
CHARMS = ('0/14 - Marketing Charm - Sell fish for more. (1 to next tier).\n'
          '1/14 - Endurance Charm - Catch more fish. (2 to next tier).\n'
          '2/14 - Quality Charm - Increase quality of treasure. (2 to next tier).\n11/840 - Charms found.')


class BuildInputsTests(unittest.TestCase):
    def test_builds_account_and_setup(self):
        account, setup = build_inputs(STATE, PROFILE, CHARMS)
        self.assertEqual((account.level, account.prestige, account.boats), (49, 4, 1))
        self.assertEqual(account.upgrades['Salesman'], 7)
        self.assertEqual(account.upgrades['Super Crates'], 2)
        self.assertEqual(account.prestige_upgrades, {'International Ties': 1, 'Business Education': 1,
                                                     'Fish Whisperer': 1, 'Ancient One': 1, 'Virtual Fisher': 0})
        self.assertEqual(account.charms['Endurance'], 1)
        self.assertEqual(account.pets['Dolphin'], 52)
        self.assertEqual(setup.rod, 'Fiberglass Rod')
        self.assertEqual((setup.biome, setup.bait, setup.pet, setup.global_booster), ('River', None, 'Dolphin', True))

    def test_unread_inputs_are_assumed(self):
        account, _ = build_inputs(STATE, PROFILE, CHARMS)
        self.assertIn('Experienced', account.assumed)
        self.assertNotIn('Fish Ovens', account.assumed, 'the special shop opens at level 50')
        later, _ = build_inputs(STATE, dict(PROFILE, level=60), CHARMS)
        self.assertEqual(later.assumed['Fish Ovens'], 'the special shop is not read yet')
        no_charms, _ = build_inputs(STATE, PROFILE, '')
        self.assertEqual(no_charms.assumed['Marketing'], '/charms is not read yet')

    def test_no_pet_means_no_pet(self):
        # /profile shows "Pet: None" for an account with no pet out.
        account, setup = build_inputs(STATE, dict(PROFILE, pet='None', pet_level=None), CHARMS)
        self.assertIsNone(setup.pet)
        self.assertNotIn('None', account.pets)
        self.assertNotIn('pet', account.assumed)

    def test_estimated_boats_pet_level_and_prestige_upgrades_are_named(self):
        sections = {name: section for name, section in STATE['sections'].items() if name != 'shop_boats'}
        account, _ = build_inputs({'sections': sections}, PROFILE, CHARMS)
        self.assertEqual(account.boats, 0)
        self.assertEqual(account.assumed['boats'], 'the boat shop is not read yet (/shop boats)')
        self.assertNotIn('boats', build_inputs(STATE, PROFILE, CHARMS)[0].assumed)
        account, setup = build_inputs(STATE, dict(PROFILE, pet='Shark', pet_level=None), CHARMS)
        self.assertEqual(setup.pet, 'Shark')
        self.assertEqual(account.assumed['pet'], 'the level of Shark is not read yet (/pet)')
        self.assertNotIn('pet', build_inputs(STATE, PROFILE, CHARMS)[0].assumed, 'Dolphin 52 is read')
        p5, _ = build_inputs(STATE, dict(PROFILE, prestige=5), CHARMS)
        self.assertEqual(p5.prestige_upgrades['International Ties'], 2)
        self.assertEqual(p5.assumed['International Ties'],
                         'assumed at the cap (2): /prestige shop does not show the level')
        self.assertNotIn('International Ties', build_inputs(STATE, PROFILE, CHARMS)[0].assumed, 'P4: cap 1 is exact')

    def test_cannot_build_without_rod_or_level(self):
        self.assertIn('level', build_inputs(STATE, {}, CHARMS))
        empty = {'sections': {}}
        self.assertIn('rod', build_inputs(empty, {'level': 10, 'prestige': 0}, ''))
