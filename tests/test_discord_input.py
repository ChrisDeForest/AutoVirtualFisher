import unittest
from unittest.mock import patch

from src.discord_input import WindowsDiscordDriver
from src.automation_models import TransportError


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
        sequence = iter(frames)
        driver.events = []
        driver._frame = lambda cancelled: next(sequence)
        driver._click = lambda point, cancelled: driver.events.append(('click', point))
        driver._text = lambda text, cancelled: driver.events.append(('text', text))
        driver._key = lambda key, cancelled: driver.events.append(('key', key))
        return driver

    @patch('src.discord_input.time.sleep')
    def test_submission_requires_verified_picker(self, sleep):
        driver = self.driver([
            {'boxes': [box('Message @Virtual Fisher', 800)]},
            {'boxes': [box('/fish', 600), box('Virtual Fisher', 620)]},
            {'boxes': [box('/fish', 800)]},
        ])
        driver.dispatch('/fish', lambda: False)
        self.assertEqual([event[0] for event in driver.events], ['click', 'text', 'click', 'key'])
        self.assertEqual(driver.events[-1], ('key', 13))

    @patch('src.discord_input.time.sleep')
    def test_unconfirmed_selection_never_submits(self, sleep):
        driver = self.driver([
            {'boxes': [box('Message @Virtual Fisher', 800)]},
            {'boxes': [box('/fish', 600), box('Virtual Fisher', 620)]},
            {'boxes': []},
        ])
        with self.assertRaises(TransportError): driver.dispatch('/fish', lambda: False)
        self.assertFalse(any(kind == 'key' for kind, _ in driver.events))

    @patch('src.discord_input.time.sleep')
    def test_picker_still_open_never_submits(self, sleep):
        picker = [box('/fish', 600), box('Virtual Fisher', 620)]
        driver = self.driver([
            {'boxes': [box('Message @Virtual Fisher', 800)]},
            {'boxes': picker},
            {'boxes': picker + [box('/fish', 800)]},
        ])
        with self.assertRaises(TransportError): driver.dispatch('/fish', lambda: False)
        self.assertFalse(any(kind == 'key' for kind, _ in driver.events))

    @patch('src.discord_input.time.sleep')
    def test_historical_command_never_matches_picker(self, sleep):
        old = [box('/fish', 600), box('Virtual Fisher', 620)]
        driver = self.driver([{'boxes': [box('Message @Virtual Fisher', 800)] + old}]
                             + [{'boxes': old}] * 5)
        with self.assertRaises(TransportError): driver.dispatch('/fish', lambda: False)
        self.assertFalse(any(kind == 'key' for kind, _ in driver.events))

    @patch('src.discord_input.time.sleep')
    def test_missing_picker_never_presses_enter(self, sleep):
        driver = self.driver([{'boxes': [box('Message @Virtual Fisher', 800)]}] + [{'boxes': []}] * 5)
        with self.assertRaises(TransportError): driver.dispatch('/fish', lambda: False)
        self.assertFalse(any(kind == 'key' for kind, _ in driver.events))

    def test_existing_draft_never_types(self):
        driver = self.driver([{'boxes': [box('already typing', 800)]}])
        with self.assertRaises(TransportError): driver.dispatch('/fish', lambda: False)
        self.assertEqual(driver.events, [])

    @patch('src.discord_input.window_process', return_value='notepad.exe')
    def test_non_discord_selected_window_is_rejected(self, process):
        driver = WindowsDiscordDriver.__new__(WindowsDiscordDriver)
        driver.window = 99
        self.assertIsNone(driver._target_window())


if __name__ == '__main__':
    unittest.main()
