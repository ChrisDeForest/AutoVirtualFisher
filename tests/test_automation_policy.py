from dataclasses import replace
from datetime import datetime, timezone
import unittest

from src.automation_models import ActionIntent, AutomationConfig
from src.automation_policy import next_action
from src.automation_state import AutomationState, apply_observation
from src.observation import Observation

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
CONFIG = AutomationConfig(account='sim', username='sim0001', context_id='sim:dm')
READY = AutomationState(run_id='run', account='sim', username='sim0001', context_id='sim:dm', context_ok=True,
                        profile={'account_name': 'sim'}, profile_observed_at=NOW.isoformat(), cooldown_seconds=3.5)


def seen(kind, text, status='verified', observation_id='o1', context_id='sim:dm'):
    return Observation(observation_id=observation_id, capture_id='c', observed_at=NOW.isoformat(),
                       context_id=context_id, kind=kind, recipient='sim', text=text, identity_status=status)


class PolicyTests(unittest.TestCase):
    def test_missing_profile_refreshes_profile(self):
        intent = next_action(replace(READY, profile=None), CONFIG, NOW)
        self.assertEqual((intent.kind, intent.expected_reply_kind), ('refresh_profile', 'profile'))

    def test_missing_cooldown_refreshes_buffs(self):
        self.assertEqual(next_action(replace(READY, cooldown_seconds=None), CONFIG, NOW).kind, 'refresh_buffs')

    def test_verified_ready_state_fishes(self):
        intent = next_action(READY, CONFIG, NOW)
        self.assertEqual((intent.kind, intent.context_id), ('fish', 'sim:dm'))
        self.assertEqual(intent, next_action(READY, CONFIG, NOW), 'a pure function of its inputs')

    def test_no_action_without_context_or_with_pending_uncertainty(self):
        pending = ActionIntent('a', 'fish', 'sim:dm', NOW.isoformat(), 'fishing')
        for state in (replace(READY, context_ok=False), replace(READY, pending=pending),
                      replace(READY, uncertain='fish a: no reply')):
            with self.subTest(state=state):
                self.assertIsNone(next_action(state, CONFIG, NOW))

    def test_bounded_run_stops_at_its_trip_limit(self):
        self.assertIsNone(next_action(replace(READY, trips=25), replace(CONFIG, max_trips=25), NOW))

    def test_unsupported_actions_are_never_chosen(self):
        self.assertIsNone(next_action(READY, replace(CONFIG, capabilities=('refresh_profile',)), NOW))


class ApplyObservationTests(unittest.TestCase):
    def test_verified_replies_update_state_once(self):
        state = replace(READY, profile=None, cooldown_seconds=None)
        state = apply_observation(state, seen('profile', 'Inventory of sim\nBalance: $5.\nLevel: 3'))
        state = apply_observation(state, seen('buffs', "sim0001's current multipliers (including bait):\n"
                                                       'Fishing cooldown: 2.5s.', observation_id='o2'))
        self.assertEqual((state.profile['balance'], state.cooldown_seconds), (5, 2.5))
        self.assertIs(apply_observation(state, seen('buffs', 'Fishing cooldown: 9s.', observation_id='o2')), state,
                      'the same observation is applied once')

    def test_unreadable_replies_do_not_establish_state(self):
        state = replace(READY, profile=None, cooldown_seconds=None)
        for kind, text in (('profile', 'Inventory of sim\nBalance: $4?,2l0.\nLevel: 1O'),
                           ('profile', 'Inventory of sim\nBalance: $5.\nLevel: 3\nSomething unreadable'),
                           ('buffs', "sim0001's current multipliers (including bait):\nFishing cooldown: 3.?s.")):
            with self.subTest(text=text):
                self.assertIs(apply_observation(state, seen(kind, text)), state)

    def test_rejected_observations_change_nothing(self):
        state = replace(READY, profile=None)
        for observation in (seen('profile', 'Inventory of x\nBalance: $1.\nLevel: 1', status='mismatch'),
                            seen('profile', 'Inventory of sim\nBalance: $1.\nLevel: 1', status='unknown'),
                            seen('profile', 'Inventory of sim\nBalance: $1.\nLevel: 1', context_id='other')):
            with self.subTest(observation=observation):
                self.assertIs(apply_observation(state, observation), state)


if __name__ == '__main__':
    unittest.main()
