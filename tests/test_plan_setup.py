import copy
import unittest
from dataclasses import replace

from tests.plan_fixtures import state
from src.game_model import trip
from src.game_model_data import load_game_data
from src.plan_data import load_plan_data
from src.plan_setup import ModelContext, bait_options, choose_setup, metrics, score

DATA, PLAN = load_game_data(), load_plan_data()


def ctx(**changes):
    return ModelContext(DATA, **changes)


class MetricsTests(unittest.TestCase):
    def test_metrics_for_the_current_setup(self):
        s = state()
        m = metrics(s, s.setup, ctx(), PLAN['route'], PLAN['prices'], all_boosts=False)
        self.assertGreater(m['money'], 0)
        self.assertGreater(m['xp'], 0)
        self.assertAlmostEqual(m['seconds'], m['cooldown'] + 0.5)
        # The part 2 model gives this account ~27 emerald fish an hour (mostly super crates), above the 18 an hour
        # that one fish boost needs, so the fish boost runs all the time and the treasure boost on what is left.
        emerald = trip(s.account, s.setup, DATA, None, None, None)['emerald'].value   # per trip, no boost
        per_hour = emerald * 3600 / m['seconds']
        self.assertGreater(per_hour, 18)
        self.assertEqual(m['fish_share'], 1.0)
        self.assertAlmostEqual(m['treasure_share'], (per_hour - 18) / 18, places=9)

    def test_factors_scale_the_prediction(self):
        s = state()
        base = metrics(s, s.setup, ctx(), PLAN['route'], PLAN['prices'], False)
        scaled = metrics(s, s.setup, ctx(factors={'money_fish': 2.0, 'xp_fish': 0.5}), PLAN['route'],
                         PLAN['prices'], False)
        self.assertAlmostEqual(scaled['money'], base['money'] * 2.0)
        self.assertAlmostEqual(scaled['xp'], base['xp'] * 0.5)

    def test_bait_cost_is_paid_per_trip_less_bait_efficiency(self):
        s = state()
        bare = metrics(s, s.setup, ctx(), PLAN['route'], PLAN['prices'], False)
        baited = metrics(s, replace(s.setup, bait='Leeches'), ctx(), PLAN['route'], PLAN['prices'], False)
        self.assertAlmostEqual(baited['bait_cost'], 25 * (1 - 0.05 * 5))
        self.assertNotEqual(bare['money'], baited['money'])

    def test_all_boosts_turn_both_boosts_on(self):
        s = state()
        m = metrics(s, s.setup, ctx(), PLAN['route'], PLAN['prices'], all_boosts=True)
        self.assertEqual((m['fish_share'], m['treasure_share']), (1, 1))

    def test_unknown_setup_is_none(self):
        s = state()
        self.assertIsNone(metrics(s, replace(s.setup, rod='Nope Rod'), ctx(), PLAN['route'], PLAN['prices'], False))


class ScoreTests(unittest.TestCase):
    def test_slowest_need_decides(self):
        m = {'money': 100, 'xp': 10, 'charms': 0, 'seconds': 4}
        self.assertEqual(score(m, {'money': 1000}), -40)
        self.assertEqual(score(m, {'money': 1000, 'xp': 1000}), -400)
        self.assertEqual(score(m, {'charms': 5}), float('-inf'))


class ChooseSetupTests(unittest.TestCase):
    def test_money_goal_picks_an_owned_rod_and_an_unlocked_bait(self):
        s = state()
        setup, m = choose_setup(s, {'money': 1e6}, 'xp_grind_1', ctx(), PLAN['route'], PLAN['prices'])
        self.assertIn(setup.rod, s.rods)
        self.assertIn(setup.bait, bait_options(s, {'money': 1e6}, PLAN['route'], PLAN['prices'], setup.biome))
        self.assertIn(setup.biome, ('River', 'Volcanic'))
        self.assertIn(setup.pet, s.account.pets)

    def test_bait_is_the_models_best_net_of_its_cost(self):
        for level, needs, phase in ((52, {'money': 1e6}, 'xp_grind_1'), (90, {'money': 1e7}, 'xp_grind_1'),
                                    (120, {'charms': 100}, 'charm_grind_1')):
            s = state(level=level)
            s.rods.add('Golden Rod')
            s.cash = 1e9
            setup, m = choose_setup(s, needs, phase, ctx(), PLAN['route'], PLAN['prices'])
            options = bait_options(s, needs, PLAN['route'], PLAN['prices'], setup.biome)
            scores = {bait: score(metrics(s, replace(setup, bait=bait), ctx(), PLAN['route'], PLAN['prices'],
                                          m['fish_share'] == 1.0 and m['treasure_share'] == 1.0), needs)
                      for bait in options}
            self.assertEqual(setup.bait, max(scores, key=scores.get), (level, needs, scores))
            self.assertEqual(m['setup'].bait, setup.bait)

    def test_bait_options_follow_unlocks_and_the_guide(self):
        s = state(level=90)
        s.cash = 1e9
        options = bait_options(s, {'money': 1}, PLAN['route'], PLAN['prices'], 'Ocean')
        self.assertIn(None, options)
        self.assertIn('Magic Bait', options)
        self.assertIn('Artifact Magnet', options)
        self.assertNotIn('Magnet', options)                 # the guide: never Magnet
        self.assertNotIn('Support Bait', options)           # locked at 90, and only for workers from P15
        self.assertNotIn('Magic Bait', bait_options(state(level=79), {'money': 1}, PLAN['route'], PLAN['prices'],
                                                    'Volcanic'))
        self.assertNotIn('Wise Bait', options)              # money still matters before the Infinity Rod

    def test_bait_on_hand_is_allowed_without_money(self):
        s = state(level=90)
        s.cash, s.fish_value = 0, 0
        s.bait_stock = {'Magic Bait': 300, 'Wise Bait': 50, 'Magnet': 99}
        options = bait_options(s, {'money': 1}, PLAN['route'], PLAN['prices'], 'Ocean')
        self.assertIn('Magic Bait', options)
        self.assertNotIn('Wise Bait', options)               # the guide's rules still apply to bait on hand
        self.assertNotIn('Magnet', options)

    def test_bait_must_be_affordable_by_the_batch(self):
        s = state(level=90)
        s.cash, s.fish_value = 0, 0
        self.assertEqual(bait_options(s, {'money': 1}, PLAN['route'], PLAN['prices'], 'Ocean'), [None])
        s.cash = 500 * 250 * (1 - 0.05 * s.account.upgrades.get('Bait Efficiency', 0))   # 500 Magic Bait
        self.assertIn('Magic Bait', bait_options(s, {'money': 1}, PLAN['route'], PLAN['prices'], 'Ocean'))
        river = bait_options(s, {'money': 1}, PLAN['route'], PLAN['prices'], 'River')
        self.assertIn('Magic Bait', river)                  # River batches are 200
        s.cash -= 1
        self.assertNotIn('Magic Bait', bait_options(s, {'money': 1}, PLAN['route'], PLAN['prices'], 'Ocean'))
        setup, _ = choose_setup(state(level=90, balance=0, fish_value=0), {'money': 1e6}, 'xp_grind_1', ctx(),
                                PLAN['route'], PLAN['prices'])
        self.assertIsNone(setup.bait)

    def test_pet_judged_by_stat_bonus_not_level(self):
        for needs, phase in (({'xp': 1e6}, 'xp_grind_2'), ({'money': 1e6}, 'xp_grind_1'),
                             ({'charms': 50}, 'charm_grind_1')):
            picks = set()
            for levels in ((60, 1), (1, 60)):
                s = state()
                s.account = replace(s.account, pets=dict(zip(('Dolphin', 'Puffer'), levels)))
                setup, m = choose_setup(s, needs, phase, ctx(), PLAN['route'], PLAN['prices'])
                picks.add(setup.pet)
                self.assertEqual(m['setup'].pet, setup.pet)
            self.assertEqual(len(picks), 1, (needs, picks))   # swapping the levels never changes the pet
        s = state()
        s.account = replace(s.account, pets={'Dolphin': 1, 'Puffer': 60})
        setup, _ = choose_setup(s, {'xp': 1e6}, 'xp_grind_2', ctx(), PLAN['route'], PLAN['prices'])
        # Puffer's catch bonus yields more fish and so more XP per trip than Dolphin's XP bonus (model: 215 v 206),
        # so it wins on merit though Dolphin is first in the XP pet order.
        self.assertEqual(setup.pet, 'Puffer')

    def test_pet_order_breaks_ties(self):
        data = copy.deepcopy(DATA)
        data['pets']['Puffer'] = dict(data['pets']['Dolphin'])   # identical bonuses
        for levels in ((1, 60), (60, 1)):
            s = state()
            s.account = replace(s.account, pets=dict(zip(('Dolphin', 'Puffer'), levels)))
            setup, m = choose_setup(s, {'xp': 1e6}, 'xp_grind_2', ModelContext(data), PLAN['route'], PLAN['prices'])
            self.assertEqual(setup.pet, 'Dolphin')           # XP order lists Dolphin first
            self.assertEqual(m['setup'].pet, 'Dolphin')

    def test_charm_grind_uses_the_charm_bait_and_rod_when_owned(self):
        s = state(level=105)
        s.rods.add('Golden Rod')
        setup, _ = choose_setup(s, {'charms': 100}, 'charm_grind_1', ctx(), PLAN['route'], PLAN['prices'])
        self.assertEqual((setup.rod, setup.bait), ('Golden Rod', 'Artifact Magnet'))

    def test_wise_bait_only_after_the_infinity_rod(self):
        s = state(level=120)
        setup, _ = choose_setup(s, {'xp': 1e9}, 'xp_grind_2', ctx(), PLAN['route'], PLAN['prices'])
        self.assertNotEqual(setup.bait, 'Wise Bait')
        s.rods.add('Infinity Rod')
        setup, _ = choose_setup(s, {'xp': 1e9}, 'xp_grind_2', ctx(), PLAN['route'], PLAN['prices'])
        self.assertEqual(setup.bait, 'Wise Bait')
        setup, _ = choose_setup(s, {'money': 1e9}, 'xp_grind_2', ctx(), PLAN['route'], PLAN['prices'])
        self.assertNotEqual(setup.bait, 'Wise Bait')     # the XP bait is for an XP goal only

    def test_charm_bait_only_for_a_charm_goal(self):
        s = state(level=260)
        setup, _ = choose_setup(s, {'money': 1e9}, 'charm_grind_2', ctx(), PLAN['route'], PLAN['prices'])
        self.assertNotEqual(setup.bait, 'Artifact Magnet')   # saving for the Golden Rod in a charm grind
        setup, _ = choose_setup(s, {'charms': 100}, 'charm_grind_2', ctx(), PLAN['route'], PLAN['prices'])
        self.assertEqual(setup.bait, 'Artifact Magnet')


if __name__ == '__main__':
    unittest.main()
