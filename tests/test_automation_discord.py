from datetime import datetime, timezone
import unittest


class FishingOverlapRegressionTests(unittest.TestCase):
    def test_profile_anchor_survives_missing_balance(self):
        from src.automation_discord import stable_frame
        from src.live_capture import align
        profile = ['epicmouse used /profile', 'Virtual Fisher 1:09 PM', 'Inventory of epicmouse',
                   'Balance: $54,950.', 'Level 31']
        after = [line.replace('Balance: $54,950.', 'Balance:') for line in profile]
        after += ['epicmouse used /buffs', 'Virtual Fisher 1:09 PM',
                  "epicmouse9925's current multipliers (including bait):", 'Fishing cooldown: 3.5s.']
        self.assertEqual(align(stable_frame(profile), stable_frame(after))['start'], 1)

    def test_sending_command_is_observed_without_resending(self):
        driver = Driver()
        transport = DiscordTransport(driver, 42, 'Virtual Fisher', 'epicmouse', 'epicmouse9925')
        transport.enable()
        transport.send(ActionIntent('wait', 'fish', transport.context_id, datetime.now(timezone.utc).isoformat(), 'fishing'))
        driver.lines += ['epicmouse used /fish', 'Virtual Fisher 1:10 PM', 'Sending command...']
        self.assertEqual(transport.poll(), [])
        self.assertTrue(transport.sending_seen)
        self.assertEqual(driver.sent, ['/fish'])
        self.assertIn('Sending command', transport.last_issue)

    def test_old_quest_noise_and_missing_invoker_do_not_hide_profile(self):
        from src.automation_discord import stable_frame
        from src.live_capture import align
        old = ['Virtual Fisher 12:17 PM', 'You caught:', '2 Fish', '4 Salmon', '9 Cod',
               '+117 XP', 'LEVEL UP! You are', 'QUEST COMPLETE!', 'Daily Level—ups Tier 1', 'Reward:', 'lx uncommon chest']
        before = stable_frame(old)
        repaired = ['epicmouse used /fish'] + [line.replace('Level—ups', 'Level-ups') for line in old]
        after = stable_frame(repaired + ['epicmouse used /profile', 'Virtual Fisher 12:20 PM',
                                        'Inventory of epicmouse', 'Balance: $54,056.', 'Level 31'])
        self.assertEqual(align(before, after)['status'], 'certain')
        self.assertEqual(align(before, after)['start'], 1)

    def test_anchor_keeps_conflicting_times_and_invokers_separate(self):
        from src.automation_discord import stable_frame
        from src.live_capture import align
        def frame(name, time):
            return stable_frame([name + ' used /fish', 'Virtual Fisher ' + time,
                                 'You caught:', '4 Salmon', '+117 XP'])
        self.assertEqual(align(frame('epicmouse', '12:17 PM'), frame('other', '12:17 PM'))['status'], 'lost')
        self.assertEqual(align(frame('epicmouse', '12:17 PM'), frame('epicmouse', '12:18 PM'))['status'], 'lost')

    def test_identical_catch_anchors_remain_ambiguous(self):
        from src.automation_discord import stable_frame
        from src.live_capture import align
        reply = ['epicmouse used /fish', 'Virtual Fisher', 'You caught:', '4 Salmon', '+117 XP']
        self.assertEqual(align(stable_frame(reply + reply), stable_frame(reply + reply))['status'], 'ambiguous')

    def test_incomplete_level_up_is_reread_before_delivery(self):
        from src.live_capture import fishing_problems
        driver = Driver()
        now = datetime.now(timezone.utc)
        transport = DiscordTransport(driver, 42, 'Virtual Fisher', 'epicmouse', 'epicmouse9925', utc=lambda: now)
        transport.enable()
        transport.send(ActionIntent('retry-fish', 'fish', transport.context_id, now.isoformat(), 'fishing'))
        driver.lines += fish(2, 4, 117) + ['LEVEL UP! You are']
        self.assertEqual(transport.poll(), [])
        self.assertEqual(transport.poll(), [])
        self.assertIsNotNone(transport.pending)
        driver.lines[-1] = 'LEVEL UP! You are now level 31.'
        self.assertEqual(transport.poll(), [])
        replies = transport.poll()
        self.assertEqual(len(replies), 1)
        self.assertEqual(fishing_problems(replies[0].text), [])

    def test_older_catch_ocr_case_does_not_hide_new_reply(self):
        from src.automation_discord import stable_frame
        from src.live_capture import align
        def reply(catch, xp):
            return ['epicmouse used /fish', 'Virtual Fisher', 'You caught:', catch, f'+{xp} XP']
        before = stable_frame(reply('8 $ cod', 119) + reply('11 Salmon', 100))
        after = stable_frame(reply('8 Cod', 119) + reply('11 Salmon', 100) + reply('5 Salmon', 85))
        self.assertEqual(align(before, after)['status'], 'certain')
        self.assertEqual(align(before, after)['start'], 2)


from src.automation_discord import DiscordTransport, composer_target, command_target
from src.automation_models import ActionIntent, CapabilityError, NotDispatched, TransportError
from tests.test_live_capture import fish


def box(text, x=300, y=800):
    return dict(text=text, left=x, top=y, right=x+160, bottom=y+20)


class Driver:
    def __init__(self):
        self.lines = fish(1, 1, 10)
        self.context = dict(window=42, title='VF Discord', channel='Virtual Fisher')
        self.status = 'ok'
        self.sent = []
        self.foreground = True

    def capture(self):
        return dict(status=self.status, lines=self.lines, context=self.context)

    def is_foreground(self):
        return self.foreground

    def dispatch(self, command, cancelled):
        if cancelled():
            raise TransportError('Stopped')
        self.sent.append(command)


class DiscordTests(unittest.TestCase):
    def test_profile_confirmation_ignores_changing_optional_ocr(self):
        from tests.test_live_capture import PROFILE
        self.intent = ActionIntent('p1', 'refresh_profile', self.transport.context_id,
                                   self.now.isoformat(), 'profile')
        self.send()
        self.driver.lines += PROFILE + ['Fish Invent«y']
        self.assertEqual(self.transport.poll(), [])
        self.driver.lines[-1] = 'Fish Inventory ???'
        self.assertEqual(len(self.transport.poll()), 1)

    def test_unreadable_profile_rod_emoji_does_not_break_overlap(self):
        from src.automation_discord import stable_frame
        from src.live_capture import align
        def profile(rod, stamp):
            return ['epicmouse used', 'profile', 'Virtual Fisher ' + stamp,
                    'Inventory of epicmouse', 'Balance: $47,210.',
                    'P4 Level 30, xp to next leveL', 'Currently using ' + rod + '.',
                    'Current biome: River', 'Pet: (Level 51)', 'Fish Value: $130,583']
        before = stable_frame(profile('Z Steel Rod', '11:28 AM') + profile('Steel Rod', '11:29 AM'))
        after = stable_frame(profile('Z Steel Rod', '11:29 AM') + profile('Z Steel Rod', '11:29 AM'))
        self.assertEqual(align(before, after)['status'], 'certain')
        self.assertEqual(align(before, after)['start'], 1)

    def test_old_fishing_caption_read_failure_does_not_lose_new_profile(self):
        from tests.test_live_capture import PROFILE
        self.intent = ActionIntent('p1', 'refresh_profile', self.transport.context_id,
                                   self.now.isoformat(), 'profile')
        self.send()
        self.driver.lines = [line.replace('Global boost by Someone!', 'Global boost by')
                             for line in self.driver.lines] + PROFILE
        self.assertEqual(self.transport.poll(), [])
        replies = self.transport.poll()
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].correlation, 'p1')

    def test_temporary_overlap_loss_can_recover_without_resending(self):
        baseline = list(self.driver.lines)
        self.send()
        self.driver.lines = fish(9, 9, 90)
        self.assertEqual(self.transport.poll(), [])
        self.driver.lines = baseline + fish(2, 2, 20)
        self.assertEqual(self.transport.poll(), [])
        self.assertEqual(len(self.transport.poll()), 1)
        self.assertEqual(self.driver.sent, ['/fish'])

    def test_caption_punctuation_change_is_not_a_new_trip(self):
        self.send()
        self.driver.lines = [line.replace('Someone!', 'Someone—!') for line in self.driver.lines]
        self.assertEqual(self.transport.poll(), [])
        self.assertTrue(self.transport.inspect_context().ok)

    def setUp(self):
        self.driver = Driver()
        self.now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        self.transport = DiscordTransport(self.driver, 42, 'Virtual Fisher', 'epicmouse',
                                          'epicmouse9925', utc=lambda: self.now)
        self.intent = ActionIntent('a1', 'fish', self.transport.context_id,
                                   self.now.isoformat(), 'fishing')

    def test_disabled_by_default(self):
        with self.assertRaises(CapabilityError):
            self.transport.send(self.intent)
        self.assertEqual(self.driver.sent, [])

    def send(self):
        self.transport.enable()
        return self.transport.send(self.intent)

    def test_target_verified_and_command_dispatched(self):
        self.assertTrue(self.transport.inspect_context().ok)
        self.assertEqual(self.send().action_id, 'a1')
        self.assertEqual(self.driver.sent, ['/fish'])

    def test_wrong_or_unavailable_context_never_sends(self):
        for change in ('channel', 'window', 'blank', 'foreground'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'channel': self.driver.context['channel'] = 'elsewhere'
                if change == 'window': self.driver.context['window'] = 99
                if change == 'blank': self.driver.status = 'blank'
                if change == 'foreground': self.driver.foreground = False
                with self.assertRaises(NotDispatched): self.send()
                self.assertEqual(self.driver.sent, [])

    def test_same_channel_label_in_changed_window_title_is_rejected(self):
        self.assertTrue(self.transport.inspect_context().ok)
        self.driver.context['title'] = 'Another server VF Discord'
        with self.assertRaises(NotDispatched): self.send()
        self.assertEqual(self.driver.sent, [])

    def test_stale_screen_does_not_acknowledge(self):
        self.send()
        self.assertEqual(self.transport.poll(), [])

    def test_new_own_reply_stabilizes_then_correlates_once(self):
        self.send()
        self.driver.lines = fish(1, 1, 10) + fish(2, 2, 20)
        self.assertEqual(self.transport.poll(), [])
        replies = self.transport.poll()
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].correlation, 'a1')
        self.assertEqual(self.transport.poll(), [])

    def test_other_player_does_not_acknowledge(self):
        self.send()
        other = fish(2, 2, 20)
        other[0], other[3] = 'other used', 'other77'
        self.driver.lines = fish(1, 1, 10) + other
        self.transport.poll()
        self.assertEqual(self.transport.poll(), [])

    def test_identical_ambiguous_replacement_pauses(self):
        self.driver.lines = fish(1, 1, 10) * 2
        self.send()
        self.transport.poll()
        self.assertFalse(self.transport.inspect_context().ok)

    def test_second_send_and_stopped_send_rejected(self):
        self.send()
        with self.assertRaises(TransportError): self.transport.send(self.intent)
        self.transport.stop()
        with self.assertRaises(CapabilityError): self.transport.send(self.intent)
        self.assertEqual(len(self.driver.sent), 1)

    def test_unsupported_action_rejected(self):
        from dataclasses import replace
        self.transport.enable()
        with self.assertRaises(CapabilityError):
            self.transport.send(replace(self.intent, kind='sell'))

    def test_missing_baseline_rejected(self):
        self.driver.lines = ['epicmouse used', 'profile']
        with self.assertRaises(NotDispatched): self.send()
        self.assertEqual(self.driver.sent, [])
        # Nothing was typed: the transport stays usable, and the screen it saw is kept for diagnosis.
        self.assertTrue(self.transport.inspect_context().ok)
        self.assertEqual(self.transport.last_lines, ['epicmouse used', 'profile'])
        self.driver.lines = fish(1, 1, 10)
        self.assertEqual(self.transport.send(self.intent).action_id, 'a1')

    def test_partial_dispatch_failure_blocks_another_send(self):
        def fail(command, cancelled):
            raise OSError('input unavailable')
        self.driver.dispatch = fail
        with self.assertRaises(TransportError): self.send()
        self.assertFalse(self.transport.inspect_context().ok)
        with self.assertRaises(TransportError): self.transport.send(self.intent)

    def test_failed_capture_before_input_is_not_dispatched(self):
        def broken():
            raise OSError('capture failed')
        self.driver.capture = broken
        with self.assertRaises(NotDispatched): self.send()
        self.assertEqual(self.driver.sent, [])

    def test_stop_between_baseline_and_input_sends_nothing(self):
        def stop_capture():
            self.transport.stop()
            return dict(status='ok', lines=self.driver.lines, context=self.driver.context)
        self.driver.capture = stop_capture
        with self.assertRaises(NotDispatched): self.send()
        self.assertEqual(self.driver.sent, [])

    def test_unexpected_reply_does_not_acknowledge(self):
        self.send()
        from tests.test_live_capture import PROFILE
        self.driver.lines += PROFILE
        self.transport.poll()
        self.assertEqual(self.transport.poll(), [])

    def test_verification_prompt_pauses(self):
        self.send()
        self.driver.lines += ['Virtual Fisher 11:42 AM', 'Please verify with /verify']
        self.transport.poll()
        self.assertFalse(self.transport.inspect_context().ok)


class TargetTests(unittest.TestCase):
    def test_tall_fish_picker_keeps_exact_owner_check(self):
        boxes = [box('[fish', x=458, y=888), box('Virtual Fisher', x=2130, y=900)]
        self.assertEqual(command_target(boxes, '/fish', (465, 1363)), (538, 898))
        boxes[-1]['text'] = 'Other Bot'
        self.assertIsNone(command_target(boxes, '/fish', (465, 1363)))

    def test_adjacent_other_bot_row_cannot_borrow_vf_label(self):
        boxes = [box('/fish', x=458, y=1274), box('Other Bot', x=2130, y=1286),
                 box('Virtual Fisher', x=2130, y=1250)]
        self.assertIsNone(command_target(boxes, '/fish', (534, 1363)))

    def test_wide_native_picker_and_draft(self):
        boxes = [box('[profile', x=458, y=1274), box('[profile', x=449, y=1356),
                 box('Virtual Fisher', x=2130, y=1286)]
        self.assertEqual(command_target(boxes, '/profile', (534, 1363)), (538, 1284))
        boxes[-1]['text'] = 'Virtual Asher'
        self.assertIsNone(command_target(boxes, '/profile', (534, 1363)))

    def test_real_yesterday_headers_preserve_invocations(self):
        from src.live_capture import capture_frame
        lines = ['epicmouse used', ':: fish', 'Virtual Fisher Yesterday at 12:09 PM',
                 'You caught:', '10 Salmon', '2 Cod', '+63 XP']
        messages = capture_frame(lines)['messages']
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['invoked_by'], 'epicmouse')
        self.assertNotIn('Yesterday at 12:09 PM', messages[0]['lines'])

    def test_composer_from_pixels(self):
        self.assertEqual(composer_target([box('Message @Virtual Fisher')], 'Virtual Fisher'), (380, 810))
        self.assertIsNone(composer_target([box('Message @someoneelse')], 'Virtual Fisher'))
        self.assertIsNone(composer_target([box('Message #Virtual Fisher')], 'Virtual Fisher'))

    def test_picker_requires_exact_command_and_bot_nearby(self):
        self.assertEqual(command_target([box('/fish', y=600), box('Virtual Fisher', y=620)], '/fish'), (380, 610))
        self.assertIsNone(command_target([box('/fish', y=600), box('Other Bot', y=620)], '/fish'))
        self.assertIsNone(command_target([box('/fish', y=600), box('/fish', y=650),
                                          box('Virtual Fisher', y=620)], '/fish'))


class ControllerIntegrationTests(unittest.TestCase):
    def test_bootstrap_and_one_trip_through_pixel_transport(self):
        import tempfile
        from pathlib import Path
        from src.automation_controller import AutomationController
        from src.automation_journal import AutomationJournal
        from src.automation_models import AutomationConfig
        from src.fishing_sessions import load_sessions
        from tests.test_live_capture import PROFILE, BUFFS
        from tests.test_automation_controller import Clock
        driver, clock = Driver(), Clock()
        def dispatch(command, cancelled):
            driver.sent.append(command)
            driver.lines += {'/profile': PROFILE, '/buffs': BUFFS, '/fish': fish(2, 2, 20)}[command]
        driver.dispatch = dispatch
        transport = DiscordTransport(driver, 42, 'Virtual Fisher', 'epicmouse',
                                     'epicmouse9925', utc=clock.utc)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = AutomationConfig('epicmouse', 'epicmouse9925', transport.context_id,
                                      max_trips=1, max_seconds=120, mode='live')
            controller = AutomationController(transport, config, AutomationJournal(root/'journal'),
                                              root/'fish', root/'review', utc=clock.utc,
                                              monotonic=clock.monotonic)
            controller.start()
            transport.enable()
            for _ in range(20):
                controller.tick()
                clock.now += .5
            self.assertEqual(driver.sent, ['/profile', '/buffs', '/fish'])
            self.assertEqual(controller.status()['trips'], 1)
            self.assertFalse(controller.running)
            self.assertEqual(load_sessions('epicmouse', root/'fish')[0][0]['tally']['trips'], 1)


if __name__ == '__main__':
    unittest.main()
