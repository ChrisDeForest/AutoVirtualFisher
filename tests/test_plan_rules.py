import math
import unittest

from tests.plan_fixtures import state
from src.game_model_data import load_game_data
from src.plan_data import load_plan_data
from src.plan_rules import Action, candidates, charm_exit, mrp, phase, select_next
from src.plan_setup import ModelContext

DATA, PLAN = load_game_data(), load_plan_data()
ROUTE, PRICES = PLAN['route'], PLAN['prices']
DAILIES = 'Better Dailies'


class PhaseTests(unittest.TestCase):
    def test_before_the_golden_rod_is_the_first_xp_grind(self):
        self.assertEqual(phase(state(), ROUTE), 'xp_grind_1')

    def test_golden_rod_starts_the_first_charm_grind(self):
        s = state(level=110)
        s.rods.add('Golden Rod')
        self.assertEqual(phase(s, ROUTE), 'charm_grind_1')

    def test_first_exit_at_190_charms_or_haste_6(self):
        s = state(level=110)
        s.rods.add('Golden Rod')
        for kind in s.charm_counts:
            s.charm_counts[kind] = 24
        s.sync_charms()
        self.assertEqual(phase(s, ROUTE), 'xp_grind_2')

    def test_level_250_then_second_grind_then_final(self):
        s = state(level=260)
        s.rods.add('Golden Rod')
        for kind in s.charm_counts:
            s.charm_counts[kind] = 30
        self.assertEqual(phase(s, ROUTE), 'charm_grind_2')
        for kind in s.charm_counts:
            s.charm_counts[kind] = 80
        self.assertEqual(phase(s, ROUTE), 'xp_grind_3')

    def test_requirements_met_is_prestige(self):
        s = state(level=500, balance=300e9)
        for kind in s.charm_counts:
            s.charm_counts[kind] = 105
        self.assertEqual(phase(s, ROUTE), 'prestige')

    def test_p4_exit_is_640_and_labelled(self):
        count, why = charm_exit(state(), ROUTE)
        self.assertEqual(count, 640)
        self.assertIn('640', why)


class CandidateTests(unittest.TestCase):
    def test_salesman_before_alloy_comes_when_alloy_is_the_purchase(self):
        s = state()   # the fixture has Salesman 7, past the 6 Alloy needs; drop it to 5
        s.account.upgrades['Salesman'] = 5
        self.assertFalse([a for a in candidates(s, 'xp_grind_1', ROUTE, PRICES, DATA) if a.forced and a.name != DAILIES])
        rod = Action('rod', 'Alloy Rod', None, 250_000, 'next rod')
        first, why = select_next(s, 'xp_grind_1', [rod], {}, ModelContext(DATA), ROUTE, PRICES)
        self.assertEqual((first.kind, first.name, first.level, first.for_rod), ('upgrade', 'Salesman', 6, 'Alloy Rod'))
        self.assertIn('before Alloy Rod', why)

    def test_better_dailies_is_the_first_forced_step_in_every_phase(self):
        for phase_name in ('xp_grind_1', 'charm_grind_1', 'xp_grind_2'):
            actions = candidates(state(), phase_name, ROUTE, PRICES, DATA)
            first = actions[0]
            self.assertEqual((first.kind, first.name, first.level, first.price, first.forced),
                             ('upgrade', 'Better Dailies', 3, 25_000, True), phase_name)
            self.assertEqual(first.reason, 'Better Dailies: bigger daily rewards')
            self.assertEqual(sum(a.name == 'Better Dailies' for a in actions), 1)
        chosen, why = select_next(state(), 'xp_grind_1', candidates(state(), 'xp_grind_1', ROUTE, PRICES, DATA), {},
                                  ModelContext(DATA), ROUTE, PRICES)
        self.assertEqual((chosen.name, chosen.level, why), ('Better Dailies', 3, 'Better Dailies: bigger daily rewards'))

    def test_better_dailies_is_absent_at_max_level(self):
        s = state()
        s.account.upgrades['Better Dailies'] = 10
        for phase_name in ('xp_grind_1', 'charm_grind_1'):
            self.assertFalse([a for a in candidates(s, phase_name, ROUTE, PRICES, DATA) if a.name == 'Better Dailies'])

    def test_worker_motivation_is_still_not_planned(self):
        self.assertFalse([a for a in candidates(state(), 'xp_grind_1', ROUTE, PRICES, DATA)
                          if a.name == 'Worker Motivation'])

    def test_no_rod_salesman_or_band_rod_in_a_charm_grind(self):
        s = state(level=110)
        s.rods |= {'Golden Rod', 'Alloy Rod'}
        s.account.upgrades['Salesman'] = 18     # Superium (unlocked at 100) wants Salesman 19
        actions = candidates(s, 'charm_grind_1', ROUTE, PRICES, DATA)
        self.assertFalse([a for a in actions if a.name == 'Salesman' and a.forced])
        self.assertFalse([a for a in actions if a.kind == 'rod'])
        self.assertTrue(all(a.money_first for a in actions if a.forced and a.name != DAILIES))   # only the entry steps (and Better Dailies) are forced

    def test_salesman_19_comes_just_before_the_superium_rod(self):
        s = state(level=200)
        s.rods |= {'Golden Rod', 'Alloy Rod'}
        s.account.upgrades['Salesman'] = 18
        superium = next(a for a in candidates(s, 'xp_grind_2', ROUTE, PRICES, DATA) if a.kind == 'rod')
        self.assertEqual(superium.name, 'Superium Rod')
        action, why = select_next(s, 'xp_grind_2', [superium], {}, ModelContext(DATA), ROUTE, PRICES)
        self.assertEqual((action.name, action.level, action.for_rod), ('Salesman', 19, 'Superium Rod'))
        self.assertEqual(why, 'Salesman 19 before Superium Rod')
        s.account.upgrades['Salesman'] = 19
        action, _ = select_next(s, 'xp_grind_2', [superium], {}, ModelContext(DATA), ROUTE, PRICES)
        self.assertEqual(action.name, 'Superium Rod')

    def test_no_salesman_step_once_it_is_past_the_rod_need(self):
        actions = candidates(state(), 'xp_grind_1', ROUTE, PRICES, DATA)
        self.assertFalse([a for a in actions if a.forced and a.name != DAILIES])

    def test_rods_follow_the_supporter_band(self):
        names = [a.name for a in candidates(state(), 'xp_grind_1', ROUTE, PRICES, DATA) if a.kind == 'rod']
        self.assertEqual(names, ['Alloy Rod'])   # Golden needs level 100; Lava/Magma never with Supporter

    def test_five_percent_rule_drops_dear_upgrades(self):
        names = {(a.name, a.level) for a in candidates(state(), 'xp_grind_1', ROUTE, PRICES, DATA)}
        self.assertNotIn(('Experienced', 1), names)   # $50k > 5% of Alloy's $250k

    def test_charm_grind_entry_is_forced(self):
        s = state(level=110)
        s.rods.add('Golden Rod')
        forced = [a for a in candidates(s, 'charm_grind_1', ROUTE, PRICES, DATA) if a.forced]
        self.assertIn(('More Chests', 4), {(a.name, a.level) for a in forced})

    def test_charm_grind_entry_is_forced_in_the_second_grind_when_the_first_was_skipped(self):
        s = state(level=260)                     # the Golden Rod first bought past level 250: no 1st charm grind
        s.rods.add('Golden Rod')
        self.assertEqual(phase(s, ROUTE), 'charm_grind_2')
        forced = {(a.name, a.level) for a in candidates(s, 'charm_grind_2', ROUTE, PRICES, DATA) if a.forced}
        self.assertIn(('More Chests', 4), forced)
        self.assertIn(('Better Chests', 1), forced)

    def test_golden_rod_is_forced_in_the_first_xp_grind_from_level_100(self):
        s = state(level=100)
        s.rods.add('Alloy Rod')
        forced = [a for a in candidates(s, 'xp_grind_1', ROUTE, PRICES, DATA) if a.forced and a.name != DAILIES]
        self.assertEqual([(a.kind, a.name) for a in forced], [('rod', 'Golden Rod')])
        self.assertFalse([a for a in candidates(state(level=99), 'xp_grind_1', ROUTE, PRICES, DATA) if a.forced and a.name != DAILIES])

    def test_mrp(self):
        self.assertAlmostEqual(mrp(1000, 1.25), 5000)
        self.assertEqual(mrp(1000, 1.0), math.inf)


class SelectTests(unittest.TestCase):
    def test_forced_action_wins(self):
        s = state()
        s.account.upgrades['Salesman'] = 5
        actions = [a for a in candidates(s, 'xp_grind_1', ROUTE, PRICES, DATA) if a.name != DAILIES]
        action, why = select_next(s, 'xp_grind_1', actions, {'money': 1e6}, ModelContext(DATA), ROUTE, PRICES)
        self.assertEqual((action.name, action.level), ('Salesman', 6))

    def test_lowest_mrp_wins_when_nothing_is_forced(self):
        s = state()
        s.account.upgrades['Salesman'] = 9
        actions = [a for a in candidates(s, 'xp_grind_1', ROUTE, PRICES, DATA) if not a.forced]
        action, why = select_next(s, 'xp_grind_1', actions, {'money': 1e6}, ModelContext(DATA), ROUTE, PRICES)
        self.assertIsNotNone(action)
        self.assertIn('MRP', why)

    def test_a_rod_without_a_charm_gain_is_never_ranked_at_its_price(self):
        s = state(level=110)
        s.rods.add('Golden Rod')
        rod = Action('rod', 'Alloy Rod', None, 250_000, 'next rod')
        action, why = select_next(s, 'charm_grind_1', [rod], {}, ModelContext(DATA), ROUTE, PRICES)
        self.assertIsNone(action)

    def test_guide_rod_without_a_modelled_gain_is_ranked_at_its_price(self):
        # Alloy fishes the River only; the Supporter Rod in Volcanic earns more, so the model sees no gain from it
        s = state()
        actions = [a for a in candidates(s, 'xp_grind_1', ROUTE, PRICES, DATA) if not a.forced]
        action, why = select_next(s, 'xp_grind_1', actions, {'money': 1e6}, ModelContext(DATA), ROUTE, PRICES)
        self.assertEqual(action.name, 'Alloy Rod')
        self.assertIn('MRP $250,000', why)
        self.assertIn('next rod in the guide', why)


if __name__ == '__main__':
    unittest.main()
