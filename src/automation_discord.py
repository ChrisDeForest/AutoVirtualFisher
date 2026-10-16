"""Pixel-observed Discord transport. Native input is opt-in and targets one foreground window.

A reply is the newest message at the bottom of the chat: one that differs from the newest message before
sending (or repeats it once more), invoked by this account, of the expected kind, read the same twice.
Nothing above the bottom message is compared, so scrolling and older OCR noise cannot block a run.
Do not manually invoke VF commands while this adapter owns the command stream.
"""
from datetime import datetime, timezone
import json
import difflib
import re
import threading
import time
from secrets import token_hex

from .account_reads import ACCOUNT_READS, NEXT_PAGE, PAGE_TWO
from .account_replies import PAGE, PARSERS as ACCOUNT_PARSERS
from .automation_models import (CapabilityError, ContextStatus, DispatchReceipt, NOT_FOREGROUND, NotDispatched,
                                TransportError)
from .live_capture import capture_frame, channel_matches
from .observation import Identity, Observation, VERIFIED, check_identity, recipients


def stable_frame(lines):
    """Match usable replies on their parsed values, retaining raw text for validation/evidence."""
    from .fishing_log import parse_fishing
    from .startup_fields import reply_fields
    frame = capture_frame(lines)
    for message in frame['messages']:
        kind, text = message['kind'], '\n'.join(message['lines'])
        values = None
        if kind in ('profile', 'buffs'):
            # Startup overlap and stabilization depend on required fields, not optional
            # inventory, XP, multipliers or emoji. Raw observations remain available.
            values = {'values': reply_fields(kind, text)}
            if kind == 'profile' and values['values'].get('account_name') and 'level' in values['values']:
                message['position_key'] = (kind, json.dumps(
                    {key: values['values'][key] for key in ('account_name', 'level')}, sort_keys=True))
        elif kind == 'fishing':
            # Overlap evidence is separate from reply acceptance. Boost-caption OCR must
            # not change the key of an old catch; the controller still validates raw text.
            overlap_text = '\n'.join(line for line in message['lines']
                                     if not line.casefold().startswith('global boost by'))
            values = {k: v for k, v in parse_fishing(overlap_text).items()
                      if k not in ('warnings', 'unparsed_line_count', 'schema_version')}
            # Fish labels can change capitalization between OCR captures. Keep
            # numerical evidence exact; normalize only names in overlap keys.
            values['catches'] = {name.casefold(): count for name, count in values['catches'].items()}
            for trip in values['trip_details']:
                trip['catches'] = {name.casefold(): count for name, count in trip['catches'].items()}
            # Positioning uses the catch and trip XP, not optional quest/chest
            # text. Full keys/raw lines still stabilize and validate new replies.
            if values['trips'] == 1 and values['catches'] and values['trip_details'][0]['xp'] is not None:
                message['position_key'] = (kind, json.dumps(
                    {'catches': values['catches'], 'xp': values['trip_details'][0]['xp']}, sort_keys=True))
        if values is not None:
            values['identity'] = recipients(kind, message['lines'], message.get('invoked_by'))
            message['key'] = (kind, json.dumps(values, sort_keys=True, ensure_ascii=False))
    return frame

COMMANDS = {'refresh_profile': '/profile', 'refresh_buffs': '/buffs', 'refresh_charms': '/charms', 'fish': '/fish',
            **{kind: read.command for kind, read in ACCOUNT_READS.items()},
            # Page 2 of a paged read is a click on the → button, never typed.
            **{kind: NEXT_PAGE for kind in PAGE_TWO}}
# Identical reads of a fishing reply with problems before it is delivered as it is.
FISHING_REREADS = 2
# Seconds after sending /profile or /buffs before an identical reply sent that minute is taken as the answer.
REFRESH_GRACE = 3.0
# Seconds a read stays fresh: the context check's read serves the reply check and a send in the same step, each
# read used once by a reply check (a reply that must be seen the same twice is seen in two separate reads); the read
# that delivers a reply also serves the send right after it. A read takes 0.5-2 s live.
FRESH_READ = 0.75
# A send time; OCR can lose its AM/PM ('Virtual Fisher 4:15').
SENT = re.compile(r'(\d{1,2}):(\d{2})(?:\s*([AP])M)?', re.I)
# A Sell click's reply: the catch edited to say it was sold.
SOLD = re.compile(r'\bsold\b', re.I)


def _bottom(messages):
    """(key, send time, how many times it repeats in a row) of the newest message at the bottom, or None."""
    if not messages:
        return None
    key, run = messages[-1]['key'], 0
    for message in reversed(messages):
        if message['key'] != key:
            break
        run += 1
    return key, messages[-1].get('sent'), run


def _is_new(bottom, baseline):
    """Whether the bottom message differs from the one before sending: other content, another send minute, or
    the same reply once more in a row. An unread send time never counts as a difference."""
    if bottom is None:
        return False
    if baseline is None:
        return True
    (key, sent, run), (old_key, old_sent, old_run) = bottom, baseline
    return key != old_key or bool(sent and old_sent and not _same_time(sent, old_sent)) or run > old_run


def _minute_of_day(sent):
    """(minute of the day, 1440), or (minute of the half day, 720) when the AM/PM was not read; None if unread."""
    match = SENT.search(sent or '')
    if not match:
        return None
    if match[3] is None:
        return (int(match[1]) % 12) * 60 + int(match[2]), 720
    hour = int(match[1]) % 12 + (12 if match[3].upper() == 'P' else 0)
    return hour * 60 + int(match[2]), 1440


def _evidence(message):
    """What two reads of a reply must agree on to take it. A shop, /rod, /pet, etc.: what its parser reads from it
    (its items and their levels and prices, the page), not every line: live, a long /shop upgrades reply never
    read twice the same in 15 s (a description letter, the AM/PM of its time), so page 2 was never clicked
    (2026-10-14). The send time counts by the minute."""
    minute = _minute_of_day(message.get('sent'))
    minute = None if minute is None else minute[0] % 720     # "9:06 AM" and "9:06" are one minute
    if message['kind'] in ('profile', 'buffs'):
        return message['key'], message.get('invoked_by'), minute
    if message['kind'] in ACCOUNT_PARSERS:
        parsed = ACCOUNT_PARSERS[message['kind']]('\n'.join(message['lines']))
        return (message['kind'], json.dumps([parsed['values'], parsed.get('page')], sort_keys=True, default=str),
                message.get('invoked_by'), minute)
    return message['key'], tuple(message['lines']), minute


def _same_time(sent, other):
    """Whether two read send times are the same minute; one without its AM/PM matches either half of the day."""
    first, second = _minute_of_day(sent), _minute_of_day(other)
    if first is None or second is None:
        return sent == other
    period = min(first[1], second[1])
    return first[0] % period == second[0] % period
from .live_capture import CAPTCHA as CHALLENGE


def _center(box):
    return ((box['left'] + box['right']) / 2, (box['top'] + box['bottom']) / 2)


def _command(intent):
    """The command an intent types: its read or fishing command, or an action's line."""
    if intent.kind == 'act':
        from .actions import Action, command_line
        return command_line(Action.from_dict(intent.arguments[0]))
    return COMMANDS[intent.kind]


def sell_point(capture):
    """Where to click Sell: the lowest 'Sell' box with a 'Return' level with it (the buttons under a catch)."""
    boxes = capture.get('original_boxes') or capture.get('boxes') or []
    returns = [box for box in boxes if box['text'].strip() == 'Return']
    sells = [box for box in boxes if box['text'].strip() == 'Sell'
             and any(abs(_center(box)[1] - _center(other)[1]) <= box['bottom'] - box['top'] for other in returns)]
    return _center(max(sells, key=lambda box: box['top'])) if sells else None


def composer_box(boxes, channel):
    """The empty message box's "Message @channel" placeholder. The blinking caret sits on its M, so OCR may
    read it without one, or read the caret in front of it as I, | or l. The name after the @ is compared loosely
    (spaces, one or two misread letters, a stray mark after it): a strict match failed one read in a few live and
    each failure cost a fish (2026-10-14)."""
    # Live 2026-10-14: the caret and the M read as "b", "bl", "BA", "[vl", "['l": up to three marks before "essage".
    pattern = re.compile(r'\S{0,3}?essage\s*@\s*(.+?)[\W_]{0,3}', re.I)
    wanted = re.sub(r'\s+', '', channel).casefold()

    def matches(text):
        found = pattern.fullmatch(text.strip())
        if not found:
            return False
        name = re.sub(r'\s+', '', found[1]).casefold()
        return name == wanted or difflib.SequenceMatcher(None, name, wanted).ratio() >= 0.85
    found = [box for box in boxes if matches(box['text'])]
    return found[0] if len(found) == 1 else None


def bottom_text(boxes, count=3):
    """The lowest few boxes read, for saying what was there when the message box was not found."""
    lowest = sorted(boxes, key=lambda box: box['top'])[-count:]
    return ' | '.join(box['text'].strip() for box in lowest) or 'nothing'


def composer_target(boxes, channel):
    box = composer_box(boxes, channel)
    return _center(box) if box is not None else None


def composer_click(boxes, channel):
    """Where to click the message box: in its empty part, well right of the placeholder (ten text heights, about
    150 px live), so the pointer stays clear of the placeholder text the box checks read."""
    box = composer_box(boxes, channel)
    if box is None:
        return None
    return (box['right'] + 10 * (box['bottom'] - box['top']), _center(box)[1])


def command_target(boxes, command, composer=None):
    """Accept one exact picker command with a nearby bot label; ambiguous layouts fail closed."""
    def in_picker(b):
        # The picker opens just above the message box. The same text elsewhere (the DM list shows the bot's
        # status, "/fish") is not a second picker row.
        height = max(1, b['bottom'] - b['top'])
        return (composer[1] - 40 * height <= b['top'] < composer[1] - height
                and abs(_center(b)[0] - composer[0]) <= 15 * height)

    found = [b for b in boxes if command_text(b['text']) == command.casefold()
             and (composer is None or in_picker(b))]
    if len(found) != 1:
        return None
    target = found[0]
    height = max(1, target['bottom'] - target['top'])
    labels = [b for b in boxes if b is not target
              and command_text(b['text']) != command.casefold()
              and abs(_center(b)[1] - _center(target)[1]) <= 2 * height
              and target['left'] - height <= b['left'] <= target['left'] + 150 * height]
    # Wide Discord rows put ownership at the far right. A VF label on an adjacent
    # row must not override a nearer other-bot label belonging to this command.
    far_labels = [b for b in labels if b['left'] >= target['right'] + 10 * height]
    labels = far_labels or labels
    if not labels:
        return None
    distance = min(abs(_center(b)[1] - _center(target)[1]) for b in labels)
    nearest = [b for b in labels if abs(_center(b)[1] - _center(target)[1]) == distance]
    return _center(target) if len(nearest) == 1 and nearest[0]['text'].strip() == 'Virtual Fisher' else None


def command_text(text):
    """Normalize only the observed OCR slash glyph; preserve the exact command name."""
    text = text.strip().casefold()
    return '/' + text[1:] if text.startswith(('[', "'")) else text


class DiscordTransport:
    capabilities = tuple(COMMANDS) + ('act',)

    def __init__(self, driver, window, channel, account, username, utc=None, monotonic=None, local=None):
        if not channel or not account or not username:
            raise ValueError('Window, channel, display name and username must be configured.')
        self.driver, self.window, self.channel = driver, window, channel
        self.identity = Identity(account, username)
        self.context_id = f'discord:{window}:{channel.casefold()}'
        self.utc = utc or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic or time.monotonic
        self.local = local or (lambda: datetime.now().astimezone())  # Discord shows local send times
        self.sent_clock, self.sent_minute = None, None
        self.enabled = False
        self.closed = False
        self.cancelled = threading.Event()
        self.pending = self.baseline = self.candidate = None
        self.blocked = None
        self.last_issue = None
        self.sending_seen = False
        self.last_frame = None
        self.last_lines = None         # the raw chat lines of the latest capture, for diagnosis
        self.title = None
        self.captcha_bottom = None     # the newest message when a captcha was delivered
        self.lock = threading.Lock()
        self.fresh = None              # (monotonic time, read) of the context check, for one later use
        self.last_read = None          # (monotonic time, read) of the read _read returned last

    def _read(self):
        """The context check's read when it is fresh (used once), else a new read."""
        fresh, self.fresh = self.fresh, None
        if fresh is None or self.monotonic() - fresh[0] > FRESH_READ:
            capture = self.driver.capture()
            fresh = (self.monotonic(), capture)
        self.last_read = fresh
        return fresh[1]

    def enable(self):
        if self.closed:
            raise TransportError('Live input has been closed.')
        self.enabled = False
        hook = getattr(self.driver, 'enable_input', None)
        if hook is not None:
            hook()
        self.cancelled.clear()
        self.enabled = True

    def stop(self):
        # No lock: cancellation must remain available during a slow capture/input operation.
        self.cancelled.set()
        self.enabled = False
        hook = getattr(self.driver, 'cancel_input', None)
        if hook is not None:
            hook()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop()
        hook = getattr(self.driver, 'close', None)
        if hook is not None:
            hook()

    def _check(self, capture):
        context = capture.get('context') or {}
        if capture.get('status') != 'ok':
            return 'Capture unavailable: ' + str(capture.get('status'))
        if context.get('window') != self.window:
            return 'Different Discord window.'
        if not channel_matches(context, self.channel):
            hints = context.get('channel_hints') or {}
            return (f'Different or unreadable channel (read: search box {hints.get("search")!r}, message box '
                    f'{hints.get("composer")!r}).')
        title = context.get('title')
        if not title:
            return 'Window title unavailable.'
        if self.title is not None and title != self.title:
            return 'Discord window title changed; context must be checked again.'
        if getattr(self.driver, 'requires_foreground', True) and not self.driver.is_foreground():
            return NOT_FOREGROUND
        # A captcha is not checked here: a new one at the bottom of the chat is delivered as a reply, and the
        # controller pauses until the person resumes. One already answered may stay on screen for a while.
        self.title = title
        return None

    def refocus(self):
        """After a pause, the next command clicks the message box first."""
        self.driver.refocus()

    def inspect_context(self):
        if self.blocked:
            return ContextStatus(False, self.context_id, self.blocked)
        try:
            capture = self.driver.capture()
            self.fresh = (self.monotonic(), capture)
            problem = self._check(capture)
        except Exception as error:
            problem = f'Capture failed: {error}'
        return ContextStatus(not problem, self.context_id, problem or self.last_issue or '')

    def send(self, intent):
        if not self.enabled or self.cancelled.is_set():
            raise CapabilityError('Live dispatch is disabled or stopped.')
        if intent.kind not in COMMANDS and intent.kind != 'act':
            raise CapabilityError(f'Unsupported action: {intent.kind}')
        with self.lock:
            if self.pending is not None or self.blocked:
                raise TransportError('An earlier outcome is unresolved; do not resend.')
            if intent.context_id != self.context_id:
                raise TransportError('Intent targets a different context.')
            # Checks before input only read the screen: a refusal here certainly sent nothing.
            try:
                before = self._read()
            except Exception as error:
                raise NotDispatched(f'Capture failed before input: {error}') from error
            self.last_lines = list(before.get('lines', []))
            problem = self._check(before)
            if problem:
                raise NotDispatched(problem)
            prepare = getattr(self.driver, 'prepare_input', None)
            if prepare is not None:
                try:
                    prepare(before, self.cancelled.is_set)
                except TransportError as error:
                    raise NotDispatched(str(error)) from error
            frame = stable_frame(before['lines'])
            self.last_frame = frame
            if self.cancelled.is_set():
                raise NotDispatched('Stopped before input.')
            point = self._next_page_point(intent, before, frame) if COMMANDS.get(intent.kind) == NEXT_PAGE else None
            try:
                # Only the bottom of the chat matters: what was newest before sending.
                self.baseline, self.pending = _bottom(frame['messages']), intent
                self.sending_seen = False
                self.candidate = None
                self.rereads = 0
                self.last_issue = None
                now = self.local()
                self.sent_clock, self.sent_minute = self.monotonic(), now.hour * 60 + now.minute
                if point is not None:
                    self.driver.click(point, self.cancelled.is_set)
                    return DispatchReceipt(intent.action_id, self.utc().isoformat(), 'Next-page button clicked')
                if intent.kind == 'act':
                    from .actions import Action, command_line
                    action = Action.from_dict(intent.arguments[0])
                    # Selling clicks the Sell button under the newest catch (it sells all); typed when there is none.
                    target = sell_point(before) if action.act == 'sell' else None
                    if target is not None:
                        self.driver.click(target, self.cancelled.is_set)
                        return DispatchReceipt(intent.action_id, self.utc().isoformat(), 'Sell button clicked')
                    line = command_line(action)
                    if ' ' not in line:
                        # No option to fill (/daily): sent like a read, so Enter waits for VF's own picker row and
                        # a miss refuses with nothing sent.
                        self.driver.dispatch(line, self.cancelled.is_set, before=before)
                    else:
                        self.driver.dispatch_action(line, action.amount if action.act == 'buy' else None,
                                                    self.cancelled.is_set, before=before)
                    return DispatchReceipt(intent.action_id, self.utc().isoformat(), 'Action command submitted')
                self.driver.dispatch(COMMANDS[intent.kind], self.cancelled.is_set, before=before)
                return DispatchReceipt(intent.action_id, self.utc().isoformat(), 'UI command submitted')
            except NotDispatched:
                # Nothing was sent (the driver says so: interrupted, or refused before the final Enter).
                self.pending = self.baseline = None
                raise
            except CapabilityError:
                # Terminal refusal with proof that no command reached the game.
                self.pending = self.baseline = None
                raise
            except Exception as error:
                # Enter may or may not have been pressed. Not repeated; the controller decides when to move on.
                self.blocked = f'Dispatch could not be confirmed: {error}'
                raise TransportError(self.blocked) from error

    def _next_page_point(self, intent, capture, frame):
        """Where to click for page 2: the rightmost button of the lowest button row, which must be an enabled
        arrow, under this read's page 1 as the newest message. Refuses (nothing clicked) otherwise."""
        messages = frame['messages']
        page = PAGE.search('\n'.join(messages[-1]['lines'])) if messages else None
        if not (messages and messages[-1]['kind'] == intent.expected_reply_kind and page and page[1] == '1'
                and int(page[2]) > 1):
            raise NotDispatched('Page 1 of this read is not the newest message.')
        rows = capture.get('buttons') or []
        if not rows:
            raise NotDispatched('Next-page button not found.')
        row = max(rows, key=lambda found: found['y'])
        arrow = max(row['runs'], key=lambda run: run['right'])
        if not arrow['enabled']:
            raise NotDispatched('The next-page button is disabled.')
        return ((arrow['left'] + arrow['right']) / 2, row['y'])

    def captcha_cleared(self):
        """After a captcha: whether a newer Virtual Fisher message that is not a captcha now sits below it. The
        bot answers nothing else until the captcha is solved, so such a message means it was."""
        if self.captcha_bottom is None:
            return False
        with self.lock:
            try:
                capture = self.driver.capture()
            except Exception:
                return False
            if self._check(capture):
                return False
            messages = stable_frame(capture['lines'])['messages']
            if not messages or messages[-1]['kind'] == 'captcha' or not _is_new(_bottom(messages), self.captcha_bottom):
                return False
            self.captcha_bottom = None
            return True

    def _identical_refresh(self, messages):
        """A /profile or /buffs reply identical to one already at the bottom cannot be told apart on screen. Its
        data is the same either way, so after a short wait an own reply of that kind sent this minute is taken."""
        # Typed reads only: a page-2 click must show a changed page.
        if (not messages or not self.pending.kind.startswith('refresh_') or self.pending.kind in PAGE_TWO
                or self.sent_clock is None):
            return False
        message = messages[-1]
        sent = _minute_of_day(message.get('sent'))
        return (message['kind'] == self.pending.expected_reply_kind
                and message.get('invoked_by') == self.identity.display_name
                and sent is not None and (sent[0] - self.sent_minute) % sent[1] <= 1
                and self.monotonic() - self.sent_clock >= REFRESH_GRACE)

    def forget(self):
        """Stop waiting for the pending command's reply (the controller has moved on without it)."""
        with self.lock:
            self.pending = self.baseline = self.candidate = None
            self.blocked = None
            self.sending_seen = False
            self.last_issue = None

    def poll(self):
        if self.pending is None or self.blocked or self.cancelled.is_set():
            return []
        with self.lock:
            try:
                capture = self._read()
                problem = self._check(capture)
                if problem:
                    # Reported by inspect_context while it lasts; nothing is blocked for later.
                    self.candidate = None
                    self.last_issue = problem
                    return []
                lines = capture['lines']
                invocations = [index for index, line in enumerate(lines) if re.match(r'^\S+ used\b', line)]
                if invocations and _command(self.pending) != NEXT_PAGE:
                    tail = lines[invocations[-1]:]
                    from .ocr_cleanup import MESSAGE_HEADER
                    command = _command(self.pending).lstrip('/').split(' ')[0]
                    if (re.match(r'^' + re.escape(self.identity.display_name) + r' used\b', tail[0])
                            and re.search(r'\b' + re.escape(command) + r'\b', '\n'.join(tail[:2]))
                            and any(MESSAGE_HEADER.fullmatch(line) for line in tail[1:])
                            and re.fullmatch(r'\W*Sending command(?:\.{3}|…)?\W*', tail[-1], re.I)):
                        self.sending_seen = True
                        self.candidate = None
                        self.last_issue = 'Discord shows Sending command; retrying reply capture without resending.'
                        return []
                self.last_lines = list(capture['lines'])
                frame = stable_frame(capture['lines'])
                self.last_frame = frame
                # The reply is the newest message at the bottom of the chat: new when it differs from what was
                # newest before sending, or when the same reply now appears one more time in a row.
                bottom = _bottom(frame['messages'])
                if not _is_new(bottom, self.baseline) and not self._identical_refresh(frame['messages']):
                    self.candidate = None
                    self.last_issue = f'Waiting for the reply to {_command(self.pending)}.'
                    return []
                message = frame['messages'][-1]
                status, recipient, reason = check_identity(self.identity, recipients(
                    message['kind'], message['lines'], message.get('invoked_by')))
                if message['kind'] == 'captcha':
                    # Delivered whoever it names: a captcha pauses the run, and the person decides.
                    evidence = ('captcha', tuple(message['lines']), message.get('sent'))
                    if self.candidate != evidence:
                        self.candidate, self.rereads = evidence, 0
                        return []
                    action = self.pending
                    observation = Observation(token_hex(12), token_hex(12), self.utc().isoformat(), self.context_id,
                                              'captcha', recipient, '\n'.join(message['lines']), status,
                                              correlation=action.action_id)
                    self.pending = self.baseline = self.candidate = None
                    self.captcha_bottom = _bottom(frame['messages'])
                    self.last_issue = 'Virtual Fisher asked for a captcha.'
                    return [observation]
                # An action's reply is the new own message at the bottom, whatever it is (a Sell click edits the
                # catch in place, which changes the bottom message); the run reads what it says.
                expected = message['kind'] == self.pending.expected_reply_kind or self.pending.kind == 'act'
                if self.pending.kind == 'act' and self.pending.arguments[0].get('act') == 'sell' \
                        and message['kind'] == 'fishing' and not SOLD.search('\n'.join(message['lines'])):
                    # Clicking Sell restyles the catch's buttons before the game edits it: the catch is not the
                    # reply until it says what was sold (seen live 2026-10-13: an unclear sell in 1 s).
                    expected = False
                if expected and self.pending.kind in PAGE_TWO:
                    # Page 2 replaces page 1 in the same message; only a later page answers the click.
                    page = PAGE.search('\n'.join(message['lines']))
                    expected = bool(page and int(page[1]) >= 2)
                cooldown = self.pending.kind == 'fish' and any(
                    line.startswith('You must wait') for line in message['lines'])
                # An own invocation is required in addition to recipient identity.
                if not (status == VERIFIED and message.get('invoked_by') == self.identity.display_name
                        and (expected or cooldown)):
                    self.candidate = None
                    invoker = message.get('invoked_by')
                    if invoker and invoker.casefold() != self.identity.display_name.casefold():
                        self.last_issue = (f'The newest reply was used by {invoker}, not {self.identity.display_name}. '
                                           'If that is you, check the names on the Automation page.')
                    else:
                        self.last_issue = 'The newest message is not the reply to this command yet.'
                    return []
                self.last_issue = None
                problems = []
                if message['kind'] == 'fishing' and not cooldown:
                    from .live_capture import fishing_problems
                    problems = fishing_problems('\n'.join(message['lines']))
                evidence = _evidence(message)
                # A fishing result that reads cleanly (one trip, known fish, its XP) is complete when first seen:
                # it is taken from one read. Other replies, and results with problems, must read the same twice.
                clean_catch = message['kind'] == 'fishing' and not cooldown and not problems
                if self.candidate != evidence and not clean_catch:
                    self.candidate, self.rereads = evidence, 0
                    return []
                if problems and self.rereads < FISHING_REREADS:
                    # A reply may still be rendering. After a few identical reads it is delivered as it is.
                    self.rereads += 1
                    self.last_issue = 'Rereading incomplete fishing reply: ' + '; '.join(problems)
                    return []
                action = self.pending
                stamp = self.utc().isoformat()
                observation = Observation(token_hex(12), token_hex(12), stamp, self.context_id,
                                          message['kind'], recipient, '\n'.join(message['lines']),
                                          VERIFIED, correlation=action.action_id)
                self.pending = self.baseline = self.candidate = None
                # Its newest message is this reply: the next send in the same step starts from this read.
                self.fresh = self.last_read
                return [observation]
            except Exception as error:
                self.candidate = None
                self.last_issue = f'Reply capture failed: {error}'
                return []
