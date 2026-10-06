"""Watch the Discord window, read new Virtual Fisher replies, save clean ones, and queue the rest for review.

Read-only: the window is captured and read with OCR; nothing is typed, clicked, or sent to Discord.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from statistics import median
import tempfile
import threading

from .bait_compare import NOT_RECORDED, assign_bait
from .catch_check import load_fish_reference
from .fishing_log import fingerprint, parse_fishing
from .fishing_sessions import save_session
from .ocr_cleanup import clean_ocr
from .paste_detect import _kind, split_messages
from .player_snapshots import save_snapshot, validate_full_state
from .player_state import _clean, parse_responses
from .review_queue import add_item

# Bot replies that carry nothing to record.
HARMLESS = re.compile(r"^(You don't have enough|You will now catch more fish|You are already boosting|"
                      r'You are already using|Current bait:|Bait Shop|/bait|You must wait|Your cooldown)', re.I)
# How far chat lines may sit from the message text's left edge, in OCR text heights. Measuring in text
# heights rather than pixels keeps this independent of window size, resolution, DPI, and Discord zoom.
LEFT_MARGIN_HEIGHTS = 3
RIGHT_REACH_HEIGHTS = 25
# Captures in a row without overlap (about a minute at the default interval) before accepting the new view.
# Brief changes, such as the slash-command menu covering messages, resolve well before this.
RESYNC_AFTER = 12
LOG_LENGTH = 30


def chat_lines(boxes: list[dict]) -> list[str]:
    """Keep the lines in the column where Virtual Fisher's messages are, in reading order."""
    anchors = [box for box in boxes if _kind(_clean(box['text']))]
    if not anchors:
        return []
    left_edge = median(box['left'] for box in anchors)
    height = median(box['bottom'] - box['top'] for box in anchors) or 1
    column = [box for box in boxes
              if left_edge - LEFT_MARGIN_HEIGHTS * height <= box['left'] <= left_edge + RIGHT_REACH_HEIGHTS * height]
    column.sort(key=lambda box: box['top'])
    rows, ordered = [], []
    for box in column:
        if rows and box['top'] - rows[-1][0]['top'] < height / 2:
            rows[-1].append(box)
        else:
            rows.append([box])
    for row in rows:
        ordered += [box['text'] for box in sorted(row, key=lambda box: box['left'])]
    return ordered


def capture_messages(lines: list[str]) -> list[dict]:
    """Split chat lines into Virtual Fisher messages: [{'kind', 'lines'}], oldest first."""
    messages = []
    for message in split_messages(clean_ocr(lines, 'capture')):
        if not message['bot']:
            continue
        body = [line.strip() for line in message['lines'] if line.strip()]
        if not body:
            continue
        kinds = [found for found in (_kind(_clean(line)) for line in body) if found]
        messages.append({'kind': kinds[0] if kinds else None, 'lines': body})
    return messages


def new_messages(previous: list | None, current: list) -> list | None:
    """Return messages below the overlap with the previous capture; None when the views do not overlap.

    Matching runs of messages (not individual texts) keeps identical results, such as two equal catches in a
    row, as separate trips. The current top message may be cut off, so alignment may skip it.
    """
    if previous is None:
        return []
    for offset in (0, 1):
        visible = current[offset:]
        for start in range(len(previous)):
            overlap = previous[start:]
            if len(overlap) <= len(visible) and visible[:len(overlap)] == overlap:
                return visible[len(overlap):]
    return None


def capture_discord() -> dict:
    """Capture and read the Discord window. Returns {'status', 'lines'}; the image is deleted at once."""
    from .screenshot_ocr import ocr_available, read_image_boxes
    from .window_capture import capture_supported, capture_window, find_discord_window

    if not capture_supported() or not ocr_available():
        return {'status': 'unsupported', 'lines': []}
    window = find_discord_window()
    if window is None:
        return {'status': 'no_window', 'lines': []}
    handle, temporary = tempfile.mkstemp(suffix='.bmp')
    os.close(handle)
    try:
        info = capture_window(window[0], Path(temporary))
        if info['minimized']:
            return {'status': 'minimized', 'lines': []}
        if info['blank']:
            return {'status': 'blank', 'lines': []}
        return {'status': 'ok', 'lines': chat_lines(read_image_boxes(Path(temporary)))}
    finally:
        os.unlink(temporary)


STATUS_TEXT = {
    'unsupported': 'Live capture needs Windows and the OCR packages (see README).',
    'no_window': 'Discord window not found; waiting for it to open.',
    'minimized': 'Discord is minimized; paused until it is restored.',
    'blank': 'The capture was blank (a minimized Remote Desktop session or a locked screen can cause this); paused.',
}


class LiveCapture:
    def __init__(self, snapshot_root, fishing_root, review_root, capture=capture_discord, interval=5.0):
        self.roots = {'snapshots': Path(snapshot_root), 'fishing': Path(fishing_root), 'review': Path(review_root)}
        self.capture, self.interval = capture, interval
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.running = False
        self.account, self.bait = None, NOT_RECORDED
        self.state = 'Stopped.'
        self.previous, self.misses = None, 0
        self.latest = {}
        self.counts = {'trips': 0, 'sessions': 0, 'snapshots': 0, 'flagged': 0, 'ignored': 0}
        self.log = []
        self.last_capture = None

    def _note(self, text):
        self.log.insert(0, (datetime.now().strftime('%H:%M:%S'), text))
        del self.log[LOG_LENGTH:]

    def start(self, account, bait, thread=True):
        with self.lock:
            if self.running:
                return
            self.account, self.bait = account, bait
            self.previous, self.misses, self.latest = None, 0, {}
            self.counts = dict.fromkeys(self.counts, 0)
            self.running, self.state = True, 'Starting: messages already on screen are treated as seen.'
            self._note(f'Started for {account}' + (f' with {bait}' if bait else ''))
            self.stop_event.clear()
        if thread:
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            if self.running:
                self._note('Stopped')
            self.running, self.state = False, 'Stopped.'
            self.stop_event.set()

    def _loop(self):
        while self.running:
            try:
                self.step()
            except Exception as error:  # keep watching; a single failed capture should not end the session
                with self.lock:
                    self.state = f'Capture failed: {error}'
                    self._note(self.state)
            if self.stop_event.wait(self.interval):
                break

    def status(self):
        with self.lock:
            return {'running': self.running, 'account': self.account, 'bait': self.bait, 'state': self.state,
                    'counts': dict(self.counts), 'log': list(self.log), 'last_capture': self.last_capture}

    def step(self):
        result = self.capture()
        with self.lock:
            if not self.running:
                return
            self.last_capture = datetime.now().strftime('%H:%M:%S')
            if result['status'] != 'ok':
                self.state = STATUS_TEXT.get(result['status'], result['status'])
                return
            messages = capture_messages(result['lines'])
            if not messages:
                self.state = 'No Virtual Fisher messages visible in Discord.'
                return
            fresh = new_messages(self.previous, messages)
            if fresh is None:
                self.misses += 1
                if self.misses < RESYNC_AFTER:
                    self.state = ('Paused: the chat view moved (scrolled or another channel). Scroll back to the '
                                  'newest messages to resume.')
                    return
                # Messages the old view did not show may be new or may already be saved; let a person decide.
                unplaced = list(messages)
                for message in self.previous or []:
                    if message in unplaced:
                        unplaced.remove(message)
                for message in unplaced:
                    if message['kind']:
                        self._flag(message['kind'], '\n'.join(message['lines']),
                                   ['Seen after the chat view changed; it may already be saved or may be new.'])
                self._note('View changed for a while; continuing from what is on screen now.')
                fresh = []
            self.misses = 0
            self.previous = messages
            self._process(fresh)
            self.state = f'Watching. Last capture {self.last_capture}.'

    def _flag(self, kind, text, reasons):
        add_item(self.roots['review'], self.account, kind or 'unknown', text, reasons)
        self.counts['flagged'] += 1
        self._note(f'Needs review ({kind or "unknown"}): {reasons[0]}')

    def _process(self, messages):
        clean_fishing = []
        for message in messages:
            kind, text = message['kind'], '\n'.join(message['lines'])
            if kind is None:
                if HARMLESS.match(_clean(message['lines'][0])):
                    self.counts['ignored'] += 1
                else:
                    self._flag(None, text, ['Not a recognized Virtual Fisher response.'])
            elif kind == 'fishing':
                reasons = self._fishing_problems(text)
                if reasons:
                    self._flag(kind, text, reasons)
                else:
                    clean_fishing.append(text)
            else:
                reasons = self._player_problems(kind, text)
                if reasons:
                    self._flag(kind, text, reasons)
                else:
                    self._save_player(kind, text)
        if clean_fishing:
            self._save_fishing('\n\n'.join(clean_fishing))

    def _fishing_problems(self, text):
        tally = parse_fishing(text)
        reasons = list(tally['warnings'])
        if tally['trips'] != 1:
            reasons.append(f'Expected one fishing result, found {tally["trips"]}.')
        reference = load_fish_reference() or {}
        unknown = [name for name in tally['catches'] if name.casefold() not in reference]
        if unknown:
            reasons.append('Not in the wiki fish table: ' + ', '.join(unknown))
        if tally['trips'] == 1 and tally['trip_details'][0]['xp'] is None:
            reasons.append('No XP line was read.')
        return reasons

    def _player_problems(self, kind, text):
        state = parse_responses(**{kind: text})
        reasons = list(state['warnings'])
        unparsed = state['unparsed'].get(kind, [])
        if unparsed:
            reasons.append('Lines not understood: ' + ' | '.join(unparsed))
        if kind == 'profile':
            name = state['profile']['account_name']
            if name and self.account and name.casefold() != self.account.casefold():
                reasons.append(f'Profile is for {name}, not {self.account}.')
            full, problems = validate_full_state(state)
            if not full:
                reasons += problems
        if kind == 'pet':
            missing = [pet for pet, details in (state['pets']['owned'] or {}).items() if details['xp_current'] is None]
            if missing:
                reasons.append('Pet XP could not be read for ' + ', '.join(missing) + '.')
        return reasons

    def _save_player(self, kind, text):
        self.latest[kind] = text
        state = parse_responses(**self.latest)
        try:
            if 'profile' in self.latest:
                result = save_snapshot(state, self.roots['snapshots'], mode='automatic')
            else:
                result = save_snapshot(state, self.roots['snapshots'], mode='manual', account_name=self.account)
        except OSError as error:
            self._flag(kind, text, [f'Snapshot could not be saved: {error}'])
            return
        if result['status'] == 'saved':
            self.counts['snapshots'] += 1
            self._note(f'Saved snapshot ({kind})')
        elif result['status'] == 'rejected':
            self._flag(kind, text, [result['reason']])

    def _save_fishing(self, text):
        tally = assign_bait(parse_fishing(text), self.bait)
        captured_at = datetime.now(timezone.utc).isoformat()
        try:
            result = save_session(tally, self.account, f'live:{captured_at}:{fingerprint(text)}',
                                  self.roots['fishing'])
        except OSError as error:
            self._flag('fishing', text, [f'Fishing session could not be saved: {error}'])
            return
        if result['status'] != 'saved':
            self._flag('fishing', text, [result.get('reason', 'Fishing session was not saved.')])
            return
        self.counts['trips'] += tally['trips']
        self.counts['sessions'] += 1
        self._note(f'Saved {tally["trips"]} fishing trip{"" if tally["trips"] == 1 else "s"}')
        if self.bait and any(event.startswith('Ran out of ') for trip in tally['trip_details']
                             for event in trip['events']):
            self._note(f'{self.bait} ran out; later trips count as no bait')
            self.bait = None
