from datetime import datetime, timedelta, timezone
import unittest

from src.quest_tracker import QuestTracker, READ_EVERY

T0 = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)
OPEN = {'daily': {'Daily Fishing': {'progress': 2500, 'goal': 5000, 'done': False, 'task': 'Catch 5000 fish'}},
        'special': {'Booster': {'progress': 0, 'goal': 360, 'done': False, 'task': 'Activate 360 minutes of boosts'}},
        'resets_in_minutes': 120}
DONE = {'daily': {'Daily Fishing': {'progress': None, 'goal': None, 'done': True, 'task': None}},
        'special': {'Charmer': {'progress': None, 'goal': None, 'done': True, 'task': None}},
        'resets_in_minutes': 60}


class Tracker(unittest.TestCase):
    def test_read_schedule(self):
        tracker = QuestTracker()
        self.assertTrue(tracker.read_due(T0, None))
        tracker.apply_read(OPEN, T0)
        self.assertFalse(tracker.read_due(T0 + timedelta(seconds=READ_EVERY - 1), T0))
        self.assertTrue(tracker.read_due(T0 + timedelta(seconds=READ_EVERY), T0))

    def test_all_done_waits_for_reset(self):
        tracker = QuestTracker()
        tracker.apply_read(DONE, T0)
        self.assertFalse(tracker.read_due(T0 + timedelta(minutes=59), T0))
        self.assertTrue(tracker.read_due(T0 + timedelta(minutes=60), T0))

    def test_booster_quest_opens_until_360_minutes(self):
        tracker = QuestTracker()
        tracker.apply_read(OPEN, T0)
        self.assertTrue(tracker.booster_open(T0))
        tracker.add_boost_minutes(340, T0)
        self.assertTrue(tracker.booster_open(T0))
        tracker.add_boost_minutes(20, T0)
        self.assertFalse(tracker.booster_open(T0))

    def test_reset_clears_quests_and_minutes(self):
        tracker = QuestTracker()
        tracker.apply_read(OPEN, T0)
        tracker.add_boost_minutes(100, T0)
        after = T0 + timedelta(minutes=121)
        self.assertFalse(tracker.booster_open(after))
        self.assertEqual(tracker.view(after)['boost_minutes'], 0)

    def test_booster_progress_from_quests_counts_after_a_restart(self):
        tracker = QuestTracker()        # a new run: no boost minutes counted yet
        tracker.apply_read({**OPEN, 'special': {'Booster': {'progress': 300, 'goal': 360, 'done': False,
                                                            'task': 'Activate 360 minutes of boosts'}}}, T0)
        self.assertTrue(tracker.booster_open(T0))
        tracker.add_boost_minutes(60, T0)
        self.assertFalse(tracker.booster_open(T0), '300 read + 60 bought')
        tracker.apply_read({**OPEN, 'special': {'Booster': {'progress': 360, 'goal': 360, 'done': False}}}, T0)
        self.assertFalse(tracker.booster_open(T0), 'the next read includes the 60: not counted twice')
        tracker = QuestTracker()
        tracker.apply_read({**OPEN, 'special': {'Booster': {'progress': 20, 'goal': 30, 'done': False}}}, T0)
        tracker.add_boost_minutes(10, T0)
        self.assertFalse(tracker.booster_open(T0), "the quest's own goal")

    def test_minutes_bought_after_the_reset_count_for_the_new_day(self):
        tracker = QuestTracker()
        tracker.apply_read(OPEN, T0)        # resets at T0 + 2 h
        tracker.add_boost_minutes(100, T0)
        after = T0 + timedelta(minutes=121)
        self.assertTrue(tracker.read_due(after, T0))
        tracker.add_boost_minutes(20, after)
        self.assertEqual(tracker.view(after)['boost_minutes'], 20)
        self.assertTrue(tracker.read_due(after + timedelta(minutes=1), T0), 'read due until the new day is read')
        self.assertEqual(tracker.view(after + timedelta(minutes=1))['boost_minutes'], 20, 'not cleared again')
        tracker.apply_read({**OPEN, 'resets_in_minutes': 24 * 60 - 3}, after + timedelta(minutes=2))
        self.assertFalse(tracker.read_due(after + timedelta(minutes=3), after + timedelta(minutes=2)))
        self.assertEqual(tracker.view(after + timedelta(minutes=3))['boost_minutes'], 20, 'kept by the read')

    def test_no_booster_quest(self):
        tracker = QuestTracker()
        tracker.apply_read(DONE, T0)
        self.assertFalse(tracker.booster_open(T0))

    def test_view(self):
        tracker = QuestTracker()
        tracker.apply_read(OPEN, T0)
        view = tracker.view(T0 + timedelta(minutes=30))
        self.assertEqual(view['resets_in'], 90 * 60)
        self.assertEqual([q['name'] for q in view['special']], ['Booster'])
        self.assertFalse(view['daily'][0]['done'])
