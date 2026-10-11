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
        return dict(status=self.status, lines=self.lines, context=self.context, buttons=self.buttons)

    buttons = ()
    clicks = None

    def click(self, point, cancelled):
        if cancelled():
            raise TransportError('Stopped')
        self.clicks = (self.clicks or []) + [point]
        if self.on_click:
            self.on_click()

    on_click = None

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

    def test_charms_reply_is_delivered(self):
        from tests.test_live_stats import CHARMS
        self.intent = ActionIntent('c1', 'refresh_charms', self.transport.context_id, self.now.isoformat(), 'charms')
        self.send()
        self.assertEqual(self.driver.sent, ['/charms'])
        self.driver.lines = self.driver.lines + ['epicmouse used', '% charms', 'Virtual Fisher 11:42 AM',
                                                 *CHARMS.split('\n')]
        self.assertEqual(self.transport.poll(), [])
        replies = self.transport.poll()
        self.assertEqual([(reply.kind, reply.correlation) for reply in replies], [('charms', 'c1')])

    def account(self, kind, text, stamp='11:42 PM'):
        return ['epicmouse used', '% ' + kind, 'Virtual Fisher ' + stamp, *text.splitlines()]

    def test_account_read_is_typed_and_delivered(self):
        from src import account_samples as sample
        self.intent = ActionIntent('u1', 'refresh_upgrades', self.transport.context_id, self.now.isoformat(),
                                   'shop_upgrades')
        self.send()
        self.assertEqual(self.driver.sent, ['/shop upgrades'])
        self.driver.lines = self.driver.lines + self.account('shop upgrades', sample.UPGRADES_PAGE_1)
        self.assertEqual(self.transport.poll(), [])
        self.assertEqual([reply.kind for reply in self.transport.poll()], ['shop_upgrades'])

    def page_one_on_screen(self):
        from src import account_samples as sample
        self.driver.lines = self.driver.lines + self.account('shop upgrades', sample.UPGRADES_PAGE_1)
        self.driver.buttons = [{'y': 640, 'height': 16, 'runs': [{'left': 137, 'right': 227, 'enabled': False},
                                                                  {'left': 239, 'right': 329, 'enabled': True}]}]
        self.intent = ActionIntent('u2', 'refresh_upgrades_page2', self.transport.context_id, self.now.isoformat(),
                                   'shop_upgrades')

    def test_page_two_clicks_the_enabled_arrow_and_waits_for_the_page_to_change(self):
        from src import account_samples as sample
        self.page_one_on_screen()
        page_two = self.account('shop upgrades', sample.UPGRADES_PAGE_2)
        self.driver.on_click = lambda: setattr(self.driver, 'lines', self.driver.lines[:-len(page_two)] + page_two)
        self.send()
        self.assertEqual(self.driver.clicks, [(284.0, 640)])
        self.assertEqual(self.driver.sent, [], 'nothing typed')
        self.assertEqual(self.transport.poll(), [])
        [reply] = self.transport.poll()
        self.assertEqual((reply.kind, reply.correlation), ('shop_upgrades', 'u2'))
        self.assertIn('Page 2/2', reply.text)

    def test_page_number_slash_read_as_one_still_clicks(self):
        # In a narrower window OCR read "Page 1/2" as "Page 112".
        from src import account_samples as sample
        self.page_one_on_screen()
        self.driver.lines = [line.replace('Page 1/2', 'Page 112') for line in self.driver.lines]
        self.send()
        self.assertEqual(self.driver.clicks, [(284.0, 640)])

    def test_page_one_unchanged_is_never_the_answer(self):
        self.page_one_on_screen()
        self.send()
        self.now = self.now.replace(minute=5)
        for _ in range(4):
            self.assertEqual(self.transport.poll(), [])

    def test_no_enabled_arrow_refuses_without_clicking(self):
        from src.automation_models import NotDispatched
        self.page_one_on_screen()
        self.driver.buttons[0]['runs'][1]['enabled'] = False
        with self.assertRaises(NotDispatched):
            self.send()
        self.assertIsNone(self.driver.clicks)

    def test_captcha_clears_once_a_newer_reply_appears_below_it(self):
        self.send()
        self.driver.lines = self.driver.lines + ['epicmouse used', '% fish', 'Virtual Fisher 11:42 AM',
                                                 'Please complete this captcha to continue fishing.']
        self.transport.poll()
        self.assertEqual([reply.kind for reply in self.transport.poll()], ['captcha'])
        self.assertFalse(self.transport.captcha_cleared(), 'the captcha is still the newest message')
        self.driver.lines = self.driver.lines + fish(3, 3, 30)
        self.assertTrue(self.transport.captcha_cleared())

    def test_captcha_message_is_delivered_as_a_captcha(self):
        self.send()
        self.driver.lines = self.driver.lines + ['epicmouse used', '% fish', 'Virtual Fisher 11:42 AM',
                                                 'Please complete this captcha to continue fishing.',
                                                 'Use /captcha with the code shown in the image.']
        self.assertEqual(self.transport.poll(), [])
        replies = self.transport.poll()
        self.assertEqual([(reply.kind, reply.correlation) for reply in replies], [('captcha', 'a1')])
        self.assertIn('captcha', replies[0].text)

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

    def test_refocus_passes_to_the_driver(self):
        self.driver.refocus = lambda: self.driver.sent.append('refocus')
        self.transport.refocus()
        self.assertEqual(self.driver.sent, ['refocus'])

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

    def test_reply_by_another_name_says_which_name_it_saw(self):
        # Observed 2026-10-08: the page's two name fields were swapped, so every reply looked like someone else's.
        transport = DiscordTransport(self.driver, 42, 'Virtual Fisher', 'epicmouse9925', 'epicmouse',
                                     utc=lambda: self.now)
        transport.enable()
        transport.send(ActionIntent('p1', 'fish', transport.context_id, self.now.isoformat(), 'fishing'))
        self.driver.lines += fish(2, 2, 20)
        self.assertEqual(transport.poll(), [])
        self.assertIn('used by epicmouse', transport.last_issue)
        self.assertIn('epicmouse9925', transport.last_issue)

    def test_unchanged_identical_replies_neither_acknowledge_nor_pause(self):
        self.driver.lines = fish(1, 1, 10) * 2
        self.send()
        self.assertEqual(self.transport.poll(), [])
        self.assertEqual(self.transport.poll(), [])
        self.assertTrue(self.transport.inspect_context().ok)

    def test_repeated_identical_reply_at_the_bottom_is_new(self):
        from tests.test_live_capture import BUFFS
        self.driver.lines = list(BUFFS)
        self.intent = ActionIntent('b1', 'refresh_buffs', self.transport.context_id, self.now.isoformat(), 'buffs')
        self.send()
        self.driver.lines = BUFFS + BUFFS
        self.assertEqual(self.transport.poll(), [])
        self.assertEqual([reply.correlation for reply in self.transport.poll()], ['b1'])

    def test_reply_found_after_old_messages_scroll_off(self):
        # Observed 2026-10-08 17:06: the oldest reply scrolled off and the title bar read as a lone
        # "Virtual fisher" line; the new reply at the bottom was readable the whole time.
        from tests.test_live_capture import PROFILE, BUFFS
        self.driver.lines = fish(6, 10, 131) + PROFILE + BUFFS
        self.send()
        self.driver.lines = ['Virtual fisher', "'fish", 'Reward:', '+370 xp'] + PROFILE + BUFFS + fish(4, 6, 83)
        self.assertEqual(self.transport.poll(), [])
        [reply] = self.transport.poll()
        self.assertIn('4 Salmon', reply.text)

    def test_persistently_unreadable_fishing_reply_is_delivered_after_rereads(self):
        self.send()
        self.driver.lines += fish(2, 4, 117, 'LEVEL UP! You are')
        replies = []
        for _ in range(4):
            replies += self.transport.poll()
        self.assertEqual(len(replies), 1, 'the controller decides what an unreadable result means')

    def test_forget_lets_the_run_move_on(self):
        def fail(command, cancelled):
            raise OSError('input unavailable')
        self.driver.dispatch = fail
        with self.assertRaises(TransportError): self.send()
        self.transport.forget()
        self.assertTrue(self.transport.inspect_context().ok)
        self.driver.dispatch = Driver().dispatch
        self.assertEqual(self.transport.send(self.intent).action_id, 'a1')

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

    def test_empty_chat_still_sends_and_finds_the_first_reply(self):
        self.driver.lines = ['epicmouse used', 'profile']
        self.send()
        self.assertEqual(self.transport.last_lines, ['epicmouse used', 'profile'])
        self.driver.lines = fish(1, 1, 10)
        self.assertEqual(self.transport.poll(), [])
        self.assertEqual(len(self.transport.poll()), 1)

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

    def test_verification_prompt_is_delivered_as_a_captcha(self):
        self.send()
        self.driver.lines = self.driver.lines + ['Virtual Fisher 11:42 AM', 'Please verify with /verify']
        self.transport.poll()
        self.assertEqual([reply.kind for reply in self.transport.poll()], ['captcha'])

    def test_an_answered_captcha_still_on_screen_does_not_block(self):
        self.driver.lines = self.driver.lines + ['Virtual Fisher 11:42 AM', 'Please complete this captcha.']
        self.driver.lines = self.driver.lines + fish(2, 2, 20)
        self.assertTrue(self.transport.inspect_context().ok)


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
        # The blinking caret sits on the M of an empty box.
        self.assertEqual(composer_target([box('essage @Virtual Fisher')], 'Virtual Fisher'), (380, 810))

    def test_sidebar_status_is_not_a_second_picker_command(self):
        # Live: the DM list shows "Virtual Fisher" with the status "/fish", read as "[fish", far above the picker.
        def at(text, left, top, right, bottom):
            return dict(text=text, left=left, top=top, right=right, bottom=bottom)
        boxes = [at('Virtual Fisher', 210, 461, 351, 479), at('[fish', 210, 488, 246, 503),
                 at('/fish', 570, 757, 617, 778), at('Catch some fish!', 570, 793, 695, 806),
                 at('Virtual Fisher', 1106, 775, 1210, 788)]
        self.assertEqual(command_target(boxes, '/fish', (693.0, 1470.5)), (593.5, 767.5))

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
        from tests.test_live_stats import CHARMS
        driver, clock = Driver(), Clock()
        def dispatch(command, cancelled):
            driver.sent.append(command)
            charms = ['epicmouse used', '% charms', 'Virtual Fisher 11:41 AM', *CHARMS.split('\n')]
            driver.lines += {'/profile': PROFILE, '/buffs': BUFFS, '/charms': charms,
                             '/fish': fish(2, 2, 20)}[command]
        driver.dispatch = dispatch
        transport = DiscordTransport(driver, 42, 'Virtual Fisher', 'epicmouse',
                                     'epicmouse9925', utc=clock.utc)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            # Bootstrap and one trip; the account reads have tests of their own.
            config = AutomationConfig('epicmouse', 'epicmouse9925', transport.context_id,
                                      max_trips=1, max_seconds=120, mode='live',
                                      capabilities=('refresh_profile', 'refresh_buffs', 'refresh_charms', 'fish'))
            controller = AutomationController(transport, config, AutomationJournal(root/'journal'),
                                              root/'fish', root/'review', utc=clock.utc,
                                              monotonic=clock.monotonic)
            controller.start()
            transport.enable()
            for _ in range(20):
                controller.tick()
                clock.now += .5
            self.assertEqual(driver.sent, ['/profile', '/buffs', '/charms', '/fish'])
            self.assertEqual(controller.status()['trips'], 1)
            self.assertEqual(controller.status()['dashboard']['charms']['total'], 11)
            self.assertFalse(controller.running)
            self.assertEqual(load_sessions('epicmouse', root/'fish')[0][0]['tally']['trips'], 1)


if __name__ == '__main__':
    unittest.main()


class IdenticalReplyTests(unittest.TestCase):
    """Observed 2026-10-08 14:56: two identical /profile replies filled the screen, so a third identical one
    scrolled the oldest away and the screen looked unchanged; /profile was sent again and again."""

    def setUp(self):
        from tests.test_live_capture import PROFILE
        self.profile = PROFILE  # sent 11:41 AM
        self.driver = Driver()
        self.clock = [0.0]
        self.local = [datetime(2026, 10, 6, 11, 41, 20)]
        self.transport = DiscordTransport(self.driver, 42, 'Virtual Fisher', 'epicmouse', 'epicmouse9925',
                                          monotonic=lambda: self.clock[0], local=lambda: self.local[0])
        self.transport.enable()

    def send(self, kind='refresh_profile', reply='profile'):
        self.transport.send(ActionIntent('p1', kind, self.transport.context_id,
                                         datetime.now(timezone.utc).isoformat(), reply))

    def poll(self, seconds):
        self.clock[0] += seconds
        return self.transport.poll()

    def test_identical_refresh_in_the_same_minute_is_accepted_after_a_short_wait(self):
        self.driver.lines = self.profile + self.profile
        self.send()
        self.assertEqual(self.poll(1), [])
        self.assertEqual(self.poll(1), [])
        self.assertEqual(self.poll(1.5), [])  # first stable read after the wait
        [reply] = self.poll(0.5)
        self.assertEqual(reply.correlation, 'p1')

    def test_identical_refresh_from_an_earlier_minute_is_not_the_reply(self):
        self.driver.lines = self.profile + self.profile
        self.local[0] = datetime(2026, 10, 6, 14, 56, 5)
        self.send()
        for _ in range(6):
            self.assertEqual(self.poll(1), [])
        # The real reply shows the new minute, so it is new even with identical content.
        self.driver.lines = self.profile + [line.replace('11:41 AM', '2:56 PM') for line in self.profile]
        self.assertEqual(self.poll(1), [])
        self.assertEqual(len(self.poll(1)), 1)

    def test_identical_fishing_reply_is_never_assumed(self):
        self.driver.lines = fish(1, 1, 10) * 2
        self.local[0] = datetime(2026, 10, 6, 11, 40, 20)  # fish() replies are sent 11:40 AM
        self.send('fish', 'fishing')
        for _ in range(8):
            self.assertEqual(self.poll(1), [])
