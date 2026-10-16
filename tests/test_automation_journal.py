import json
from pathlib import Path
import tempfile
import unittest

from src.automation_journal import JOURNAL_SCHEMA_VERSION, AutomationJournal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.journal = AutomationJournal(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def event(self, run, kind, action=None, **fields):
        self.journal.append({'run_id': run, 'type': kind, 'action_id': action, **fields})

    def test_events_are_immutable_files_with_identity(self):
        self.event('run1', 'run_started')
        [path] = list(self.root.rglob('*.json'))
        stored = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(stored['schema_version'], JOURNAL_SCHEMA_VERSION)
        self.assertTrue(stored['event_id'] and stored['at'])
        self.assertEqual((stored['run_id'], stored['type']), ('run1', 'run_started'))
        with self.assertRaises(ValueError):
            self.journal.append({'type': 'intent'})  # no run id

    def test_recover_reports_each_action_by_its_last_event(self):
        self.event('run1', 'intent', 'a1', intent={'kind': 'fish'})
        self.event('run1', 'dispatching', 'a1')
        self.event('run1', 'dispatched', 'a1')
        self.event('run1', 'acknowledged', 'a1', result='trip', tally={'trips': 1})
        self.event('run1', 'intent', 'a2', intent={'kind': 'fish'})
        self.event('run1', 'intent', 'a3', intent={'kind': 'fish'})
        self.event('run1', 'dispatching', 'a3')
        self.event('run1', 'intent', 'a4', intent={'kind': 'fish'})
        self.event('run1', 'dispatching', 'a4')
        self.event('run1', 'dispatched', 'a4')
        self.event('run1', 'uncertain', 'a4', reason='no reply')
        recovered = AutomationJournal(self.root).recover()
        actions = recovered['actions']
        self.assertEqual({action: actions[action]['status'] for action in actions},
                         {'a1': 'acknowledged', 'a2': 'intended', 'a3': 'dispatching', 'a4': 'uncertain'})
        self.assertEqual(recovered['never_sent'], ['a2'])
        self.assertEqual(recovered['unresolved'], ['a3', 'a4'], 'possibly sent, outcome unknown')
        self.assertEqual(recovered['unprojected'], ['a1'])
        self.assertEqual(actions['a1']['tally'], {'trips': 1})

    def test_later_runs_continue_earlier_actions_in_order(self):
        self.event('20261006T120000Z-aaaa', 'intent', 'a1', intent={})
        self.event('20261006T120000Z-aaaa', 'dispatching', 'a1')
        self.event('20261006T130000Z-bbbb', 'resolved', 'a1', outcome='happened')
        self.assertEqual(AutomationJournal(self.root).recover()['actions']['a1']['status'], 'resolved')

    def test_unreadable_event_files_are_reported_not_trusted(self):
        self.event('run1', 'intent', 'a1', intent={})
        (self.root / 'run1' / '999999-broken.json').write_text('{', encoding='utf-8')
        recovered = AutomationJournal(self.root).recover()
        self.assertEqual(len(recovered['warnings']), 1)
        self.assertEqual(recovered['actions']['a1']['status'], 'intended')
        self.assertEqual(recovered['damaged'], ['run1/999999-broken.json'], 'reported until a person checks it')
        self.event('run2', 'journal_damage_resolved', outcome='happened', files=recovered['damaged'])
        self.assertEqual(AutomationJournal(self.root).recover()['damaged'], [])


if __name__ == '__main__':
    unittest.main()


class RecoveryCacheTests(unittest.TestCase):
    # Every Start recovered the whole journal: 90,374 events took 8.9 s live (2026-10-16), longer each day. Runs whose
    # files have not changed are not read again.
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.journal = AutomationJournal(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def event(self, run, kind, action=None, **fields):
        self.journal.append({'run_id': run, 'type': kind, 'action_id': action, **fields})

    def history(self):
        self.event('20261006T120000Z-aaaa', 'intent', 'a1', intent={'kind': 'fish'})
        self.event('20261006T120000Z-aaaa', 'dispatching', 'a1')
        self.event('20261006T120000Z-aaaa', 'intent', 'a2', intent={'kind': 'fish'})
        self.event('20261006T120000Z-aaaa', 'dispatching', 'a2')
        self.event('20261006T120000Z-aaaa', 'dispatched', 'a2')
        self.event('20261006T120000Z-aaaa', 'acknowledged', 'a2', result='trip', tally={'trips': 1})
        self.event('20261006T130000Z-bbbb', 'resolved', 'a1', outcome='unknown', reason='carried')
        (self.root / '20261006T130000Z-bbbb' / '000099-broken.json').write_text('{', encoding='utf-8')
        self.event('20261006T130000Z-bbbb', 'journal_damage_resolved', None, files=['20261006T120000Z-zzzz/x.json'])

    def reads(self, journal):
        from unittest import mock
        real = Path.read_text
        with mock.patch.object(Path, 'read_text', autospec=True, side_effect=real) as read:
            result = journal.recover()
        return result, read.call_count

    def test_cached_recovery_equals_a_full_read(self):
        from src import automation_journal
        self.history()
        first, _ = self.reads(AutomationJournal(self.root))
        automation_journal._RECOVERY.clear()
        fresh, _ = self.reads(AutomationJournal(self.root))
        again, count = self.reads(AutomationJournal(self.root))
        self.assertEqual(first, fresh)
        self.assertEqual(again, fresh)
        self.assertEqual(count, 0, 'nothing changed: nothing is read again')
        self.assertEqual(fresh['actions']['a1']['status'], 'resolved')
        self.assertEqual(fresh['unprojected'], ['a2'])
        self.assertEqual(len(fresh['warnings']), 1)

    def test_only_a_changed_run_is_read_again(self):
        self.history()
        AutomationJournal(self.root).recover()
        self.event('20261006T130000Z-bbbb', 'projected', 'a2')
        recovered, count = self.reads(AutomationJournal(self.root))
        run_files = len(list((self.root / '20261006T130000Z-bbbb').glob('*.json')))
        self.assertEqual(count, run_files, 'the earlier run is not read again')
        self.assertEqual(recovered['actions']['a2']['status'], 'projected')
        self.assertEqual(recovered['unprojected'], [])

    def test_callers_cannot_change_the_cache(self):
        self.history()
        AutomationJournal(self.root).recover()['actions']['a2']['tally']['trips'] = 99
        self.assertEqual(AutomationJournal(self.root).recover()['actions']['a2']['tally'], {'trips': 1})


class LaunchWarmupTests(unittest.TestCase):
    def test_launch_warms_the_saved_accounts_journal_in_the_background(self):
        from src import automation_journal
        from src.player_snapshots import account_key
        from src.player_web import warm_recovery
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            AutomationJournal(root / account_key('epicmouse') / 'journal').append(
                {'run_id': 'run1', 'type': 'intent', 'action_id': 'a1', 'intent': {}})
            warm_recovery(root, 'epicmouse').join(5)
            key = str((root / account_key('epicmouse') / 'journal').resolve())
            self.assertIn('run1', automation_journal._RECOVERY[key])
            self.assertIsNone(warm_recovery(root, None), 'no saved account: nothing to warm')
