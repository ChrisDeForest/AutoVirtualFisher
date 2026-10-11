"""Windows input boundary. Importing this module does not initialize APIs or send input."""
import ctypes
from ctypes import wintypes
import time

from .automation_models import FocusLost, Interrupted, NotDispatched, TransportError
from .automation_discord import CHALLENGE, _center, composer_click, composer_target, command_target, command_text
from .live_capture import channel_matches, make_capture
from .window_capture import window_process

# Seconds between the selecting and sending Enters (a command with options opens its option boxes in between), and
# between the checks that the message box emptied. Live, 0.2 s left commands with options sitting in the box.
ENTER_GAP = 0.4
# Enters pressed again while the sent command's draft is still in the box.
EXTRA_ENTERS = 2
# /fish has no options and keeps its pace (the user's choice): shorter gaps and one Enter more at most.
FISH_GAP = 0.3
FISH_EXTRA_ENTERS = 1


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
            ('GetCursorPos', [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
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
        foreground = self.api.GetForegroundWindow() == self.window
        if not foreground:
            self.box_focused = False
        return foreground

    def refocus(self):
        """Click the message box again before the next command (after a pause)."""
        self.box_focused = False

    def _guard(self, cancelled):
        if cancelled():
            raise TransportError('Stopped.')
        if not self.is_foreground():
            raise FocusLost('Discord stopped being the active window.')

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

    def click(self, point, cancelled):
        """Click a message button at window coordinates, with the same foreground and stop guards as typing, then
        put the pointer back where it was."""
        before = wintypes.POINT()
        saved = self.api.GetCursorPos(ctypes.byref(before))
        try:
            self._click(point, cancelled)
        except FocusLost as error:
            raise Interrupted('Discord stopped being the active window before the button was clicked; it is '
                              'clicked once Discord is back.') from error
        finally:
            if saved:
                self.api.SetCursorPos(before.x, before.y)

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
                or not channel_matches(context, self.channel):
            raise TransportError('Target changed or capture unavailable during command entry.')
        challenges = len(CHALLENGE.findall('\n'.join(frame.get('lines', []))))
        if self.challenges is not None and challenges > self.challenges:
            raise TransportError('Verification prompt appeared during entry; command not submitted.')
        if self.challenges is None:
            self.challenges = challenges
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
        """Type a slash command and send it. Every step first checks that Discord is still the active window.
        Until the final Enter nothing has reached the game: losing focus before it raises Interrupted, and the
        next attempt clears what was left in the message box, at the spot it was typed, before retyping."""
        self.challenges = None  # captcha text already on screen; only one appearing during entry stops it
        interrupted = (f'Typing {command} was interrupted when Discord stopped being the active window; it is '
                       'cleared and retyped once Discord is back.')
        try:
            frame = self._frame(cancelled)
            initial_boxes = frame.get('boxes', [])
            placeholder = frame.get('original_boxes', initial_boxes)
            composer = composer_target(placeholder, self.channel)
            if composer is None and getattr(self, 'draft_composer', None) is not None:
                composer, initial_boxes, placeholder = self._clear_draft(cancelled)
            if composer is None:
                # Discord hides the "Message @..." placeholder whenever the box holds anything, even a space.
                raise NotDispatched('The message box placeholder is not visible: it may hold a draft (even a space '
                                    'or new line). Click it, clear it (Ctrl+A, Delete), then start again.')
            # From the first click, anything left in the box is this command's draft. Discord keeps the box focused
            # between commands, so it is clicked only the first time and after focus loss, a pause or a failed
            # entry: the pointer stays free in between.
            self.draft_composer = composer_click(placeholder, self.channel)
            if not getattr(self, 'box_focused', False):
                self._click(self.draft_composer, cancelled)
                self.box_focused = True
            self._text(command, cancelled)
            # The picker is rendered asynchronously. No submit until an exact VF command row is seen.
            target = None
            old_commands = [b for b in initial_boxes if command_text(b['text']) == command.casefold()]

            def seen_before(b):
                # Same text at the same spot as before typing (chat history, the DM list's status).
                height = max(1, b['bottom'] - b['top'])
                return any(abs(_center(b)[0] - _center(old)[0]) <= height
                           and abs(_center(b)[1] - _center(old)[1]) <= height for old in old_commands)

            for _ in range(5):
                time.sleep(0.2)
                frame = self._frame(cancelled)
                # Text already visible before typing is not evidence of a newly opened picker.
                picker_boxes = [b for b in frame.get('boxes', [])
                                if command_text(b['text']) != command.casefold() or not seen_before(b)]
                target = command_target(picker_boxes, command, composer)
                if target is not None:
                    break
            if target is None:
                # Enter was never pressed, so the game received nothing. Clear the typed draft for the next run.
                try:
                    self._clear(cancelled)
                    self.draft_composer = None
                    note = 'the typed draft was cleared'
                except TransportError:
                    note = 'clear the typed draft from the message box before starting again'
                self.box_focused = False
                raise NotDispatched(f'VF slash-command picker could not be verified; command not submitted ({note}).')
            # Keyboard selection followed by submission, as Discord's slash-command flow requires. Never click the
            # picker command. The final Enter's key-down is the point of sending: before it, nothing was sent.
            # A command with options opens its option boxes on the first Enter; the sending Enter waits for them.
            self._key(0x0D, cancelled)
            time.sleep(self._gap(command))
            self._key(0x0D, cancelled)
        except NotDispatched:
            raise
        except FocusLost as error:
            self.box_focused = False
            raise Interrupted(interrupted) from error
        except TransportError as error:
            self.box_focused = False
            raise NotDispatched(f'{error} {command} was not sent.') from error
        if self._left_the_box(command, cancelled):
            self.draft_composer = None

    @staticmethod
    def _gap(command):
        return FISH_GAP if command == '/fish' else ENTER_GAP

    def _left_the_box(self, command, cancelled):
        """After the sending Enter: check the message box is empty again (its placeholder shows) and press Enter
        again while the draft is still there. An Enter on an empty box sends nothing, so this never sends twice.
        Returns False when the draft never left: it may still have been sent, so the command counts as dispatched
        and the next dispatch clears whatever is left. Trouble while checking stops the check, never the command."""
        try:
            extra = FISH_EXTRA_ENTERS if command == '/fish' else EXTRA_ENTERS
            for attempt in range(extra + 1):
                time.sleep(self._gap(command))
                frame = self._frame(cancelled)
                if composer_target(frame.get('original_boxes', frame.get('boxes', [])), self.channel) is not None:
                    return True
                if attempt < extra:
                    self._key(0x0D, cancelled)
        except TransportError:
            return True
        return False

    def _clear_draft(self, cancelled):
        """Go back a step: clear what an interrupted command left in the message box, at the spot it was typed,
        and check the box is empty again. Returns (composer, boxes, placeholder boxes); composer is None if it did
        not clear."""
        self._click(self.draft_composer, cancelled)
        self.box_focused = True
        self._clear(cancelled)
        frame = self._frame(cancelled)
        boxes = frame.get('boxes', [])
        placeholder = frame.get('original_boxes', boxes)
        composer = composer_target(placeholder, self.channel)
        if composer is not None:
            self.draft_composer = None
        return composer, boxes, placeholder
