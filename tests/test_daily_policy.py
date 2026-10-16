from datetime import datetime, timedelta, timezone
import unittest

from src.daily_policy import DailyState, apply, due, held_for_level

NOW = datetime(2026, 10, 16, 2, 45, 49, tzinfo=timezone.utc)
MIDNIGHT = datetime(2026, 10, 17, tzinfo=timezone.utc)


class Due(unittest.TestCase):
    def test_fresh_state_is_due(self):
        self.assertTrue(due(DailyState(), NOW))

    def test_after_a_claim_due_at_next_midnight(self):
        state = apply(DailyState(), 'done', 'streak 9', NOW)
        self.assertEqual((state.streak, state.next_due), (9, MIDNIGHT))
        self.assertFalse(due(state, MIDNIGHT - timedelta(seconds=1)))
        self.assertTrue(due(state, MIDNIGHT))

    def test_cooldown_reply_sets_the_next_try(self):
        state = apply(DailyState(), 'done', 'on cooldown, 21h 14m 11s left', NOW)
        self.assertEqual(state.next_due, NOW + timedelta(hours=21, minutes=14, seconds=11))

    def test_a_claim_counts_from_when_it_was_sent(self):
        # Final review: sent 23:59:58, its reply read after 00:00 UTC: the claim is the day it was sent, so the
        # new day's daily is due at once and none is skipped.
        sent = datetime(2026, 10, 16, 23, 59, 58, tzinfo=timezone.utc)
        read = MIDNIGHT + timedelta(seconds=3)
        state = apply(DailyState(), 'done', 'streak 9', read, sent_at=sent)
        self.assertEqual((state.last_claim, state.next_due), (sent, MIDNIGHT))
        self.assertTrue(due(state, read))
        # A cooldown or unclear reply still counts from when it was read.
        state = apply(DailyState(), 'done', 'on cooldown, 1h 0m 0s left', read, sent_at=sent)
        self.assertEqual(state.next_due, read + timedelta(hours=1))

    def test_unknown_reply_waits_for_tomorrow(self):
        state = apply(DailyState(), 'unknown', 'reply not recognised', NOW)
        self.assertFalse(due(state, NOW + timedelta(hours=1)))
        self.assertTrue(due(state, MIDNIGHT))

    def test_claimed_today_without_next_due_is_not_due(self):
        self.assertFalse(due(DailyState(last_claim=NOW - timedelta(hours=1)), NOW))

    def test_round_trip_and_bad_data(self):
        state = apply(DailyState(), 'done', 'streak 9', NOW)
        self.assertEqual(DailyState.from_dict(state.to_dict()), state)
        self.assertEqual(DailyState.from_dict({'next_due': 'garbage'}), DailyState())


class LevelHold(unittest.TestCase):
    def test_hold_near_20_50_100_only(self):
        self.assertTrue(held_for_level(99, 120))
        self.assertFalse(held_for_level(99, 600))
        self.assertFalse(held_for_level(130, 60))
        self.assertFalse(held_for_level(49, None))
