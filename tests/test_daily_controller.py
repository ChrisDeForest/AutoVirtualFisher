"""Part 8: /daily, the Duplicator emerald reserve and the vote reminder in the controller (simulation transport)."""
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

import src.automation_controller as controller_module
from src.automation_controller import AutomationController
from src.automation_journal import AutomationJournal
from src.automation_models import AutomationConfig
from src.automation_transport import SimulationTransport
from tests.test_automation_controller import Clock

CONFIG = AutomationConfig(account='sim', username='sim0001', context_id='sim:dm',
                          capabilities=('refresh_profile', 'refresh_buffs', 'refresh_boosts', 'refresh_shop_boosts',
                                        'refresh_quests', 'fish', 'act'))
STEP = 0.5
# The fake clock starts at 2026-10-06 12:00 UTC: the next 00:00 UTC is 12 hours in.
TO_MIDNIGHT = 12 * 3600


class ScriptedDaily(SimulationTransport):
    """The simulation, with a set reply to /daily when `daily_reply` is given."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.daily_reply = None

    def _act(self, intent):
        if intent.arguments[0].get('act') == 'daily' and self.daily_reply is not None:
            return self.daily_reply
        return super()._act(intent)


class DailyControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.journal_root, self.fishing, self.review = root / 'journal', root / 'fishing', root / 'review'
        self.state_path = root / 'accounts' / 'sim' / 'account_state.json'
        self.clock = Clock()
        self.sim = ScriptedDaily(self.clock.monotonic, self.clock.utc, cooldown=3.5, latency=0.4)

    def tearDown(self):
        self.temporary.cleanup()

    def make(self, config=CONFIG):
        self.controller = AutomationController(self.sim, config, AutomationJournal(self.journal_root), self.fishing,
                                               self.review, monotonic=self.clock.monotonic, utc=self.clock.utc,
                                               state_path=self.state_path, plan_spawn=lambda work: None)
        return self.controller

    def run_for(self, seconds):
        end = self.clock.now + seconds
        while self.clock.now < end and self.controller.running:
            self.controller.tick()
            self.clock.now = round(self.clock.now + STEP, 6)

    def jump(self, seconds):
        while self.controller.state.pending is not None and self.controller.running:
            self.controller.tick()
            self.clock.now = round(self.clock.now + STEP, 6)
        self.clock.now = round(self.clock.now + seconds, 6)

    def daily_sends(self):
        return sum(intent.kind == 'act' and intent.arguments[0].get('act') == 'daily' for _, intent in self.sim.sent)

    # --- settings ---

    def test_settings_defaults_and_round_trip(self):
        controller = self.make()
        self.assertTrue(controller.claim_daily, 'Claim daily is on by default')
        self.assertIsNone(controller.voted_at)
        controller.set_follow(True)
        controller.set_boosts(True)
        controller.set_boost_order(['Auto30m'])
        controller.set_claim_daily(False)
        controller.mark_voted()
        again = self.make()
        self.assertFalse(again.claim_daily)
        self.assertEqual(again.voted_at, self.clock.utc())
        self.assertTrue(again.follow_plan)
        self.assertTrue(again.keep_boosts)
        self.assertEqual(again.boost_order[0], 'Auto30m')
        again.set_follow(False)
        third = self.make()
        self.assertFalse(third.claim_daily)
        self.assertEqual(third.voted_at, self.clock.utc())
        saved = json.loads((self.state_path.parent / 'executor.json').read_text(encoding='utf-8'))
        self.assertEqual(set(saved), {'follow_plan', 'keep_boosts', 'boost_order', 'claim_daily', 'daily',
                                      'voted_at'})

    def test_a_saved_false_string_is_off(self):
        # Final review: bool('false') is True; only a saved false (or "false") turns Claim daily off.
        self.state_path.parent.mkdir(parents=True)
        for saved, on in ((False, False), ('false', False), (True, True), (None, True)):
            (self.state_path.parent / 'executor.json').write_text(json.dumps({'claim_daily': saved}),
                                                                   encoding='utf-8')
            self.assertEqual(self.make().claim_daily, on, saved)

    def test_an_unreadable_settings_file_claims_daily(self):
        self.state_path.parent.mkdir(parents=True)
        (self.state_path.parent / 'executor.json').write_text('{not json', encoding='utf-8')
        controller = self.make()
        self.assertTrue(controller.claim_daily)
        self.assertIsNone(controller.voted_at)
        self.assertIsNone(controller.daily.last_claim)

    # --- claiming ---

    def test_sent_once_at_run_start_then_not_again_that_day(self):
        controller = self.make()
        controller.start()
        self.run_for(120)
        self.assertEqual(self.daily_sends(), 1)
        daily = controller.status()['daily']
        self.assertEqual(daily['streak'], 1)
        self.assertTrue(daily['claim'])
        self.assertFalse(daily['due'])
        self.assertEqual(daily['next_due'], '2026-10-07T00:00:00+00:00')
        self.assertIsNotNone(daily['last_claim'])
        self.assertNotIn('Claim daily', controller.recent, 'never in Follow the plan bookkeeping')
        self.assertNotIn('Claim daily', controller.held)
        self.assertGreater(controller.state.trips, 5, 'fishing went on')
        self.jump(6 * 3600)
        self.run_for(60)
        self.assertEqual(self.daily_sends(), 1)
        self.assertTrue(controller.running)

    def test_a_cooldown_reply_reschedules_exactly(self):
        self.sim.daily_reply = ':x: Daily reward on cooldown! (1h 0m 5s)'
        controller = self.make()
        controller.start()
        self.run_for(120)
        self.assertEqual(self.daily_sends(), 1)
        entry = next(e for e in controller.action_log if e['label'] == 'Claim daily')
        self.assertEqual(entry['note'], 'on cooldown, 1h 0m 5s left')
        due_at = controller.daily.next_due
        replied = due_at - timedelta(seconds=3605)
        self.assertTrue(self.clock.utc() - timedelta(seconds=120) <= replied <= self.clock.utc(), replied)
        self.assertIsNone(controller.daily.last_claim)
        self.sim.daily_reply = None
        self.jump(3000)
        self.run_for(30)
        self.assertEqual(self.daily_sends(), 1, 'not yet due')
        self.jump(700)
        self.run_for(120)
        self.assertEqual(self.daily_sends(), 2)
        self.assertEqual(controller.daily.streak, 1)

    def test_an_unknown_reply_waits_for_the_next_reset(self):
        self.sim.daily_reply = 'Something else entirely'
        controller = self.make()
        controller.start()
        self.run_for(120)
        self.assertEqual(self.daily_sends(), 1)
        self.assertTrue(controller.running)
        self.assertEqual(controller.status()['daily']['unknown_day'], '2026-10-06')
        self.jump(TO_MIDNIGHT - 1200)
        self.run_for(60)
        self.assertEqual(self.daily_sends(), 1, 'not resent the same UTC day')
        self.sim.daily_reply = None
        self.jump(1200)
        self.run_for(120)
        self.assertEqual(self.daily_sends(), 2, 'tried again after 00:00 UTC')

    def test_a_restart_does_not_resend_the_same_day(self):
        controller = self.make()
        controller.start()
        self.run_for(120)
        controller.stop()
        self.assertEqual(self.daily_sends(), 1)
        again = self.make()
        self.assertEqual(again.daily.streak, 1)
        again.start()
        self.run_for(120)
        self.assertEqual(self.daily_sends(), 1)

    def test_toggle_off_never_sends(self):
        controller = self.make()
        controller.set_claim_daily(False)
        controller.start()
        self.run_for(120)
        self.jump(TO_MIDNIGHT)
        self.run_for(60)
        self.assertEqual(self.daily_sends(), 0)
        self.assertFalse(controller.status()['daily']['claim'])
        self.assertTrue(controller.status()['daily']['due'])

    def held_at(self, seconds):
        controller = self.make()
        real = controller._dashboard

        def dashboard():
            stats = real()
            return {**stats, 'profile': {**stats['profile'], 'level': 99}, 'next_level_in_seconds': seconds}
        controller._dashboard = dashboard
        controller.start()
        self.run_for(120)
        return controller

    def test_held_while_level_100_is_two_minutes_away(self):
        controller = self.held_at(120)
        self.assertEqual(self.daily_sends(), 0)
        self.assertTrue(controller.running)
        self.assertEqual(controller.status()['daily']['note'], 'Waiting for the next level')

    def test_the_level_hold_is_checked_every_thirty_seconds_not_every_tick(self):
        controller = self.make()
        calls = []
        real = controller._dashboard

        def dashboard():
            calls.append(self.clock.now)
            stats = real()
            return {**stats, 'profile': {**stats['profile'], 'level': 99}, 'next_level_in_seconds': 120}
        controller._dashboard = dashboard
        controller.start()
        self.run_for(30)
        controller.daily_hold_until = 0
        calls.clear()
        controller._claim_daily()
        self.assertEqual(controller.daily_note, 'Waiting for the next level')
        held_from = len(calls)
        for _ in range(20):
            self.clock.now = round(self.clock.now + 0.2, 6)
            controller.daily_note = ''
            controller._claim_daily()
            self.assertEqual(controller.daily_note, 'Waiting for the next level')
        self.assertEqual(len(calls), held_from, 'no dashboard copies while held')
        self.clock.now = round(self.clock.now + 30, 6)
        controller._claim_daily()
        self.assertEqual(len(calls), held_from + 1, 'checked again after 30 s')

    def test_sent_while_level_100_is_ten_minutes_away(self):
        self.held_at(600)
        self.assertEqual(self.daily_sends(), 1)

    def test_daily_errors_never_stop_the_run(self):
        original = controller_module.daily_due

        def broken(*args, **kwargs):
            raise RuntimeError('daily exploded')
        controller_module.daily_due = broken
        try:
            controller = self.make()
            controller.start()
            self.run_for(60)
            self.assertTrue(controller.running)
            self.assertGreater(controller.state.trips, 5)
            self.assertIn('daily exploded', controller.daily_note)
            self.assertIn('daily exploded', controller.status()['daily']['note'])
        finally:
            controller_module.daily_due = original
        self.assertEqual(self.daily_sends(), 0)

    def test_status_before_a_run(self):
        status = self.make().status()
        self.assertEqual(status['daily'], {'claim': True, 'last_claim': None, 'next_due': None, 'streak': None,
                                           'due': True, 'note': '', 'unknown_day': None})
        self.assertEqual(status['vote'], {'voted_at': None, 'next_at': None, 'available': True})
        self.assertIsNone(status['boosts']['reserve'])

    def test_a_claim_sent_before_midnight_counts_for_that_day(self):
        # Final review: sent 23:59:58 UTC, its reply read after 00:00: the new day's daily is not skipped.
        controller = self.make()
        sent = self.clock.utc() + timedelta(seconds=TO_MIDNIGHT - 2)
        self.clock.now = TO_MIDNIGHT + 3
        controller.sent_at = (self.clock.now - 5, sent.isoformat())
        controller._daily_result('done', 'streak 4')
        self.assertEqual(controller.daily.last_claim, sent)
        self.assertTrue(controller.status()['daily']['due'])

    # --- Follow the plan, Keep boosts and the daily together ---

    def test_daily_follow_and_boosts_together_at_run_start(self):
        from unittest.mock import patch
        from src.actions import Action
        salesman = Action('buy', 'Salesman', level=8, shop='upgrades')
        config = AutomationConfig(account='sim', username='sim0001', context_id='sim:dm',
                                  capabilities=CONFIG.capabilities + ('refresh_upgrades',))
        controller = AutomationController(self.sim, config, AutomationJournal(self.journal_root), self.fishing,
                                          self.review, monotonic=self.clock.monotonic, utc=self.clock.utc,
                                          state_path=self.state_path, plan_spawn=lambda work: work())
        self.controller = controller
        computed = {}      # plan request number -> sends so far when it was worked out

        def plan_for(inputs):
            computed[controller.planner.requested] = len(self.sim.sent)
            return {'error': None, 'phase_now': 'xp_grind_1', 'next': None, 'prestige': {}, 'assumptions': [],
                    'cut_short': False, 'league': [],
                    'steps': [{'action': 'Salesman 8', 'kind': 'buy', 'status': 'ready', 'do': [salesman.to_dict()]}]}
        controller.planner.compute = plan_for
        queued_before_daily, at_daily_send, after_daily, enqueued = [], [], [], []
        enqueue, finish, send = controller._enqueue, controller._finish_action, self.sim.send

        def recording_enqueue(label, actions):
            if label == 'Claim daily':
                queued_before_daily.append([entry['label'] for entry in controller.queue])
            enqueue(label, actions)
            enqueued.append((label, controller.planner.view()['request'], len(self.sim.sent)))

        def recording_finish(action, result, note):
            finish(action, result, note)
            if (action or {}).get('act') == 'daily':
                after_daily.append((set(controller.awaiting_reads or ()), len(self.sim.sent)))

        def recording_send(intent):
            if intent.kind == 'act' and intent.arguments[0].get('act') == 'daily':
                at_daily_send.append([entry['label'] for entry in controller.queue])
            return send(intent)
        controller._enqueue, controller._finish_action = recording_enqueue, recording_finish
        self.sim.send = recording_send
        controller.set_follow(True)
        controller.set_boosts(True)
        controller.start()
        with patch('src.automation_controller.HOLD_BACK_TRIPS', 1):
            self.run_for(240)
        self.assertTrue(controller.running)
        self.assertEqual(self.daily_sends(), 1)
        acts = [intent.arguments[0] for _, intent in self.sim.sent if intent.kind == 'act']
        boosts = [act['name'] for act in acts if act.get('shop') == 'boosts']
        self.assertTrue(boosts, 'boosts were bought')
        self.assertEqual(len(boosts), len(set(boosts)), 'each boost bought once')
        self.assertTrue(any(act.get('name') == 'Salesman' for act in acts), 'the plan step was bought')
        self.assertEqual(queued_before_daily, [[]], 'queued only with nothing else queued')
        self.assertEqual(at_daily_send, [['Claim daily']], 'sent with no plan step or boost buy waiting')
        (reads, done_at), = after_daily
        self.assertIn('refresh_profile', reads)
        # Every plan step queued after the daily comes from a plan worked out after the daily's reads came back.
        profile_read = next(index for index, (_, intent) in enumerate(self.sim.sent)
                            if index >= done_at and intent.kind == 'refresh_profile')
        later = [(request, sends) for label, request, sends in enqueued if label == 'Salesman 8' and sends > done_at]
        self.assertTrue(later, 'Follow the plan carried on after the daily')
        for request, _ in later:
            self.assertGreater(computed[request], profile_read)

    # --- Duplicator's reserve ---

    def reserve_run(self, duplicator, level=600, follow=True):
        """A run with keep boosts on, /daily off, Follow the plan on (not acting: no plan is made) and the boost
        policy recording the Emerald Fish it is given; 260 Emerald Fish on hand and the profile at `level`. Returns
        the last count the policy saw."""
        seen = []
        original = controller_module.buys

        def recording(wanted, order, expiries, now, emerald, held=()):
            seen.append(emerald)
            return []
        controller_module.buys = recording
        try:
            controller = self.make()
            synced = controller._synced

            def at_level():
                stats = synced()
                return {**stats, 'profile': {**stats['profile'], 'level': level}}
            controller._synced = at_level
            controller.set_boosts(True)
            controller.set_claim_daily(False)
            controller.set_follow(follow)
            if duplicator is not None:
                controller.account['sections']['shop_special'] = {
                    'values': {'items': {'Duplicator': duplicator}}, 'unread': []}
            controller.start()
            self.run_for(60)
            controller.emerald, controller.emerald_at = 260, controller.utc()
            seen.clear()
            controller.tick()
        finally:
            controller_module.buys = original
        self.assertTrue(seen, 'the policy was asked')
        return seen[-1]

    def test_boosts_spend_only_what_is_above_the_next_duplicator_level(self):
        self.assertEqual(self.reserve_run({'level': 2, 'max': 10, 'price': 250}), 10)
        self.assertEqual(self.controller.status()['boosts']['reserve'], {'emerald': 250, 'level': 3})

    def test_the_plan_price_table_stands_in_for_an_unread_price(self):
        self.assertEqual(self.reserve_run({'level': 2, 'max': 10, 'price': None}), 10)

    def test_the_reserve_never_goes_below_zero(self):
        self.assertEqual(self.reserve_run({'level': 5, 'max': 10, 'price': 475}), 0)

    def test_no_duplicator_listed_no_reserve(self):
        self.assertEqual(self.reserve_run(None), 260)
        self.assertIsNone(self.controller.status()['boosts']['reserve'])

    def test_a_stale_duplicator_below_its_unlock_level_no_reserve(self):
        # Final review: after a prestige the old /shop special still lists Duplicator; below level 500 it cannot be
        # bought, so boosts get every Emerald Fish.
        self.assertEqual(self.reserve_run({'level': 2, 'max': 10, 'price': 250}, level=120), 260)
        self.assertIsNone(self.controller.status()['boosts']['reserve'])

    def test_an_unknown_level_keeps_the_reserve_of_a_listed_duplicator(self):
        self.assertEqual(self.reserve_run({'level': 2, 'max': 10, 'price': 250}, level=None), 10)

    def test_no_reserve_without_follow_the_plan_only_a_note(self):
        # Final review: nothing buys Duplicator while Follow the plan is off, so the Emerald Fish is not held back.
        self.assertEqual(self.reserve_run({'level': 2, 'max': 10, 'price': 250}, follow=False), 260)
        boosts = self.controller.status()['boosts']
        self.assertIsNone(boosts['reserve'])
        self.assertEqual(boosts['duplicator'], {'emerald': 250, 'level': 3})

    def test_a_maxed_duplicator_no_reserve(self):
        self.assertEqual(self.reserve_run({'level': 10, 'max': 10, 'price': None}), 260)
        self.assertIsNone(self.controller.status()['boosts']['reserve'])

    # --- vote ---

    def test_vote_available_again_after_twelve_hours(self):
        controller = self.make()
        self.assertTrue(controller.status()['vote']['available'])
        controller.mark_voted()
        vote = controller.status()['vote']
        self.assertFalse(vote['available'])
        self.assertEqual(vote['voted_at'], self.clock.utc().isoformat())
        self.assertEqual(vote['next_at'], (self.clock.utc() + timedelta(hours=12)).isoformat())
        self.clock.now += 12 * 3600 - 1
        self.assertFalse(controller.status()['vote']['available'])
        self.clock.now += 1
        self.assertTrue(controller.status()['vote']['available'])


if __name__ == '__main__':
    unittest.main()
