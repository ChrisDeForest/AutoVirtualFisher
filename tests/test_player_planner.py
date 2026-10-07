from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from src.fishing_log import fingerprint, parse_fishing
from src.bait_compare import assign_bait
from src.fishing_sessions import save_session
from src.player_planner import load_level_increments, plan, xp_between, xp_required
from src.player_snapshots import save_snapshot
from src.player_state import parse_responses

FIXTURES = Path(__file__).parent / 'fixtures'
# Synthetic table: the requirement at level L is 100 * L.
INCREMENTS = [100] * 200


class LevelModelTests(unittest.TestCase):
    def test_shipped_table_matches_live_profile(self):
        increments = load_level_increments()
        self.assertEqual(xp_required(131, increments), 477_500)

    def test_requirement_is_running_total(self):
        self.assertEqual(xp_required(1, INCREMENTS), 100)
        self.assertEqual(xp_required(5, INCREMENTS), 500)
        self.assertIsNone(xp_required(0, INCREMENTS))
        self.assertIsNone(xp_required(500, INCREMENTS))

    def test_xp_between_levels(self):
        # 40 XP left in level 10, then levels 11 and 12 (1,100 + 1,200) to reach 13.
        self.assertEqual(xp_between(10, 40, 11, INCREMENTS), 40)
        self.assertEqual(xp_between(10, 40, 13, INCREMENTS), 40 + 1_100 + 1_200)
        self.assertIsNone(xp_between(10, 40, 10, INCREMENTS))


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.snapshots = Path(self.temporary.name) / 'snapshots'
        self.sessions = Path(self.temporary.name) / 'sessions'

    def tearDown(self):
        self.temporary.cleanup()

    def snapshot(self, level=10, current=960, required=None, quests='', mode='automatic', extra='', buffs=''):
        required = 100 * level if required is None else required
        profile = (f'Inventory of epicmouse\nBalance: $1,000\n'
                   f'Level {level}, {current:,}/{required:,} XP to next level\n{extra}')
        state = parse_responses(profile=profile, quests=quests, buffs=buffs)
        result = save_snapshot(state, self.snapshots, mode=mode)
        self.assertEqual(result['status'], 'saved', result)
        return result['snapshot']

    def fish(self, text):
        save_session(parse_fishing(text), 'epicmouse', fingerprint(text), self.sessions)

    def make_plan(self, **options):
        return plan('epicmouse', self.snapshots, self.sessions, increments=INCREMENTS, **options)

    def test_section_ages_come_from_observation_times(self):
        state = parse_responses(profile='Inventory of epicmouse\nBalance: $1,000\nLevel 10, 960/1,000 XP to next level',
                                buffs="epicmouse9925's current multipliers (including bait):\nSell price: 2.00x")
        verified = lambda at: {'observed_at': at, 'source_observation_id': at, 'identity_status': 'verified'}
        save_snapshot(state, self.snapshots, sections={'profile': verified('2026-10-06T12:00:00+00:00'),
                                                       'buffs': verified('2026-10-06T12:09:00+00:00')})
        result = self.make_plan(now=datetime(2026, 10, 6, 12, 10, tzinfo=timezone.utc))
        self.assertEqual(result['section_ages']['profile']['age_seconds'], 600)
        self.assertEqual(result['section_ages']['buffs']['age_seconds'], 60)
        self.assertEqual(result['section_ages']['quests']['status'], 'unknown')
        self.assertEqual(result['money']['multiplier_as_of'], '2026-10-06T12:09:00+00:00',
                         'the sell price is dated by when /buffs was seen, not when the snapshot was written')

    def test_pasted_snapshot_ages_are_unknown(self):
        self.snapshot()
        ages = self.make_plan()['section_ages']
        self.assertEqual(ages['profile']['status'], 'unknown')

    def test_needs_a_full_snapshot(self):
        self.assertEqual(self.make_plan()['status'], 'needs_snapshot')

    def test_next_level_without_fishing_data_has_no_trip_estimate(self):
        self.snapshot()
        result = self.make_plan()
        self.assertEqual(result['status'], 'ok')
        self.assertIsNone(result['xp_per_trip'])
        self.assertEqual(result['targets'][0], {'level': 11, 'xp_needed': 40, 'trips': None})

    def test_targets_use_measured_xp_per_trip(self):
        self.snapshot()
        self.fish('You caught:\n3 Cod\n+30 XP\n\nYou caught:\n2 Cod\n+20 XP')
        result = self.make_plan(target=13)
        self.assertEqual(result['xp_per_trip'], 25)
        by_level = {target['level']: target for target in result['targets']}
        self.assertEqual(by_level[11], {'level': 11, 'xp_needed': 40, 'trips': 2})
        self.assertEqual(by_level[13]['xp_needed'], 2_340)
        self.assertEqual(by_level[13]['trips'], 94)
        self.assertIn(15, by_level)
        self.assertIn(20, by_level)

    def test_model_check_counts_matching_snapshots(self):
        self.snapshot(level=10)
        self.snapshot(level=11, current=5)
        result = self.make_plan()
        self.assertEqual(result['model'], {'matches': 2, 'mismatches': [], 'usable': True})

    def test_mismatch_disables_multi_level_targets(self):
        self.snapshot(level=10, required=999)
        result = self.make_plan(target=15)
        self.assertFalse(result['model']['usable'])
        self.assertEqual(result['model']['mismatches'], [{'level': 10, 'observed': 999, 'expected': 1_000}])
        self.assertEqual([target['level'] for target in result['targets']], [11])

    def test_quest_reset_is_timed_from_when_the_quests_were_observed(self):
        quests = 'Daily Fishing - 7/500\nCatch 500 fish\nQuests reset in 20h'
        state = parse_responses(profile='Inventory of epicmouse\nBalance: $1,000\nLevel 10, 960/1,000 XP to next level',
                                quests=quests)
        seen = lambda at: {'observed_at': at, 'source_observation_id': at, 'identity_status': 'verified'}
        quests_seen = seen('2026-10-05T12:00:00+00:00')
        save_snapshot(state, self.snapshots, sections={'profile': seen('2026-10-06T08:00:00+00:00'),
                                                       'quests': quests_seen})
        # A later profile republishes the same quests; that must not push their reset later.
        save_snapshot(state, self.snapshots, sections={'profile': seen('2026-10-06T09:00:00+00:00'),
                                                       'quests': quests_seen})
        result = self.make_plan(now=datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc))
        self.assertTrue(result['quests_reset'], 'reset at 08:00, 20 h after the quests were seen')
        self.assertEqual(result['quests_as_of'], '2026-10-05T12:00:00+00:00')

    def test_republished_pasted_quests_keep_their_first_save_time(self):
        quests = 'Daily Fishing - 7/500\nCatch 500 fish\nQuests reset in 20h'
        first = self.snapshot(quests=quests)
        path = next(self.snapshots.rglob(f'{first["snapshot_id"]}.json'))
        stored = json.loads(path.read_text(encoding='utf-8'))
        stored['captured_at'] = (datetime.now(timezone.utc) - timedelta(hours=21)).isoformat()
        path.write_text(json.dumps(stored), encoding='utf-8')
        self.snapshot(quests=quests, level=11, current=5)  # same quests, pasted again with a new profile
        self.assertTrue(self.make_plan()['quests_reset'])

    def test_quest_estimates(self):
        quests = ('Daily Level-ups - 0/2\nLevel up 2 time(s)\nDaily Fishing - 100/500\nCatch 500 fish.\n'
                  'Daily Artifact Hunter - 1/5\nFind 5 chests.\nDaily Done - 3/3\nDo a thing\n'
                  'SPECIAL DAILY QUEST:\nPet Locator - 0/1\nFind a pet.\nQuests reset in 20h')
        self.snapshot(quests=quests)
        self.fish('You caught:\n8 Cod\n+50 XP\nYou found an artifact box\n\n'
                  'You caught:\n12 Cod\n+50 XP\nYou found a Quality Charm!')
        quests = {quest['name']: quest for quest in self.make_plan()['quests']}
        self.assertEqual(quests['Daily Fishing']['status'], 'estimate')
        self.assertEqual(quests['Daily Fishing']['trips'], 40)  # 400 fish at 10 per trip
        self.assertEqual(quests['Daily Artifact Hunter']['trips'], 8)  # 4 chests at 0.5 per trip
        self.assertEqual(quests['Daily Level-ups']['trips'], 23)  # 40 + 1,100 XP at 50 per trip
        self.assertEqual(quests['Daily Done']['status'], 'complete')
        self.assertEqual(quests['Pet Locator']['status'], 'no_data')
        self.assertIsNone(quests['Pet Locator']['trips'])

    def test_quests_come_from_latest_snapshot_with_quests_and_can_be_stale(self):
        snapshot = self.snapshot(quests='Daily Fishing - 1/500\nCatch 500 fish\nQuests reset in 1h')
        self.snapshot(level=11, current=5)
        captured = datetime.fromisoformat(snapshot['captured_at'])
        fresh = self.make_plan(now=captured + timedelta(minutes=30))
        self.assertEqual(fresh['quests_as_of'], snapshot['captured_at'])
        self.assertFalse(fresh['quests_reset'])
        self.assertTrue(self.make_plan(now=captured + timedelta(minutes=61))['quests_reset'])

    def test_partial_snapshot_quests_are_used(self):
        self.snapshot()
        save_snapshot(parse_responses(quests='Daily Fishing - 7/500\nCatch 500 fish'), self.snapshots,
                      mode='manual', account_name='epicmouse')
        quests = self.make_plan()['quests']
        self.assertEqual(quests[0]['progress'], 7)

    def test_money_per_trip_uses_sell_multiplier(self):
        self.snapshot(buffs='Sell price: 2.00x')
        self.fish('You caught:\n3 Cod\n+30 XP\n\nYou caught:\n2 Cod\n+20 XP')
        money = self.make_plan(money_target=2_000)['money']
        self.assertEqual(money['multiplier'], 2.0)
        self.assertEqual(money['base_per_trip'], 25)
        self.assertEqual(money['per_trip'], 50)
        self.assertEqual(money['target'], {'amount': 2_000, 'have': 1_000, 'needed': 1_000, 'trips': 20,
                                           'includes_unsold_fish': False})

    def baited_session(self, text, bait, **conditions):
        tally = assign_bait(parse_fishing(text), bait)
        if conditions:
            tally['conditions'] = conditions
        save_session(tally, 'epicmouse', fingerprint(text + repr(conditions)), self.sessions)

    def test_gross_estimate_is_unchanged_and_labelled(self):
        self.snapshot(buffs='Sell price: 2.00x\nFishing cooldown: 4s')
        self.fish('You caught:\n3 Cod\n+30 XP\n\nYou caught:\n2 Cod\n+20 XP')
        money = self.make_plan()['money']
        self.assertEqual((money['per_trip'], money['gross_per_trip']), (50, 50))
        self.assertEqual(money['gross_per_hour'], 50 * 3600 / 4, 'from the observed cooldown')

    def test_net_needs_a_known_bait_cost(self):
        self.snapshot(buffs='Sell price: 2.00x', extra='Bait: Worms (10)')
        self.fish('You caught:\n3 Cod\n+30 XP\n\nYou caught:\n2 Cod\n+20 XP')
        money = self.make_plan()['money']
        self.assertEqual((money['bait'], money['bait_cost_per_trip'], money['net_per_trip']), ('Worms', 4, 46))
        self.snapshot(buffs='Sell price: 2.00x', extra='Bait: Glitter Lure (10)')
        money = self.make_plan()['money']
        self.assertIsNone(money['bait_cost_per_trip'])
        self.assertIsNone(money['net_per_trip'], 'unknown cost leaves net unknown')
        self.assertIsNone(money['gross_per_hour'], 'no cooldown observed')

    def test_strategy_uses_only_trips_under_current_conditions(self):
        buffs = ('Sell price: 2.00x\nFish catch: 1.41x\nFish quality: 1.21x\nTreasure chance: 1.50x\n'
                 'Treasure quality: 1.13x\nXP Multiplier: 2.12x')
        self.snapshot(buffs=buffs, extra='Current biome: River\nCurrently using Steel Rod.\nPet: Dolphin (Level 51)\n'
                                         'Bait: Worms (10)')
        now = {'biome': 'River', 'rod': 'Steel Rod', 'pet': 'Dolphin (Level 51)', 'fish_catch': 1.41,
               'fish_quality': 1.21, 'treasure_chance': 1.5, 'treasure_quality': 1.13, 'xp_multiplier': 2.12}
        self.baited_session('You caught:\n4 Cod\n+40 XP\n\nYou caught:\n6 Cod\n+60 XP\nGlobal boost by A!',
                            'Worms', **now)
        self.baited_session('You caught:\n9 Cod\n+90 XP', 'Worms', **{**now, 'biome': 'Ocean'})
        self.baited_session('You caught:\n7 Cod\n+70 XP', 'Leeches', **now)
        self.baited_session('You caught:\n5 Cod\n+50 XP', 'Worms', **{**now, 'pet': 'Dolphin (Level 50)'})
        self.baited_session('You caught:\n6 Cod\n+66 XP', 'Worms', **{**now, 'fish_catch': 1.61})  # a bought boost
        self.baited_session('You caught:\n8 Cod\n+80 XP', 'Worms')
        strategy = self.make_plan()['money']['strategy']
        self.assertEqual(strategy['conditions'], {**now, 'bait': 'Worms'})
        self.assertEqual((strategy['trips'], strategy['excluded_trips'], strategy['boosted_trips_left_out']),
                         (1, 6, 1), 'the globally boosted trip is left out even under the same conditions')
        self.assertEqual(strategy['gross_per_trip'], 80)
        self.assertEqual(strategy['net_per_trip'], 76)
        self.assertEqual(strategy['missing_conditions'], [])

    def test_strategy_reports_conditions_it_cannot_check(self):
        self.snapshot(buffs='Sell price: 2.00x', extra='Current biome: River\nBait: Worms (10)')
        strategy = self.make_plan()['money']['strategy']
        self.assertEqual(strategy['trips'], 0)
        self.assertIn('rod', strategy['missing_conditions'])
        self.assertIn('fish_catch', strategy['missing_conditions'])

    def test_money_target_counts_unsold_fish(self):
        self.snapshot(buffs='Sell price: 2.00x', extra='Fish Value: $100')
        self.fish('You caught:\n3 Cod\n+30 XP\n\nYou caught:\n2 Cod\n+20 XP')
        target = self.make_plan(money_target=2_000)['money']['target']
        self.assertEqual((target['have'], target['needed'], target['trips']), (1_100, 900, 18))
        self.assertTrue(target['includes_unsold_fish'])
        reached = self.make_plan(money_target=500)['money']['target']
        self.assertEqual((reached['needed'], reached['trips']), (0, 0))

    def test_money_without_buffs_or_fishing(self):
        self.snapshot()
        money = self.make_plan(money_target=2_000)['money']
        self.assertIsNone(money['multiplier'])
        self.assertIsNone(money['per_trip'])
        self.assertIsNone(money['target']['trips'])


if __name__ == '__main__':
    unittest.main()
