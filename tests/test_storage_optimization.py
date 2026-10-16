import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from src import fishing_sessions as sessions, progress_store
from src.fishing_log import parse_fishing
from src.player_snapshots import account_key


def test_save_avoids_full_history_read_and_concurrent_duplicate(tmp_path):
    tally = parse_fishing('You caught:\n2 Cod\n+10 XP')
    sessions.save_session(tally, 'test', 'first', tmp_path)
    with patch.object(sessions, 'load_sessions', side_effect=AssertionError('history reread')):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: sessions.save_session(tally, 'test', 'next', tmp_path), range(8)))
    assert sum(r['status'] == 'saved' for r in results) == 1
    assert len(list(tmp_path.rglob('*.json'))) == 2


def test_legacy_normalizes_without_rewrite(tmp_path):
    tally = parse_fishing('You caught:\n2 Cod\n+10 XP')
    result = sessions.save_session(tally, 'test', 'first', tmp_path)
    path = tmp_path / account_key('test') / (result['session']['session_id'] + '.json')
    record = json.loads(path.read_text())
    del record['tally']['chest_money']
    record['tally']['finds'] = {'G01d Fish': 2, 'Gold Fish': 3}
    path.write_text(json.dumps(record))
    original = path.read_bytes()
    loaded, _ = sessions.load_sessions('test', tmp_path)
    assert loaded[0]['tally']['chest_money'] == 0
    assert sessions.fishing_totals('test', tmp_path)['finds'] == {'Gold Fish': 5}
    assert path.read_bytes() == original


def write_event(folder, name, kind, at, **fields):
    """One journal file as AutomationJournal writes it (its run is the folder)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps({'schema_version': 1, 'type': kind, 'at': at, 'run_id': folder.name,
                                           'action_id': None, **fields}))


def act(folder, name, at, label):
    write_event(folder, name, 'intent', at, action_id=f'{label}-act',
                intent={'kind': 'act', 'preconditions': [label], 'arguments': []})


def test_journal_reads_only_changed_files_and_detects_same_count_edits(tmp_path):
    folder = tmp_path / 'run'
    write_event(folder, '0.json', 'run_started', '2026-10-14T10:00:00Z')
    write_event(folder, '1.json', 'dispatched', '2026-10-14T10:00:01Z')
    act(folder, '2.json', '2026-10-14T10:00:02Z', 'Buy Salesman 8')
    cache = {}
    progress_store.read_journal(tmp_path, cache)
    write_event(folder, '3.json', 'run_stopped', '2026-10-14T10:00:03Z', reason='first')
    with patch.object(progress_store, '_read_event', wraps=progress_store._read_event) as read:
        journal = progress_store.read_journal(tmp_path, cache)
        assert read.call_count == 1
    assert (journal['runs'][0]['stopped'], len(journal['acts'])) == ('first', 1)
    write_event(folder, '3.json', 'run_stopped', '2026-10-14T10:00:03Z', reason='later')   # same count, edited
    (folder / '2.json').unlink()
    journal = progress_store.read_journal(tmp_path, cache)
    assert (journal['runs'][0]['stopped'], journal['acts']) == ('later', [])
    journal['runs'][0]['stopped'] = 'caller mutation'
    assert progress_store.read_journal(tmp_path, cache)['runs'][0]['stopped'] == 'later'

import tempfile
import unittest

class StorageOptimizationTests(unittest.TestCase):
    def test_save_index(self):
        with tempfile.TemporaryDirectory() as root:
            test_save_avoids_full_history_read_and_concurrent_duplicate(Path(root))

    def test_legacy(self):
        with tempfile.TemporaryDirectory() as root:
            test_legacy_normalizes_without_rewrite(Path(root))

    def test_incremental(self):
        with tempfile.TemporaryDirectory() as root:
            test_journal_reads_only_changed_files_and_detects_same_count_edits(Path(root))

    def test_index_backfill_recovery_external_edits_and_deleted_sources(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            tally = parse_fishing('You caught:\n2 Cod\n+10 XP')
            saved = sessions.save_session(tally, 'test', 'first', root)
            folder = root / account_key('test')
            index = folder / '.sessions.sqlite3'
            index.unlink()
            with patch.object(sessions, 'load_sessions', side_effect=AssertionError('history reread')):
                self.assertEqual(sessions.save_session(tally, 'test', 'first', root)['status'], 'duplicate')
                index.write_bytes(b'corrupt index')
                self.assertEqual(sessions.save_session(tally, 'test', 'first', root)['status'], 'duplicate')
            source = folder / (saved['session']['session_id'] + '.json')
            changed = json.loads(source.read_text())
            changed['source_fingerprint'] = 'external'
            source.write_text(json.dumps(changed))
            self.assertEqual(sessions.save_session(tally, 'test', 'external', root)['status'], 'duplicate')
            source.unlink()
            self.assertEqual(sessions.save_session(tally, 'test', 'external', root)['status'], 'saved')

    def test_warm_index_reads_no_unchanged_json_and_repairs_bad_json(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            tally = parse_fishing('You caught:\n2 Cod\n+10 XP')
            sessions.save_session(tally, 'test', 'first', root)
            folder = root / account_key('test')
            broken = folder / 'broken.json'
            broken.write_text('{')
            result = sessions.save_session(tally, 'test', 'second', root)
            self.assertTrue(any('broken.json' in warning for warning in result['warnings']))
            with patch.object(sessions, '_read_session', side_effect=AssertionError('unchanged JSON read')):
                self.assertEqual(sessions.save_session(tally, 'test', 'second', root)['status'], 'duplicate')
            broken.unlink()
            self.assertEqual(sessions.save_session(tally, 'test', 'second', root)['warnings'], [])

    def test_journal_deleted_run_and_legacy_cache_are_rebuilt(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            folder = root / 'run'
            path = folder / '1.json'
            write_event(folder, '1.json', 'run_started', '2026-10-14T10:00:00Z')
            for legacy in ((1, [{'type': 'outdated'}]), {'1.json': ((1, 2, 3, 4), {'type': 'outdated'})}):
                cache = {'run': legacy}
                self.assertEqual([r['run_id'] for r in progress_store.read_journal(root, cache)['runs']], ['run'])
            path.write_text('{')
            self.assertEqual(progress_store.read_journal(root, cache), {'runs': [], 'acts': []})
            path.unlink()
            folder.rmdir()
            self.assertEqual(progress_store.read_journal(root, cache), {'runs': [], 'acts': []})
            self.assertEqual(cache, {})

    def test_event_cache_has_explicit_memory_bounds(self):
        # The newest runs are kept (by name: a run's folder is named by when it started), and only the newest run
        # keeps its events per file, within MAX_CACHED_EVENTS; the others keep their digest alone.
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            for run in range(4):
                for event in range(3):
                    write_event(root / str(run), f'{event}.json', 'dispatched', f'2026-10-14T1{run}:00:0{event}Z')
            cache = {}
            with patch.object(progress_store, 'MAX_CACHED_RUNS', 2, create=True), \
                    patch.object(progress_store, 'MAX_CACHED_EVENTS', 4, create=True):
                for _ in range(2):
                    self.assertEqual(len(progress_store.read_journal(root, cache)['runs']), 4)
                    self.assertEqual(sorted(cache), ['2', '3'])
                    self.assertEqual([len(entry['files'] or {}) for _, entry in sorted(cache.items())], [0, 3])
                for event in (3, 4):                                                    # over the event budget
                    write_event(root / '3', f'{event}.json', 'dispatched', f'2026-10-14T13:00:0{event}Z')
                self.assertEqual(len(progress_store.read_journal(root, cache)['runs']), 4)
                self.assertIsNone(cache['3']['files'])

    def test_malformed_session_shapes_are_warnings(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            folder = root / account_key('test')
            folder.mkdir()
            (folder / 'list.json').write_text('[]')
            records, warnings = sessions.load_sessions('test', root)
            self.assertEqual(records, [])
            self.assertEqual(len(warnings), 1)

    def test_duplicate_payload_is_stable_after_index_rebuild(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            tally = {'schema_version': 1, 'trips': 1, 'finds': {'G01d Fish': 2}, 'chest_money': None}
            first = sessions.save_session(tally, 'test', 'same', root)['session']
            duplicate = sessions.save_session(tally, 'test', 'same', root)['session']
            (root / account_key('test') / '.sessions.sqlite3').unlink()
            rebuilt = sessions.save_session(tally, 'test', 'same', root)['session']
            self.assertEqual(first, duplicate)
            self.assertEqual(duplicate, rebuilt)
            self.assertEqual(first['tally']['chest_money'], 0)

    def test_bad_tally_counts_warn_instead_of_crash(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            tally = parse_fishing('You caught:\n2 Cod\n+10 XP')
            saved = sessions.save_session(tally, 'test', 'same', root)['session']
            path = root / account_key('test') / (saved['session_id'] + '.json')
            for bad in (['Cod'], {'Cod': 'many'}, {'Cod': None}):
                saved['tally']['catches'] = bad
                path.write_text(json.dumps(saved))
                records, warnings = sessions.load_sessions('test', root)
                self.assertEqual(records, [])
                self.assertEqual(len(warnings), 1)

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows locking behavior')
    def test_lock_non_contention_errors_raise_immediately(self):
        import errno
        import msvcrt
        with tempfile.TemporaryDirectory() as root:
            error = OSError(errno.EINVAL, 'invalid lock')
            with patch.object(msvcrt, 'locking', side_effect=error), \
                    patch('src.session_index.time.sleep', side_effect=AssertionError('must not retry')):
                with self.assertRaises(OSError) as raised:
                    sessions.save_session({'trips': 1}, 'test', 'same', Path(root))
                self.assertIs(raised.exception, error)

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows locking behavior')
    def test_lock_contention_has_a_deadline(self):
        import errno
        import msvcrt
        with tempfile.TemporaryDirectory() as root:
            error = OSError(errno.EACCES, 'busy lock')
            with patch.object(msvcrt, 'locking', side_effect=error), \
                    patch('src.session_index.time.monotonic', side_effect=[0, 31]), \
                    patch('src.session_index.time.sleep', side_effect=AssertionError('deadline expired')):
                with self.assertRaises(OSError) as raised:
                    sessions.save_session({'trips': 1}, 'test', 'same', Path(root))
                self.assertIs(raised.exception, error)

    def test_one_journal_cache_per_root_is_shared(self):
        # The controller and the page's disk view read the same journal: one cache for both, by the folder.
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            write_event(root / 'run', '1.json', 'run_started', '2026-10-14T10:00:00Z')
            try:
                first = progress_store.read_journal(root)
                with patch.object(progress_store, '_read_event', side_effect=AssertionError('read again')):
                    again = progress_store.read_journal(root / '.' / 'run' / '..')
                self.assertEqual(again, first)
                again['runs'].clear()                                           # the caller's copy only
                self.assertEqual(progress_store.read_journal(root), first)
            finally:
                progress_store._JOURNALS.pop(str(root.resolve()), None)

    def test_finished_runs_are_not_worked_out_again(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            write_event(root / 'run-a', '1.json', 'run_started', '2026-10-14T10:00:00Z')
            write_event(root / 'run-a', '2.json', 'run_stopped', '2026-10-14T10:30:00Z', reason='Stopped.')
            write_event(root / 'run-b', '1.json', 'run_started', '2026-10-14T11:00:00Z')
            cache = {}
            progress_store.read_journal(root, cache)
            write_event(root / 'run-b', '2.json', 'dispatched', '2026-10-14T11:00:05Z')
            with patch.object(progress_store, 'journal_digest', wraps=progress_store.journal_digest) as digest:
                journal = progress_store.read_journal(root, cache)
            self.assertEqual(digest.call_count, 1)                              # run-b only
            self.assertEqual([r['end'] for r in journal['runs']], ['2026-10-14T10:30:00Z', '2026-10-14T11:00:05Z'])
            # An older run edited in place, or losing a file, is noticed and worked out again.
            write_event(root / 'run-a', '2.json', 'run_stopped', '2026-10-14T10:30:00Z', reason='Edited.')
            self.assertEqual(progress_store.read_journal(root, cache)['runs'][0]['stopped'], 'Edited.')
            (root / 'run-a' / '2.json').unlink()
            self.assertIsNone(progress_store.read_journal(root, cache)['runs'][0]['stopped'])

    def test_cached_events_keep_only_what_progress_reads(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            tally = {'trips': 1, 'fishing_xp': 9, 'finds': {'rare chest': 1}, 'chest_contents': {},
                     'pets_found': {}, 'charm_levels': {}, 'catches': {'Cod': 2}, 'text': 'x' * 1000}
            write_event(root / 'run', '1.json', 'acknowledged', '2026-10-14T10:00:00Z', action_id='run-00001-fish',
                        result='trip', tally=tally, observation={'text': 'y' * 1000})
            write_event(root / 'run', '2.json', 'intent', '2026-10-14T10:00:01Z', action_id='run-00002-fish',
                        intent={'kind': 'fish', 'preconditions': [], 'command': '/fish'})
            cache = {}
            journal = progress_store.read_journal(root, cache)
            self.assertEqual((journal['runs'][0]['trips'], journal['runs'][0]['chests']), (1, 1))
            kept = {name: event for name, (_, event) in cache['run']['files'].items()}
            self.assertEqual(set(kept['1.json']['tally']),
                             {'trips', 'fishing_xp', 'finds', 'chest_contents', 'pets_found', 'charm_levels'})
            self.assertNotIn('observation', kept['1.json'])
            self.assertNotIn('intent', kept['2.json'])                            # not an act: not read
