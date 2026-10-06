import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from src.player_state import parse_responses
from src.player_snapshots import (account_key, compare_latest, latest_full_snapshots,
                                  list_accounts, load_history, save_snapshot,
                                  validate_full_state)


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

    def test_saved_snapshot_omits_pasted_text(self):
        state = parse_responses(profile='Inventory of BugParticle\nBalance: $100\nLevel: 2\n'
                                        'Secret note: meet at dawn\nFish Inventory\n3 Cod\n3 Cod',
                                buffs='Mystery buff: whisper-123')
        self.assertTrue(state['unparsed']['profile'])
        result = save_snapshot(state, self.root)
        self.assertEqual(result['status'], 'saved')
        text = next(self.root.rglob('*.json')).read_text(encoding='utf-8')
        for raw in ('meet at dawn', 'Secret note', 'whisper-123', '3 Cod'):
            self.assertNotIn(raw, text)
        stored = json.loads(text)
        self.assertNotIn('unparsed', stored['state'])
        self.assertEqual(stored['state']['unparsed_line_counts'],
                         {'profile': 2, 'buffs': 1, 'prestige_shop': 0, 'pet': 0})
        self.assertIn('profile: Unrecognized line (line omitted)', stored['state']['warnings'])
        self.assertEqual(stored['parser_warnings'], stored['state']['warnings'])
        self.assertEqual(stored['state']['inventory']['fish'], {'Cod': 3})
        self.assertIn('meet at dawn', state['unparsed']['profile'][0], 'caller state is not modified')
        self.assertEqual(save_snapshot(state, self.root)['status'], 'unchanged')

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


class SnapshotComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _state(self, balance, level=10):
        state = parsed_state(balance=balance, level=level)
        state['profile'].update({'xp_current': 100, 'fish_value': 1000,
                                 'bait': 'Worms', 'bait_quantity': 10})
        state['inventory'] = {'fish': {'Fish': 5}, 'exotic_fish': {'Gold Fish': 1},
                              'special': {'Hooks': 3}}
        state['pets']['owned'] = {
            'Dolphin': {'level': 5, 'description': 'XP', 'xp_current': 20,
                        'xp_required': 100, 'xp_to_next_level': 80}}
        return state

    def test_compare_latest_reports_scalar_inventory_and_pet_deltas(self):
        older = self._state(100)
        newer = deepcopy(older)
        newer['profile'].update({'balance': 150, 'level': 11, 'xp_current': 140,
                                 'fish_value': 1200, 'bait_quantity': 7})
        newer['inventory']['fish']['Fish'] = 7
        newer['inventory']['exotic_fish']['Gold Fish'] = 2
        newer['inventory']['special']['Hooks'] = 1
        newer['pets']['owned']['Dolphin'].update({'level': 6, 'xp_current': 35})
        save_snapshot(older, self.root, mode='manual')
        save_snapshot(newer, self.root, mode='manual')

        result = compare_latest('BugParticle', self.root)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['changes']['balance']['delta'], 50)
        self.assertEqual(result['changes']['level']['delta'], 1)
        self.assertEqual(result['changes']['xp_current']['delta'], 40)
        self.assertEqual(result['changes']['fish_value']['delta'], 200)
        self.assertEqual(result['changes']['bait'], {'status': 'ok', 'bait': 'Worms', 'delta': -3})
        self.assertEqual(result['changes']['inventory']['fish']['deltas'], {'Fish': 2})
        self.assertEqual(result['changes']['inventory']['exotic_fish']['deltas'], {'Gold Fish': 1})
        self.assertEqual(result['changes']['inventory']['special']['deltas'], {'Hooks': -2})
        self.assertEqual(result['changes']['pets']['status'], 'ok')
        self.assertEqual(result['changes']['pets']['pets']['Dolphin']['level']['delta'], 1)
        self.assertEqual(result['changes']['pets']['pets']['Dolphin']['xp_current']['delta'], 15)

    def test_compare_keeps_missing_values_unknown_and_reports_bait_change(self):
        older = self._state(100)
        newer = self._state(110)
        older['profile']['fish_value'] = None
        newer['profile'].update({'bait': 'Magic Bait', 'bait_quantity': 99})
        newer['inventory']['fish'] = None
        newer['pets']['owned'] = None
        save_snapshot(older, self.root, mode='manual')
        save_snapshot(newer, self.root, mode='manual')

        changes = compare_latest('BugParticle', self.root)['changes']
        self.assertEqual(changes['fish_value'], {'status': 'unknown'})
        self.assertEqual(changes['bait'], {'status': 'changed', 'from': 'Worms', 'to': 'Magic Bait'})
        self.assertNotIn('delta', changes['bait'])
        self.assertEqual(changes['pets'], {'status': 'unknown'})
        self.assertEqual(changes['inventory']['fish'], {'status': 'unknown'})

    def test_partial_snapshots_are_excluded(self):
        save_snapshot(self._state(100), self.root, mode='manual')
        partial = parse_responses(pet='You have no pets!')
        save_snapshot(partial, self.root, mode='manual', account_name='BugParticle')
        save_snapshot(self._state(125), self.root, mode='manual')
        snapshots, warnings = latest_full_snapshots('BugParticle', self.root)
        self.assertEqual([item['state']['profile']['balance'] for item in snapshots], [125, 100])
        self.assertEqual(warnings, [])
        self.assertEqual(compare_latest('BugParticle', self.root)['changes']['balance']['delta'], 25)

    def test_compare_requires_two_full_snapshots(self):
        save_snapshot(self._state(100), self.root)
        result = compare_latest('BugParticle', self.root)
        self.assertEqual(result['status'], 'needs_another_full_snapshot')
        self.assertIsNone(result['older'])
        self.assertIsNotNone(result['newer'])


if __name__ == '__main__':
    unittest.main()
