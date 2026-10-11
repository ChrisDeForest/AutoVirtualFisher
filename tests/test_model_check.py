import json
import tempfile
import unittest
from pathlib import Path

from src.game_model import Account, Setup, Value, buffs, fish_shares, trip
from src.game_model_data import load_game_data
from src.model_check import (MINIMUMS, ModelTracker, buff_corrections, compare_buffs, hidden_effect, measure,
                             model_view, trip_boosted)

DATA = load_game_data()
# This account (P4, level 49), as read live on 2026-10-10.
ACCOUNT = Account(level=49, prestige=4, charms={'Endurance': 1, 'Quality': 2}, upgrades={'Salesman': 7},
                  prestige_upgrades={'International Ties': 1, 'Business Education': 1, 'Fish Whisperer': 1,
                                     'Ancient One': 1})
SETUP = Setup(rod='Fiberglass Rod', biome='River')


class CompareBuffsTests(unittest.TestCase):
    def test_this_accounts_buffs(self):
        game = {'sell_price': {'value': 2.91, 'unit': 'multiplier'},
                'fish_catch': {'value': 1.41, 'unit': 'multiplier'}}
        rows = {row['name']: row for row in compare_buffs(buffs(ACCOUNT, SETUP, DATA), game, DATA)}
        self.assertTrue(rows['sell_price']['ok'])
        self.assertEqual(rows['sell_price']['correction'], 1.0)
        # Model 1.25 x 1.1 x 1.025 x 0.95 = 1.3389; the game shows 1.41: about x1.05, the official server bonus.
        self.assertFalse(rows['fish_catch']['ok'])
        self.assertAlmostEqual(rows['fish_catch']['correction'], 1.41 / 1.338906, places=5)
        self.assertEqual(rows['fish_catch']['effect'], 'the official server bonus')
        self.assertIsNone(rows['xp_multiplier']['ok'], 'not shown by the game: not compared')
        self.assertEqual(buff_corrections(rows.values())['fish_catch'], rows['fish_catch']['correction'])
        self.assertEqual(buff_corrections(rows.values())['xp_multiplier'], 1.0)

    def test_percent_units_are_converted(self):
        game = {'fish_catch': {'value': 41, 'unit': 'percent'}, 'sell_price': {'value': 191.06, 'unit': 'percent'}}
        rows = {row['name']: row for row in compare_buffs(buffs(ACCOUNT, SETUP, DATA), game, DATA)}
        self.assertAlmostEqual(rows['fish_catch']['game'], 1.41, places=12)
        self.assertEqual(rows['fish_catch']['effect'], 'the official server bonus')
        self.assertTrue(rows['sell_price']['ok'])

    def test_hidden_effects_match_within_half_a_percent(self):
        self.assertEqual(hidden_effect('sell_price', 1.203, DATA), 'a max clan')
        self.assertIsNone(hidden_effect('sell_price', 1.25, DATA))


def tally(catches, xp=100, events=(), chest_xp=0):
    return {'trips': 1, 'catches': dict(catches), 'fishing_xp': xp, 'chest_xp': chest_xp,
            'trip_details': [{'catches': dict(catches), 'xp': xp, 'chest_xp': chest_xp, 'events': list(events)}]}


PREDICTED = trip(ACCOUNT, SETUP, DATA)


class MeasureTests(unittest.TestCase):
    def test_measures_one_trip(self):
        measured = measure(tally({'Salmon': 4, 'Cod': 11}, 198, ['Found rare chest', 'Chest: $1,200',
                                                                 'Found Quality Charm']), DATA, 2.0, 'River')
        self.assertEqual(measured['fish'], 15)
        self.assertEqual(measured['money_fish'], (4 * 3 + 11 * 10) * 2.0)
        self.assertEqual((measured['xp_fish'], measured['chest_chance'], measured['charms']), (198, 1, 1))
        self.assertEqual(measured['tiers'], [0, 4, 11, 0, 0])
        self.assertEqual(measured['chest_rewards'], [('rare', 'money', 1200)])

    def test_chest_xp_is_not_xp_from_fish(self):
        # The trip's "+550 XP" line includes the chest's 500 XP, as the catch check knows.
        measured = measure(tally({'Cod': 5}, 550, ['Found epic crate', 'Chest: 500 XP'], chest_xp=500), DATA, 2.0,
                           'River')
        self.assertEqual(measured['xp_fish'], 50)
        self.assertEqual(measured['chest_rewards'], [('epic', 'xp', 500)])
        older = {'trips': 1, 'catches': {'Cod': 5}, 'fishing_xp': 550, 'chest_xp': 500, 'trip_details': [{}]}
        self.assertEqual(measure(older, DATA, 2.0, 'River')['xp_fish'], 50, 'without trip details: the tally')

    def test_unusual_trips_are_left_out(self):
        self.assertIsNone(measure(tally({'Banana': 3}), DATA, 2.0, 'River'))
        self.assertIsNone(measure(tally({'Hot cod': 3}), DATA, 2.0, 'River'), 'a Volcanic fish in River')
        self.assertIsNone(measure(dict(tally({'Cod': 3}), trips=2), DATA, 2.0, 'River'))
        self.assertIsNone(measure(tally({}), DATA, 2.0, 'River'))
        self.assertEqual(measure(tally({'Salmon': 2}), DATA, 2.0, 'Volcanic')['tiers'], [2, 0, 0, 0, 0])


class TrackerTests(unittest.TestCase):
    def test_factor_applies_after_the_minimum_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'model_check.json'
            tracker = ModelTracker(path)
            fish = {'Salmon': 4, 'Cod': 11}
            for _ in range(49):
                tracker.observe(tally(fish), SETUP, PREDICTED, DATA, 2.0)
            self.assertEqual(tracker.factors()['fish']['status'], 'measuring 49/50 trips')
            self.assertEqual(tracker.factors()['fish']['value'], 1.0)
            tracker.observe(tally(fish), SETUP, PREDICTED, DATA, 2.0)
            factor = tracker.factors()['fish']
            self.assertEqual(factor['status'], 'applied')
            self.assertAlmostEqual(factor['value'], 15 / PREDICTED['fish'].value, places=9)
            tracker.save()
            # A restart resumes the same window: the factor stays applied, and new trips add to it.
            again = ModelTracker(path)
            self.assertEqual(again.factors()['fish']['status'], 'applied')
            self.assertAlmostEqual(again.factors()['fish']['value'], factor['value'], places=9)
            self.assertTrue(again.observe(tally(fish), SETUP, PREDICTED, DATA, 2.0))
            self.assertEqual(len(again.window), 51)
            # A setup change still carries the factor, as before.
            other = Setup(rod='Steel Rod', biome='River')
            again.observe(tally(fish), other, trip(ACCOUNT, other, DATA), DATA, 2.0)
            self.assertEqual(again.factors()['fish']['status'], 'carried from an earlier setup')
            self.assertAlmostEqual(again.factors()['fish']['value'], factor['value'], places=9)

    def test_a_new_buffs_reply_starts_a_new_window(self):
        tracker = ModelTracker()
        shown = {'sell_price': 2.91, 'fish_catch': 1.41}
        for _ in range(50):
            tracker.observe(tally({'Cod': 10}), SETUP, PREDICTED, DATA, 2.0, shown)
        self.assertEqual(tracker.factors()['fish']['status'], 'applied')
        tracker.observe(tally({'Cod': 10}), SETUP, PREDICTED, DATA, 2.0, dict(shown, fish_catch=1.4100000001))
        self.assertEqual(len(tracker.window), 51, 'the same reply, read again')
        # An event changes /buffs while the model's own prediction stays the same.
        tracker.observe(tally({'Cod': 10}), SETUP, PREDICTED, DATA, 2.0, dict(shown, fish_catch=2.12))
        self.assertEqual(len(tracker.window), 1)
        self.assertEqual(tracker.factors()['fish']['status'], 'carried from an earlier setup')

    def test_only_a_used_trip_counts(self):
        tracker = ModelTracker()
        self.assertFalse(tracker.observe(tally({'Banana': 3}), SETUP, PREDICTED, DATA, 2.0))
        self.assertTrue(tracker.observe(tally({'Cod': 3}), SETUP, PREDICTED, DATA, 2.0))

    def test_a_trip_says_whether_it_was_boosted(self):
        self.assertTrue(trip_boosted(dict(tally({'Cod': 3}), trip_details=[{'boosted': True}])))
        self.assertFalse(trip_boosted(dict(tally({'Cod': 3}), trip_details=[{'boosted': False}])))
        self.assertTrue(trip_boosted({'trips': 1, 'boosted_trips': 1}), 'without trip details: the tally')
        self.assertFalse(trip_boosted(tally({'Cod': 3})))

    def test_window_resets_on_a_setup_change_and_keeps_500(self):
        tracker = ModelTracker()
        for _ in range(MINIMUMS['fish'][1]):
            tracker.observe(tally({'Cod': 10}), SETUP, PREDICTED, DATA, 2.0)
        other = Setup(rod='Steel Rod', biome='River')
        tracker.observe(tally({'Cod': 10}), other, trip(ACCOUNT, other, DATA), DATA, 2.0)
        self.assertEqual(tracker.factors()['fish']['status'], 'carried from an earlier setup')
        for _ in range(600):
            tracker.observe(tally({'Cod': 10}), other, trip(ACCOUNT, other, DATA), DATA, 2.0)
        self.assertEqual(len(tracker.window), 500)

    def test_big_factor_is_flagged(self):
        tracker = ModelTracker()
        for _ in range(50):
            tracker.observe(tally({'Cod': 100}), SETUP, PREDICTED, DATA, 2.0)
        self.assertTrue(tracker.factors()['fish']['flag'])

    def test_fish_mix_is_learned_after_200_trips(self):
        tracker = ModelTracker()
        for _ in range(199):
            tracker.observe(tally({'Fish': 2, 'Salmon': 4, 'Cod': 4}), SETUP, PREDICTED, DATA, 2.0)
        self.assertEqual(tracker.learned_mix(), {})
        tracker.observe(tally({'Fish': 2, 'Salmon': 4, 'Cod': 4}), SETUP, PREDICTED, DATA, 2.0)
        learned = tracker.learned_mix()['Fiberglass Rod|River']
        quality = PREDICTED['buffs']['fish_quality'].value
        self.assertEqual(learned['trips'], 200)
        self.assertAlmostEqual(learned['shares'][2], 0.4 / quality, places=9)
        self.assertAlmostEqual(learned['shares'][1], 0.4 / quality, places=9)
        self.assertAlmostEqual(sum(learned['shares']), 1.0, places=9)

    def test_a_learned_mix_reproduces_what_was_measured(self):
        for quality in (0.7, 1.5):
            predicted = dict(PREDICTED, buffs=dict(PREDICTED['buffs'], fish_quality=Value(quality)))
            tracker = ModelTracker()
            for _ in range(200):
                tracker.observe(tally({'Fish': 5, 'Salmon': 3, 'Cod': 2}), SETUP, predicted, DATA, 2.0)
            learned = tracker.learned_mix()['Fiberglass Rod|River']['shares']
            shares = fish_shares(learned, quality)
            for share, expected in zip(shares, (0.5, 0.3, 0.2, 0, 0)):
                self.assertAlmostEqual(share, expected, places=12, msg=quality)
            mix = trip(ACCOUNT, SETUP, DATA, tracker.learned_mix())['shares']
            self.assertAlmostEqual(sum(row['share'] for row in mix), 1.0, places=12)

    def test_chest_scale_after_5_chests(self):
        tracker = ModelTracker()
        for amount in (1000, 1000, 1000, 1000):
            tracker.observe(tally({'Cod': 5}, events=['Found rare chest', f'Chest: ${amount}']), SETUP, PREDICTED,
                            DATA, 2.0)
        self.assertIsNone(tracker.chest_scales()['money'])
        tracker.observe(tally({'Cod': 5}, events=['Found common chest', 'Chest: $500']), SETUP, PREDICTED, DATA, 2.0)
        self.assertAlmostEqual(tracker.chest_scales()['money'], 4500 / (4 * 1.4 + 1.0), places=9)
        self.assertIsNone(tracker.chest_scales()['xp'])

    def test_chest_scales_use_the_last_100_chests_of_each_kind(self):
        # Chest rewards grow with level, upgrades and prestige: only recent chests count.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'model_check.json'
            tracker = ModelTracker(path)
            for amount in [1000] * 100 + [5000] * 100:
                tracker.observe(tally({'Cod': 5}, events=['Found rare chest', f'Chest: ${amount}']), SETUP,
                                PREDICTED, DATA, 2.0)
            for _ in range(5):
                tracker.observe(tally({'Cod': 5}, events=['Found epic crate', 'Chest: 340 XP']), SETUP, PREDICTED,
                                DATA, 2.0)
            self.assertAlmostEqual(tracker.chest_scales()['money'], 5000 / 1.4, places=9)
            self.assertAlmostEqual(tracker.chest_scales()['xp'], 200.0, places=9)
            tracker.save()
            saved = json.loads(path.read_text(encoding='utf-8'))['chest_recent']
            self.assertEqual((len(saved['money']), len(saved['xp'])), (100, 5))
            self.assertAlmostEqual(ModelTracker(path).chest_scales()['money'], 5000 / 1.4, places=9)

    def test_crates_are_left_out_of_chest_scales(self):
        tracker = ModelTracker()
        for _ in range(5):
            tracker.observe(tally({'Cod': 5}, events=['Found super crate', 'Crate: $90,000']), SETUP, PREDICTED,
                            DATA, 2.0)
        self.assertIsNone(tracker.chest_scales()['money'])

    def test_bad_fields_start_fresh_and_are_noted(self):
        fish = {'Salmon': 4, 'Cod': 11}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'model_check.json'
            good = ModelTracker(path)
            for _ in range(200):
                good.observe(tally(fish, events=['Found rare chest', 'Chest: $1,000']), SETUP, PREDICTED, DATA, 2.0)
            good.save()
            saved = json.loads(path.read_text(encoding='utf-8'))
            for broken in ({'window': {'key': 'k', 'trips': 5}},
                           {'window': {'key': 'k', 'trips': [1, {'measured': {}}]}},
                           {'factors': 'x'}, {'factors': {'fish': {'value': 'big'}}},
                           {'mix': []}, {'mix': {'Fiberglass Rod|River': {'trips': 'many'}}},
                           {'chest_recent': {'money': 3}}, {'chest_recent': {'money': [[1, 'x']]}},
                           {'window': None, 'factors': None, 'mix': None, 'chest_recent': None}):
                with self.subTest(broken=broken):
                    path.write_text(json.dumps({**saved, **broken}), encoding='utf-8')
                    tracker = ModelTracker(path)
                    self.assertIn('model_check.json', tracker.problem or '')
                    tracker.factors(), tracker.learned_mix(), tracker.chest_scales()
                    self.assertTrue(tracker.observe(tally(fish), SETUP, PREDICTED, DATA, 2.0))
                    tracker.save()
                    view = model_view(STATE, PROFILE, {}, '', tracker, DATA, trips_per_hour=900)
                    self.assertIsNone(view['error'])
                    self.assertIn(tracker.problem, view['estimates'])
            # The good fields of a file are kept: only the bad one starts fresh.
            path.write_text(json.dumps({**saved, 'chest_recent': {'money': 3}}), encoding='utf-8')
            kept = ModelTracker(path)
            self.assertEqual(kept.factors()['fish']['status'], 'applied')
            self.assertIn('Fiberglass Rod|River', kept.learned_mix())
            self.assertIsNone(kept.chest_scales()['money'])
            self.assertIsNone(ModelTracker(Path(folder) / 'missing.json').problem, 'no file is not a problem')

    def test_a_broken_file_starts_fresh(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'model_check.json'
            path.write_text('{oops', encoding='utf-8')
            self.assertEqual(ModelTracker(path).factors()['fish']['status'], 'measuring 0/50 trips')


STATE = {'sections': {
    'shop_upgrades': {'values': {'items': {'Salesman': {'level': 7}}}, 'unread': []},
    'prestige_shop': {'values': {'upgrades': {'International Ties': True, 'Business Education': True,
                                              'Fish Whisperer': True, 'Ancient One': True,
                                              'Virtual Fisher': False}}, 'unread': []}}}
PROFILE = {'level': 49, 'prestige': 4, 'rod': 'Fiberglass Rod', 'biome': 'River'}


class ModelViewTests(unittest.TestCase):
    def test_view_has_every_section(self):
        tracker = ModelTracker()
        view = model_view(STATE, PROFILE, {'sell_price': {'value': 2.91}}, '', tracker, DATA, trips_per_hour=900)
        self.assertIsNone(view['error'])
        self.assertEqual(view['setup']['rod'], 'Fiberglass Rod')
        self.assertIsNone(view['setup']['read_at'], 'the test state has no read times')
        self.assertEqual([row['name'] for row in view['buffs']][0], 'sell_price')
        rows = {row['name']: row for row in view['trip']}
        self.assertEqual(rows['fish']['status'], 'measuring 0/50 trips')
        self.assertAlmostEqual(rows['fish']['per_hour'], rows['fish']['corrected'] * 900, places=6)  # 900 trips/h
        self.assertEqual(len(view['mix']['rows']), 5)
        self.assertEqual(view['mix']['source'], "the rod's own fish mix")  # the wiki's Fiberglass River row
        lava = model_view(STATE, dict(PROFILE, rod='Lava Rod'), {}, '', tracker, DATA, trips_per_hour=900)
        self.assertIn('estimated from Alloy Rod', lava['mix']['source'])
        self.assertTrue(any('estimated from Alloy Rod' in item for item in lava['estimates']))

    def test_the_latest_trip_says_whether_a_global_boost_is_on(self):
        boosted_state = {'sections': dict(STATE['sections'],
                                          boosts={'values': {'global_duration': '40m', 'active': {}}, 'unread': []})}
        read = model_view(boosted_state, PROFILE, {}, '', ModelTracker(), DATA, trips_per_hour=900)
        self.assertIn('global booster', read['setup']['boosts'], 'no trip yet: the /boosts read')
        ended = model_view(boosted_state, PROFILE, {}, '', ModelTracker(), DATA, trips_per_hour=900,
                           global_booster=False)
        self.assertNotIn('global booster', ended['setup']['boosts'])
        fish = {row['name']: row for row in read['trip']}['fish']['predicted']
        self.assertAlmostEqual({row['name']: row for row in ended['trip']}['fish']['predicted'], fish / 1.5,
                               places=9)
        started = model_view(STATE, PROFILE, {}, '', ModelTracker(), DATA, trips_per_hour=900, global_booster=True)
        self.assertIn('global booster', started['setup']['boosts'])

    def test_a_rod_not_listed_for_the_biome_is_shown_as_an_estimate(self):
        view = model_view(STATE, dict(PROFILE, rod='Plastic Rod', biome='Volcanic'), {}, '', ModelTracker(), DATA,
                          trips_per_hour=900)
        self.assertIsNone(view['error'])
        self.assertIn('Plastic Rod is not listed for Volcanic', view['estimates'])
        self.assertEqual({row['name']: row for row in view['trip']}['fish']['source'], 'estimate')

    def test_an_account_with_no_pet_is_modelled(self):
        view = model_view(STATE, dict(PROFILE, pet='None'), {}, '', ModelTracker(), DATA, trips_per_hour=900)
        self.assertIsNone(view['error'])
        self.assertEqual(view['setup']['pet'], 'none')

    def test_buff_corrections_apply_to_the_prediction_not_the_comparison(self):
        game = {'fish_catch': {'value': 1.41, 'unit': 'multiplier'}}
        plain = model_view(STATE, PROFILE, {}, '', ModelTracker(), DATA, trips_per_hour=900)
        view = model_view(STATE, PROFILE, game, '', ModelTracker(), DATA, trips_per_hour=900)
        row = {row['name']: row for row in view['buffs']}['fish_catch']
        self.assertAlmostEqual(row['model'], 1.25 * 1.1 * 0.95, places=9, msg='compared before correcting')
        self.assertFalse(row['ok'])
        fish = {row['name']: row for row in view['trip']}['fish']
        plain_fish = {row['name']: row for row in plain['trip']}['fish']
        self.assertAlmostEqual(fish['predicted'], plain_fish['predicted'] * 1.41 / row['model'], places=9)

    def test_per_hour_from_the_cooldown_is_an_estimate(self):
        view = model_view(STATE, PROFILE, {}, '', ModelTracker(), DATA)
        fish = {row['name']: row for row in view['trip']}['fish']
        self.assertEqual(fish['per_hour_source'], 'estimate')
        self.assertIn('seconds per trip: cooldown + 0.5 s until this run has a rate', view['estimates'])
        rated = model_view(STATE, PROFILE, {}, '', ModelTracker(), DATA, trips_per_hour=900)
        self.assertEqual({row['name']: row for row in rated['trip']}['fish']['per_hour_source'],
                         {row['name']: row for row in rated['trip']}['fish']['source'])

    def test_errors_never_raise(self):
        self.assertIn('level', model_view(STATE, {}, {}, '', ModelTracker(), DATA)['error'])
        view = model_view(STATE, dict(PROFILE, rod='Banana Rod'), {}, '', ModelTracker(), DATA)
        self.assertIn('unknown rod Banana Rod', view['error'])
