import unittest

from tests.plan_fixtures import CHARMS, account_state, state, synced
from src.plan_state import from_run


class PlanStateTests(unittest.TestCase):
    def test_built_from_the_runs_reads(self):
        s = state()
        self.assertEqual(s.level, 52)
        self.assertEqual(s.account.prestige, 4)
        self.assertEqual(s.rods, {'Supporter Rod', 'Plastic Rod', 'Steel Rod', 'Fiberglass Rod'})
        self.assertEqual(s.cash, 442680)
        self.assertEqual(s.fish_value, 120000)
        self.assertEqual(s.money(), 562680)
        self.assertEqual(s.exotic, {'Gold': 40, 'Emerald': 9, 'Lava': 3, 'Diamond': 0})
        self.assertEqual(s.charm_counts['Quality'], 4)   # level 2 (1 + 2) and 1 of the 3 for level 3
        self.assertEqual(s.charm_total(), 11)  # the reply's "11/840 - Charms found", partial progress included
        self.assertEqual(s.hooks, 11)
        self.assertEqual(s.league_prices['Super Crates'], 150)
        self.assertEqual(s.xp, 1000)

    def test_copy_is_independent(self):
        s = state()
        t = s.copy()
        t.charm_counts['Haste'] = 50
        t.account.upgrades['Salesman'] = 9
        t.rods.add('Alloy Rod')
        self.assertEqual(s.charm_counts['Haste'], 1)
        self.assertEqual(s.account.upgrades['Salesman'], 7)
        self.assertNotIn('Alloy Rod', s.rods)

    def test_sync_charms_sets_levels_from_counts(self):
        s = state()
        s.charm_counts['Haste'] = 21.5
        s.sync_charms()
        self.assertEqual(s.account.charms['Haste'], 6)

    def test_charms_found_since_the_read_are_added(self):
        s = from_run(account_state(), synced(), CHARMS, since={'Haste Charm': 2, 'Quality Charm': 1})
        self.assertEqual(s.charm_total(), 14)
        self.assertEqual(s.charm_counts['Haste'], 3)     # 1 read (level 1, 2 to next tier) and 2 found since
        self.assertEqual(s.account.charms['Haste'], 2)   # 3 charms make level 2
        self.assertEqual(s.account.charms['Quality'], 2)  # 4 + 1 = 5, one short of level 3

    def test_maxed_league_upgrades_are_left_out_and_unread_prices_kept(self):
        reads = account_state()
        reads['sections']['shop_league']['values']['items']['Pet Helper']['level'] = 5
        s = from_run(reads, synced(), CHARMS)
        self.assertNotIn('Pet Helper', s.league_prices)
        self.assertIn('Duplicator 2.0', s.league_prices)
        self.assertIsNone(s.league_prices['Duplicator 2.0'])

    def test_bait_on_hand_from_the_bait_read(self):
        self.assertEqual(state().bait_stock, {})                      # read, none owned
        reads = account_state()
        reads['sections']['bait']['values'] = {'current': 'Fish', 'owned': {'Fish': 909, 'Magic Bait': 1485}}
        built = from_run(reads, synced(), CHARMS)
        self.assertEqual(built.bait_stock, {'Fish': 909, 'Magic Bait': 1485})
        t = built.copy()
        t.bait_stock['Fish'] = 0
        self.assertEqual(built.bait_stock['Fish'], 909)

    def test_missing_balance_is_not_planned(self):
        reads = synced()
        reads['profile']['balance'] = None
        self.assertIn('balance', from_run(account_state(), reads, CHARMS))


if __name__ == '__main__':
    unittest.main()
