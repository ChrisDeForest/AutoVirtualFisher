import time
import unittest
from dataclasses import replace
from unittest import mock

import src.planner as planner
from tests.plan_fixtures import state
from src.game_model_data import load_game_data
from src.plan_data import load_plan_data
from src.plan_rules import phase
from src.plan_setup import ModelContext, choose_setup
from src.planner import make_plan

DATA, PLAN = load_game_data(), load_plan_data()


class PlannerTests(unittest.TestCase):
    def test_plans_this_account_to_prestige(self):
        started = time.perf_counter()
        plan = make_plan(state(), ModelContext(DATA), PLAN)
        elapsed = time.perf_counter() - started
        self.assertIsNone(plan['error'])
        self.assertFalse(plan['cut_short'])
        self.assertTrue(plan['prestige']['reached'])
        self.assertLess(elapsed, 2.0)
        buys = [s['action'] for s in plan['steps'] if s['kind'] == 'buy']
        self.assertNotIn('Salesman 6', buys)              # Salesman 7 already covers Alloy (6)
        self.assertLess(buys.index('Alloy Rod'), buys.index('Golden Rod'))
        golden = next(i for i, s in enumerate(plan['steps']) if s['action'] == 'Golden Rod')
        self.assertTrue(any(s['phase'] == 'charm_grind_1' for s in plan['steps'][golden:]))
        seconds = [s['seconds'] for s in plan['steps']]
        self.assertEqual(seconds, sorted(seconds))

    def test_charm_grinds_are_fished_on_the_charm_setup(self):
        stretches, forward = [], planner.fish_forward

        def logged(st, rates, until, stops, **kw):
            before, current, charms = st.copy(), phase(st, PLAN['route']), st.charm_total()
            result = forward(st, rates, until, stops, **kw)
            stretches.append((current, before, result, st.charm_total() - charms))
            return result

        with mock.patch.object(planner, 'fish_forward', logged):
            plan = make_plan(state(), ModelContext(DATA), PLAN)
        self.assertTrue(plan['prestige']['reached'])
        grind = [entry for entry in stretches if entry[0] == 'charm_grind_1' and entry[2]['trips'] > 0]
        current, before, result, gained = grind[-1]       # after the entry purchases
        charm_setup, charm_m = choose_setup(before, {'charms': 1.0}, current, ModelContext(DATA), PLAN['route'],
                                            PLAN['prices'])
        self.assertEqual((before.setup.rod, before.setup.bait), ('Golden Rod', 'Artifact Magnet'))
        self.assertEqual((before.setup.pet, before.setup.biome), (charm_setup.pet, charm_setup.biome))
        _, money_m = choose_setup(before, {'money': 1.0}, current, ModelContext(DATA), PLAN['route'], PLAN['prices'])
        per_hour = gained / result['seconds'] * 3600
        money_per_hour = money_m['charms'] / money_m['seconds'] * 3600
        self.assertGreater(per_hour, 2 * money_per_hour)
        self.assertAlmostEqual(per_hour, charm_m['charms'] / charm_m['seconds'] * 3600, delta=0.2 * per_hour)
        for current, before, result, _ in stretches:
            if current == 'charm_grind_2' and result['trips'] > 0:
                self.assertEqual((before.setup.rod, before.setup.bait), ('Golden Rod', 'Artifact Magnet'))
        steps = plan['steps']
        self.assertFalse([s for s in steps if s['phase'].startswith('charm_grind') and 'best setup for money' in
                          s['why'] and 'Artifact Magnet' in s['action']])
        self.assertFalse([s for s in steps if s['phase'] == 'charm_grind_1' and s['action'] == 'Salesman 19'])
        self.assertFalse([s for s in steps if s['phase'].startswith('charm_grind') and s['action'].endswith(' Rod')
                          and s['action'] != 'Golden Rod'])

    def test_the_first_charm_grind_ends_at_its_exit(self):
        stretches, forward = [], planner.fish_forward

        def logged(st, rates, until, stops, **kw):
            current = phase(st, PLAN['route'])
            result = forward(st, rates, until, stops, **kw)
            stretches.append((current, st.charm_total()))
            return result

        with mock.patch.object(planner, 'fish_forward', logged):
            make_plan(state(), ModelContext(DATA), PLAN)
        last = max(charms for current, charms in stretches if current == 'charm_grind_1')
        self.assertLess(last, 190 + 5)                   # 190 charms or Haste 6: no long overshoot

    def test_every_bait_change_lists_a_bait_purchase_with_amounts(self):
        plan = make_plan(state(), ModelContext(DATA), PLAN)
        baits = [s for s in plan['steps'] if s['kind'] == 'bait']
        self.assertTrue(baits)
        for step in baits:
            self.assertRegex(step['action'], r'^Buy .+, (200|500) at a time$')
            self.assertGreater(step['cost'], 0)
            self.assertRegex(step['why'], r'about [\d,]+ bait \(\$[\d,]+\) until the next bait change')
        names = [s['action'].split(',')[0][4:] for s in baits]
        self.assertTrue(all(a != b for a, b in zip(names, names[1:])))     # one step per change, not per restock
        switched = [s for s in plan['steps'] if s['kind'] == 'switch' and 'bait ' in s['action']
                    and 'bait none' not in s['action']]
        self.assertGreaterEqual(len(baits), len(switched))

    def test_magic_bait_once_it_pays(self):
        plan = make_plan(state(), ModelContext(DATA), PLAN)
        used = {s['action'].split(',')[0][4:] for s in plan['steps'] if s['kind'] == 'bait'}
        self.assertIn('Magic Bait', used)
        self.assertIn('Artifact Magnet', used)                 # the charm grinds

    def test_route_estimates_in_use_are_listed(self):
        plan = make_plan(state(), ModelContext(DATA), PLAN)
        self.assertIn(PLAN['route']['special_estimates'][0], plan['assumptions'])   # Bait Lover 4 is bought
        late = state()
        late.account = replace(late.account, prestige=18)
        self.assertTrue(any('P18-29' in note for note in make_plan(late, ModelContext(DATA), PLAN,
                                                                   limits=(5, 2.0))['assumptions']))

    def test_unread_league_price_is_a_step(self):
        s = state()
        s.league_prices['Super Crates'] = None
        league = [x for x in make_plan(s, ModelContext(DATA), PLAN, limits=(5, 2.0))['steps'] if x['kind'] == 'league']
        self.assertEqual([x['action'] for x in league], ['Super Crates 3', 'Bait Helper 2'])
        self.assertIn('price not read yet', league[0]['why'])

    def test_missing_says_how_much_is_left(self):
        plan = make_plan(state(), ModelContext(DATA), PLAN, limits=(3, 2.0))
        self.assertFalse(plan['prestige']['reached'])
        missing = plan['prestige']['missing']
        self.assertEqual(missing[0], 'level 500 (the plan reaches 52)')
        self.assertTrue(missing[1].endswith('more money'))
        self.assertEqual(missing[2], '829 more charms')

    def test_purchase_steps_say_how_much_to_sell(self):
        plan = make_plan(state(balance=0, fish_value=10_000), ModelContext(DATA), PLAN)
        first = next(s for s in plan['steps'] if s['kind'] == 'buy')
        self.assertGreater(first['sell'], 0)

    def test_league_step_waits_for_hooks(self):
        plan = make_plan(state(), ModelContext(DATA), PLAN)
        league = [s for s in plan['steps'] if s['kind'] == 'league']
        self.assertEqual([s['action'] for s in league[:1]], ['Super Crates 3'])
        self.assertEqual(league[0]['cost'], 150)
        self.assertIn('waiting', league[0]['why'])

    def test_step_limit_cuts_the_plan_short(self):
        plan = make_plan(state(), ModelContext(DATA), PLAN, limits=(3, 2.0))
        self.assertTrue(plan['cut_short'])

    def test_prestige_lists_the_route_a_purchase(self):
        plan = make_plan(state(), ModelContext(DATA), PLAN)
        self.assertEqual(plan['prestige']['shop'], ['International Ties'])  # P5 on Route A


if __name__ == '__main__':
    unittest.main()
