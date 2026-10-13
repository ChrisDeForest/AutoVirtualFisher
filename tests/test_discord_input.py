import unittest
from unittest.mock import patch

from src.discord_input import WindowsDiscordDriver
from src.automation_models import NotDispatched, TransportError


def box(text, y):
    return dict(text=text, left=300, right=460, top=y, bottom=y+20)


class NativeDispatchTests(unittest.TestCase):
    @patch('sys.platform', 'win32')
    @patch('src.discord_input.window_process', return_value='Discord.exe')
    @patch('src.discord_input.make_capture')
    @patch('src.discord_input.ctypes.WinDLL', create=True)
    def test_capture_uses_selected_window(self, dll, capture, process):
        api = dll.return_value
        api.GetWindowTextLengthW.return_value = 40
        def title(window, buffer, size):
            buffer.value = 'DM - Discord'
            return len(buffer.value)
        api.GetWindowTextW.side_effect = title
        driver = WindowsDiscordDriver(99, 'Virtual Fisher')
        provider = capture.call_args.kwargs['window_provider']
        self.assertEqual(provider(), (99, 'DM - Discord'))
        process.assert_called_with(99)
        api.SendInput.assert_not_called()

    def driver(self, frames):
        driver = WindowsDiscordDriver.__new__(WindowsDiscordDriver)
        driver.channel = 'Virtual Fisher'
        from itertools import chain, repeat
        sequence = chain(frames, repeat(frames[-1]))
        driver.events = []
        driver.deep = []
        driver._frame = lambda cancelled, deep=False: driver.deep.append(deep) or next(sequence)
        driver._click = lambda point, cancelled: driver.events.append(('click', point))
        driver._text = lambda text, cancelled: driver.events.append(('text', text))
        driver._key = lambda key, cancelled: driver.events.append(('key', key))
        driver._clear = lambda cancelled: driver.events.append(('clear', None))
        return driver

    @patch('src.discord_input.time.sleep')
    def test_submission_does_not_depend_on_selected_composer_ocr(self, sleep):
        driver = self.driver([
            {'boxes': [box('Message @Virtual Fisher', 800)]},
            {'boxes': [box('/profile', 600), box('Virtual Fisher', 620)]},
            {'boxes': [box('[profile I', 800)], 'original_boxes': [box("'profile", 800)]},
        ])
        driver.dispatch('/profile', lambda: False)
        self.assertEqual(driver.events[-1], ('key', 13))

    @patch('src.discord_input.time.sleep')
    def test_original_composer_read_survives_caret_in_enlarged_read(self, sleep):
        driver = self.driver([
            {'boxes': [box('IMessage@Virtual Fisher', 800)],
             'original_boxes': [box('Message @Virtual Fisher', 800)]},
            {'boxes': [box('[fish', 600), box('Virtual Fisher', 620)]},
            {'boxes': [box('[fish', 800)]},
        ])
        driver.dispatch('/fish', lambda: False)
        self.assertEqual(driver.events[-1], ('key', 13))

    @patch('src.discord_input.time.sleep')
    def test_click_lands_in_the_box_right_of_the_placeholder(self, sleep):
        driver = self.driver([{'boxes': [box('Message @Virtual Fisher', 800)]},
                              {'boxes': [box('/fish', 600), box('Virtual Fisher', 620)]}])
        driver.dispatch('/fish', lambda: False)
        self.assertEqual(driver.events[0], ('click', (660.0, 810.0)))

    def test_picker_read_reaches_a_full_picker_in_a_tall_window(self):
        # Live: a 1535 px window, "/rod" matched eight commands, and the picker's top row sat at y=757.
        from src.live_capture import INPUT_REGION_TOP
        self.assertLess(INPUT_REGION_TOP * 1535, 757 - 40)

    @patch('src.discord_input.time.sleep')
    def test_historical_bracket_glyph_never_matches_picker(self, sleep):
        old = [box('[fish', 600), box('Virtual Fisher', 620)]
        driver = self.driver([{'boxes': [box('Message @Virtual Fisher', 800)] + old}]
                             + [{'boxes': old}] * 5)
        with self.assertRaises(NotDispatched): driver.dispatch('/fish', lambda: False)
        self.assertFalse(any(kind == 'key' for kind, _ in driver.events))

    @patch('src.discord_input.time.sleep')
    def test_enter_selection_does_not_require_an_extra_capture(self, sleep):
        driver = self.driver([
            {'boxes': [box('Message @Virtual Fisher', 800)]},
            {'boxes': [box('/fish', 600), box('Virtual Fisher', 620)]},
            {'boxes': []}, {'boxes': [box('/fish', 800)]},
        ])
        driver.dispatch('/fish', lambda: False)
        self.assertEqual(driver.events[-1], ('key', 13))

    @patch('src.discord_input.time.sleep')
    def test_typing_starts_from_the_transports_read_and_reads_light_after(self, sleep):
        # The transport's read from just now is the first frame; the reads while typing are light ones.
        driver = WindowsDiscordDriver.__new__(WindowsDiscordDriver)
        driver.window, driver.channel, driver.title = 7, 'Virtual Fisher', None
        context = {'window': 7, 'title': 'Discord', 'channel': 'Virtual Fisher'}
        frames = [{'status': 'ok', 'context': context, 'boxes': [box('/fish', 600), box('Virtual Fisher', 620)]},
                  {'status': 'ok', 'context': context, 'boxes': [], 'original_boxes': [box('Message @Virtual Fisher', 800)]}]
        calls = []
        driver.capture = lambda **kwargs: calls.append(kwargs) or frames[min(len(calls), len(frames)) - 1]
        driver._guard = lambda cancelled: None
        driver.is_foreground = lambda: True
        driver.box_focused = True
        events = []
        driver._text = lambda text, cancelled: events.append(('text', text))
        driver._key = lambda key, cancelled: events.append(('key', key))
        before = {'status': 'ok', 'context': context, 'lines': [], 'boxes': [],
                  'original_boxes': [box('Message @Virtual Fisher', 800)]}
        driver.dispatch('/fish', lambda: False, before=before)
        self.assertEqual(events, [('text', '/fish'), ('key', 13), ('key', 13)])
        self.assertEqual(calls, [{'light': True}, {'light': True}])     # picker check, then the box emptied

    def test_a_new_verification_prompt_is_seen_in_light_reads(self):
        # Light reads have no chat lines: the prompt is counted on the window's raw text.
        driver = WindowsDiscordDriver.__new__(WindowsDiscordDriver)
        driver.window, driver.channel, driver.title, driver.challenges = 7, 'Virtual Fisher', None, None
        driver._guard = lambda cancelled: None
        context = {'window': 7, 'title': 'Discord', 'channel': 'Virtual Fisher'}
        driver._frame(lambda: False, {'status': 'ok', 'context': context, 'original_boxes': [box('hello', 100)]})
        prompt = {'status': 'ok', 'context': context, 'original_boxes': [box('hello', 100), box('Please complete this captcha', 300)]}
        with self.assertRaises(TransportError):
            driver._frame(lambda: False, prompt)

    @patch('src.discord_input.time.sleep')
    def test_submission_requires_verified_picker(self, sleep):
        driver = self.driver([
            {'boxes': [box('Message @Virtual Fisher', 800)]},
            {'boxes': [box('/fish', 600), box('Virtual Fisher', 620)]},
            {'boxes': [box('Message @Virtual Fisher', 800)]},   # sent: the box is empty again
        ])
        driver.dispatch('/fish', lambda: False)
        self.assertEqual([event[0] for event in driver.events], ['click', 'text', 'key', 'key'])
        self.assertEqual(driver.events[-1], ('key', 13))

    @patch('src.discord_input.time.sleep')
    def test_command_seen_elsewhere_before_typing_does_not_hide_the_picker(self, sleep):
        # Live: the DM list's "/fish" status was on screen before typing; the picker row opened elsewhere.
        sidebar = dict(text='[fish', left=210, top=488, right=246, bottom=503)
        driver = self.driver([
            {'boxes': [box('Message @Virtual Fisher', 800), sidebar]},
            {'boxes': [sidebar, box('/fish', 600), box('Virtual Fisher', 620)]},
        ])
        driver.dispatch('/fish', lambda: False)
        self.assertEqual(driver.events[-1], ('key', 13))

    @patch('src.discord_input.time.sleep')
    def test_historical_command_never_matches_picker(self, sleep):
        old = [box('/fish', 600), box('Virtual Fisher', 620)]
        driver = self.driver([{'boxes': [box('Message @Virtual Fisher', 800)] + old}]
                             + [{'boxes': old}] * 5)
        with self.assertRaises(NotDispatched): driver.dispatch('/fish', lambda: False)
        self.assertFalse(any(kind == 'key' for kind, _ in driver.events))

    @patch('src.discord_input.time.sleep')
    def test_a_command_missed_by_the_picker_read_is_found_zoomed_in(self, sleep):
        # Live (27" 2560x1440, 1294x1399 window): the picker's "/boosts" title was not read at the usual zoom,
        # only its description and the "Virtual Fisher" label; read at x4 it was there.
        missed = [box('Check your currently active boos&', 625), dict(box('Virtual Fisher', 610), left=848, right=917)]
        found = [box('[boosts', 600), dict(box('Virtual Fisher', 610), left=848, right=917)]
        driver = self.driver([
            {'boxes': [box('Message @Virtual Fisher', 800)]},
            {'boxes': missed},
            {'boxes': missed, 'deep_boxes': [missed, found]},
        ])
        driver.dispatch('/boosts', lambda: False)
        self.assertEqual(driver.events[-1], ('key', 13))
        self.assertEqual(driver.deep[1:3], [False, True])   # zoomed in only after a miss

    @patch('src.discord_input.time.sleep')
    def test_missing_picker_never_presses_enter(self, sleep):
        driver = self.driver([{'boxes': [box('Message @Virtual Fisher', 800)]}] + [{'boxes': []}] * 5)
        # Enter was never pressed, so nothing reached the game; the typed draft is cleared for the next run.
        with self.assertRaises(NotDispatched): driver.dispatch('/fish', lambda: False)
        self.assertFalse(any(kind == 'key' for kind, _ in driver.events))
        self.assertEqual(driver.events[-1], ('clear', None))

    def test_existing_draft_never_types(self):
        driver = self.driver([{'boxes': [box('already typing', 800)]}])
        with self.assertRaises(NotDispatched) as refused: driver.dispatch('/fish', lambda: False)
        self.assertIn('draft', str(refused.exception))
        self.assertEqual(driver.events, [])

    def test_changed_target_before_click_is_not_dispatched(self):
        driver = self.driver([{'boxes': []}])
        def changed(cancelled):
            raise TransportError('Target changed or capture unavailable during command entry.')
        driver._frame = changed
        with self.assertRaises(NotDispatched): driver.dispatch('/fish', lambda: False)
        self.assertEqual(driver.events, [])

    EMPTY = {'boxes': [box('Message @Virtual Fisher', 800)]}
    PICKER = {'boxes': [box('/shop boats', 600), box('Virtual Fisher', 620)]}
    DRAFT = {'boxes': [box('/shop category boats', 800)]}

    def keys(self, driver):
        return [event for event in driver.events if event[0] == 'key']

    @patch('src.discord_input.time.sleep')
    def test_box_emptied_after_two_enters_needs_no_more(self, sleep):
        driver = self.driver([self.EMPTY, self.PICKER, self.EMPTY])
        driver.dispatch('/shop boats', lambda: False)
        self.assertEqual(self.keys(driver), [('key', 13)] * 2)
        self.assertIsNone(driver.draft_composer)

    @patch('src.discord_input.time.sleep')
    def test_draft_left_in_the_box_gets_another_enter(self, sleep):
        # Live: commands with options sat in the box after the two Enters until Enter was pressed by hand.
        driver = self.driver([self.EMPTY, self.PICKER, self.DRAFT, self.EMPTY])
        driver.dispatch('/shop boats', lambda: False)
        self.assertEqual(self.keys(driver), [('key', 13)] * 3)
        self.assertIsNone(driver.draft_composer)

    @patch('src.discord_input.time.sleep')
    def test_enters_are_spaced_for_the_option_boxes(self, sleep):
        driver = self.driver([self.EMPTY, self.PICKER, self.EMPTY])
        order = []
        driver._key = lambda key, cancelled: order.append('key')
        sleep.side_effect = lambda seconds: order.append(seconds)
        driver.dispatch('/shop boats', lambda: False)
        first = order.index('key')
        self.assertGreaterEqual(order[first + 1], 0.4)      # the option boxes open before the sending Enter

    @patch('src.discord_input.time.sleep')
    def test_fish_gets_one_more_enter_with_short_gaps(self, sleep):
        fish = {'boxes': [box('/fish', 600), box('Virtual Fisher', 620)]}
        draft = {'boxes': [box('/fish', 800)]}
        driver = self.driver([self.EMPTY, fish, draft])
        order = []
        driver._key = lambda key, cancelled: order.append('key')
        sleep.side_effect = lambda seconds: order.append(seconds)
        driver.dispatch('/fish', lambda: False)
        self.assertEqual(order.count('key'), 3)              # a third Enter while the draft stays, and no more
        first = order.index('key')
        self.assertEqual([x for x in order[first:] if x != 'key'], [0.3] * 3)   # Enter gap, two checks
        self.assertIsNotNone(driver.draft_composer)           # left for the next attempt to clear
        driver = self.driver([self.EMPTY, fish, self.EMPTY])
        driver.dispatch('/fish', lambda: False)
        self.assertEqual(self.keys(driver), [('key', 13)] * 2)
        self.assertIsNone(driver.draft_composer)

    @patch('src.discord_input.time.sleep')
    def test_a_draft_that_never_leaves_is_left_for_the_next_attempt_to_clear(self, sleep):
        driver = self.driver([self.EMPTY, self.PICKER, self.DRAFT])
        driver.dispatch('/shop boats', lambda: False)          # it may have been sent: reported as dispatched
        self.assertEqual(self.keys(driver), [('key', 13)] * 4)
        self.assertIsNotNone(driver.draft_composer)            # the next dispatch clears what is left

    @patch('src.discord_input.time.sleep')
    def test_trouble_while_checking_after_sending_is_not_a_failure(self, sleep):
        driver = self.driver([self.EMPTY, self.PICKER])
        reads = iter([self.EMPTY, self.PICKER])
        def frame(cancelled, deep=False):
            try:
                return next(reads)
            except StopIteration:
                raise TransportError('capture failed') from None
        driver._frame = frame
        driver.dispatch('/shop boats', lambda: False)          # sent already: no NotDispatched
        self.assertEqual(self.keys(driver), [('key', 13)] * 2)

    @patch('src.discord_input.time.sleep')
    def test_cancellation_between_enters_prevents_second_key(self, sleep):
        driver = self.driver([
            {'boxes': [box('Message @Virtual Fisher', 800)]},
            {'boxes': [box('/fish', 600), box('Virtual Fisher', 620)]},
        ])
        stopped = [False]
        def key(value, cancelled):
            if cancelled():
                raise TransportError('Stopped')
            driver.events.append(('key', value))
            stopped[0] = True
        driver._key = key
        # Stopped before the final Enter: nothing was sent.
        with self.assertRaises(NotDispatched):
            driver.dispatch('/fish', lambda: stopped[0])
        self.assertEqual([event for event in driver.events if event[0] == 'key'], [('key', 13)])

    @patch('src.discord_input.window_process', return_value='notepad.exe')
    def test_non_discord_selected_window_is_rejected(self, process):
        driver = WindowsDiscordDriver.__new__(WindowsDiscordDriver)
        driver.window = 99
        self.assertIsNone(driver._target_window())


if __name__ == '__main__':
    unittest.main()


class FocusTests(unittest.TestCase):
    driver = NativeDispatchTests.driver
    """Discord can lose focus at any step (alt-tab, remote desktop). Before the final Enter nothing was sent."""

    def frames(self):
        return [{'boxes': [box('Message @Virtual Fisher', 800)]},
                {'boxes': [box('/fish', 600), box('Virtual Fisher', 620)]}]

    def lose_focus_at(self, driver, kind, nth=1):
        from src.automation_models import FocusLost
        original = getattr(driver, '_' + kind)
        seen = {'count': 0}

        def step(*args):
            seen['count'] += 1
            if seen['count'] == nth:
                raise FocusLost('Discord stopped being the active window.')
            return original(*args)
        setattr(driver, '_' + kind, step)

    @patch('src.discord_input.time.sleep')
    def test_focus_lost_while_typing_is_an_interruption_not_an_unknown(self, sleep):
        from src.automation_models import Interrupted
        driver = self.driver(self.frames())
        self.lose_focus_at(driver, 'text')
        with self.assertRaises(Interrupted) as raised:
            driver.dispatch('/fish', lambda: False)
        self.assertIn('/fish', str(raised.exception))
        self.assertFalse(any(kind == 'key' for kind, _ in driver.events), 'never pressed Enter')

    @patch('src.discord_input.time.sleep')
    def test_focus_lost_before_the_final_enter_is_an_interruption(self, sleep):
        from src.automation_models import Interrupted
        driver = self.driver(self.frames())
        self.lose_focus_at(driver, 'key', nth=2)
        with self.assertRaises(Interrupted):
            driver.dispatch('/fish', lambda: False)

    @patch('src.discord_input.time.sleep')
    def test_the_next_attempt_clears_the_interrupted_draft_then_retypes(self, sleep):
        from src.automation_models import Interrupted
        driver = self.driver(self.frames())
        self.lose_focus_at(driver, 'text')
        with self.assertRaises(Interrupted):
            driver.dispatch('/fish', lambda: False)
        # Back in focus: the box holds the half-typed draft, so its placeholder is hidden until it is cleared.
        later = [{'boxes': [box('/fi', 800)]}, {'boxes': [box('Message @Virtual Fisher', 800)]},
                 {'boxes': [box('/fish', 600), box('Virtual Fisher', 620)]}]
        from itertools import chain, repeat
        sequence = chain(later, repeat(later[-1]))
        driver.deep = []
        driver._frame = lambda cancelled, deep=False: driver.deep.append(deep) or next(sequence)
        driver.events.clear()
        driver.dispatch('/fish', lambda: False)
        self.assertEqual(driver.events[:2], [('click', (660.0, 810.0)), ('clear', None)])
        self.assertEqual(driver.events[-1], ('key', 13))


class ComposerClickTests(unittest.TestCase):
    """The message box is clicked once, then again only after Discord lost focus, a pause, or a failed entry, so
    the pointer stays free (Discord keeps the box focused between commands)."""
    driver = NativeDispatchTests.driver

    def frames(self):
        return [{'boxes': [box('Message @Virtual Fisher', 800)]},
                {'boxes': [box('/fish', 600), box('Virtual Fisher', 620)]}]

    def clicks(self, driver):
        return [event for event in driver.events if event[0] == 'click']

    def again(self, driver):
        from itertools import chain, repeat
        frames = self.frames()
        sequence = chain(frames, repeat(frames[-1]))
        driver.deep = []
        driver._frame = lambda cancelled, deep=False: driver.deep.append(deep) or next(sequence)
        driver.events.clear()

    @patch('src.discord_input.time.sleep')
    def test_later_commands_type_without_clicking(self, sleep):
        driver = self.driver(self.frames())
        driver.dispatch('/fish', lambda: False)
        self.again(driver)
        driver.dispatch('/fish', lambda: False)
        self.assertEqual(self.clicks(driver), [])
        self.assertEqual(driver.events[-1], ('key', 13))

    @patch('src.discord_input.time.sleep')
    def test_focus_loss_seen_between_commands_clicks_again(self, sleep):
        driver = self.driver(self.frames())
        driver.dispatch('/fish', lambda: False)
        driver.api = type('Api', (), {'GetForegroundWindow': lambda self: 1})()
        driver.window = 2
        self.assertFalse(driver.is_foreground())
        self.again(driver)
        driver.dispatch('/fish', lambda: False)
        self.assertEqual(self.clicks(driver), [('click', (660.0, 810.0))])

    @patch('src.discord_input.time.sleep')
    def test_resume_after_pause_clicks_again(self, sleep):
        driver = self.driver(self.frames())
        driver.dispatch('/fish', lambda: False)
        driver.refocus()
        self.again(driver)
        driver.dispatch('/fish', lambda: False)
        self.assertEqual(self.clicks(driver), [('click', (660.0, 810.0))])

    @patch('src.discord_input.time.sleep')
    def test_unverified_picker_clicks_on_the_next_attempt(self, sleep):
        driver = self.driver(self.frames())
        driver.dispatch('/fish', lambda: False)
        sequence = iter([{'boxes': [box('Message @Virtual Fisher', 800)]}] + [{'boxes': []}] * 5)
        driver.deep = []
        driver._frame = lambda cancelled, deep=False: driver.deep.append(deep) or next(sequence)
        with self.assertRaises(NotDispatched):
            driver.dispatch('/fish', lambda: False)
        self.again(driver)
        driver.dispatch('/fish', lambda: False)
        self.assertEqual(self.clicks(driver), [('click', (660.0, 810.0))])

    def test_button_click_puts_the_pointer_back(self):
        driver = WindowsDiscordDriver.__new__(WindowsDiscordDriver)
        moves = []
        class Api:
            def GetCursorPos(self, point):
                point._obj.x, point._obj.y = 1700, 300
                return True
            def SetCursorPos(self, x, y):
                moves.append((x, y))
                return True
        driver.api = Api()
        driver._click = lambda point, cancelled: moves.append(('click', point))
        driver.click((50, 60), lambda: False)
        self.assertEqual(moves, [('click', (50, 60)), (1700, 300)])
