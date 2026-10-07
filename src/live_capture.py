"""Watch the Discord window, read new Virtual Fisher replies, save clean ones, and queue the rest for review.

Read-only: the window is captured and read with OCR; nothing is typed, clicked, or sent to Discord.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from secrets import token_hex
from statistics import median
import tempfile
import threading

from .bait_compare import NOT_RECORDED, assign_bait, load_bait_reference
from .catch_check import load_fish_reference
from .fishing_log import fingerprint, parse_fishing
from .fishing_sessions import save_session
from .observation import BOT_NAME, VERIFIED, Identity, Observation, check_identity, recipients
from .ocr_cleanup import META, clean_ocr
from .paste_detect import _kind, split_messages
from .player_snapshots import save_snapshot, validate_full_state
from .player_state import _clean, _name, parse_responses
from .review_queue import add_item

# Bot replies that carry nothing to record.
HARMLESS = re.compile(r"^(You don't have enough|You will now catch more fish|You are already boosting|"
                      r'You are already using|Current bait:|Bait Shop|/bait|You must wait|Your cooldown)', re.I)
# How far chat lines may sit from the message text's left edge, in OCR text heights. Measuring in text
# heights rather than pixels keeps this independent of window size, resolution, DPI, and Discord zoom.
LEFT_MARGIN_HEIGHTS = 3
RIGHT_REACH_HEIGHTS = 25
# A line starting this far right of the text edge may have lost a leading count to an emoji.
REREAD_OFFSET_HEIGHTS = 1
# Reread scales that must agree before a recovered count is trusted (one alone misread "10" as "IO").
REREAD_SCALES = (2, 3)
# Seconds between captures. Shorter than the 3.5 s fishing cooldown, so at most one new reply normally arrives
# between captures and each capture overlaps the one before (a 5 s interval allowed two).
DEFAULT_INTERVAL = 3.0
# Captures in a row without overlap (about a minute at the default interval) before accepting the new view.
# Brief changes, such as the slash-command menu covering messages, resolve well before this.
RESYNC_AFTER = 20
LOG_LENGTH = 30
# Kept diagnostic captures (--keep-captures) are full-window images; only the newest this many are kept.
KEEP_LIMIT = 200
KEPT_NAME = re.compile(r'\d{8}-\d{6}-\d{6}')
# Longest wait for the worker when stopping; a capture blocked longer is abandoned and its result discarded.
STOP_TIMEOUT = 10.0
# Replies that say which bait is in use: /bait ("Current bait: NONE"), choosing the bait already in use, and the
# bait shop's "Selected: Worms (10)". The profile's "Bait:" line is read by the profile parser.
BAIT_LINE = re.compile(r'(?:Current bait:|You are already using|Selected:)\s*(.+?)(?:\s*\([\d,]+\))?\.?', re.I)
# Which snapshot section each player reply fills.
KIND_SECTION = {'profile': 'profile', 'buffs': 'buffs', 'prestige_shop': 'prestige', 'pet': 'pets', 'quests': 'quests'}


def chat_column(boxes: list[dict]) -> tuple[list[dict], float | None, float | None]:
    """Return (boxes in Virtual Fisher's message column in reading order, text left edge, text height)."""
    anchors = [box for box in boxes if _kind(_clean(box['text']))]
    if not anchors:
        return [], None, None
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
        ordered += sorted(row, key=lambda box: box['left'])
    return ordered, left_edge, height


def chat_lines(boxes: list[dict]) -> list[str]:
    """Keep the lines in the column where Virtual Fisher's messages are, in reading order."""
    return [box['text'] for box in chat_column(boxes)[0]]


def repair_counts(boxes: list[dict], edge: float, height: float, reread) -> list[dict]:
    """Recover counts OCR dropped together with an emoji ("Salmon" for "5 🐟 Salmon").

    A short line without digits that starts well right of the text edge is reread from the edge with
    `reread(left, top, right, bottom) -> lines`. The reread is used only when it gives the same words with a
    number in front; otherwise the line is kept and later flagged.
    """
    repaired = []
    for box in boxes:
        text = box['text'].strip()
        if (not re.search(r'\d', text) and 0 < len(text.split()) <= 4
                and box['left'] >= edge + REREAD_OFFSET_HEIGHTS * height):
            found = ' '.join(line.strip() for line in reread(edge - height / 2, box['top'] - 0.4 * height,
                                                             box['right'] + height, box['bottom'] + 0.4 * height))
            match = re.fullmatch(r'(\d{1,3}(?:,\d{3})+|\d+)\s+(.+)', found)
            if match and match[2].casefold() == text.casefold():
                box = {**box, 'text': f'{match[1]} {text}', 'left': edge}
        repaired.append(box)
    return repaired


def _bait_name(text):
    if text.casefold() in ('none', 'no bait'):
        return None
    return next((known for known in load_bait_reference() or {} if known.casefold() == text.casefold()), text)


def bait_evidence(kind: str | None, lines: list[str]) -> tuple[bool, str | None]:
    """Return (True, bait in use, None for no bait) when a reply says which bait is in use, else (False, None)."""
    if kind == 'profile':
        bait = parse_responses(profile='\n'.join(lines))['profile']['bait']
        return (True, _bait_name(bait)) if bait else (False, None)
    if kind is None:
        for line in lines:
            if match := BAIT_LINE.fullmatch(_clean(line)):
                return True, _bait_name(_name(match[1]))
    return False, None


def capture_frame(lines: list[str]) -> dict:
    """Read one capture: {'messages': Virtual Fisher messages oldest first, each {'kind', 'lines', 'key',
    'invoked_by', 'sent'}, 'fragment': the cut-off text above the first message header}. Messages from any other
    author, including other bots, are left out."""
    messages, fragment = [], ()
    for index, message in enumerate(split_messages(clean_ocr(lines, 'capture', capture=True))):
        if index == 0 and message['author'] is None and message['when'] is None:
            fragment = tuple(line.strip() for line in message['lines'] if line.strip())
            continue
        if not message['bot'] or (message['author'] or '').casefold() != BOT_NAME.casefold():
            continue
        body, meta = [], {}
        for line in (line.strip() for line in message['lines']):
            if line.startswith(META):
                field, _, value = line.removeprefix(META).partition(' ')
                meta[field] = value.removeprefix('by ').strip()
            elif line:
                body.append(line)
        if not body:
            continue
        kinds = [found for found in (_kind(_clean(line)) for line in body) if found]
        kind = kinds[0] if kinds else None
        messages.append({'kind': kind, 'lines': body, 'key': (kind, _understood(kind, body)),
                         'invoked_by': meta.get('used'), 'sent': meta.get('sent')})
    return {'messages': messages, 'fragment': fragment}


def capture_messages(lines: list[str]) -> list[dict]:
    """The Virtual Fisher messages of one capture, oldest first (see capture_frame)."""
    return capture_frame(lines)['messages']


def _understood(kind, lines):
    """The lines the parser understands. Matching captures on these ignores stray text such as the message box."""
    text = '\n'.join(lines)
    if kind == 'fishing':
        unknown = {warning.removeprefix('Unrecognized line: ') for warning in parse_fishing(text)['warnings']}
        return tuple(line for line in lines if _clean(line) not in unknown)
    if kind is not None:
        unparsed = set(parse_responses(**{kind: text})['unparsed'].get(kind, []))
        return tuple(line for line in lines if line not in unparsed)
    return tuple(lines)


def _same(older, newer):
    """Two readings may be the same message: same understood content, and no conflicting send time."""
    return (older['key'] == newer['key']
            and not (older.get('sent') and newer.get('sent') and older['sent'] != newer['sent']))


def _could_be_cut_off(top, above):
    """Whether the current top message, skipped as cut off, can be what scrolled into that place."""
    if top['key'] == above['key'] or not top.get('lines') or not above.get('lines'):
        return True
    return set(top['lines']) <= set(above['lines'])


def align(previous: dict | None, current: dict) -> dict:
    """Place the current capture against the previous one.

    Frames are {'messages', 'fragment'} as from capture_frame. Returns {'status', 'start', 'earliest_start',
    'reason'}: 'certain' when every placement consistent with the evidence agrees on where new messages begin,
    'ambiguous' when they disagree (then current[start:] is new under every placement, while
    current[earliest_start:start] may be new or may be repeats of messages already seen), and 'lost' when the
    views do not overlap. Message content alone cannot tell a repeated identical reply from an unchanged
    screen; the cut-off text above the first message and send times can, and are used when present.
    """
    entries = current['messages']
    if previous is None:
        return {'status': 'certain', 'start': len(entries), 'earliest_start': len(entries), 'reason': 'baseline'}
    older = previous['messages']
    candidates = []
    for offset in (0, 1):
        visible = entries[offset:]
        for start in range(len(older)):
            overlap = older[start:]
            if len(overlap) <= len(visible) and all(map(_same, overlap, visible)):
                if offset and start and not _could_be_cut_off(entries[0], older[start - 1]):
                    continue
                candidates.append((offset, start, offset + len(overlap)))
    if not candidates:
        return {'status': 'lost', 'start': None, 'earliest_start': None,
                'reason': 'The chat view does not overlap the previous capture.'}
    # The cut-off text above the first message is evidence of scrolling: it stays the same only if nothing
    # moved, and after a scroll it is the bottom of the message that moved up out of view.
    if previous['fragment'] and current['fragment']:
        def consistent(candidate):
            offset, start, end = candidate
            if end == len(entries):
                return current['fragment'] == previous['fragment']
            if not offset and start:
                lines = older[start - 1].get('lines')
                return not lines or bool(set(current['fragment']) & set(lines))
            return True
        candidates = [candidate for candidate in candidates if consistent(candidate)] or candidates
    starts = sorted({end for _, _, end in candidates})
    if len(starts) == 1:
        return {'status': 'certain', 'start': starts[0], 'earliest_start': starts[0], 'reason': ''}
    return {'status': 'ambiguous', 'start': starts[-1], 'earliest_start': starts[0],
            'reason': f'Identical replies on screen: {starts[-1] - starts[0]} may be new or a repeat of ones already '
                      'seen, and nothing on screen tells which.'}


def align_start(previous: list | None, current: list) -> int | None:
    """Index in `current` (message keys) where messages that are new under every placement begin; None without
    overlap. Matching runs of messages (not individual texts) keeps identical results, such as two equal catches
    in a row, as separate trips. The current top message may be cut off, so alignment may skip it."""
    frame = lambda keys: None if keys is None else {'messages': [{'key': key} for key in keys], 'fragment': ()}
    return align(frame(previous), frame(current))['start']


def new_messages(previous: list | None, current: list) -> list | None:
    """Return the messages below the overlap with the previous capture; None when the views do not overlap."""
    start = align_start(previous, current)
    return None if start is None else current[start:]


def context_key(context: dict) -> str:
    """Identify the captured window and channel. Discord titles its window after the open channel or DM, so a
    changed title means a different channel; the same title is not proof of the same channel by itself."""
    return f'{context.get("window")}:{context.get("title")}'


def make_capture(keep_dir: Path | None = None, keep_limit: int = KEEP_LIMIT):
    """Return a capture function for LiveCapture. Each call captures and reads the Discord window and returns
    {'status', 'lines', 'context'}; the image is deleted at once unless `keep_dir` is set, in which case captures whose
    chat text changed are kept there as PNG plus JSON (OCR boxes and final lines) for analysis."""
    last_kept = {'lines': None}

    def capture() -> dict:
        from .screenshot_ocr import ocr_available, read_image_boxes, read_region_lines
        from .window_capture import bmp_to_png, capture_supported, capture_window, find_discord_window

        if not capture_supported() or not ocr_available():
            return {'status': 'unsupported', 'lines': []}
        window = find_discord_window()
        if window is None:
            return {'status': 'no_window', 'lines': []}
        handle, temporary = tempfile.mkstemp(suffix='.bmp')
        os.close(handle)
        image = Path(temporary)
        try:
            info = capture_window(window[0], image)
            if info['minimized']:
                return {'status': 'minimized', 'lines': []}
            if info['blank']:
                return {'status': 'blank', 'lines': []}
            boxes = read_image_boxes(image)
            column, edge, height = chat_column(boxes)

            def reread(left, top, right, bottom):
                reads = [read_region_lines(image, left, top, right, bottom, scale) for scale in REREAD_SCALES]
                return reads[0] if all(read == reads[0] for read in reads) else []

            if column:
                column = repair_counts(column, edge, height, reread)
            lines = [box['text'] for box in column]
            if keep_dir is not None and lines != last_kept['lines']:
                _keep(image, Path(keep_dir), info, boxes, lines, bmp_to_png, keep_limit)
                last_kept['lines'] = lines
            return {'status': 'ok', 'lines': lines, 'context': {'window': window[0], 'title': window[1]}}
        finally:
            image.unlink(missing_ok=True)

    return capture


def _keep(image, folder, info, boxes, lines, bmp_to_png, limit=KEEP_LIMIT):
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    bmp_to_png(image, folder / f'{stamp}.png')
    (folder / f'{stamp}.json').write_text(json.dumps({'info': info, 'ocr_lines': boxes, 'chat_lines': lines},
                                                     indent=1), encoding='utf-8')
    prune_kept(folder, limit)


def prune_kept(folder: Path, limit: int = KEEP_LIMIT) -> None:
    """Delete all but the newest `limit` kept captures (each a PNG with its JSON). Only files named like
    kept captures are touched."""
    stamps = sorted({path.stem for path in Path(folder).glob('*.png') if KEPT_NAME.fullmatch(path.stem)})
    for stamp in stamps[:max(len(stamps) - limit, 0)]:
        for suffix in ('.png', '.json'):
            (Path(folder) / f'{stamp}{suffix}').unlink(missing_ok=True)


capture_discord = make_capture()


STATUS_TEXT = {
    'unsupported': 'Live capture needs Windows and the OCR packages (see README).',
    'no_window': 'Discord window not found; waiting for it to open.',
    'minimized': 'Discord is minimized; paused until it is restored.',
    'blank': 'The capture was blank (a minimized Remote Desktop session or a locked screen can cause this); paused.',
}


class LiveCapture:
    def __init__(self, snapshot_root, fishing_root, review_root, capture=capture_discord, interval=DEFAULT_INTERVAL, clock=None):
        self.roots = {'snapshots': Path(snapshot_root), 'fishing': Path(fishing_root), 'review': Path(review_root)}
        self.capture, self.interval = capture, interval
        # UTC time each capture is observed at; replaceable so tests can use a fixed clock.
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.generation = 0
        self.running = False
        self.account, self.bait, self.identity = None, NOT_RECORDED, None
        # 'assumed' (chosen on the page), 'verified' (a reply said so), or 'unknown' (bait is NOT_RECORDED).
        self.bait_status = 'unknown'
        self.state = 'Stopped.'
        self.previous, self.misses, self.held = None, 0, None
        self.previous_lines = []
        # How the latest capture lined up, and how many uncertain placements are waiting in review this run.
        self.alignment, self.unresolved = None, 0
        # The window and channel capture started in; captures of any other are not read.
        self.context_id = None
        self.latest = {}
        self.counts = {'trips': 0, 'sessions': 0, 'snapshots': 0, 'flagged': 0, 'ignored': 0}
        self.log = []
        self.last_capture = None

    def _note(self, text):
        self.log.insert(0, (datetime.now().strftime('%H:%M:%S'), text))
        del self.log[LOG_LENGTH:]

    def start(self, account, bait, thread=True, username=None):
        """Watch for `account` (its display name) and Discord `username`; replies are saved only when the names
        they carry match these."""
        with self.lock:
            if self.running:
                return
            self.account, self.bait = account, bait
            # The bait chosen on the page is only an assumption until a reply confirms it.
            self.bait_status = 'unknown' if bait == NOT_RECORDED else 'assumed'
            self.identity = Identity(account, username or None)
            self.previous, self.misses, self.latest, self.held = None, 0, {}, None
            self.previous_lines, self.alignment, self.unresolved = [], None, 0
            self.context_id = None
            self.counts = dict.fromkeys(self.counts, 0)
            self.running, self.state = True, 'Starting: messages already on screen are treated as seen.'
            self._note(f'Started for {account}' + (f' ({username})' if username else '')
                       + (f' with {bait}' if bait else ''))
            # Each run has its own generation and stop event, so a worker from an earlier run that is still
            # inside a slow capture can neither be revived by this start nor have its result accepted.
            self.generation += 1
            self.stop_event = threading.Event()
            generation, stop_event = self.generation, self.stop_event
        if thread:
            self.thread = threading.Thread(target=self._loop, args=(generation, stop_event), daemon=True)
            self.thread.start()

    def stop(self, timeout=STOP_TIMEOUT):
        with self.lock:
            if self.running:
                if self.held is not None:
                    self._process([self.held])
                    self.held = None
                self._note('Stopped')
            self.running, self.state = False, 'Stopped.'
            self.generation += 1
            self.stop_event.set()
            worker = self.thread
        # Joined outside the lock: the worker needs the lock to finish its current step.
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout)

    def _loop(self, generation, stop_event):
        while not stop_event.is_set():
            try:
                self.step(generation)
            except Exception as error:  # keep watching; a single failed capture should not end the session
                with self.lock:
                    if generation == self.generation:
                        self.state = f'Capture failed: {error}'
                        self._note(self.state)
            if stop_event.wait(self.interval):
                break

    def status(self):
        with self.lock:
            return {'running': self.running, 'account': self.account, 'bait': self.bait, 'state': self.state,
                    'username': self.identity.username if self.identity else None,
                    'alignment': self.alignment, 'unresolved': self.unresolved,
                    'bait_state': {'bait': None if self.bait == NOT_RECORDED else self.bait,
                                   'status': self.bait_status},
                    'counts': dict(self.counts), 'log': list(self.log), 'last_capture': self.last_capture}

    def step(self, generation=None):
        result = self.capture()
        with self.lock:
            # A capture that outlived its run (stopped, or stopped and started again) is discarded.
            if not self.running or (generation is not None and generation != self.generation):
                return
            self.last_capture = datetime.now().strftime('%H:%M:%S')
            if result['status'] != 'ok':
                self.state = STATUS_TEXT.get(result['status'], result['status'])
                return
            context = result.get('context')
            context_id = context_key(context) if context else None
            if context_id and self.context_id is None:
                self.context_id = context_id
            elif context_id and context_id != self.context_id:
                # Matching text in another channel says nothing about this one, so it is not read at all.
                self.state = (f'Paused: Discord is showing a different window or channel ({context.get("title")}). '
                              'Return to the channel capture started in, or stop and start again.')
                return
            frame = capture_frame(result['lines'])
            messages = frame['messages']
            capture_id, observed_at = token_hex(6), self.clock().isoformat()
            for index, message in enumerate(messages):
                message.update(capture_id=capture_id, observed_at=observed_at, context_id=context_id,
                               observation_id=f'{capture_id}-{index}')
            if not messages:
                self.state = 'No Virtual Fisher messages visible in Discord.'
                return
            alignment = align(self.previous, frame)
            start = alignment['start']
            self.alignment = {'status': alignment['status'], 'reason': alignment['reason']}
            if start is None:
                self.misses += 1
                if self.misses < RESYNC_AFTER:
                    self.state = ('Paused: the chat view moved (scrolled or another channel). Scroll back to the '
                                  'newest messages to resume.')
                    return
                # Messages the old view did not show may be new or may already be saved; let a person decide.
                unplaced = list(messages)
                for key in [message['key'] for message in (self.previous or {'messages': []})['messages']]:
                    match = next((message for message in unplaced if message['key'] == key), None)
                    if match:
                        unplaced.remove(match)
                for message in unplaced:
                    if message['kind']:
                        self._flag(message['kind'], '\n'.join(message['lines']),
                                   ['Seen after the chat view changed; it may already be saved or may be new.'])
                self._note('View changed for a while; continuing from what is on screen now.')
                self.misses, self.previous, self.held = 0, frame, None
                self.unresolved += 1
                self.state = f'Watching. Last capture {self.last_capture}.'
                return
            self.misses = 0
            ready = []
            if alignment['status'] == 'ambiguous':
                # Never guess a count: what may be a repeat goes to review once per distinct evidence, and
                # dependent actions stay frozen (status 'unresolved') until a person has looked.
                maybe = messages[alignment['earliest_start']:start - (1 if self.held is not None else 0)]
                evidence = fingerprint('\n'.join(self.previous_lines) + '\n\x00\n' + '\n'.join(result['lines']))
                if any(line.startswith('You ran out of') for message in maybe for line in message['lines']):
                    self._set_bait(NOT_RECORDED, 'unknown', 'Bait may have run out in an uncertain message')
                if maybe and self._flag(None if len({m['kind'] for m in maybe}) > 1 else maybe[0]['kind'],
                                        '\n\n'.join('\n'.join(m['lines']) for m in maybe),
                                        [alignment['reason']], evidence_id=evidence):
                    self.unresolved += 1
            if self.held is not None and start >= 1:
                now = messages[start - 1]
                # Process the held newest message once something follows it or it reads the same twice.
                if start < len(messages) or now['lines'] == self.held['lines']:
                    # It has been on screen since the held capture, so that is when it was observed.
                    ready.append({**now, **{field: self.held[field] for field in
                                            ('capture_id', 'observed_at', 'observation_id')}})
                    self.held = None
                else:
                    self.held = now
            fresh = messages[start:]
            if fresh:
                # The newest message may still change (rendering, or the message box below it), so wait.
                ready += fresh[:-1]
                self.held = fresh[-1]
            self.previous, self.previous_lines = frame, list(result['lines'])
            self._process(ready)
            self.state = f'Watching. Last capture {self.last_capture}.'

    def _flag(self, kind, text, reasons, observation=None, evidence_id=None):
        """Queue a message for review; returns False when the same evidence is already queued."""
        item = add_item(self.roots['review'], self.account, kind or 'unknown', text, reasons,
                        identity_status=observation.identity_status if observation else None,
                        observation_id=observation.observation_id if observation else None, evidence_id=evidence_id)
        if item.get('duplicate'):
            return False
        self.counts['flagged'] += 1
        self._note(f'Needs review ({kind or "unknown"}): {reasons[0]}')
        return True

    def _observe(self, message):
        """Wrap a message in an Observation that says whether it is the configured player's reply."""
        named = recipients(message['kind'], message['lines'], message.get('invoked_by'))
        status, recipient, reason = check_identity(self.identity, named)
        return Observation(observation_id=message.get('observation_id', ''),
                           capture_id=message.get('capture_id', ''), observed_at=message.get('observed_at', ''),
                           context_id=message.get('context_id'), kind=message['kind'], recipient=recipient,
                           text='\n'.join(message['lines']), identity_status=status, reason=reason)

    def _set_bait(self, bait, status, why):
        if (bait, status) != (self.bait, self.bait_status):
            self._note(f'{why}: ' + ('bait unknown' if bait == NOT_RECORDED else bait or 'no bait'))
        self.bait, self.bait_status = bait, status

    def _ran_out(self, names):
        """A verified "You ran out of …": no bait from the next trip, unless it names a different bait than the
        one in use, which leaves the bait unknown."""
        if self.bait in (None, NOT_RECORDED) or any(name.casefold() == self.bait.casefold() for name in names):
            self._set_bait(None, 'verified', 'Bait ran out')
        else:
            self._set_bait(NOT_RECORDED, 'unknown', f'Ran out of {", ".join(names)}, not {self.bait}')

    def _process(self, messages):
        clean_fishing = []

        def flush():
            # Trips so far used the bait in effect before the next change, so save them before applying it.
            if clean_fishing:
                self._save_fishing(list(clean_fishing))
                clean_fishing.clear()

        for message in messages:
            kind, text = message['kind'], '\n'.join(message['lines'])
            observation = self._observe(message)
            found, bait = bait_evidence(kind, message['lines'])
            if kind is None and found:
                if observation.identity_status != VERIFIED:
                    self._flag(None, text, [observation.reason], observation)
                else:
                    flush()
                    self._set_bait(bait, 'verified', 'Bait in use confirmed')
                    self.counts['ignored'] += 1
                continue
            if kind is None:
                first = _clean(message['lines'][0])
                # Shop pages (/shop …, /clan shop) are not parsed yet; ignore them rather than flag them.
                if HARMLESS.match(first) or re.search(r'\bShop$', first):
                    self.counts['ignored'] += 1
                else:
                    self._flag(None, text, ['Not a recognized Virtual Fisher response.'], observation)
            elif observation.identity_status != VERIFIED:
                # Nothing is saved or changed for a reply not confirmed to be the configured player's.
                self._flag(kind, text, [observation.reason], observation)
            elif kind == 'fishing':
                reasons = self._fishing_problems(text)
                if reasons:
                    self._flag(kind, text, reasons, observation)
                else:
                    clean_fishing.append(observation)
                # A credible depletion ends the bait even when the rest of the trip could not be read.
                ran_out = parse_fishing(text)['bait_ran_out']
                if ran_out:
                    flush()
                    self._ran_out(ran_out)
            else:
                reasons = self._player_problems(kind, text)
                if reasons:
                    self._flag(kind, text, reasons, observation)
                else:
                    if kind == 'profile':
                        # Earlier trips keep the conditions (and bait) from before this profile.
                        flush()
                    if found:
                        self._set_bait(bait, 'verified', 'Bait in use confirmed by profile')
                    self._save_player(kind, observation)
        flush()

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
            full, problems = validate_full_state(state)
            if not full:
                reasons += problems
        if kind == 'pet':
            missing = [pet for pet, details in (state['pets']['owned'] or {}).items() if details['xp_current'] is None]
            if missing:
                reasons.append('Pet XP could not be read for ' + ', '.join(missing) + '.')
        return reasons

    def _save_player(self, kind, observation):
        self.latest[kind] = observation
        state = parse_responses(**{name: seen.text for name, seen in self.latest.items()})
        # Each section keeps the time and source of the reply it came from, however later it is saved.
        sections = {KIND_SECTION[name]: {'observed_at': seen.observed_at, 'source_observation_id': seen.observation_id,
                                         'identity_status': seen.identity_status}
                    for name, seen in self.latest.items()}
        try:
            if 'profile' in self.latest:
                result = save_snapshot(state, self.roots['snapshots'], mode='automatic', sections=sections)
            else:
                result = save_snapshot(state, self.roots['snapshots'], mode='manual', account_name=self.account,
                                       sections=sections)
        except OSError as error:
            self._flag(kind, observation.text, [f'Snapshot could not be saved: {error}'], observation)
            return
        if result['status'] == 'saved':
            self.counts['snapshots'] += 1
            self._note(f'Saved snapshot ({kind})')
        elif result['status'] == 'rejected':
            self._flag(kind, observation.text, [result['reason']], observation)

    def _save_fishing(self, observations):
        text = '\n\n'.join(observation.text for observation in observations)
        tally = assign_bait(parse_fishing(text), self.bait)
        seen = self.latest.get('profile')
        if seen is not None:
            # The conditions these trips were fished in, from your latest confirmed /profile this run, so money
            # estimates can compare like with like.
            profile = parse_responses(profile=seen.text)['profile']
            tally['conditions'] = {'biome': profile['biome'], 'rod': profile['rod'], 'pet': profile['pet'],
                                   'profile_observed_at': seen.observed_at}
        provenance = {'source': 'live_capture', 'identity_status': VERIFIED,
                      'observations': [{field: value for field, value in observation.to_dict().items()
                                        if field not in ('text', 'reason')} for observation in observations]}
        source = 'live:' + ','.join(observation.observation_id for observation in observations)
        try:
            result = save_session(tally, self.account, source, self.roots['fishing'], provenance=provenance)
        except OSError as error:
            self._flag('fishing', text, [f'Fishing session could not be saved: {error}'])
            return
        if result['status'] != 'saved':
            self._flag('fishing', text, [result.get('reason', 'Fishing session was not saved.')])
            return
        self.counts['trips'] += tally['trips']
        self.counts['sessions'] += 1
        self._note(f'Saved {tally["trips"]} fishing trip{"" if tally["trips"] == 1 else "s"}')
