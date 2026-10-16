import time
import unittest
from dataclasses import replace
from unittest import mock

import src.planner as planner
from tests.plan_fixtures import state
from src.game_model_data import load_game_data
from src.plan_data import load_plan_data
from src.plan_rules import phase
from src.plan_setup import ModelContext, bait_shop_price, choose_setup
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
            self.assertRegex(step['why'], r'about [\d,]+ bait until the next bait change(, [\d,]+ from the bait on '
                                          r'hand)?(, [\d,]+ bought \(\$[\d,]+\))?$')
        names = [s['action'].split(',')[0][4:] for s in baits]
        self.assertTrue(all(a != b for a, b in zip(names, names[1:])))     # one step per change, not per restock
        switched = [s for s in plan['steps'] if s['kind'] == 'switch' and 'bait ' in s['action']
                    and 'bait none' not in s['action']]
        self.assertGreaterEqual(len(baits), len(switched))

    def test_bait_purchases_cost_the_full_shop_price(self):
        s = state()
        s.account.upgrades['Bait Efficiency'] = 9
        plan = make_plan(s, ModelContext(DATA), PLAN)
        buys = [(step, step['do'][0]) for step in plan['steps'] if step['kind'] == 'bait' and step['do']]
        self.assertTrue(buys)
        for step, buy in buys:
            full = bait_shop_price(buy['name']) * buy['amount']
            self.assertEqual((buy['price'], step['cost']), (full, full), step)

    def test_bait_on_hand_is_used_first(self):
        bare = make_plan(state(), ModelContext(DATA), PLAN)
        first = next(s for s in bare['steps'] if s['kind'] == 'bait')
        bait = first['action'].split(',')[0][4:]
        stocked = state()
        stocked.bait_stock = {bait: 100}
        plan = make_plan(stocked, ModelContext(DATA), PLAN)
        step = next(s for s in plan['steps'] if s['kind'] == 'bait')
        self.assertIn(f'100 {bait} on hand', step['action'])
        self.assertRegex(step['why'], r'100 from the bait on hand')
        plenty = state()
        plenty.bait_stock = {bait: 10 ** 7}
        plan = make_plan(plenty, ModelContext(DATA), PLAN)
        step = next(s for s in plan['steps'] if s['kind'] == 'bait')
        self.assertEqual(step['action'], f'Use the 10,000,000 {bait} on hand')
        self.assertEqual(step['cost'], 0)
        # bait on hand is already paid for, so the route is no slower
        self.assertLessEqual(plan['prestige']['seconds'], bare['prestige']['seconds'])

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

    def test_an_unread_league_price_is_an_assumption_not_a_step(self):
        # Live: no league price was read (11 Hooks on hand) and every league upgrade topped the route as "now".
        s = state()
        s.league_prices['Super Crates'] = None
        plan = make_plan(s, ModelContext(DATA), PLAN, limits=(5, 2.0))
        self.assertEqual([x['action'] for x in plan['league']], ['Bait Helper 2'])
        self.assertTrue(any('Super Crates' in note and 'price' in note for note in plan['assumptions']))

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

    def test_steps_say_where_to_buy(self):
        plan = make_plan(state(balance=0, fish_value=10_000), ModelContext(DATA), PLAN)
        where = {'league': '/shop league', 'special': '/shop special', 'bait': '/shop bait', 'switch': ''}
        for step in plan['steps']:
            if step['kind'] in where:
                self.assertEqual(step['where'], where[step['kind']], step)
        bought = {step['action']: step['where'] for step in plan['steps'] if step['kind'] == 'buy'}
        self.assertTrue(bought)
        self.assertTrue(set(bought.values()) <= {'/shop upgrades', '/shop rods', '/shop boats'}, bought)

    def test_steps_carry_their_actions(self):
        from src.actions import Action
        plan = make_plan(state(balance=0, fish_value=10_000), ModelContext(DATA), PLAN)
        for step in plan['steps']:
            actions = [Action.from_dict(item) for item in step['do']]
            if step['kind'] == 'buy':
                self.assertEqual([a.act for a in actions], ['buy'], step)
                self.assertTrue(step['action'].startswith(actions[0].name), step)
            if step['kind'] == 'switch':
                self.assertTrue(actions and {a.act for a in actions} <= {'rod', 'biome', 'bait', 'pet'}, step)
            if step['kind'] == 'league' and step['cost']:
                self.assertEqual(actions[0].shop, 'league', step)
        bought = [s for s in plan['steps'] if s['kind'] == 'buy']
        self.assertTrue(bought)
        first = Action.from_dict(bought[0]['do'][0])
        self.assertEqual(first.price, bought[0]['cost'])
        self.assertIn(first.shop, ('upgrades', 'rods', 'boats'))

    def test_league_step_waits_for_hooks(self):
        plan = make_plan(state(), ModelContext(DATA), PLAN)
        league = plan['league']
        self.assertEqual([s['action'] for s in league[:1]], ['Super Crates 3'])
        self.assertEqual(league[0]['cost'], 150)
        self.assertIn('waiting', league[0]['why'])

    def test_league_steps_are_their_own_track(self):
        plan = make_plan(state(), ModelContext(DATA), PLAN)
        self.assertFalse([s for s in plan['steps'] if s['kind'] == 'league'])
        self.assertEqual([s['action'] for s in plan['league']][:1], ['Super Crates 3'])
        self.assertEqual(plan['league'][0]['status'], 'cannot_afford')     # 11 Hooks of 150
        self.assertEqual(plan['hooks'], 11)

    def test_the_phase_the_account_is_in(self):
        plan = make_plan(state(), ModelContext(DATA), PLAN)
        self.assertEqual(plan['phase_now'], plan['steps'][0]['phase'])
        later = [s for s in plan['steps'] if s['do'] and s['phase'] != plan['phase_now']]
        self.assertTrue(later)
        self.assertEqual({s['status'] for s in later}, {'later'})

    def test_a_bait_buy_comes_before_the_switch_to_it(self):
        from src.actions import Action
        plan = make_plan(state(), ModelContext(DATA), PLAN)
        bought = []
        for step in plan['steps']:
            actions = [Action.from_dict(item) for item in step['do']]
            if step['kind'] == 'bait' and actions:
                bought.append(actions[0].name)
            if step['kind'] == 'switch':
                # A bait switched to, if it is bought at all, was bought already.
                for action in actions:
                    if action.act == 'bait' and action.name and any(
                            s['kind'] == 'bait' and s['do'] and s['do'][0]['name'] == action.name
                            for s in plan['steps']):
                        self.assertIn(action.name, bought, step)

    def test_ready_marks(self):
        from src.actions import Action
        from src.plan_state import from_run
        from tests.plan_fixtures import CHARMS, account_state, synced
        now = from_run(account_state(), synced(), CHARMS)
        now.cash, now.fish_value, now.hooks = 100.0, 0.0, 30
        now.exotic = {'Gold': 0, 'Emerald': 0, 'Lava': 0, 'Diamond': 30}
        now.rods = {'Steel Rod'}
        now.bait_stock = {}
        buy = lambda name, cost, currency='$', shop='upgrades', **kw: {
            'phase': 'xp_grind_1', 'kind': 'buy', 'cost': cost, 'currency': currency,
            'do': [Action('buy', name, shop=shop, price=cost, **kw).to_dict()]}
        switch = lambda *actions: {'phase': 'xp_grind_1', 'kind': 'switch', 'cost': 0, 'currency': '$',
                                   'do': [a.to_dict() for a in actions]}
        steps = [buy('Bait Lover', 10, 'Diamond Fish', 'special'), buy('Salesman', 60),
                 buy('Golden Rod', 50, shop='rods'),                      # 40 left of 100: cannot afford
                 switch(Action('rod', 'Golden Rod')),                     # not owned, its buy not ready
                 buy('Boost Booster', 25, 'Diamond Fish', 'special'),     # 20 left: cannot afford
                 buy('Fish Ovens', 5, 'Diamond Fish', 'special'),         # Diamond Fish blocked after it
                 buy('Magic Bait', 30, shop='bait', amount=100),          # money blocked by Golden Rod
                 switch(Action('rod', 'Steel Rod'), Action('pet', 'Dolphin')),
                 switch(Action('biome', 'Sky')),                          # unlocked at 250, level is lower
                 {**buy('Better Fish', 1), 'phase': 'charm_grind_1'},
                 {'phase': 'xp_grind_1', 'kind': 'bait', 'cost': 0, 'currency': '$', 'do': []}]
        league = [{'kind': 'league', 'cost': 25, 'currency': 'Hooks', 'do': [{'act': 'buy'}]},
                  {'kind': 'league', 'cost': 10, 'currency': 'Hooks', 'do': [{'act': 'buy'}]}]
        plan = {'phase_now': 'xp_grind_1', 'steps': steps, 'league': league}
        planner._mark_ready(plan, now, PLAN['prices'])
        self.assertEqual([s.get('status') for s in steps],
                         ['ready', 'ready', 'cannot_afford', 'cannot_afford', 'cannot_afford', 'cannot_afford',
                          'cannot_afford', 'ready', 'cannot_afford', 'later', None])
        self.assertEqual([s['status'] for s in league], ['ready', 'cannot_afford'])   # 5 Hooks left

    def test_a_switch_to_a_rod_a_ready_buy_gets_is_ready(self):
        from src.actions import Action
        from src.plan_state import from_run
        from tests.plan_fixtures import CHARMS, account_state, synced
        now = from_run(account_state(), synced(), CHARMS)
        now.cash, now.fish_value, now.rods, now.bait_stock = 1000.0, 0.0, {'Steel Rod'}, {}
        steps = [{'phase': 'p', 'kind': 'buy', 'cost': 500, 'currency': '$',
                  'do': [Action('buy', 'Golden Rod', shop='rods', price=500).to_dict()]},
                 {'phase': 'p', 'kind': 'bait', 'cost': 100, 'currency': '$',
                  'do': [Action('buy', 'Magic Bait', shop='bait', amount=100, price=100).to_dict()]},
                 {'phase': 'p', 'kind': 'switch', 'cost': 0, 'currency': '$',
                  'do': [Action('rod', 'Golden Rod').to_dict(), Action('bait', 'Magic Bait').to_dict()]}]
        planner._mark_ready({'phase_now': 'p', 'steps': steps, 'league': []}, now, PLAN['prices'])
        self.assertEqual([s['status'] for s in steps], ['ready', 'ready', 'ready'])

    def test_special_steps_carry_their_exotic_fish_price(self):
        plan = make_plan(state(), ModelContext(DATA), PLAN)
        special = next(s for s in plan['steps'] if s['kind'] == 'special' and s['do'])
        self.assertTrue(special['currency'].endswith(' Fish'), special)
        self.assertIn(f'({special["cost"]} {special["currency"]})', special['action'])
        self.assertEqual(special['do'][0]['price'], special['cost'])
        self.assertEqual(special['do'][0]['currency'], special['currency'])   # the Progress tab names it

    def test_an_upgrade_the_game_said_is_maxed_is_never_planned_past_it(self):
        from tests.plan_fixtures import CHARMS, account_state, synced
        from src.plan_state import from_run
        account = {**account_state(), 'maxed': {'Artifact Specialist': 3}}
        plan = make_plan(from_run(account, synced(), CHARMS), ModelContext(DATA), PLAN)
        self.assertFalse([s for s in plan['steps'] if s['action'].startswith('Artifact Specialist')])

    def test_step_limit_cuts_the_plan_short(self):
        plan = make_plan(state(), ModelContext(DATA), PLAN, limits=(3, 2.0))
        self.assertTrue(plan['cut_short'])

    def test_prestige_lists_the_route_a_purchase(self):
        plan = make_plan(state(), ModelContext(DATA), PLAN)
        self.assertEqual(plan['prestige']['shop'], ['International Ties'])  # P5 on Route A


if __name__ == '__main__':
    unittest.main()
