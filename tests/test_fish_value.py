from pathlib import Path
import tempfile
import unittest

from src.catch_check import load_fish_reference
from src.fish_value import base_value, latest_sell_multiplier, sell_multiplier, value_check
from src.paste_detect import detect
from src.player_snapshots import load_history, save_snapshot
from src.player_state import parse_responses

FIXTURES = Path(__file__).parent / 'fixtures'
REFERENCE = load_fish_reference()


def fixture(name):
    return (FIXTURES / name).read_text(encoding='utf-8')


class BaseValueTests(unittest.TestCase):
    def test_base_value_and_unknown_fish(self):
        self.assertEqual(base_value({'Fish': 291, 'Salmon': 3642, 'Cod': 4623}, REFERENCE), (57_447, []))
        self.assertEqual(base_value({'Cod': 2, 'Moon fish': 1}, REFERENCE), (20, ['Moon fish']))

    def test_sell_multiplier_from_buffs(self):
        state = parse_responses(buffs=fixture('live_buffs.txt'))
        self.assertEqual(sell_multiplier(state), 4.83)
        self.assertIsNone(sell_multiplier(parse_responses(profile='Balance: $1')))


class SnapshotValueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def history(self, account):
        return load_history(account, self.root)[0]

    def test_both_live_profiles_match_base_value_times_sell_price(self):
        save_snapshot(parse_responses(profile=fixture('live_profile.txt'), buffs=fixture('live_buffs.txt')), self.root)
        copy = detect(fixture('live_discord_copy.txt'))['responses']
        save_snapshot(parse_responses(profile=copy['profile'], buffs=copy['buffs']), self.root)
        for account in ('BugParticle', 'epicmouse'):
            with self.subTest(account=account):
                self.assertEqual(value_check(self.history(account), REFERENCE), {'matches': 1, 'mismatches': []})

    def test_mismatch_is_reported(self):
        state = parse_responses(profile=fixture('live_profile.txt'), buffs=fixture('live_buffs.txt'))
        state['profile']['fish_value'] = 150_000
        save_snapshot(state, self.root)
        check = value_check(self.history('BugParticle'), REFERENCE)
        self.assertEqual(check['matches'], 0)
        self.assertEqual(check['mismatches'][0]['observed'], 150_000)
        self.assertEqual(check['mismatches'][0]['expected'], round(28_400 * 4.83))

    def test_snapshots_without_buffs_are_not_checked(self):
        save_snapshot(parse_responses(profile=fixture('live_profile.txt')), self.root)
        self.assertEqual(value_check(self.history('BugParticle'), REFERENCE), {'matches': 0, 'mismatches': []})

    def test_latest_sell_multiplier_comes_from_newest_snapshot_with_buffs(self):
        save_snapshot(parse_responses(profile=fixture('live_profile.txt'), buffs=fixture('live_buffs.txt')), self.root)
        save_snapshot(parse_responses(profile=fixture('live_profile.txt').replace('$2,343,033', '$2,343,034')),
                      self.root)
        multiplier, captured_at = latest_sell_multiplier(self.history('BugParticle'))
        self.assertEqual(multiplier, 4.83)
        self.assertEqual(captured_at, self.history('BugParticle')[1]['captured_at'])
        self.assertIsNone(latest_sell_multiplier([]))


if __name__ == '__main__':
    unittest.main()
