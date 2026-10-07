from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest

from src.automation_controller import AutomationController
from src.automation_journal import AutomationJournal
from src.automation_models import AutomationConfig, ContextStatus
from src.automation_transport import SimulationTransport
from src.fishing_sessions import load_sessions
from src.review_queue import list_items

CONFIG = AutomationConfig(account='sim', username='sim0001', context_id='sim:dm')
STEP = 0.1


class Crash(BaseException):
    """Stands in for the process dying: nothing in the controller catches it."""


class FaultyJournal(AutomationJournal):
    """Raises `error` instead of writing the `nth` event of type `on`."""

    def __init__(self, root, on=None, nth=1, error=Crash):
        super().__init__(root)
        self.on, self.nth, self.error, self.seen = on, nth, error, 0

    def append(self, event):
        if event['type'] == self.on:
            self.seen += 1
            if self.seen == self.nth:
                raise self.error(f'fault writing {self.on}')
        super().append(event)


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def utc(self):
        return datetime(2026, 10, 6, 12, tzinfo=timezone.utc) + timedelta(seconds=self.now)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.journal_root, self.fishing, self.review = root / 'journal', root / 'fishing', root / 'review'
        self.clock = Clock()
        self.sim = SimulationTransport(self.clock.monotonic, self.clock.utc, cooldown=3.5, latency=0.4)
        self.checks = []
        original = self.sim.send

        def checked_send(intent):
            # At every hand-over: the intent is already journaled, and no other command is unresolved.
            events = AutomationJournal(self.journal_root).recover()['actions']
            pending = self.controller.state.pending  # reserved for this very command, and nothing else
            self.checks.append((events.get(intent.action_id, {}).get('status'),
                                None if pending == intent else pending or 'not reserved', self.clock.now))
            return original(intent)

        self.sim.send = checked_send

    def tearDown(self):
        self.temporary.cleanup()

    def make(self, journal=None, config=CONFIG):
        self.controller = AutomationController(self.sim, config, journal or AutomationJournal(self.journal_root),
                                               self.fishing, self.review, monotonic=self.clock.monotonic,
                                               utc=self.clock.utc)
        return self.controller

    def run_for(self, seconds, controller=None):
        controller = controller or self.controller
        end = self.clock.now + seconds
        while self.clock.now < end and controller.status()['running']:
            controller.tick()
            self.clock.now = round(self.clock.now + STEP, 6)

    def saved_trips(self):
        return sum(session['tally']['trips'] for session in load_sessions('sim', self.fishing)[0])

    def fish_sends(self):
        return [time for time, intent in self.sim.sent if intent.kind == 'fish']

    # --- the normal cycle ---

    def test_bootstraps_then_fishes_with_intent_journaled_before_each_send(self):
        self.make(config=replace(CONFIG, max_trips=3)).start()
        self.run_for(30)
        self.assertEqual([intent.kind for _, intent in self.sim.sent],
                         ['refresh_profile', 'refresh_buffs', 'fish', 'fish', 'fish'])
        self.assertTrue(all(status == 'dispatching' for status, _, _ in self.checks), self.checks)
        self.assertTrue(all(pending is None for _, pending, _ in self.checks), 'never two unresolved commands')
        self.assertEqual(self.saved_trips(), 3)
        status = self.controller.status()
        self.assertEqual((status['running'], status['trips']), (False, 3))
        self.assertIn('trip limit', status['reason'])

    def test_fishing_waits_for_the_verified_cooldown_plus_margin(self):
        self.make(config=replace(CONFIG, max_trips=10)).start()
        self.run_for(60)
        sends = self.fish_sends()
        gaps = [later - earlier for earlier, later in zip(sends, sends[1:])]
        self.assertTrue(all(gap >= 3.5 + 0.5 - 1e-9 for gap in gaps), gaps)
        self.assertLess(max(gaps), 3.5 + 0.5 + 0.2, 'and not much longer')

    def test_cooldown_reply_is_not_a_trip_and_resets_the_wait(self):
        self.make(config=replace(CONFIG, max_trips=4, cooldown_margin=-2.0)).start()  # deliberately too early
        self.run_for(60)
        self.assertEqual(self.saved_trips(), 4)
        self.assertEqual(self.sim.totals['trips'], 4)
        results = [info.get('result') for info in AutomationJournal(self.journal_root).recover()['actions'].values()]
        self.assertIn('cooldown', results)

    def test_another_players_reply_and_duplicates_are_not_counted(self):
        self.sim.script = {2: 'other_player_first', 3: 'duplicate'}
        self.make(config=replace(CONFIG, max_trips=4)).start()
        self.run_for(40)
        self.assertEqual(self.saved_trips(), 4)
        self.assertEqual(self.sim.totals['trips'], 4)

    def test_reply_without_correlation_evidence_is_not_acknowledged(self):
        self.sim.script = {2: 'uncorrelated'}
        self.make().start()
        self.run_for(40)
        status = self.controller.status()
        self.assertEqual(status['phase'], 'NEEDS_REVIEW')
        self.assertEqual(len(self.fish_sends()), 1, 'nothing more is sent while the outcome is uncertain')
        self.assertEqual(self.saved_trips(), 0)

    def test_unreadable_result_pauses_and_goes_to_review(self):
        self.sim.script = {3: 'unreadable'}
        self.make().start()
        self.run_for(40)
        self.assertEqual(self.controller.status()['phase'], 'NEEDS_REVIEW')
        self.assertEqual(len(self.fish_sends()), 2)
        self.assertEqual(self.saved_trips(), 1)
        self.assertEqual([item['kind'] for item in list_items(self.review)[0]], ['fishing'])

    def test_unreadable_refresh_is_not_a_successful_bootstrap(self):
        for index, kind in ((0, 'profile'), (1, 'buffs')):
            with self.subTest(kind=kind):
                self.setUp()
                self.sim.script = {index: 'unreadable'}
                self.make().start()
                self.run_for(20)
                self.assertEqual(len(self.sim.sent), index + 1, 'nothing is sent after the unreadable reply')
                status = self.controller.status()
                self.assertEqual(status['phase'], 'NEEDS_REVIEW')
                self.assertIsNone(self.controller.state.profile if kind == 'profile'
                                  else self.controller.state.cooldown_seconds)
                [item] = list_items(self.review)[0]
                self.assertEqual(item['kind'], kind)
                result = AutomationJournal(self.journal_root).recover()['actions'][status['unresolved'][0]]
                self.assertEqual(result['result'], 'unreadable')
                self.tearDown()
        self.setUp()

    def test_wrong_context_pauses_without_sending(self):
        self.sim.context = ContextStatus(False, 'sim:dm', 'Discord is minimized')
        self.make().start()
        self.run_for(10)
        self.assertEqual(self.sim.sent, [])
        self.assertEqual(self.controller.status()['phase'], 'PAUSED')
        self.sim.context = ContextStatus(True, 'other:channel')
        self.run_for(10)
        self.assertEqual(self.sim.sent, [], 'a different channel is not the configured one')

    # --- failures: never a blind resend ---

    def test_missing_reply_times_out_into_uncertain_and_is_not_resent(self):
        self.sim.script = {2: 'missing'}
        self.make().start()
        self.run_for(19)
        self.assertEqual(self.controller.status()['phase'], 'WAITING_REPLY')
        self.run_for(60)
        status = self.controller.status()
        self.assertEqual(status['phase'], 'NEEDS_REVIEW')
        self.assertIn('20', status['reason'])
        self.assertEqual(len(self.fish_sends()), 1)
        [action] = status['unresolved']
        self.controller.resolve(action, 'happened')
        self.run_for(10)
        self.assertGreater(len(self.fish_sends()), 1, 'fishing resumes once a person has resolved it')

    def test_crash_before_send_is_forgotten_safely(self):
        self.make(journal=FaultyJournal(self.journal_root, on='dispatching', nth=3)).start()
        with self.assertRaises(Crash):
            self.run_for(10)
        self.assertEqual(len(self.fish_sends()), 0, 'the fish command was journaled but never handed over')
        restarted = self.make(config=replace(CONFIG, max_trips=2))
        restarted.start()
        self.assertEqual(restarted.status()['unresolved'], [])
        self.run_for(20)
        self.assertEqual((self.saved_trips(), self.sim.totals['trips']), (2, 2))

    def test_crash_after_send_before_receipt_reconciles_from_the_visible_reply(self):
        self.make(journal=FaultyJournal(self.journal_root, on='dispatched', nth=3)).start()
        with self.assertRaises(Crash):
            self.run_for(10)
        self.assertEqual(len(self.fish_sends()), 1)
        restarted = self.make(config=replace(CONFIG, max_trips=3))
        restarted.start()
        self.assertEqual(restarted.status()['phase'], 'NEEDS_REVIEW', 'uncertain until evidence is seen')
        self.run_for(30)
        self.assertEqual(self.sim.totals['trips'], 4, 'the reconciled trip plus three new ones')
        self.assertEqual(self.saved_trips(), 4, 'the crashed trip is counted once, from its reply')

    def test_crash_after_send_with_no_reply_stays_uncertain(self):
        self.sim.script = {2: 'missing'}
        self.make(journal=FaultyJournal(self.journal_root, on='dispatched', nth=3)).start()
        with self.assertRaises(Crash):
            self.run_for(10)
        restarted = self.make()
        restarted.start()
        self.run_for(60)
        self.assertEqual(len(self.fish_sends()), 1, 'zero blind resends')
        self.assertEqual(restarted.status()['phase'], 'NEEDS_REVIEW')

    def test_crash_after_receipt_before_acknowledgment(self):
        self.make(journal=FaultyJournal(self.journal_root, on='acknowledged', nth=3)).start()
        with self.assertRaises(Crash):
            self.run_for(10)
        restarted = self.make(config=replace(CONFIG, max_trips=1))
        restarted.start()
        self.run_for(20)
        self.assertEqual((self.sim.totals['trips'], self.saved_trips()), (2, 2))

    def test_crash_before_projection_is_projected_once_on_restart(self):
        self.make(journal=FaultyJournal(self.journal_root, on='projected', nth=1)).start()
        with self.assertRaises(Crash):
            self.run_for(10)
        self.assertEqual(self.saved_trips(), 1, 'saved, but the projection was not journaled')
        restarted = self.make(config=replace(CONFIG, max_trips=1))
        restarted.start()
        self.run_for(20)
        self.assertEqual((self.sim.totals['trips'], self.saved_trips()), (2, 2), 'not saved twice')

    def test_journal_write_failure_before_send_sends_nothing(self):
        self.make(journal=FaultyJournal(self.journal_root, on='intent', nth=1, error=OSError)).start()
        self.run_for(10)
        self.assertEqual(self.sim.sent, [])
        self.assertEqual(self.controller.status()['phase'], 'FAILED')

    def test_journal_write_failure_after_send_is_uncertain(self):
        self.make(journal=FaultyJournal(self.journal_root, on='dispatched', nth=3, error=OSError)).start()
        self.run_for(30)
        self.assertEqual(len(self.fish_sends()), 1)
        self.assertIn(self.controller.status()['phase'], ('FAILED', 'NEEDS_REVIEW'))

    def test_send_failure_is_uncertain_not_retried(self):
        self.sim.script = {2: 'send_error'}
        self.make().start()
        self.run_for(30)
        self.assertEqual(len(self.fish_sends()), 1)
        self.assertEqual(self.controller.status()['phase'], 'NEEDS_REVIEW')

    def test_stop_during_a_blocked_send_prevents_later_dispatch(self):
        entered, release = threading.Event(), threading.Event()
        inner = self.sim.send

        def blocking(intent):
            if intent.kind == 'fish':
                entered.set()
                release.wait(5)
            return inner(intent)

        self.sim.send = blocking
        controller = self.make()
        controller.start()
        worker = threading.Thread(target=lambda: self.run_for(30, controller))
        worker.start()
        self.assertTrue(entered.wait(5))
        controller.stop()
        release.set()
        worker.join(5)
        self.assertEqual(len(self.fish_sends()), 1, 'the command in flight was recorded; nothing after it')
        recovered = AutomationJournal(self.journal_root).recover()
        fish = [info for info in recovered['actions'].values() if info.get('intent', {}).get('kind') == 'fish']
        self.assertEqual(len(fish), 1)
        self.assertNotEqual(fish[0]['status'], 'intended', 'its hand-over is in the journal')

    def test_concurrent_ticks_send_one_command(self):
        entered, release = threading.Event(), threading.Event()
        inner = self.sim.send

        def blocking(intent):
            entered.set()
            release.wait(5)
            return inner(intent)

        self.sim.send = blocking
        controller = self.make()
        controller.start()
        first = threading.Thread(target=controller.tick)
        first.start()
        self.assertTrue(entered.wait(5))
        second = threading.Thread(target=controller.tick)  # e.g. the page's thread and the worker together
        second.start()
        second.join(5)
        release.set()
        first.join(5)
        self.assertEqual([intent.kind for _, intent in self.sim.sent], ['refresh_profile'])
        self.assertEqual(controller.status()['pending'], self.sim.sent[0][1].action_id)

    def test_damaged_journal_blocks_sending_until_resolved(self):
        self.make(journal=FaultyJournal(self.journal_root, on='dispatched', nth=3)).start()
        with self.assertRaises(Crash):
            self.run_for(10)
        dispatching = [path for path in self.journal_root.rglob('*.json')
                       if '"dispatching"' in path.read_text(encoding='utf-8')][-1]
        dispatching.write_text('{"schema_version": 1, "type": "dispa', encoding='utf-8')  # torn write
        sends = len(self.sim.sent)
        restarted = self.make()
        restarted.start()
        self.run_for(30)
        status = restarted.status()
        self.assertEqual(len(self.sim.sent), sends, 'a possibly sent command is not treated as unsent')
        self.assertEqual(status['phase'], 'NEEDS_REVIEW')
        self.assertIn('damaged-journal', status['unresolved'])
        restarted.resolve('damaged-journal', 'happened')
        self.run_for(10)
        self.assertGreater(len(self.sim.sent), sends, 'sending resumes once a person has checked')

    # --- acceptance ---

    def test_one_hundred_simulated_trips(self):
        self.make(config=replace(CONFIG, max_trips=100)).start()
        self.run_for(1000)
        recovered = AutomationJournal(self.journal_root).recover()
        trips = [info for info in recovered['actions'].values() if info.get('result') == 'trip']
        self.assertEqual(len(trips), 100, '100 acknowledgments')
        sessions = load_sessions('sim', self.fishing)[0]
        catches = {}
        for session in sessions:
            for fish, count in session['tally']['catches'].items():
                catches[fish] = catches.get(fish, 0) + count
        self.assertEqual(self.saved_trips(), 100)
        self.assertEqual(catches, self.sim.totals['catches'], 'the logbook tally is exact')
        self.assertEqual(len(self.fish_sends()), 100, 'no extra sends')
        self.assertTrue(all(pending is None for _, pending, _ in self.checks), 'no overlapping commands')
        gaps = [b - a for a, b in zip(self.fish_sends(), self.fish_sends()[1:])]
        self.assertGreaterEqual(min(gaps), 4.0 - 1e-9, 'cooldown spacing')

    def test_one_hundred_trips_across_a_crash_and_restart(self):
        self.make(journal=FaultyJournal(self.journal_root, on='dispatched', nth=52),
                  config=replace(CONFIG, max_trips=100)).start()
        with self.assertRaises(Crash):
            self.run_for(1000)
        done = self.saved_trips()
        restarted = self.make(config=replace(CONFIG, max_trips=100 - done - 1))  # one trip awaits reconciliation
        restarted.start()
        self.run_for(1000)
        self.assertEqual(self.saved_trips(), 100)
        self.assertEqual(self.sim.totals['trips'], 100)
        self.assertEqual(len(self.fish_sends()), 100)


if __name__ == '__main__':
    unittest.main()
