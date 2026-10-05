import json
from pathlib import Path
import tempfile
import unittest

from src.player_state import parse_responses
from src.player_snapshots import (account_key, list_accounts, load_history,
                                  save_snapshot, validate_full_state)


def parsed_state(name='BugParticle', balance=100, level=2):
    return parse_responses(profile=f'Inventory of {name}\nBalance: ${balance}\nLevel: {level}')


class SnapshotStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_validation(self):
        valid, reasons = validate_full_state(parsed_state())
        self.assertTrue(valid)
        self.assertEqual(reasons, [])

        for field, value, expected in (
                ('account_name', None, 'account name'),
                ('balance', -1, 'balance'),
                ('level', 2.5, 'level')):
            with self.subTest(field=field):
                state = parsed_state()
                state['profile'][field] = value
                valid, reasons = validate_full_state(state)
                self.assertFalse(valid)
                self.assertTrue(any(expected in reason.lower() for reason in reasons), reasons)

    def test_account_keys_are_safe_and_collision_resistant(self):
        slash = account_key('A/B')
        colon = account_key('A:B')
        self.assertRegex(slash, r'^[a-z0-9-]+-[0-9a-f]{8}$')
        self.assertRegex(colon, r'^[a-z0-9-]+-[0-9a-f]{8}$')
        self.assertNotEqual(slash, colon)
        self.assertEqual(account_key('BugParticle'), account_key('bugparticle'))
        self.assertNotIn('/', slash)

    def test_save_writes_required_metadata_atomically(self):
        result = save_snapshot(parsed_state(), self.root)
        self.assertEqual(result['status'], 'saved')
        files = list(self.root.rglob('*.json'))
        self.assertEqual(len(files), 1)
        self.assertEqual(list(self.root.rglob('*.tmp')), [])
        stored = json.loads(files[0].read_text(encoding='utf-8'))
        self.assertEqual(stored['schema_version'], 1)
        self.assertEqual(stored['account_name'], 'BugParticle')
        self.assertEqual(stored['account_key'], account_key('BugParticle'))
        self.assertEqual(stored['save_mode'], 'automatic')
        self.assertEqual(stored['validity'], 'full')
        self.assertEqual(stored['state']['profile']['balance'], 100)
        self.assertTrue(stored['snapshot_id'])
        self.assertTrue(stored['captured_at'].endswith('Z'))

    def test_automatic_save_deduplicates_warning_order(self):
        first = parsed_state()
        first['warnings'] = ['second', 'first']
        second = parsed_state()
        second['warnings'] = ['first', 'second']
        self.assertEqual(save_snapshot(first, self.root)['status'], 'saved')
        self.assertEqual(save_snapshot(second, self.root)['status'], 'unchanged')
        self.assertEqual(len(list(self.root.rglob('*.json'))), 1)

    def test_manual_save_keeps_intentional_duplicates(self):
        state = parsed_state()
        self.assertEqual(save_snapshot(state, self.root, mode='manual')['status'], 'saved')
        self.assertEqual(save_snapshot(state, self.root, mode='manual')['status'], 'saved')
        self.assertEqual(len(list(self.root.rglob('*.json'))), 2)

    def test_partial_save_requires_explicit_account(self):
        state = parse_responses(pet='+14 pet XP per fishing trip (average)\nYou have no pets!')
        rejected = save_snapshot(state, self.root, mode='manual')
        self.assertEqual(rejected['status'], 'rejected')
        saved = save_snapshot(state, self.root, mode='manual', account_name='BugParticle')
        self.assertEqual(saved['status'], 'saved')
        self.assertEqual(saved['snapshot']['validity'], 'partial')

    def test_explicit_account_cannot_override_profile_identity(self):
        result = save_snapshot(parsed_state(), self.root, mode='manual', account_name='epicmouse')
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('does not match', result['reason'])

    def test_accounts_and_history_are_isolated_and_newest_first(self):
        save_snapshot(parsed_state('BugParticle', 100), self.root, mode='manual')
        save_snapshot(parsed_state('BugParticle', 200), self.root, mode='manual')
        save_snapshot(parsed_state('epicmouse', 300), self.root, mode='manual')
        accounts, warnings = list_accounts(self.root)
        self.assertEqual(accounts, ['BugParticle', 'epicmouse'])
        self.assertEqual(warnings, [])
        history, warnings = load_history('BugParticle', self.root, limit=1)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['state']['profile']['balance'], 200)
        self.assertEqual(warnings, [])

    def test_malformed_and_unsupported_snapshots_are_skipped(self):
        save_snapshot(parsed_state(), self.root)
        folder = self.root / account_key('BugParticle')
        (folder / 'bad.json').write_text('{broken', encoding='utf-8')
        (folder / 'future.json').write_text(json.dumps({'schema_version': 999}), encoding='utf-8')
        history, warnings = load_history('BugParticle', self.root)
        self.assertEqual(len(history), 1)
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any('bad.json' in warning for warning in warnings))
        self.assertTrue(any('future.json' in warning for warning in warnings))


if __name__ == '__main__':
    unittest.main()
