from datetime import datetime, timedelta, timezone
import unittest

from src.automation_state import automation_view, section_freshness

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def at(seconds_ago):
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


def snapshot(**sections):
    return {'schema_version': 2, 'captured_at': NOW.isoformat(), 'state': {},
            'sections': {name: {'observed_at': when, 'source_observation_id': name, 'identity_status': status}
                         for name, (when, status) in sections.items()}}


def live(**changes):
    """A running capture whose latest capture was readable, recent, and in the verified channel."""
    status = {'running': True, 'capture_status': 'ok', 'captured_at': at(1), 'interval': 3.0,
              'context': {'status': 'verified', 'channel': 'Virtual Fisher'},
              'alignment': {'status': 'certain', 'reason': ''}, 'unresolved': 0,
              'bait_state': {'bait': 'Worms', 'status': 'verified'}}
    return {**status, **changes}


FRESH = snapshot(profile=(at(5), 'verified'), buffs=(at(5), 'verified'))


class FreshnessTests(unittest.TestCase):
    def test_recent_verified_observation_is_fresh(self):
        result = section_freshness(snapshot(profile=(at(10), 'verified')), 'profile', NOW, max_age=30)
        self.assertEqual((result['status'], result['age_seconds']), ('fresh', 10))

    def test_old_observation_is_stale(self):
        self.assertEqual(section_freshness(snapshot(profile=(at(31), 'verified')), 'profile', NOW, 30)['status'],
                         'stale')

    def test_missing_provenance_is_unknown_not_fresh(self):
        legacy = {'schema_version': 1, 'captured_at': at(1), 'state': {}}
        self.assertEqual(section_freshness(legacy, 'profile', NOW, 30)['status'], 'unknown',
                         'a recent save time does not prove the values are recent')
        pasted = snapshot(profile=(None, 'user_attributed'))
        self.assertEqual(section_freshness(pasted, 'profile', NOW, 30)['status'], 'unknown')
        self.assertEqual(section_freshness(snapshot(profile=(at(1), 'verified')), 'buffs', NOW, 30)['status'],
                         'unknown')


class ReadinessTests(unittest.TestCase):
    def test_everything_verified_is_ready(self):
        view = automation_view(FRESH, NOW, live=live())
        self.assertTrue(view['actions_allowed'] and view['financial_ready'] and view['bait_ready'], view['blockers'])

    def test_financial_actions_need_fresh_profile_and_buffs(self):
        view = automation_view(snapshot(profile=(at(5), 'verified'), buffs=(at(600), 'verified')), NOW, live=live())
        self.assertTrue(view['actions_allowed'])
        self.assertFalse(view['financial_ready'])
        self.assertTrue(any('buffs' in blocker for blocker in view['blockers']))
        legacy = automation_view({'schema_version': 1, 'captured_at': at(1), 'state': {}}, NOW, live=live())
        self.assertFalse(legacy['financial_ready'])
        self.assertFalse(automation_view(None, NOW, live=live())['financial_ready'])

    def test_nothing_is_ready_unless_capture_is_watching_the_right_channel_now(self):
        cases = {
            'no capture': None,
            'stopped': live(running=False),
            'blank': live(capture_status='blank'),
            'minimized': live(capture_status='minimized'),
            'another channel': live(capture_status='other_context', context={'status': 'changed'}),
            'channel not read': live(context={'status': 'unverified'}),
            'old capture': live(captured_at=at(60)),
            'view lost': live(alignment={'status': 'lost', 'reason': 'scrolled'}),
            'ambiguous': live(alignment={'status': 'ambiguous', 'reason': 'repeat'}),
            'review waiting': live(unresolved=1),
        }
        for name, status in cases.items():
            with self.subTest(name):
                view = automation_view(FRESH, NOW, live=status)
                self.assertFalse(view['actions_allowed'])
                self.assertFalse(view['financial_ready'])
                self.assertTrue(view['blockers'])

    def test_bait_decisions_need_confirmed_bait(self):
        for state, ready in (({'bait': 'Worms', 'status': 'verified'}, True),
                             ({'bait': None, 'status': 'verified'}, True),
                             ({'bait': 'Worms', 'status': 'assumed'}, False),
                             ({'bait': None, 'status': 'unknown'}, False)):
            with self.subTest(state=state):
                self.assertEqual(automation_view(FRESH, NOW, live=live(bait_state=state))['bait_ready'], ready)


if __name__ == '__main__':
    unittest.main()
