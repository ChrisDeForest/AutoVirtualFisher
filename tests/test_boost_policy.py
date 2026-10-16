import unittest

from src.boost_policy import BOOSTS, buys, clean_order, seconds_from_note, wanted

RULES = {'fish_min_rate': 6, 'treasure_min_rate': 18, 'worker_min_rate': 36, 'always_from': ['charm_grind_1']}


class Wanted(unittest.TestCase):
    def test_rates(self):
        self.assertEqual(wanted('xp_grind_1', None, RULES, False), set())
        self.assertEqual(wanted('xp_grind_1', 6, RULES, False), set())
        self.assertEqual(wanted('xp_grind_1', 7, RULES, False), {'Fish20m'})
        self.assertEqual(wanted('xp_grind_1', 18, RULES, False), {'Fish20m', 'Treasure20m'})
        self.assertEqual(wanted('xp_grind_1', 36, RULES, False), set(BOOSTS))

    def test_always_phase_and_booster_quest(self):
        self.assertEqual(wanted('charm_grind_1', 0, RULES, False), set(BOOSTS))
        self.assertEqual(wanted('xp_grind_1', None, RULES, True), set(BOOSTS))


class Buys(unittest.TestCase):
    def test_order_affordability_and_expiry(self):
        everything = set(BOOSTS)
        self.assertEqual(buys(everything, BOOSTS, {}, 100, 20), ['Fish20m', 'Treasure20m', 'Auto30m'])
        self.assertEqual(buys(everything, BOOSTS, {}, 100, 13), ['Fish20m', 'Treasure20m'])
        self.assertEqual(buys(everything, ('Auto30m', 'Fish20m', 'Treasure20m'), {}, 100, 13),
                         ['Auto30m'])   # 8 spent, 5 left: neither 6-cost boost fits
        self.assertEqual(buys(everything, BOOSTS, {'Fish20m': 200}, 100, 20), ['Treasure20m', 'Auto30m'])
        self.assertEqual(buys(everything, BOOSTS, {'Fish20m': 100}, 100, 20)[0], 'Fish20m')  # expired exactly now

    def test_unknown_emerald_and_held(self):
        self.assertEqual(buys(set(BOOSTS), BOOSTS, {}, 0, None), [])
        self.assertEqual(buys(set(BOOSTS), BOOSTS, {}, 0, 20, held={'Fish20m'}), ['Treasure20m', 'Auto30m'])

    def test_never_unwanted(self):
        self.assertEqual(buys({'Fish20m'}, BOOSTS, {}, 0, 100), ['Fish20m'])


class Helpers(unittest.TestCase):
    def test_clean_order(self):
        self.assertEqual(clean_order(['Auto30m', 'Bogus', 'Auto30m']), ('Auto30m', 'Fish20m', 'Treasure20m'))
        self.assertEqual(clean_order(None), BOOSTS)

    def test_seconds_from_note(self):
        self.assertEqual(seconds_from_note('boost on for 20m'), 1200)
        self.assertEqual(seconds_from_note('already boosting, 19m 38s left'), 1178)
        self.assertEqual(seconds_from_note('already boosting, 1h 2m left'), 3720)
        self.assertIsNone(seconds_from_note('bought something'))
