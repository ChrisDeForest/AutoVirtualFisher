"""Windows input boundary. Importing this module does not initialize APIs or send input."""
import ctypes
from ctypes import wintypes
import time

from .automation_models import NotDispatched, TransportError
from .automation_discord import CHALLENGE, composer_target, command_target, command_text
from .live_capture import make_capture
from .window_capture import window_process


class WindowsDiscordDriver:
    def __init__(self, window, channel):
        import sys
        if sys.platform != 'win32':
            raise RuntimeError('Native dispatch requires Windows.')
        self.window, self.channel = window, channel
        self.title = None
        self.capture = make_capture(window_provider=self._target_window, input_region=True)
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        for name, args, result in (
            ('GetForegroundWindow', [], wintypes.HWND),
            ('SetCursorPos', [ctypes.c_int, ctypes.c_int], wintypes.BOOL),
            ('GetWindowRect', [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            ('GetWindowTextLengthW', [wintypes.HWND], ctypes.c_int),
            ('GetWindowTextW', [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            ('SendInput', [wintypes.UINT, ctypes.c_void_p, ctypes.c_int], wintypes.UINT),
        ):
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = args, result

    def _target_window(self):
        process = window_process(self.window)
        if (process or '').casefold() not in ('discord.exe', 'discordptb.exe', 'discordcanary.exe'):
            return None
        length = self.api.GetWindowTextLengthW(self.window)
        text = ctypes.create_unicode_buffer(length + 1)
        if not self.api.GetWindowTextW(self.window, text, length + 1):
            return None
        return self.window, text.value

    def is_foreground(self):
        return self.api.GetForegroundWindow() == self.window

    def _guard(self, cancelled):
        if cancelled() or not self.is_foreground():
            raise TransportError('Stopped or foreground window changed.')

    def _event(self, kind, payload, cancelled, release=False):
        if not release:
            self._guard(cancelled)
        class Mouse(ctypes.Structure):
            _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('data', wintypes.DWORD),
                        ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
        class Keyboard(ctypes.Structure):
            _fields_ = [('vk', wintypes.WORD), ('scan', wintypes.WORD), ('flags', wintypes.DWORD),
                        ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
        class Payload(ctypes.Union):
            _fields_ = [('mouse', Mouse), ('keyboard', Keyboard)]
        class Input(ctypes.Structure):
            _fields_ = [('kind', wintypes.DWORD), ('payload', Payload)]
        data = Payload()
        if kind == 0:
            data.mouse = Mouse(0, 0, 0, payload, 0, 0)
        else:
            data.keyboard = Keyboard(*payload, 0, 0)
        event = Input(kind, data)
        if self.api.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
            raise TransportError('Windows did not accept the input event.')

    def _click(self, point, cancelled):
        self._guard(cancelled)
        rect = wintypes.RECT()
        if not self.api.GetWindowRect(self.window, ctypes.byref(rect)):
            raise TransportError('Window position unavailable.')
        x, y = point
        if not self.api.SetCursorPos(round(rect.left+x), round(rect.top+y)):
            raise TransportError('Could not position the pointer.')
        self._event(0, 0x0002, cancelled)
        # Release even when stop arrives after mouse-down.
        self._event(0, 0x0004, lambda: False, release=True)

    def _key(self, vk, cancelled):
        self._event(1, (vk, 0, 0), cancelled)
        self._event(1, (vk, 0, 2), lambda: False, release=True)

    def _text(self, text, cancelled):
        for character in text:
            self._event(1, (0, ord(character), 4), cancelled)
            self._event(1, (0, ord(character), 6), lambda: False, release=True)

    def _frame(self, cancelled):
        self._guard(cancelled)
        frame = self.capture()
        context = frame.get('context') or {}
        if frame['status'] != 'ok' or context.get('window') != self.window \
                or (context.get('channel') or '').casefold() != self.channel.casefold():
            raise TransportError('Target changed or capture unavailable during command entry.')
        if CHALLENGE.search('\n'.join(frame.get('lines', []))):
            raise TransportError('Verification prompt appeared during entry; command not submitted.')
        title = context.get('title')
        if not title or (self.title is not None and self.title != title):
            raise TransportError('Window title changed during command entry.')
        self.title = title
        return frame

    def _clear(self, cancelled):
        """Select all of the focused message box and delete it. Never presses Enter."""
        self._event(1, (0x11, 0, 0), cancelled)                      # Ctrl down
        try:
            self._event(1, (0x41, 0, 0), cancelled)                  # A
            self._event(1, (0x41, 0, 2), lambda: False, release=True)
        finally:
            self._event(1, (0x11, 0, 2), lambda: False, release=True)
        self._key(0x08, cancelled)                                   # Backspace

    def dispatch(self, command, cancelled):
        # Until the first click nothing has reached Discord: a refusal here certainly sent nothing.
        try:
            frame = self._frame(cancelled)
        except TransportError as error:
            raise NotDispatched(str(error)) from error
        initial_boxes = frame.get('boxes', [])
        composer = composer_target(frame.get('original_boxes', initial_boxes), self.channel)
        if composer is None:
            # Discord hides the "Message @..." placeholder whenever the box holds anything, even a space.
            raise NotDispatched('The message box placeholder is not visible: it may hold a draft (even a space '
                                'or new line). Click it, clear it (Ctrl+A, Delete), then start again.')
        self._click(composer, cancelled)
        self._text(command, cancelled)
        # The picker is rendered asynchronously. No submit until an exact VF command row is seen.
        target = None
        old_commands = {command_text(b['text']) for b in initial_boxes}
        for _ in range(5):
            time.sleep(0.2)
            frame = self._frame(cancelled)
            # Text already visible before typing is not evidence of a newly opened picker.
            picker_boxes = [b for b in frame.get('boxes', [])
                            if command_text(b['text']) != command.casefold()
                            or command.casefold() not in old_commands]
            target = command_target(picker_boxes, command, composer)
            if target is not None:
                break
        if target is None:
            # Enter was never pressed, so the game received nothing. Clear the typed draft for the next run.
            try:
                self._clear(cancelled)
                note = 'the typed draft was cleared'
            except TransportError:
                note = 'clear the typed draft from the message box before starting again'
            raise NotDispatched(f'VF slash-command picker could not be verified; command not submitted ({note}).')
        # Keyboard selection followed by submission, as Discord's slash-command flow requires.
        # Each key retains foreground/cancellation guards. Never click the picker command.
        self._key(0x0D, cancelled)
        time.sleep(0.2)
        self._key(0x0D, cancelled)
