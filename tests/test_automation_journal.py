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
