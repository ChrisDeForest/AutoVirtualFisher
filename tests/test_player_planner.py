from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from src.fishing_log import fingerprint, parse_fishing
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

    def snapshot(self, level=10, current=960, required=None, quests='', mode='automatic'):
        required = 100 * level if required is None else required
        profile = (f'Inventory of epicmouse\nBalance: $1,000\n'
                   f'Level {level}, {current:,}/{required:,} XP to next level')
        state = parse_responses(profile=profile, quests=quests)
        result = save_snapshot(state, self.snapshots, mode=mode)
        self.assertEqual(result['status'], 'saved', result)
        return result['snapshot']

    def fish(self, text):
        save_session(parse_fishing(text), 'epicmouse', fingerprint(text), self.sessions)

    def make_plan(self, **options):
        return plan('epicmouse', self.snapshots, self.sessions, increments=INCREMENTS, **options)

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


if __name__ == '__main__':
    unittest.main()
