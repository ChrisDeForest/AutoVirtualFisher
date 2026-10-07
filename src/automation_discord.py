"""Pixel-observed Discord transport. Native input is opt-in and targets one foreground window.

Correlation uses a pre-dispatch baseline, unambiguous overlap, a new own invocation/reply,
and two stable captures. It cannot reconcile an action from a previous process automatically.
Do not manually invoke VF commands while this adapter owns the command stream.
"""
from datetime import datetime, timezone
import re
import threading
from secrets import token_hex

from .automation_models import CapabilityError, ContextStatus, DispatchReceipt, TransportError
from .live_capture import align, capture_frame
from .observation import Identity, Observation, VERIFIED, check_identity, recipients

COMMANDS = {'refresh_profile': '/profile', 'refresh_buffs': '/buffs', 'fish': '/fish'}
CHALLENGE = re.compile(r'captcha|\bverify\b|verification|anti.bot', re.I)


def _center(box):
    return ((box['left'] + box['right']) / 2, (box['top'] + box['bottom']) / 2)


def composer_target(boxes, channel):
    pattern = re.compile(r'Message\s*@\s*' + re.escape(channel), re.I)
    found = [box for box in boxes if pattern.fullmatch(box['text'].strip())]
    return _center(found[0]) if len(found) == 1 else None


def command_target(boxes, command, composer=None):
    """Accept one exact picker command with a nearby bot label; ambiguous layouts fail closed."""
    found = [b for b in boxes if b['text'].strip().casefold() == command.casefold()]
    if len(found) != 1:
        return None
    target = found[0]
    height = max(1, target['bottom'] - target['top'])
    if composer is not None and not (composer[1] - 15 * height <= target['top'] < composer[1] - height
                                     and abs(_center(target)[0] - composer[0]) <= 15 * height):
        return None
    bot = [b for b in boxes if b['text'].strip() == 'Virtual Fisher'
           and abs(b['top'] - target['top']) <= 2 * height
           and abs(b['left'] - target['left']) <= 10 * height]
    return _center(target) if len(bot) == 1 else None


class DiscordTransport:
    capabilities = tuple(COMMANDS)

    def __init__(self, driver, window, channel, account, username, utc=None):
        if not channel or not account or not username:
            raise ValueError('Window, channel, display name and username must be configured.')
        self.driver, self.window, self.channel = driver, window, channel
        self.identity = Identity(account, username)
        self.context_id = f'discord:{window}:{channel.casefold()}'
        self.utc = utc or (lambda: datetime.now(timezone.utc))
        self.enabled = False
        self.cancelled = threading.Event()
        self.pending = self.baseline = self.candidate = None
        self.blocked = None
        self.title = None
        self.lock = threading.Lock()

    def enable(self):
        self.cancelled.clear()
        self.enabled = True

    def stop(self):
        # No lock: cancellation must remain available during a slow capture/input operation.
        self.cancelled.set()
        self.enabled = False

    def _check(self, capture):
        context = capture.get('context') or {}
        if capture.get('status') != 'ok':
            return 'Capture unavailable: ' + str(capture.get('status'))
        if context.get('window') != self.window:
            return 'Different Discord window.'
        if (context.get('channel') or '').casefold() != self.channel.casefold():
            return 'Different or unreadable channel.'
        title = context.get('title')
        if not title:
            return 'Window title unavailable.'
        if self.title is not None and title != self.title:
            return 'Discord window title changed; context must be checked again.'
        if not self.driver.is_foreground():
            return 'Discord must remain in the foreground.'
        if CHALLENGE.search('\n'.join(capture.get('lines', []))):
            return 'Verification prompt visible; manual review required.'
        self.title = title
        return None

    def inspect_context(self):
        if self.blocked:
            return ContextStatus(False, self.context_id, self.blocked)
        try:
            problem = self._check(self.driver.capture())
        except Exception as error:
            problem = f'Capture failed: {error}'
        return ContextStatus(not problem, self.context_id, problem or '')

    def send(self, intent):
        if not self.enabled or self.cancelled.is_set():
            raise CapabilityError('Live dispatch is disabled or stopped.')
        if intent.kind not in COMMANDS:
            raise CapabilityError(f'Unsupported action: {intent.kind}')
        with self.lock:
            if self.pending is not None or self.blocked:
                raise TransportError('An earlier outcome is unresolved; do not resend.')
            if intent.context_id != self.context_id:
                raise TransportError('Intent targets a different context.')
            try:
                before = self.driver.capture()
                problem = self._check(before)
                if problem:
                    raise TransportError(problem)
                baseline = capture_frame(before['lines'])
                if not baseline['messages']:
                    raise TransportError('No complete VF reply visible to establish a baseline.')
                self.baseline, self.pending = baseline, intent
                self.candidate = None
                if self.cancelled.is_set():
                    raise TransportError('Stopped before input.')
                self.driver.dispatch(COMMANDS[intent.kind], self.cancelled.is_set)
                return DispatchReceipt(intent.action_id, self.utc().isoformat(), 'Native UI command submitted')
            except Exception as error:
                # Even a partially typed command leaves the input state uncertain. Never repeat it automatically.
                self.blocked = f'Dispatch could not be confirmed: {error}'
                raise TransportError(self.blocked) from error

    def poll(self):
        if self.pending is None or self.blocked or self.cancelled.is_set():
            return []
        with self.lock:
            try:
                capture = self.driver.capture()
                problem = self._check(capture)
                if problem:
                    self.blocked = problem
                    return []
                frame = capture_frame(capture['lines'])
                placement = align(self.baseline, frame)
                if placement['status'] != 'certain':
                    self.blocked = 'Reply placement uncertain: ' + placement['reason']
                    return []
                fresh = frame['messages'][placement['start']:]
                matches = []
                for message in fresh:
                    status, recipient, reason = check_identity(self.identity, recipients(
                        message['kind'], message['lines'], message.get('invoked_by')))
                    expected = message['kind'] == self.pending.expected_reply_kind
                    cooldown = self.pending.kind == 'fish' and any(
                        line.startswith('You must wait') for line in message['lines'])
                    # An invocation is required in addition to recipient identity for UI correlation.
                    if status == VERIFIED and message.get('invoked_by') == self.identity.display_name \
                            and (expected or cooldown):
                        matches.append((message, recipient))
                if len(matches) > 1:
                    self.blocked = 'Multiple own replies after dispatch; correlation ambiguous.'
                    return []
                if not matches:
                    self.candidate = None
                    return []
                message, recipient = matches[0]
                evidence = (message['key'], tuple(message['lines']), message.get('sent'))
                if self.candidate != evidence:
                    self.candidate = evidence
                    return []
                action = self.pending
                stamp = self.utc().isoformat()
                observation = Observation(token_hex(12), token_hex(12), stamp, self.context_id,
                                          message['kind'], recipient, '\n'.join(message['lines']),
                                          VERIFIED, correlation=action.action_id)
                self.pending = self.baseline = self.candidate = None
                return [observation]
            except Exception as error:
                self.blocked = f'Reply capture failed: {error}'
                return []
