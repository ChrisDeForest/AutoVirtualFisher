import unittest

from tests.plan_fixtures import state
from src.plan_data import load_plan_data
from src.plan_economy import level_up_money
from src.plan_forward import buy_special, fish_forward

PLAN = load_plan_data()
RATE = {'money': 1000.0, 'xp': 500.0, 'charms': 0.1, 'gold': 0.5, 'emerald': 0.1, 'lava': 0.2, 'diamond': 0.0,
        'seconds': 4.0}


class FishForwardTests(unittest.TestCase):
    def test_fishes_until_the_money_is_there(self):
        s = state()
        target = s.money() + 50_000
        result = fish_forward(s, lambda st: RATE, lambda st, m: max(0, (target - st.money()) / m['money']),
                              stop_levels=set())
        self.assertEqual(result['stopped'], 'until')
        self.assertGreaterEqual(s.money(), target - 1e-6)
        self.assertLess(result['trips'], 50)                 # level-up money helps
        self.assertAlmostEqual(result['seconds'], result['trips'] * 4.0)

    def test_level_ups_pay_and_reset_xp(self):
        s = state()
        cash = s.cash
        fish_forward(s, lambda st: RATE, lambda st, m: 0 if st.level >= 53 else 1e9, stop_levels=set())
        self.assertEqual(s.level, 53)
        self.assertGreaterEqual(s.cash, cash + level_up_money(53))

    def test_stops_at_an_unlock_level(self):
        s = state(level=99)
        result = fish_forward(s, lambda st: RATE, lambda st, m: 1e12, stop_levels={100})
        self.assertEqual((result['stopped'], s.level), ('level', 100))

    def test_charms_spread_over_kinds_not_maxed(self):
        s = state()
        s.charm_counts['Quality'] = 105                         # maxed at P4 (level 14)
        before = dict(s.charm_counts)
        target = s.charm_total() + 70
        fish_forward(s, lambda st: dict(RATE, charms=1.0), lambda st, m: max(0, target - st.charm_total()),
                     stop_levels=set())
        self.assertEqual(s.charm_counts['Quality'], 105)
        for kind in before:
            if kind != 'Quality':                               # the 70 charms go evenly to the other seven kinds
                self.assertAlmostEqual(s.charm_counts[kind] - before[kind], 10, places=6)
        self.assertAlmostEqual(s.charm_total(), target, places=6)

    def test_money_per_trip_goes_to_fish_value_only(self):
        # the model's money per trip already holds chest money; no other key adds cash
        s = state(level=60)
        cash = s.cash
        fish_forward(s, lambda st: dict(RATE, xp=0.001, chest_money=1000.0), lambda st, m: 0 if st.fish_value
                     >= 120000 + 10000 - 1e-6 else (120000 + 10000 - st.fish_value) / m['money'], stop_levels=set())
        self.assertEqual(s.cash, cash)

    def test_no_progress_is_stuck(self):
        s = state()
        result = fish_forward(s, lambda st: dict(RATE, money=0.0, xp=0.0), lambda st, m: 1e9, stop_levels=set())
        self.assertEqual(result['stopped'], 'stuck')

    def test_special_shop_used_when_tables_given(self):
        s = state()
        s.exotic['Lava'] = 40
        fish_forward(s, lambda st: RATE, lambda st, m: 0 if st.level >= 53 else 1e9, stop_levels=set(),
                     route=PLAN['route'], prices=PLAN['prices'])
        self.assertGreaterEqual(s.account.upgrades.get('Fish Ovens'), 2)


class SpecialShopTests(unittest.TestCase):
    def test_lava_buys_fish_ovens_in_order(self):
        s = state()
        s.exotic['Lava'] = 40
        lines = buy_special(s, PLAN['route'], PLAN['prices'])
        self.assertEqual(s.account.upgrades.get('Fish Ovens'), 2)  # 10 + 25
        self.assertEqual(s.exotic['Lava'], 5)
        self.assertEqual(len(lines), 2)

    def test_statistician_waits_for_level_250(self):
        s = state()
        s.exotic['Gold'] = 1000
        buy_special(s, PLAN['route'], PLAN['prices'])
        self.assertFalse(s.account.upgrades.get('Statistician'))


if __name__ == '__main__':
    unittest.main()
