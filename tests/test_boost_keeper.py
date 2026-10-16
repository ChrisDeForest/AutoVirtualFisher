"""Part 7: the boost keeper and quest tracking in the controller, driven by the simulation transport."""
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


class ScriptedBoosts(SimulationTransport):
    """The simulation, with set replies to chosen boost buys (name -> text, or a function of the time)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.boost_replies = {}

    def _act(self, intent):
        from src.actions import Action
        action = Action.from_dict(intent.arguments[0])
        reply = self.boost_replies.get(action.name) if action.shop == 'boosts' else None
        if reply is None:
            return super()._act(intent)
        return reply(self) if callable(reply) else reply


class BoostKeeperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.journal_root, self.fishing, self.review = root / 'journal', root / 'fishing', root / 'review'
        self.state_path = root / 'accounts' / 'sim' / 'account_state.json'
        self.clock = Clock()
        self.sim = ScriptedBoosts(self.clock.monotonic, self.clock.utc, cooldown=3.5, latency=0.4)

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
        """Let the pending command finish, then move the clock on without ticking (a long stretch of fishing)."""
        while self.controller.state.pending is not None and self.controller.running:
            self.controller.tick()
            self.clock.now = round(self.clock.now + STEP, 6)
        self.clock.now = round(self.clock.now + seconds, 6)

    def boost_sends(self):
        return [intent.arguments[0]['name'] for _, intent in self.sim.sent
                if intent.kind == 'act' and intent.arguments[0].get('shop') == 'boosts']

    def started(self, keep=True):
        controller = self.make()
        if keep:
            controller.set_boosts(True)
        controller.start()
        return controller

    # --- settings ---

    def test_settings_persist_and_do_not_clobber_each_other(self):
        controller = self.make()
        controller.set_follow(True)
        controller.set_boosts(True)
        controller.set_boost_order(['Auto30m', 'Fish20m'])
        again = self.make()
        self.assertTrue(again.follow_plan)
        self.assertTrue(again.keep_boosts)
        self.assertEqual(again.boost_order, ('Auto30m', 'Fish20m', 'Treasure20m'))
        again.set_follow(False)
        third = self.make()
        self.assertFalse(third.follow_plan)
        self.assertTrue(third.keep_boosts)
        self.assertEqual(third.boost_order, ('Auto30m', 'Fish20m', 'Treasure20m'))

    def test_an_unreadable_settings_file_gives_the_defaults(self):
        self.state_path.parent.mkdir(parents=True)
        (self.state_path.parent / 'executor.json').write_text('{not json', encoding='utf-8')
        controller = self.make()
        self.assertEqual((controller.follow_plan, controller.keep_boosts, controller.boost_order),
                         (False, False, ('Fish20m', 'Treasure20m', 'Auto30m')))

    # --- buying ---

    def test_booster_quest_buys_all_three_in_order(self):
        controller = self.started()
        self.run_for(90)
        self.assertEqual(self.boost_sends(), ['Fish20m', 'Treasure20m', 'Auto30m'])
        done = [entry['label'] for entry in reversed(controller.action_log) if entry['result'] == 'done']
        self.assertEqual(done, ['Boost: Fish boost', 'Boost: Treasure boost', 'Boost: Worker'])
        boosts = controller.status()['boosts']
        self.assertEqual(set(boosts['running']), {'Fish20m', 'Treasure20m', 'Auto30m'})
        self.assertTrue(boosts['keep'])
        self.assertEqual(boosts['order'], ['Fish20m', 'Treasure20m', 'Auto30m'])
        self.assertEqual(boosts['emerald'], 490 - 6 - 6 - 8)
        self.assertEqual(set(boosts['wanted']), {'Fish20m', 'Treasure20m', 'Auto30m'})
        self.assertEqual(controller.status()['quests']['boost_minutes'], 70)
        self.assertGreater(controller.state.trips, 5, 'fishing went on between the buys')
        self.assertEqual(controller.status()['executor']['held'], [], 'boosts never enter Follow the plan')
        self.assertNotIn('Boost: Fish boost', controller.recent)

    def test_the_tile_order_decides_the_buy_order(self):
        controller = self.make()
        controller.set_boosts(True)
        controller.set_boost_order(['Auto30m', 'Treasure20m', 'Fish20m'])
        controller.start()
        self.run_for(90)
        self.assertEqual(self.boost_sends(), ['Auto30m', 'Treasure20m', 'Fish20m'])

    def test_no_second_buy_while_running(self):
        controller = self.started()
        self.run_for(90)
        for _ in range(6):
            self.jump(150)
            self.run_for(20)
        self.assertLess(self.clock.now, 20 * 60)
        self.assertEqual(self.boost_sends(), ['Fish20m', 'Treasure20m', 'Auto30m'])
        self.assertTrue(controller.running)

    def test_rebuys_after_expiry(self):
        controller = self.started()
        self.run_for(90)
        self.jump(20 * 60)
        self.run_for(60)
        self.assertEqual(self.boost_sends(), ['Fish20m', 'Treasure20m', 'Auto30m', 'Fish20m', 'Treasure20m'])
        self.jump(10 * 60)
        self.run_for(60)
        self.assertEqual(self.boost_sends()[-1], 'Auto30m')
        self.assertEqual(len(self.boost_sends()), 6)
        self.assertTrue(controller.running)

    def test_already_boosting_counts_as_running(self):
        def already(sim):
            sim.boosts['Fish20m'] = sim.monotonic() + 300    # /boosts shows it too
            return 'You are already boosting that! Time left: 5m 0s'
        self.sim.boost_replies['Fish20m'] = already
        controller = self.started()
        self.run_for(90)
        fish = [entry for entry in controller.action_log if entry['label'] == 'Boost: Fish boost']
        self.assertEqual((fish[0]['result'], fish[0]['note']), ('done', 'already boosting, 5m 0s left'))
        left = controller.status()['boosts']['running']['Fish20m']
        self.assertTrue(200 < left <= 300, left)
        self.assertEqual(controller.status()['boosts']['emerald'], 490 - 6 - 8)
        self.assertEqual(controller.status()['quests']['boost_minutes'], 50)
        self.assertEqual(self.boost_sends().count('Fish20m'), 1)

    def test_unknown_reply_holds_the_boost(self):
        self.sim.boost_replies['Fish20m'] = 'Your catch, its buttons restyled'
        controller = self.started()
        self.run_for(90)
        for _ in range(6):
            self.jump(150)
            self.run_for(20)
        self.assertEqual(self.boost_sends().count('Fish20m'), 1)
        self.assertTrue(controller.running)
        self.assertEqual(controller.status()['boosts']['held'], ['Fish20m'])
        trips = controller.state.trips
        self.run_for(30)
        self.assertGreater(controller.state.trips, trips, 'the run keeps fishing')

    def test_cant_afford_waits_for_a_new_emerald_count_or_ten_minutes(self):
        self.sim.boost_replies['Fish20m'] = "You can't afford that!"
        controller = self.started()
        self.run_for(90)
        self.assertEqual(self.boost_sends().count('Fish20m'), 1)
        self.assertIn('Fish20m', controller.boost_held)
        self.assertNotIn('Fish20m', controller.status()['boosts']['held'], 'not an unknown outcome')
        self.sim.boost_replies.clear()
        self.jump(5 * 60)
        self.run_for(60)
        self.assertEqual(self.boost_sends().count('Fish20m'), 1, 'held')
        self.jump(10 * 60)      # past the hold, and the 15 minutes between buys of one boost
        self.run_for(60)
        self.assertIn('Fish20m', controller.status()['boosts']['running'])
        self.assertEqual(self.boost_sends().count('Fish20m'), 2)

    def test_cant_afford_is_not_cleared_by_the_profile_it_triggers(self):
        # The /profile read after the failed buy shows Emerald Fish too: it must not clear the hold at once.
        old = self.sim._play

        def play(intent, now):
            text, kind = old(intent, now)
            if intent.kind == 'refresh_profile':
                text += '\nExotic Fish\n490 Emerald Fish'
            return text, kind
        self.sim._play = play
        self.sim.boost_replies['Fish20m'] = "You can't afford that!"
        controller = self.started()
        self.run_for(90)
        self.assertEqual(self.boost_sends().count('Fish20m'), 1)
        self.assertEqual(controller.emerald, 490, '/profile was read after the buys')
        now = controller.utc()
        self.assertGreater(controller.boost_held['Fish20m'], now, 'still held after /profile')
        controller._boost_read('shop_boosts', {'values': {'emerald': controller.boost_poor['Fish20m']}})
        self.assertIn('Fish20m', controller.boost_held, 'the same count as at the failure')
        controller._boost_read('shop_boosts', {'values': {'emerald': 1000}})
        self.assertNotIn('Fish20m', controller.boost_held, 'a /shop boosts read with a new count')

    def boosts_reply(self, change):
        """/boosts replies passed through `change` (a misread)."""
        old = self.sim._play

        def play(intent, now):
            text, kind = old(intent, now)
            return (change(text) if intent.kind == 'refresh_boosts' else text), kind
        self.sim._play = play

    def fish_on(self, controller, minutes):
        """Run `minutes` of fishing in stretches; True when trips kept being made throughout."""
        made = True
        for _ in range(int(minutes * 60 // 170)):
            trips = controller.state.trips
            self.jump(150)
            self.run_for(20)
            made = made and controller.state.trips > trips
        return made

    def test_a_glyph_before_a_boosts_line_never_rebuys(self):
        self.boosts_reply(lambda text: text.replace('More Fish', "r' More Fish"))   # an icon, as OCR reads it
        controller = self.started()
        self.run_for(90)
        self.assertTrue(self.fish_on(controller, 18), 'fishing goes on')
        self.assertEqual(self.boost_sends(), ['Fish20m', 'Treasure20m', 'Auto30m'])
        self.assertIn('Fish20m', controller.status()['boosts']['running'])
        self.assertTrue(controller.running)

    def test_a_boosts_read_missing_every_line_never_rebuys(self):
        # The worst misread: /boosts shows no boost at all. A buy's own reply outranks a read just after it, one
        # missed read is not enough to call a boost off, and one boost is bought at most every 15 minutes anyway.
        self.boosts_reply(lambda text: '\n'.join(line for line in text.split('\n') if ': ' not in line
                                                 or line.startswith(('Global', 'Current'))))
        controller = self.started()
        self.run_for(90)
        self.assertTrue(self.fish_on(controller, 18), 'fishing goes on')
        self.assertEqual(self.boost_sends(), ['Fish20m', 'Treasure20m', 'Auto30m'])
        self.assertTrue(controller.running)

    def test_one_buy_per_boost_per_fifteen_minutes_whatever_the_reads(self):
        controller = self.started()
        self.run_for(90)
        self.assertEqual(len(self.boost_sends()), 3)
        controller.boost_expiry.clear()     # however the keeper came to think they are off
        self.sim.boosts.clear()
        self.run_for(60)
        self.jump(10 * 60)
        self.run_for(60)
        self.assertEqual(len(self.boost_sends()), 3)
        self.jump(4 * 60)
        self.run_for(60)
        self.assertEqual(self.boost_sends(), ['Fish20m', 'Treasure20m', 'Auto30m'] * 2)

    def test_no_buy_until_the_run_start_reads_are_in(self):
        old = self.sim._play

        def play(intent, now):      # /profile shows Emerald Fish: the keeper knows it before /shop boosts
            text, kind = old(intent, now)
            if intent.kind == 'refresh_profile':
                text += '\nExotic Fish\n490 Emerald Fish'
            return text, kind
        self.sim._play = play
        config = AutomationConfig(account='sim', username='sim0001', context_id='sim:dm',
                                  capabilities=CONFIG.capabilities + ('refresh_upgrades', 'refresh_rod', 'refresh_bait'))
        controller = self.make(config)
        controller.set_boosts(True)
        controller._boosts_wanted = lambda now: {'Fish20m'}     # as in an always_from phase
        controller.start()
        start_reads = set(controller.state.refresh_due) - {'refresh_charms'}
        for _ in range(400):
            controller.tick()
            self.clock.now = round(self.clock.now + STEP, 6)
            if self.boost_sends():
                break
        self.assertEqual(self.boost_sends(), ['Fish20m'])
        self.assertFalse(start_reads & set(controller.state.refresh_due), controller.state.refresh_due)
        self.assertIsNotNone(controller.boosts_read_at)
        self.assertIsNotNone(controller.quests_read_at)

    def test_a_boost_buy_keeps_the_reads_follow_the_plan_waits_for(self):
        controller = self.started()
        controller.awaiting_reads = {'refresh_rod', 'refresh_upgrades'}
        controller._finish_action({'act': 'buy', 'name': 'Fish20m', 'shop': 'boosts', 'price': 6}, 'done',
                                  'boost on for 20m')
        self.assertTrue({'refresh_rod', 'refresh_upgrades', 'refresh_boosts'} <= controller.awaiting_reads)

    def plan_with(self, controller, *actions, follow=True):
        """A plan whose next step is a ready switch to `actions`; Follow the plan set, but not acting on it."""
        view = controller.planner.view
        step = {'kind': 'switch', 'status': 'ready', 'seconds': 0, 'do': [dict(action) for action in actions]}
        controller.planner.view = lambda: {**view(), 'plan': {'steps': [step], 'phase_now': None}}
        controller.follow_plan = follow
        controller._follow = lambda: None

    def test_a_planned_switch_waits_only_while_following_the_plan(self):
        controller = self.started(keep=False)
        self.run_for(30)
        self.plan_with(controller, {'act': 'rod', 'name': 'Steel Rod'})
        controller.set_boosts(True)
        self.run_for(30)
        self.assertEqual(self.boost_sends(), [])
        self.assertEqual(controller.status()['boosts']['note'], 'Waiting for a planned switch')
        controller.follow_plan = False
        self.run_for(60)
        self.assertEqual(len(self.boost_sends()), 3, 'the plan is not followed: its switch never comes')
        self.assertEqual(controller.status()['boosts']['note'], '')

    def test_a_pet_only_switch_does_not_wait(self):
        controller = self.started(keep=False)
        self.run_for(30)
        self.plan_with(controller, {'act': 'pet', 'name': 'Dolphin'})
        controller.set_boosts(True)
        self.run_for(60)
        self.assertEqual(len(self.boost_sends()), 3)

    def test_emerald_rate_over_fishing_time(self):
        controller = self.started(keep=False)
        self.run_for(60)
        fished = controller.fishing_seconds
        self.assertGreater(fished, 40)
        self.jump(20 * 60)      # paused, a captcha, Discord not in front: not fishing time
        self.run_for(30)
        self.assertLess(controller.fishing_seconds - fished, 60)
        controller.run_tallies = [{'chest_contents': {'Emerald Fish': 12}}]
        controller.fishing_seconds = 29 * 60
        self.assertIsNone(controller._emerald_per_hour(), 'under 30 minutes of fishing: one crate could sway it')
        controller.fishing_seconds = 3600
        self.assertEqual(controller._emerald_per_hour(), 12)

    def test_off_by_default_and_toggle_off_stops_buying(self):
        controller = self.started(keep=False)
        self.run_for(90)
        self.assertEqual(self.boost_sends(), [])
        self.assertFalse(controller.status()['boosts']['keep'])
        controller.set_boosts(True)
        self.run_for(60)
        self.assertEqual(len(self.boost_sends()), 3)
        controller.set_boosts(False)
        self.jump(31 * 60)
        self.run_for(60)
        self.assertEqual(len(self.boost_sends()), 3)

    def test_keeper_error_never_stops_the_run(self):
        original = controller_module.buys

        def broken(*args, **kwargs):
            raise RuntimeError('policy exploded')
        controller_module.buys = broken
        try:
            controller = self.started()
            self.run_for(60)
        finally:
            controller_module.buys = original
        self.assertTrue(controller.running)
        self.assertGreater(controller.state.trips, 5)
        self.assertIn('policy exploded', controller.status()['boosts']['note'])
        self.assertEqual(self.boost_sends(), [])

    def test_nothing_is_bought_while_paused(self):
        controller = self.started()
        controller.pause()
        self.run_for(30)
        self.assertEqual(self.boost_sends(), [])

    # --- quests ---

    def test_quests_read_at_start_and_shown(self):
        controller = self.started(keep=False)
        self.run_for(30)
        quests = controller.status()['quests']
        self.assertEqual(quests['special'][0]['name'], 'Booster')
        self.assertIsNotNone(quests['read_at'])
        self.assertTrue((5 * 60 + 29) * 60 - 60 < quests['resets_in'] <= (5 * 60 + 29) * 60, quests['resets_in'])

    def test_quests_are_read_again_every_half_hour(self):
        controller = self.started(keep=False)
        self.run_for(30)
        self.jump(31 * 60)
        self.run_for(30)
        self.assertEqual(sum(intent.kind == 'refresh_quests' for _, intent in self.sim.sent), 2)
        self.assertTrue(controller.running)

    def test_status_shapes_before_a_run(self):
        status = self.make().status()
        self.assertEqual(status['boosts'], {'keep': False, 'order': ['Fish20m', 'Treasure20m', 'Auto30m'],
                                            'running': {}, 'held': [], 'emerald': None, 'per_hour': None,
                                            'wanted': [], 'note': ''})
        self.assertEqual(status['quests'], {'daily': [], 'special': [], 'resets_in': None, 'boost_minutes': 0,
                                            'read_at': None})


if __name__ == '__main__':
    unittest.main()
