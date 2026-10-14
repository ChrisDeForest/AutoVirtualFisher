import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.automation_live import show_alert


class CaptchaSoundTests(unittest.TestCase):
    def test_popup_opens_while_chime_is_still_playing(self):
        sound_started = threading.Event()
        release_sound = threading.Event()
        popup_opened = threading.Event()
        sound_finished = threading.Event()

        def beep(frequency, duration):
            sound_started.set()
            release_sound.wait(2)
            sound_finished.set()

        user32 = SimpleNamespace(MessageBoxW=lambda *args: popup_opened.set())
        with patch.dict('sys.modules', winsound=SimpleNamespace(Beep=beep)), \
                patch('ctypes.windll', SimpleNamespace(user32=user32), create=True):
            try:
                show_alert('Captcha detected')
                self.assertTrue(sound_started.wait(1), 'captcha should play a chime')
                self.assertTrue(popup_opened.wait(1), 'popup must not wait for the chime')
                self.assertFalse(sound_finished.is_set())
            finally:
                release_sound.set()
                sound_finished.wait(1)

    def test_audio_failure_does_not_prevent_popup(self):
        popup_opened = threading.Event()
        sound_attempted = threading.Event()

        def beep(frequency, duration):
            sound_attempted.set()
            raise RuntimeError('Audio unavailable')

        user32 = SimpleNamespace(MessageBoxW=lambda *args: popup_opened.set())
        with patch.dict('sys.modules', winsound=SimpleNamespace(Beep=beep)), \
                patch('ctypes.windll', SimpleNamespace(user32=user32), create=True):
            show_alert('Captcha detected')
            self.assertTrue(sound_attempted.wait(1))
            self.assertTrue(popup_opened.wait(1))
