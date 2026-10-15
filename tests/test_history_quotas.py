"""Authoritative histories stop new work at quota; evidence is never pruned."""
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from src import storage_limits
from src.automation_journal import AutomationJournal
from src.fishing_sessions import save_session


class HistoryQuotaTests(TestCase):
    def test_trip_file_limit_refuses_new_but_allows_duplicate(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(storage_limits, 'TRIP_MAX_FILES', 1):
            root = Path(temporary)
            self.assertEqual(save_session({'trips': 1}, 'Me', 'first', root)['status'], 'saved')
            self.assertEqual(save_session({'trips': 1}, 'Me', 'first', root)['status'], 'duplicate')
            with self.assertRaisesRegex(OSError, 'limit'):
                save_session({'trips': 1}, 'Me', 'next', root)
            self.assertEqual(len(list(root.rglob('*.json'))), 1)

    def test_trip_byte_limit_refuses_before_publication(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(storage_limits, 'TRIP_MAX_BYTES', 1):
            root = Path(temporary)
            with self.assertRaisesRegex(OSError, 'limit'):
                save_session({'trips': 1}, 'Me', 'first', root)
            self.assertEqual(list(root.rglob('*.json')), [])

    def test_index_storage_errors_become_oserrors(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(storage_limits, 'SESSION_INDEX_MAX_BYTES', 1):
            with self.assertRaises(OSError):
                save_session({'trips': 1}, 'Me', 'first', Path(temporary))
            self.assertEqual(list(Path(temporary).rglob('*.json')), [])

    def test_journal_limit_prevents_actions_but_preserves_outcomes(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(storage_limits, 'JOURNAL_MAX_FILES', 1):
            root = Path(temporary)
            journal = AutomationJournal(root)
            journal.append({'run_id': 'run', 'type': 'intent', 'action_id': 'action'})
            for kind in ('run_started', 'intent', 'dispatching'):
                with self.assertRaisesRegex(OSError, 'limit'):
                    journal.append({'run_id': 'run', 'type': kind, 'action_id': 'next'})
            for kind in ('dispatched', 'acknowledged', 'uncertain', 'projected', 'resolved', 'run_stopped'):
                journal.append({'run_id': 'run', 'type': kind, 'action_id': 'action'})
            self.assertEqual(len(journal.events()[0]), 7)
            restarted = AutomationJournal(root)
            with self.assertRaises(OSError):
                restarted.append({'run_id': 'next', 'type': 'run_started'})

    def test_journal_bytes_and_usage_scan_only_once(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(storage_limits, 'JOURNAL_MAX_BYTES', 1):
            journal = AutomationJournal(Path(temporary))
            with self.assertRaisesRegex(OSError, 'limit'):
                journal.append({'run_id': 'run', 'type': 'intent'})
            self.assertEqual(list(Path(temporary).rglob('*.json')), [])

    def test_journal_usage_reuses_initial_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = AutomationJournal(Path(temporary))
            with mock.patch.object(journal, '_load_usage', wraps=journal._load_usage) as scan:
                journal.append({'run_id': 'run', 'type': 'run_started'})
                journal.append({'run_id': 'run', 'type': 'intent', 'action_id': 'one'})
                journal.append({'run_id': 'run', 'type': 'dispatching', 'action_id': 'one'})
                self.assertEqual(scan.call_count, 1)
            files = list(Path(temporary).rglob('*.json'))
            self.assertEqual(journal.usage, (3, sum(path.stat().st_size for path in files)))

    def test_sqlite_disk_error_translates_to_oserror(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch('src.session_index.sqlite3.connect', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk'):
                save_session({'trips': 1}, 'Me', 'first', Path(temporary))


class HistoryQuotaControllerTests(TestCase):
    def test_journal_quota_stops_the_run_instead_of_leaving_a_failed_poll_loop(self):
        from tests import test_automation_controller as fixtures
        fixture = fixtures.ControllerTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        controller = fixture.make()
        controller.start()
        with mock.patch.object(storage_limits,'JOURNAL_MAX_FILES',0):
            controller.tick()
        self.assertEqual(controller.phase,'FAILED')
        self.assertFalse(controller.running)
        self.assertTrue(controller.stop_event.is_set())
        self.assertIn('storage limit',controller.reason)

class JournalUsageRefreshTests(TestCase):
    def test_archiving_frees_quota_without_restarting_journal(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(storage_limits, 'JOURNAL_MAX_FILES', 1):
            root = Path(temporary)
            journal = AutomationJournal(root)
            journal.append({'run_id': 'old', 'type': 'intent'})
            with self.assertRaises(OSError):
                journal.append({'run_id': 'new', 'type': 'intent'})
            for path in root.rglob('*.json'):
                path.unlink()
            journal.append({'run_id': 'new', 'type': 'intent'})
            self.assertEqual(len(list(root.rglob('*.json'))), 1)

    def test_run_start_refreshes_usage_from_other_instances(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(storage_limits, 'JOURNAL_MAX_FILES', 2):
            root = Path(temporary)
            journal = AutomationJournal(root)
            journal.append({'run_id': 'one', 'type': 'run_started'})
            AutomationJournal(root).append({'run_id': 'two', 'type': 'run_started'})
            with self.assertRaisesRegex(OSError, 'limit'):
                journal.append({'run_id': 'three', 'type': 'run_started'})
            self.assertEqual(len(list(root.rglob('*.json'))), 2)
