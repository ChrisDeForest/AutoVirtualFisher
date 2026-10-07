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

    def test_financial_actions_need_fresh_profile_and_buffs(self):
        view = automation_view(snapshot(profile=(at(5), 'verified'), buffs=(at(5), 'verified')), NOW)
        self.assertTrue(view['financial_ready'], view['blockers'])
        self.assertFalse(view['bait_ready'], 'bait in use was never confirmed')
        view = automation_view(snapshot(profile=(at(5), 'verified'), buffs=(at(600), 'verified')), NOW)
        self.assertFalse(view['financial_ready'])
        self.assertTrue(any('buffs' in blocker for blocker in view['blockers']))
        legacy = automation_view({'schema_version': 1, 'captured_at': at(1), 'state': {}}, NOW)
        self.assertFalse(legacy['financial_ready'])
        self.assertFalse(automation_view(None, NOW)['financial_ready'])

    def test_bait_decisions_need_confirmed_bait(self):
        fresh = snapshot(profile=(at(5), 'verified'), buffs=(at(5), 'verified'))
        for state, ready in (({'bait': 'Worms', 'status': 'verified'}, True),
                             ({'bait': None, 'status': 'verified'}, True),
                             ({'bait': 'Worms', 'status': 'assumed'}, False),
                             ({'bait': None, 'status': 'unknown'}, False)):
            with self.subTest(state=state):
                self.assertEqual(automation_view(fresh, NOW, live={'bait_state': state})['bait_ready'], ready)

    def test_uncertain_alignment_blocks_actions(self):
        fresh = snapshot(profile=(at(5), 'verified'), buffs=(at(5), 'verified'))
        view = automation_view(fresh, NOW, live={'alignment': {'status': 'ambiguous', 'reason': 'repeat'},
                                                 'unresolved': 1})
        self.assertFalse(view['financial_ready'])
        self.assertFalse(view['actions_allowed'])


if __name__ == '__main__':
    unittest.main()
