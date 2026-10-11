import unittest

from src.plan_economy import (charm_requirement, charms_for_level, increments, level_for_charms, level_up_money,
                              prestige_requirements, xp_to_level)
from src.player_planner import xp_required


class EconomyTests(unittest.TestCase):
    def test_level_up_money_matches_the_sheet(self):
        self.assertEqual(level_up_money(31), 6200)       # seen in this account's logs
        self.assertEqual(level_up_money(32), 6400)
        self.assertEqual(level_up_money(14), 700)
        self.assertEqual(level_up_money(15), 1500)
        self.assertEqual(level_up_money(50), 80000)
        self.assertEqual(level_up_money(63), 100000)     # floor(1.6 × 63) × 1,000
        self.assertEqual(level_up_money(100), 640000)
        self.assertEqual(level_up_money(250), 51200000)
        self.assertEqual(level_up_money(489), 100000000)  # floor(0.2048 × 489) × 1,000,000

    def test_worked_numbers_for_this_account(self):
        total = sum(level_up_money(level) for level in range(33, 501))
        self.assertAlmostEqual(total / 20.7e9, 1, delta=0.01)
        xp = sum(xp_required(level, increments()) for level in range(32, 500))
        self.assertAlmostEqual(xp / 5.70e9, 1, delta=0.01)

    def test_prestige_requirements(self):
        self.assertEqual(prestige_requirements(0), (250, 5_000_000_000, 440))
        self.assertEqual(prestige_requirements(4), (500, 250_000_000_000, 840))
        self.assertEqual(prestige_requirements(5), (600, 500_000_000_000, 960))
        self.assertEqual(prestige_requirements(6)[1], 1_000_000_000_000)
        self.assertEqual(prestige_requirements(26)[1], 900_000_000_000_000)
        self.assertIsNone(prestige_requirements(27))

    def test_charm_counts(self):
        self.assertEqual(charms_for_level(14), 105)
        self.assertEqual(level_for_charms(104.9), 13)
        self.assertEqual(level_for_charms(105), 14)
        self.assertEqual(charms_for_level(6), 21)
        self.assertEqual(level_for_charms(20.9999999999), 6)   # a float sum a hair under 21 is level 6
        self.assertEqual(level_for_charms(20.99), 5)
        self.assertEqual(charm_requirement(4), 840)

    def test_xp_to_level(self):
        from tests.plan_fixtures import state
        account = state()                                   # level 52, 1,000 XP into it
        table = increments()
        self.assertEqual(xp_to_level(account, 53), xp_required(52, table) - 1000)
        self.assertEqual(xp_to_level(account, 55),
                         xp_required(52, table) - 1000 + xp_required(53, table) + xp_required(54, table))
        self.assertEqual(xp_to_level(account, 52), 0)


if __name__ == '__main__':
    unittest.main()
