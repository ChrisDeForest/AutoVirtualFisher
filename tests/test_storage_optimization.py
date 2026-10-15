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


def test_journal_reads_only_changed_files_and_detects_same_count_edits(tmp_path):
    folder = tmp_path / 'run'
    folder.mkdir()
    for i in range(3):
        (folder / f'{i}.json').write_text(json.dumps({'schema_version': 1, 'type': 'intent', 'n': i}))
    cache = {}
    progress_store.journal_events(tmp_path, cache)
    (folder / '3.json').write_text(json.dumps({'schema_version': 1, 'type': 'intent', 'n': 3}))
    with patch.object(progress_store, '_read_event', wraps=progress_store._read_event) as read:
        assert len(progress_store.journal_events(tmp_path, cache)) == 4
        assert read.call_count == 1
    (folder / '1.json').write_text(json.dumps({'schema_version': 1, 'type': 'changed', 'n': 1}))
    (folder / '2.json').unlink()
    events = progress_store.journal_events(tmp_path, cache)
    assert [e['type'] for e in events] == ['intent', 'changed', 'intent']
    events[0]['type'] = 'caller mutation'
    assert progress_store.journal_events(tmp_path, cache)[0]['type'] == 'intent'

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
            folder.mkdir()
            path = folder / '1.json'
            path.write_text(json.dumps({'schema_version': 1, 'type': 'intent'}))
            cache = {'run': (1, [{'type': 'outdated'}])}
            self.assertEqual(progress_store.journal_events(root, cache)[0]['type'], 'intent')
            path.write_text('{')
            self.assertEqual(progress_store.journal_events(root, cache), [])
            path.unlink()
            folder.rmdir()
            self.assertEqual(progress_store.journal_events(root, cache), [])
            self.assertEqual(cache, {})

    def test_event_cache_has_explicit_memory_bounds(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            for run in range(4):
                folder = root / str(run)
                folder.mkdir()
                for event in range(3):
                    (folder / f'{event}.json').write_text(json.dumps({'schema_version': 1, 'type': 'intent'}))
            cache = {}
            with patch.object(progress_store, 'MAX_CACHED_RUNS', 2, create=True), \
                    patch.object(progress_store, 'MAX_CACHED_EVENTS', 4, create=True):
                self.assertEqual(len(progress_store.journal_events(root, cache)), 12)
                self.assertLessEqual(len(cache), 2)
                self.assertLessEqual(sum(len(files) for files in cache.values()), 4)

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

    def test_trusted_readonly_events_skip_copy_but_default_is_detached(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            folder = root / 'run'
            folder.mkdir()
            (folder / '1.json').write_text(json.dumps({'schema_version': 1, 'type': 'intent'}))
            cache = {}
            first = progress_store.journal_events(root, cache, detached=False)
            second = progress_store.journal_events(root, cache, detached=False)
            self.assertIs(first[0], second[0])
            isolated = progress_store.journal_events(root, cache)
            self.assertIsNot(first[0], isolated[0])
