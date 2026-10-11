import json
import os
import time
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from src.player_state import parse_responses
from src.player_snapshots import (account_key, compare_latest, growth_series, latest_full_snapshots,
                                  list_accounts, load_history, save_snapshot,
                                  validate_full_state, write_json_atomically)


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
        self.assertEqual(stored['schema_version'], 2)
        self.assertEqual(stored['sections'], {'profile': {'observed_at': None, 'source_observation_id': None,
                                                          'identity_status': 'user_attributed'}},
                         'a pasted snapshot says who attributed it, and does not invent an observation time')
        self.assertEqual(stored['account_name'], 'BugParticle')
        self.assertEqual(stored['account_key'], account_key('BugParticle'))
        self.assertEqual(stored['save_mode'], 'automatic')
        self.assertEqual(stored['validity'], 'full')
        self.assertEqual(stored['state']['profile']['balance'], 100)
        self.assertTrue(stored['snapshot_id'])
        self.assertTrue(stored['captured_at'].endswith('Z'))

    def test_a_stale_temporary_file_never_blocks_a_write(self):
        # A hard kill mid-write leaves the temporary file behind; the next write must still succeed, atomically.
        folder = self.root / 'account'
        folder.mkdir()
        (folder / '.model_check.tmp').write_text('{"half": ', encoding='utf-8')
        write_json_atomically(folder, 'model_check', {'n': 1})
        self.assertEqual(json.loads((folder / 'model_check.json').read_text(encoding='utf-8')), {'n': 1})
        write_json_atomically(folder, 'model_check', {'n': 2})
        self.assertEqual(json.loads((folder / 'model_check.json').read_text(encoding='utf-8')), {'n': 2})
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ['.model_check.tmp', 'model_check.json'],
                         'the stale file is left alone; no temporary file of its own is left behind')

    def test_old_temporary_files_of_the_same_record_are_cleaned_up(self):
        folder = self.root / 'account'
        folder.mkdir()
        old = time.time() - 120
        names = {'old_own': '.model_check.0123abcd.tmp', 'old_legacy': '.model_check.tmp',
                 'fresh_own': '.model_check.89abcdef.tmp', 'old_other': '.account_state.0123abcd.tmp',
                 'old_longer_id': '.model_check.extra.0123abcd.tmp'}
        for key, name in names.items():
            (folder / name).write_text('{"half": ', encoding='utf-8')
            if key.startswith('old'):
                os.utime(folder / name, (old, old))
        write_json_atomically(folder, 'model_check', {'n': 1})
        left = sorted(path.name for path in folder.iterdir())
        self.assertEqual(left, sorted([names['fresh_own'], names['old_other'], names['old_longer_id'],
                                       'model_check.json']),
                         'only this record\'s temporary files older than a minute go')

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
                                buffs='Mystery buff: whisper-123',
                                quests='Daily Fishing - 1/500\nCatch 500 fish\nquest-secret-77')
        self.assertTrue(state['unparsed']['profile'])
        result = save_snapshot(state, self.root)
        self.assertEqual(result['status'], 'saved')
        text = next(self.root.rglob('*.json')).read_text(encoding='utf-8')
        for raw in ('meet at dawn', 'Secret note', 'whisper-123', '3 Cod', 'quest-secret-77'):
            self.assertNotIn(raw, text)
        stored = json.loads(text)
        self.assertNotIn('unparsed', stored['state'])
        self.assertEqual(stored['state']['unparsed_line_counts'],
                         {'profile': 2, 'buffs': 1, 'prestige_shop': 0, 'pet': 0, 'quests': 1})
        self.assertIn('quests: Unrecognized line (line omitted)', stored['state']['warnings'])
        self.assertEqual(stored['state']['quests']['daily']['Daily Fishing']['progress'], 1)
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




class GrowthSeriesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def save(self, profile):
        state = parse_responses(profile='Inventory of BugParticle\n' + profile)
        result = save_snapshot(state, self.root)
        self.assertEqual(result['status'], 'saved', result)
        return result['snapshot']['captured_at']

    def test_series_are_oldest_first_and_cover_each_metric(self):
        first = self.save('Balance: $100\nLevel 10, 250/1,000 XP to next level\nFish Value: $50\n'
                          'Exotic Fish\n3 Gold Fish\n2 Lava Fish')
        second = self.save('Balance: $400\nLevel 11, 500/1,000 XP to next level\nFish Value: $70\n'
                           'Exotic Fish\n4 Gold Fish\n2 Lava Fish')
        result = growth_series('BugParticle', self.root)
        self.assertEqual(result['warnings'], [])
        self.assertEqual(result['series']['balance'], [(first, 100), (second, 400)])
        self.assertEqual(result['series']['level_progress'], [(first, 10.25), (second, 11.5)])
        self.assertEqual(result['series']['fish_value'], [(first, 50), (second, 70)])
        self.assertEqual(result['series']['exotic_total'], [(first, 5), (second, 6)])

    def test_missing_values_are_skipped_not_zero(self):
        self.save('Balance: $100\nLevel: 10')
        later = self.save('Balance: $200\nL10, 750 XP to next level\nFish Value: $9\nExotic Fish')
        series = growth_series('BugParticle', self.root)['series']
        self.assertEqual(len(series['balance']), 2)
        self.assertEqual(series['level_progress'], [])
        self.assertEqual(series['fish_value'], [(later, 9)])
        self.assertEqual(series['exotic_total'], [(later, 0)])

    def test_partial_snapshots_are_excluded(self):
        self.save('Balance: $100\nLevel: 10')
        save_snapshot(parse_responses(pet='You have no pets! Find pets by fishing.'), self.root,
                      mode='manual', account_name='BugParticle')
        self.assertEqual(len(growth_series('BugParticle', self.root)['series']['balance']), 1)

    def test_unreadable_timestamps_are_skipped_with_warning(self):
        self.save('Balance: $100\nLevel: 10')
        self.save('Balance: $200\nLevel: 10')
        path = sorted(self.root.rglob('*.json'))[0]
        stored = json.loads(path.read_text(encoding='utf-8'))
        stored['captured_at'] = 'not a time'
        path.write_text(json.dumps(stored), encoding='utf-8')
        result = growth_series('BugParticle', self.root)
        self.assertEqual([value for _, value in result['series']['balance']], [200])
        self.assertTrue(any('timestamp' in warning for warning in result['warnings']), result['warnings'])

    def test_unknown_account_has_empty_series(self):
        result = growth_series('Nobody', self.root)
        self.assertEqual(result['series'], {'balance': [], 'level_progress': [], 'fish_value': [],
                                            'exotic_total': []})

def observed(at, observation_id):
    return {'observed_at': at, 'source_observation_id': observation_id, 'identity_status': 'verified'}


T0, T1, T2 = '2026-10-06T12:00:00+00:00', '2026-10-06T12:05:00+00:00', '2026-10-06T12:10:00+00:00'
BUFFS = "BugParticle's current multipliers (including bait):\nSell price: 2.00x"


class SectionProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def save(self, sections, buffs=''):
        state = parse_responses(profile='Inventory of BugParticle\nBalance: $100\nLevel: 2', buffs=buffs)
        return save_snapshot(state, self.root, sections=sections)

    def test_buffs_later_keep_the_profile_observation_time(self):
        self.save({'profile': observed(T0, 'p1')})
        self.assertEqual(self.save({'profile': observed(T0, 'p1'), 'buffs': observed(T1, 'b1')}, BUFFS)['status'],
                         'saved')
        newest = load_history('BugParticle', self.root)[0][0]
        self.assertEqual(newest['sections']['profile']['observed_at'], T0)
        self.assertEqual(newest['sections']['buffs']['observed_at'], T1)
        self.assertEqual(growth_series('BugParticle', self.root)['series']['balance'], [(T0, 100)],
                         'one profile observation is one chart point, at the time it was observed')

    def test_refreshed_profile_updates_only_profile_provenance(self):
        self.save({'profile': observed(T0, 'p1'), 'buffs': observed(T1, 'b1')}, BUFFS)
        result = self.save({'profile': observed(T2, 'p2'), 'buffs': observed(T1, 'b1')}, BUFFS)
        self.assertEqual(result['status'], 'saved', 'the same values seen again are a newer observation')
        sections = result['snapshot']['sections']
        self.assertEqual((sections['profile']['observed_at'], sections['buffs']['observed_at']), (T2, T1))
        self.assertEqual(self.save({'profile': observed(T2, 'p2'), 'buffs': observed(T1, 'b1')}, BUFFS)['status'],
                         'unchanged')
        self.assertEqual([time for time, _ in growth_series('BugParticle', self.root)['series']['balance']],
                         [T0, T2])

    def test_legacy_snapshots_still_load_without_provenance(self):
        save_snapshot(parsed_state(), self.root)
        path = next(self.root.rglob('*.json'))
        stored = json.loads(path.read_text(encoding='utf-8'))
        stored['schema_version'] = 1
        del stored['sections']
        path.write_text(json.dumps(stored), encoding='utf-8')
        history, warnings = load_history('BugParticle', self.root)
        self.assertEqual((len(history), warnings), (1, []))
        self.assertEqual(list_accounts(self.root)[0], ['BugParticle'])
        self.assertEqual(path.read_text(encoding='utf-8'), json.dumps(stored), 'old files are not rewritten')


if __name__ == '__main__':
    unittest.main()
