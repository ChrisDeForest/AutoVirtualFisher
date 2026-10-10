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
        driver._frame = lambda cancelled: next(sequence)
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
    def test_submission_requires_verified_picker(self, sleep):
        driver = self.driver([
            {'boxes': [box('Message @Virtual Fisher', 800)]},
            {'boxes': [box('/fish', 600), box('Virtual Fisher', 620)]},
            {'boxes': [box('/fish', 800)]},
        ])
        driver.dispatch('/fish', lambda: False)
        self.assertEqual([event[0] for event in driver.events], ['click', 'text', 'key', 'key'])
        self.assertEqual(driver.events[-1], ('key', 13))

    @patch('src.discord_input.time.sleep')
    def test_historical_command_never_matches_picker(self, sleep):
        old = [box('/fish', 600), box('Virtual Fisher', 620)]
        driver = self.driver([{'boxes': [box('Message @Virtual Fisher', 800)] + old}]
                             + [{'boxes': old}] * 5)
        with self.assertRaises(NotDispatched): driver.dispatch('/fish', lambda: False)
        self.assertFalse(any(kind == 'key' for kind, _ in driver.events))

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
        driver._frame = lambda cancelled: next(sequence)
        driver.events.clear()
        driver.dispatch('/fish', lambda: False)
        self.assertEqual(driver.events[:2], [('click', (380.0, 810.0)), ('clear', None)])
        self.assertEqual(driver.events[-1], ('key', 13))
