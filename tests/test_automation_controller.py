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
        self.make(config=replace(CONFIG, max_trips=10, jitter_max=0)).start()
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

    def kinds(self):
        return [intent.kind for _, intent in self.sim.sent]

    def test_unreadable_profile_is_set_aside_and_retried_every_10_trips(self):
        # The same misread twice gives up at once; fishing goes on and /profile is tried again 10 trips later.
        self.sim.script = {0: 'unreadable_fixed', 1: 'unreadable_fixed'}
        self.make(config=replace(CONFIG, max_trips=12)).start()
        self.run_for(120)
        self.assertEqual(self.kinds(), ['refresh_profile', 'refresh_profile', 'refresh_buffs'] + ['fish'] * 10
                         + ['refresh_profile', 'fish', 'fish'])
        self.assertEqual(self.controller.state.profile['balance'], 1000, 'the retry read it')
        self.assertTrue(all(not item['blocks_actions'] for item in list_items(self.review)[0]))

    def test_unreadable_buffs_fishes_on_an_assumed_cooldown_until_read(self):
        self.sim.script = {1: 'unreadable_fixed', 2: 'unreadable_fixed', 13: 'unreadable_fixed'}
        self.make(config=replace(CONFIG, max_trips=21)).start()
        self.run_for(240)
        kinds = self.kinds()
        self.assertEqual(kinds[:3], ['refresh_profile', 'refresh_buffs', 'refresh_buffs'])
        self.assertEqual(kinds[3:13], ['fish'] * 10)
        self.assertEqual(kinds[13], 'refresh_buffs', 'tried again after 10 trips; this read fails too')
        self.assertEqual(kinds[24], 'refresh_buffs', 'and again 10 trips later, which reads')
        self.assertEqual(self.controller.state.cooldown_seconds, 3.5)
        self.assertNotIn(self.controller.status()['phase'], ('NEEDS_REVIEW', 'FAILED'))

    def test_startup_retries_only_failed_command_then_fishes(self):
        self.sim.script = {1: 'unreadable', 2: 'unreadable'}
        self.make(config=replace(CONFIG, max_trips=1)).start()
        self.run_for(30)
        self.assertEqual([intent.kind for _, intent in self.sim.sent],
                         ['refresh_profile', 'refresh_buffs', 'refresh_buffs', 'refresh_buffs', 'fish'])
        self.assertEqual(self.saved_trips(), 1)
        self.assertFalse(self.controller.status()['unresolved'])
        self.assertTrue(all(not item['blocks_actions'] for item in list_items(self.review)[0]))
        buffs_times = [at for at, intent in self.sim.sent if intent.kind == 'refresh_buffs']
        self.assertTrue(all(b - a >= 2.4 - STEP for a, b in zip(buffs_times, buffs_times[1:])))

    def test_new_launch_fetches_fresh_data_and_unbounded_run_stops_on_request(self):
        self.make().start()
        self.run_for(12)
        self.controller.stop()
        previous = len(self.sim.sent)
        self.make().start()
        self.run_for(12)
        self.assertEqual([intent.kind for _, intent in self.sim.sent[previous:]][:2],
                         ['refresh_profile', 'refresh_buffs'])
        self.assertTrue(self.controller.running)
        self.controller.stop()
        sends = len(self.sim.sent)
        self.run_for(20)
        self.assertEqual(len(self.sim.sent), sends)

    def test_missing_startup_reply_is_asked_again_after_the_timeout(self):
        self.sim.script = {0: 'missing'}
        self.make(config=replace(CONFIG, max_trips=1)).start()
        self.run_for(40)
        kinds = [intent.kind for _, intent in self.sim.sent]
        self.assertEqual(kinds[:3], ['refresh_profile', 'refresh_profile', 'refresh_buffs'])
        self.assertGreaterEqual(self.sim.sent[1][0] - self.sim.sent[0][0], 20)
        self.assertEqual(self.saved_trips(), 1)

    def test_discord_sending_wait_is_bounded_and_never_resends(self):
        self.sim.script = {0: 'missing'}
        self.sim.sending_seen = True
        self.make().start()
        self.run_for(30)
        self.assertEqual(self.controller.status()['phase'], 'WAITING_DELIVERY')
        self.assertEqual(len(self.sim.sent), 1)
        self.run_for(35)
        self.assertGreaterEqual(self.sim.sent[1][0] - self.sim.sent[0][0], 60, 'waits the full delivery bound')

    def test_timeout_preserves_transport_failure_reason(self):
        self.sim.script = {0: 'missing'}
        self.make().start()
        self.run_for(1)
        self.sim.context = ContextStatus(False, 'sim:dm', 'Reply placement uncertain: overlap lost')
        self.run_for(40)
        self.assertIn('overlap lost', self.controller.status()['reason'])
        self.assertEqual(len(self.sim.sent), 1)

    def test_profile_retry_recovers_before_buffs_and_stop_cancels_retry(self):
        self.sim.script = {0: 'unreadable'}
        self.make(config=replace(CONFIG, max_trips=1)).start()
        self.run_for(20)
        self.assertEqual([intent.kind for _, intent in self.sim.sent],
                         ['refresh_profile', 'refresh_profile', 'refresh_buffs', 'fish'])
        self.assertEqual(self.saved_trips(), 1)

    def test_stop_during_startup_retry_sends_nothing_more(self):
        self.sim.script = {0: 'unreadable'}
        self.make().start()
        self.run_for(1)
        self.assertEqual(self.controller.status()['phase'], 'RETRYING_STARTUP')
        self.assertEqual(len(self.sim.sent), 1)
        self.controller.stop()
        self.run_for(20)
        self.assertEqual(len(self.sim.sent), 1)
        self.assertFalse(AutomationJournal(self.journal_root).recover()['unresolved'])

    def test_startup_ignores_optional_ocr_and_fishes_with_required_values(self):
        original = self.sim._play
        def play(intent, now):
            if intent.kind == 'refresh_profile':
                return ('Inventory of sim\nBalance: $50.\nP4 Level 30, xp to next leveL\nFish Invent«y\na777 Salmon', 'profile')
            if intent.kind == 'refresh_buffs':
                return ("sim0001's current multipliers (including bait):\nFish quality: ??\nFishing cooldown: 3.5s.", 'buffs')
            return original(intent, now)
        self.sim._play = play
        self.make(config=replace(CONFIG, max_trips=1)).start()
        self.run_for(20)
        self.assertEqual([intent.kind for _, intent in self.sim.sent], ['refresh_profile', 'refresh_buffs', 'fish'])
        self.assertEqual(self.saved_trips(), 1)

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

    def test_pause_holds_sending_and_resume_carries_on(self):
        self.make(config=replace(CONFIG, max_trips=4)).start()
        self.run_for(10)
        self.controller.pause()
        sent = len(self.sim.sent)
        self.run_for(30)
        status = self.controller.status()
        self.assertTrue(status['paused'])
        self.assertEqual(status['phase'], 'PAUSED_BY_YOU')
        self.assertLessEqual(len(self.sim.sent), sent, 'nothing more is sent while paused')
        self.assertEqual(self.controller.status()['pending'], None, 'a reply already on its way is still read')
        self.controller.resume()
        self.run_for(30)
        self.assertEqual(self.saved_trips(), 4)
        self.assertFalse(self.controller.status()['paused'])

    # --- live stats and periodic resync ---

    def test_profile_and_buffs_are_resynced_every_n_trips(self):
        self.make(config=replace(CONFIG, max_trips=5, resync_trips=3)).start()
        self.run_for(60)
        self.assertEqual([intent.kind for _, intent in self.sim.sent],
                         ['refresh_profile', 'refresh_buffs', 'fish', 'fish', 'fish',
                          'refresh_profile', 'refresh_buffs', 'fish', 'fish'])
        self.assertEqual(self.controller.status()['dashboard']['trips_since_sync'], 2)

    def test_unreadable_resync_never_stops_fishing(self):
        self.sim.script = {5: 'unreadable_fixed', 6: 'unreadable_fixed'}
        self.make(config=replace(CONFIG, max_trips=5, resync_trips=3)).start()
        self.run_for(60)
        self.assertEqual(self.saved_trips(), 5)
        self.assertTrue(all(not item['blocks_actions'] for item in list_items(self.review)[0]))

    def test_profile_with_unread_xp_is_read_again_after_a_few_trips(self):
        self.sim.script = {0: 'xp_unread'}
        self.make(config=replace(CONFIG, max_trips=4, resync_trips=100, retry_unread_trips=2)).start()
        self.run_for(60)
        self.assertEqual([intent.kind for _, intent in self.sim.sent],
                         ['refresh_profile', 'refresh_buffs', 'fish', 'fish', 'refresh_profile', 'fish', 'fish'])
        profile = self.controller.status()['dashboard']['profile']
        self.assertEqual(profile['level'], 10)
        self.assertIsNotNone(profile['xp_current'], 'the second read showed the XP progress')
        self.assertEqual(self.saved_trips(), 4)

    def test_captcha_pauses_and_alerts_until_resumed(self):
        self.sim.script = {3: 'captcha'}
        alerts = []
        self.make(config=replace(CONFIG, max_trips=5))
        self.controller.alert = alerts.append
        self.controller.start()
        self.run_for(30)
        status = self.controller.status()
        self.assertTrue(status['paused'])
        self.assertEqual(status['phase'], 'CAPTCHA')
        self.assertIn('/captcha', status['reason'])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(len(self.sim.sent), 4, 'nothing more is sent after the captcha')
        self.assertEqual(self.saved_trips(), 1, 'the captcha reply is not a trip')
        self.controller.resume()
        self.run_for(30)
        self.assertEqual(self.saved_trips(), 5)

    def test_each_fish_waits_a_random_extra_in_steps(self):
        self.make(config=replace(CONFIG, max_trips=12)).start()
        self.run_for(80)
        fish = self.fish_sends()
        gaps = [later - earlier - 3.5 - CONFIG.cooldown_margin for earlier, later in zip(fish, fish[1:])]
        self.assertTrue(all(-STEP <= gap <= 0.5 + 2 * STEP for gap in gaps), gaps)
        self.assertGreater(len({round(gap, 1) for gap in gaps}), 1, 'the extra wait varies')
        extras = self.controller.jitters
        self.assertTrue(all(0 <= extra <= 0.5 and abs(extra * 20 - round(extra * 20)) < 1e-9 for extra in extras))

    def test_dashboard_carries_synced_stats_forward(self):
        self.make(config=replace(CONFIG, max_trips=2)).start()
        self.run_for(30)
        dashboard = self.controller.status()['dashboard']
        self.assertEqual(dashboard['gained']['xp'], self.sim.totals['xp'])
        self.assertEqual(dashboard['gained']['trips'], 2)
        self.assertEqual(dashboard['profile']['account_name'], 'sim')
        self.assertEqual(dashboard['buffs']['fishing_cooldown']['value'], 3.5)
        self.assertEqual(sum(dashboard['changes']['fish'].values()), sum(self.sim.totals['catches'].values()))
        self.assertIn('xp', dashboard['per_hour'])
        self.assertEqual(set(dashboard['finds']), {'run', 'all'})
        self.assertEqual(dashboard['finds']['run']['chests'], {})

    # --- unknown outcomes: noted, never stop the run ---

    def assert_moves_on(self, outcome):
        self.sim.script = {2: outcome}
        self.make().start()
        self.run_for(60)
        status = self.controller.status()
        self.assertNotIn(status['phase'], ('NEEDS_REVIEW', 'FAILED'))
        sends = self.fish_sends()
        self.assertGreater(len(sends), 2, 'fishing continues')
        self.assertGreaterEqual(sends[1] - sends[0], 3.5, 'the next fish waits at least a cooldown')
        self.assertTrue(all(not item['blocks_actions'] for item in list_items(self.review)[0]))
        return status

    def test_missing_reply_is_noted_and_fishing_continues(self):
        self.assert_moves_on('missing')
        self.assertTrue(list_items(self.review)[0])

    def test_unreadable_result_is_noted_and_fishing_continues(self):
        self.assert_moves_on('unreadable')
        self.assertIn('fishing', [item['kind'] for item in list_items(self.review)[0]])

    def test_reply_without_correlation_is_noted_and_fishing_continues(self):
        self.assert_moves_on('uncorrelated')

    def test_send_failure_is_noted_and_fishing_continues(self):
        self.assert_moves_on('send_error')

    def test_readable_trip_with_a_garbled_extra_line_still_counts(self):
        # Observed 2026-10-09: one garbled level-up line made a whole trip (catch, XP, level-up) uncounted.
        self.sim.script = {3: 'noisy'}
        self.make(config=replace(CONFIG, max_trips=3)).start()
        self.run_for(30)
        self.assertEqual(self.saved_trips(), 3)
        self.assertEqual(self.controller.status()['dashboard']['gained']['xp'], self.sim.totals['xp'])
        [note] = list_items(self.review)[0]
        self.assertFalse(note['blocks_actions'])
        self.assertIn('the rest was counted', ' '.join(note['reasons']))

    def test_three_unknown_outcomes_in_a_row_stop_the_run(self):
        self.sim.script = {2: 'missing', 3: 'missing', 4: 'missing'}
        self.make().start()
        self.run_for(200)
        status = self.controller.status()
        self.assertEqual(status['phase'], 'FAILED')
        self.assertIn('3 commands in a row', status['reason'])
        self.assertEqual(len(self.fish_sends()), 3)

    def test_noted_outcome_is_not_carried_into_the_next_run(self):
        self.sim.script = {2: 'missing'}
        self.make(config=replace(CONFIG, max_trips=1)).start()
        self.run_for(60)
        self.make(config=replace(CONFIG, max_trips=1)).start()
        self.run_for(20)
        self.assertFalse([item for item in list_items(self.review)[0] if 'earlier run' in ' '.join(item['reasons'])])

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
        self.assertNotEqual(restarted.status()['phase'], 'NEEDS_REVIEW', 'an earlier run never blocks this one')
        self.run_for(30)
        self.assertEqual(self.sim.totals['trips'], 4, 'the reconciled trip plus three new ones')
        self.assertEqual(self.saved_trips(), 4, 'the crashed trip is counted once, from its reply')

    def test_crash_after_send_with_no_reply_is_noted_and_does_not_block(self):
        self.sim.script = {2: 'missing'}
        self.make(journal=FaultyJournal(self.journal_root, on='dispatched', nth=3)).start()
        with self.assertRaises(Crash):
            self.run_for(10)
        restarted = self.make(config=replace(CONFIG, max_trips=1))
        restarted.start()
        self.run_for(30)
        self.assertEqual(self.saved_trips(), 1)
        notes = [item for item in list_items(self.review)[0] if 'earlier run' in ' '.join(item['reasons'])]
        self.assertEqual(len(notes), 1)
        self.assertFalse(notes[0]['blocks_actions'])

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

    def test_refusal_before_input_pauses_then_resumes_in_the_same_run(self):
        # Observed 2026-10-08: Discord left the foreground just before a /fish, which ended a 36-trip run.
        from src.automation_models import NotDispatched
        original, refused = self.sim.send, []
        def refuse_while_away(intent):
            if self.clock.now < 10:
                refused.append(intent.kind)
                raise NotDispatched('Discord must remain in the foreground.')
            return original(intent)
        self.sim.send = refuse_while_away
        self.make(config=replace(CONFIG, max_trips=1)).start()
        self.run_for(5)
        status = self.controller.status()
        self.assertEqual(status['phase'], 'PAUSED')
        self.assertIn('foreground', status['reason'])
        self.assertLessEqual(len(refused), 5, 'retried calmly, not every tick')
        self.run_for(20)
        self.assertEqual(self.saved_trips(), 1, 'the same run carries on once Discord is back')
        self.assertFalse(list_items(self.review)[0], 'nothing was sent, so there is nothing to review')

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

    def test_damaged_journal_is_noted_and_does_not_block(self):
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
        self.assertGreater(len(self.sim.sent), sends)
        self.assertEqual(status['unresolved'], [])
        self.assertTrue(any('could not be read' in ' '.join(item['reasons']) for item in list_items(self.review)[0]))

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
