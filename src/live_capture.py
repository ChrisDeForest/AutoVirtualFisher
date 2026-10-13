"""Watch the Discord window, read new Virtual Fisher replies, save clean ones, and queue the rest for review.

Read-only: the window is captured and read with OCR; nothing is typed, clicked, or sent to Discord.
"""
from datetime import datetime, timezone
import json
from math import ceil
import os
from pathlib import Path
import re
from secrets import token_hex
from statistics import median
import tempfile
import threading
import time

from .bait_compare import NOT_RECORDED, assign_bait, load_bait_reference
from .catch_check import load_fish_reference
from .fishing_log import fingerprint, parse_fishing
from .fishing_sessions import save_session
from .observation import BOT_NAME, MISMATCH, VERIFIED, Identity, Observation, check_identity, recipients
from .ocr_cleanup import META, clean_ocr
from .paste_detect import _kind, split_messages
from .player_planner import buff_conditions
from .player_snapshots import load_history, save_snapshot, validate_full_state
from .player_state import _clean, _name, parse_responses
from .review_queue import add_item, blocking_items

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
# Where the enlarged read of the message box and slash-command picker starts, as a share of the window's height.
# A full picker reaches about 750 px above the box: half of a 1535 px window cut off its top row (the command).
INPUT_REGION_TOP = .25
# Zooms the picker is also read at after a miss: live (27" 2560x1440), "/boosts" went unread at x2 and x3, read at x4.
DEEP_SCALES = (3, 4)
# Seconds from the start of one capture to the next. The fishing cooldown changes as you level up and resets
# when you prestige, so capture keeps pace with the cooldown in your latest /buffs: a capture every three quarters
# of it, so at most one new reply normally arrives between captures and each capture overlaps the one before.
INTERVAL_FRACTION = 0.75
# Never slower than this (also the pace before any cooldown is known: under the 3.5 s starting cooldown)...
MAX_INTERVAL = DEFAULT_INTERVAL = 3.0
# ...and never faster than a capture plus OCR can reliably finish.
MIN_INTERVAL = 1.0
# How long the view may stay without overlap before capture accepts the new view. Brief changes, such as the
# slash-command menu covering messages, resolve well before this.
RESYNC_SECONDS = 60
RESYNC_AFTER = ceil(RESYNC_SECONDS / DEFAULT_INTERVAL)  # captures, at the default pace
LOG_LENGTH = 30
# Kept diagnostic captures (--keep-captures) are full-window images; only the newest this many are kept.
KEEP_LIMIT = 200
KEPT_NAME = re.compile(r'\d{8}-\d{6}-\d{6}')
# Longest wait for the worker when stopping; a capture blocked longer is abandoned and its result discarded.
STOP_TIMEOUT = 10.0
# Replies that say which bait is in use: /bait ("Current bait: NONE"), choosing the bait already in use, and the
# bait shop's "Selected: Worms (10)". The profile's "Bait:" line is read by the profile parser.
BAIT_LINE = re.compile(r'(?:Current bait:|You are already using|Selected:)\s*(.+?)(?:\s*\([\d,]+\))?\.?', re.I)
# Part of the window height where Discord's top bar (channel header and search box) sits.
TOP_BAR_FRACTION = 0.15
# Which snapshot section each player reply fills.
KIND_SECTION = {'profile': 'profile', 'buffs': 'buffs', 'prestige_shop': 'prestige', 'pet': 'pets', 'quests': 'quests'}


def fishing_core_problems(text: str) -> list[str]:
    """Problems with what a trip needs to count: exactly one result, known fish, and its XP line. Extra lines
    that cannot be read (events, rewards) are left out of this; see fishing_problems."""
    tally = parse_fishing(text)
    reasons = []
    if tally['trips'] != 1:
        reasons.append(f'Expected one fishing result, found {tally["trips"]}.')
    reference = load_fish_reference() or {}
    unknown = [name for name in tally['catches'] if name.casefold() not in reference]
    if unknown:
        reasons.append('Not in the wiki fish table: ' + ', '.join(unknown))
    if tally['trips'] == 1 and tally['trip_details'][0]['xp'] is None:
        reasons.append('No XP line was read.')
    # A fish name alone ("Salmon") is a catch line that lost its count: the catch itself is unknown.
    reasons += [warning for warning in tally['warnings']
                if warning.split(':', 1)[-1].strip().casefold() in reference]
    return reasons


def fishing_problems(text: str) -> list[str]:
    """Why one fishing reply cannot be counted as exactly one trip; empty when it reads cleanly."""
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


def join_boost_caption(boxes):
    """Join a split boost caption only when its text shares the same visual row."""
    result, consumed = [], set()
    for index, box in enumerate(boxes):
        if index in consumed:
            continue
        if box['text'].strip() == 'Global boost by':
            height = max(1, box['bottom'] - box['top'])
            neighbors = [(i, other) for i, other in enumerate(boxes) if i != index
                         and box['right'] <= other['left'] <= box['right'] + 3 * height
                         and abs((other['top'] + other['bottom'] - box['top'] - box['bottom']) / 2)
                         <= height / 2]
            if len(neighbors) == 1:
                i, other = neighbors[0]
                consumed.add(i)
                box = {**box, 'text': box['text'] + ' ' + other['text'], 'right': other['right']}
        result.append(box)
    return result


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


# Virtual Fisher's captcha (anti-bot) message; the code itself is in an image.
CAPTCHA = re.compile(r'captcha|\bverify\b|verification|anti.bot', re.I)
# The first line of a reply to a slash command: "epicmouse used /fish".
INVOKED = re.compile(r'\S+ used\b')


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
        key = (kind, _understood(kind, body))
        if any(CAPTCHA.search(line) for line in body):
            kind = 'captcha'
            key = (kind, tuple(body))
        messages.append({'kind': kind, 'lines': body, 'key': key,
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
    if kind in ('profile', 'buffs', 'prestige_shop', 'pet', 'quests'):
        unparsed = set(parse_responses(**{kind: text})['unparsed'].get(kind, []))
        return tuple(line for line in lines if line not in unparsed)
    return tuple(lines)


def _same(older, newer):
    """Two readings may be the same message: same understood content, and no conflicting send time."""
    if older.get('position_key') is not None and newer.get('position_key') is not None:
        content_matches = older['position_key'] == newer['position_key']
        if older.get('invoked_by') and newer.get('invoked_by') and older['invoked_by'] != newer['invoked_by']:
            return False
    else:
        content_matches = older['key'] == newer['key']
    return (content_matches
            and not (older.get('sent') and newer.get('sent') and older['sent'] != newer['sent']))


def _could_be_cut_off(top, above):
    """Whether the current top message, skipped as cut off, can be what scrolled into that place."""
    if top.get('sent') and above.get('sent') and top['sent'] != above['sent']:
        return False
    if _same(top, above) or not top.get('lines') or not above.get('lines'):
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


def _cooldown_seconds(state: dict) -> float | None:
    cooldown = (state.get('buffs') or {}).get('fishing_cooldown')
    if isinstance(cooldown, dict) and cooldown.get('unit') == 'seconds' and cooldown.get('value', 0) > 0:
        return cooldown['value']
    return None


# The search box: "Search Virtual Fisher#7036". OCR can misread the tag's digits ("#7O36") or space it out.
SEARCH = re.compile(r'Search\s+(.+?)(?:\s*#\s*\w{4})?')


def channel_name(boxes: list[dict], image_height: float) -> str | None:
    """The open DM or server as Discord's search box shows it in the capture ("Search Virtual Fisher#7036",
    "Search Fishing Club"), read from the top bar only; None when OCR did not read it."""
    for box in sorted(boxes, key=lambda box: box['top']):
        if box['top'] > image_height * TOP_BAR_FRACTION:
            break
        if match := re.fullmatch(SEARCH, box['text'].strip()):
            name = match[1].strip()
            # A narrow (snapped) window shortens it to "Search Virtual …": not a name on its own.
            return None if TRUNCATED.search(name) else name
    return None


TRUNCATED = re.compile(r'(\.{2,}|…)$')


def channel_hints(boxes: list[dict], image_height: float) -> dict:
    """Every place the open DM's name shows: the search box (possibly shortened), the chat header in the top bar
    ("Virtual Fisher /fish"), and the message box placeholder ("Message @Virtual Fisher", hidden while a draft is
    typed). channel_matches weighs them."""
    hints = {'search': None, 'header': [], 'composer': None}
    for box in boxes:
        text = box['text'].strip()
        if box['top'] <= image_height * TOP_BAR_FRACTION:
            if match := re.fullmatch(SEARCH, text):
                hints['search'] = hints['search'] or match[1].strip()
            else:
                hints['header'].append(text)
        if match := re.fullmatch(r'[I|l]?M?essage\s*@\s*(.+)', text, re.I):
            hints['composer'] = match[1].strip()
    return hints


def channel_matches(context: dict, expected: str) -> bool:
    """Whether the capture shows the expected DM. A full name anywhere confirms it: the search box, the message
    box placeholder, or the top bar's chat header. A full search name that differs always fails, and a shortened
    search name must agree with the expected one."""
    expected_key = expected.casefold()
    if (context.get('channel') or '').casefold() == expected_key:
        return True
    hints = context.get('channel_hints') or {}
    search = (hints.get('search') or '').strip()
    if search:
        prefix = TRUNCATED.sub('', search).strip().casefold()
        if not TRUNCATED.search(search) or not expected_key.startswith(prefix):
            return False
    if (hints.get('composer') or '').casefold() == expected_key:
        return True
    return any(text.casefold() == expected_key or text.casefold().startswith(expected_key + ' ')
               for text in hints.get('header') or [])


def context_key(context: dict) -> str:
    """Identify the captured window and channel. Discord titles its window after the open channel or DM, so a
    changed title means a different channel; the same title is not proof of the same channel by itself."""
    return f'{context.get("window")}:{context.get("title")}'


def newest_message_start(boxes) -> int:
    """Index of the newest message's "<name> used" line in the chat column, or 0 when there is none."""
    starts = [index for index, box in enumerate(boxes) if INVOKED.match(box['text'].strip())]
    return starts[-1] if starts else 0


def make_capture(keep_dir: Path | None = None, keep_limit: int = KEEP_LIMIT, window_provider=None, input_region=False,
                 newest_only=False):
    """Return a capture function for LiveCapture. Each call captures and reads the Discord window and returns
    {'status', 'lines', 'context'}; the image is deleted at once unless `keep_dir` is set, in which case captures whose
    chat text changed are kept there as PNG plus JSON (OCR boxes and final lines) for analysis.
    `light=True` (typing a command) reads only what command entry checks: the window's text and the message box
    region, without the chat rereads, the reply controls or the buttons; never kept. `deep=True` also reads that
    region at DEEP_SCALES ('deep_boxes', one list per zoom), about 0.5 s more.
    `newest_only=True` (the automation, whose reply is always the newest message) rereads only the newest message,
    from its "<name> used" line down: the messages above were reread while they were newest. The rereads of every
    message cost about 1.4 s of a 1.9 s read live."""
    last_kept = {'lines': None}

    def capture(light=False, deep=False) -> dict:
        from .screenshot_ocr import ocr_available, read_image_boxes, read_region_lines, read_region_boxes
        from .window_capture import bmp_to_png, capture_supported, capture_window, find_discord_window

        if not capture_supported() or not ocr_available():
            return {'status': 'unsupported', 'lines': []}
        window = window_provider() if window_provider is not None else find_discord_window()
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
            region = (0, info['height'] * INPUT_REGION_TOP, info['width'], info['height'])
            input_boxes = read_region_boxes(image, region, 2) if input_region else boxes
            deep_boxes = [read_region_boxes(image, region, scale) for scale in DEEP_SCALES] if input_region and deep else None
            column, edge, height = chat_column(boxes)

            def reread(left, top, right, bottom):
                reads = [read_region_lines(image, left, top, right, bottom, scale) for scale in REREAD_SCALES]
                return reads[0] if all(read == reads[0] for read in reads) else []

            if column and not light:
                start = newest_message_start(column) if newest_only else 0
                column = column[:start] + repair_counts(column[start:], edge, height, reread)
            lines = [box['text'] for box in column]
            if input_region and column and not light:
                from .reply_ocr import repair_reply_regions
                controls = read_region_boxes(image, (0, info['height'] * .5, info['width'], info['height']), 3)
                joined = join_boost_caption(column)
                start = newest_message_start(joined) if newest_only else 0
                lines = [box['text'] for box in joined[:start]] + repair_reply_regions(
                    joined[start:], image, info['height'], controls)
            if keep_dir is not None and not light and lines != last_kept['lines']:
                _keep(image, Path(keep_dir), info, boxes, lines, bmp_to_png, keep_limit)
                last_kept['lines'] = lines
            try:
                from .ocr_image import button_rows
                # Arrow buttons have no text; found by colour before the image is deleted.
                buttons = [] if light else button_rows(image, boxes)
            except (OSError, ValueError):
                buttons = []
            frame = {'status': 'ok', 'lines': lines, 'boxes': input_boxes, 'original_boxes': boxes,
                     'buttons': buttons, 'context': {'window': window[0], 'title': window[1],
                                                     'channel': channel_name(boxes, info['height']),
                                                     'channel_hints': channel_hints(boxes, info['height'])}}
            if deep_boxes is not None:
                frame['deep_boxes'] = deep_boxes
            return frame
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
        # `interval` is the starting and slowest pace; see _retime for how it follows the fishing cooldown.
        self.capture, self.max_interval = capture, interval
        self.interval, self.interval_reason = interval, 'Default pace: no fishing cooldown known yet.'
        self.cooldown = self.tightened = None
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
        # How the latest capture lined up.
        self.alignment = None
        # The window and channel capture started in; captures of any other are not read. `channel` is the channel
        # name read from the capture's own pixels (the search box), checked as well as the window title.
        self.context_id = self.channel = None
        # The latest capture: its status ('ok', 'blank', 'other_context', …), when it was taken, and whether it
        # showed the pinned channel ('verified', 'unverified', 'changed').
        self.capture_status, self.captured_at, self.context_status = None, None, 'unverified'
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
            self.previous_lines, self.alignment = [], None
            self.context_id = self.channel = None
            self.capture_status, self.captured_at, self.context_status = None, None, 'unverified'
            self.cooldown, self.tightened = self._saved_cooldown(account), None
            self._retime()
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

    def _saved_cooldown(self, account):
        """The fishing cooldown from the newest saved /buffs, as a starting point until this run sees one."""
        try:
            history = load_history(account, self.roots['snapshots'])[0]
        except OSError:
            return None
        return next((seconds for seconds in (_cooldown_seconds(item['state']) for item in history)
                     if seconds is not None), None)

    def _retime(self):
        """Set the pace: three quarters of the known cooldown, or faster if replies were seen arriving faster
        than that, within MIN_INTERVAL and the starting interval."""
        interval, reason = self.max_interval, 'Default pace: no fishing cooldown known yet.'
        if self.cooldown is not None:
            interval = self.cooldown * INTERVAL_FRACTION
            reason = f'Paced to your {self.cooldown:g} s fishing cooldown from /buffs.'
        if self.tightened is not None and self.tightened < interval:
            interval, reason = self.tightened, ('Replies were arriving faster than captures; speeded up until the '
                                                'next /buffs shows your cooldown.')
        self.interval = round(min(max(interval, MIN_INTERVAL), self.max_interval), 2)
        self.interval_reason = reason

    def _loop(self, generation, stop_event):
        while not stop_event.is_set():
            began = time.monotonic()
            try:
                self.step(generation)
            except Exception as error:  # keep watching; a single failed capture should not end the session
                with self.lock:
                    if generation == self.generation:
                        self.capture_status, self.context_status = 'error', 'unverified'
                        self.alignment = {'status': 'unknown', 'reason': f'Capture failed: {error}'}
                        self.state = f'Capture failed: {error}'
                        self._note(self.state)
            # Paced from the start of each capture, so slow OCR does not stretch the gap between captures.
            if stop_event.wait(max(0.0, self.interval - (time.monotonic() - began))):
                break

    def status(self):
        with self.lock:
            account = self.account
        unresolved = blocking_items(self.roots['review'], account) if account else 0
        with self.lock:
            return {'running': self.running, 'account': self.account, 'bait': self.bait, 'state': self.state,
                    'username': self.identity.username if self.identity else None,
                    # Review items about your own replies (or replies that may be yours) still in the queue.
                    'unresolved': unresolved, 'alignment': self.alignment,
                    'capture_status': self.capture_status, 'captured_at': self.captured_at,
                    'context': {'status': self.context_status, 'channel': self.channel},
                    'interval': self.interval, 'interval_reason': self.interval_reason,
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
            observed_at = self.captured_at = self.clock().isoformat()
            # Certainty belongs to one capture: it is restored only below, when this capture overlaps the last one
            # placed. Unreadable, empty, or other-channel captures leave it unknown (or lost).
            self.alignment = {'status': 'unknown', 'reason': 'The latest capture could not be placed.'}
            self.capture_status = result['status']
            if result['status'] != 'ok':
                self.context_status = 'unverified'
                self.state = STATUS_TEXT.get(result['status'], result['status'])
                return
            context = result.get('context') or {}
            context_id = context_key(context) if context else None
            channel = context.get('channel')
            if context_id and self.context_id is None:
                self.context_id = context_id
            if channel and self.channel is None and context_id == self.context_id:
                self.channel = channel
            if (context_id and context_id != self.context_id) or (channel and self.channel
                                                                  and channel.casefold() != self.channel.casefold()):
                # Matching text in another channel says nothing about this one, so it is not read at all.
                self.capture_status, self.context_status = 'other_context', 'changed'
                self.state = (f'Paused: Discord is showing a different window or channel '
                              f'({channel or context.get("title")}). Return to the channel capture started in, or '
                              'stop and start again.')
                return
            # Verified only when this capture shows the pinned window and title and its own pixels show the
            # pinned channel; otherwise replies are still read (each is identity-checked) but no action may rely
            # on the channel.
            self.context_status = 'verified' if context_id and channel and self.channel else 'unverified'
            frame = capture_frame(result['lines'])
            messages = frame['messages']
            capture_id = token_hex(6)
            for index, message in enumerate(messages):
                message.update(capture_id=capture_id, observed_at=observed_at, context_id=context_id,
                               observation_id=f'{capture_id}-{index}')
            if not messages:
                # The previous view is kept: capture resumes once a capture overlaps it again.
                self.alignment = {'status': 'lost', 'reason': 'No Virtual Fisher messages are visible.'}
                self.state = 'No Virtual Fisher messages visible in Discord.'
                return
            alignment = align(self.previous, frame)
            start = alignment['start']
            self.alignment = {'status': alignment['status'], 'reason': alignment['reason']}
            if start is None:
                self.misses += 1
                if self.misses < ceil(RESYNC_SECONDS / self.interval):
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
                if maybe:
                    self._flag(None if len({m['kind'] for m in maybe}) > 1 else maybe[0]['kind'],
                               '\n\n'.join('\n'.join(m['lines']) for m in maybe), [alignment['reason']],
                               evidence_id=evidence)
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
            arrived = sum(message['kind'] == 'fishing' for message in fresh)
            if arrived >= 2:
                # More than one result between captures: the cooldown is shorter than the pace assumes (for
                # example after a level-up), so capture that much more often until /buffs says otherwise.
                self.tightened = self.interval / arrived
                self._retime()
            if fresh:
                # The newest message may still change (rendering, or the message box below it), so wait.
                ready += fresh[:-1]
                self.held = fresh[-1]
            self.previous, self.previous_lines = frame, list(result['lines'])
            self._process(ready)
            self.state = f'Watching. Last capture {self.last_capture}.'

    def _flag(self, kind, text, reasons, observation=None, evidence_id=None):
        """Queue a message for review; returns False when the same evidence is already queued."""
        # Only a reply clearly naming someone else leaves your state unaffected; anything else may be yours, so
        # it blocks actions until it is dismissed or fixed.
        blocks = not (observation and observation.identity_status == MISMATCH)
        item = add_item(self.roots['review'], self.account, kind or 'unknown', text, reasons,
                        identity_status=observation.identity_status if observation else None,
                        observation_id=observation.observation_id if observation else None, evidence_id=evidence_id,
                        blocks_actions=blocks)
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

    def _ran_out_during(self, names):
        """The trip saying "You ran out of X" was fished with X. That confirms an assumed or unknown bait; when it
        contradicts a confirmed one, that trip's bait is unknown."""
        named = _bait_name(names[0])
        if self.bait_status != 'verified' or (self.bait and named and self.bait.casefold() == named.casefold()):
            self._set_bait(named, 'verified', 'The game named the bait in use')
        else:
            self._set_bait(NOT_RECORDED, 'unknown', f'Ran out of {named}, but {self.bait or "no bait"} was confirmed')

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
                reasons = fishing_problems(text)
                if reasons:
                    self._flag(kind, text, reasons, observation)
                ran_out = parse_fishing(text)['bait_ran_out']
                if ran_out:
                    # "You ran out of X" says this trip used X: earlier trips keep the bait known before it.
                    flush()
                    self._ran_out_during(ran_out)
                if not reasons:
                    clean_fishing.append(observation)
                if ran_out:
                    # A credible depletion ends the bait even when the rest of the trip could not be read.
                    flush()
                    self._set_bait(None, 'verified', 'Bait ran out')
            elif kind not in ('profile', 'buffs', 'prestige_shop', 'pet', 'quests'):
                # Charms, captchas and account replies are read by automation runs; this logbook keeps no record.
                self.counts['ignored'] += 1
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
        if kind == 'buffs':
            seconds = _cooldown_seconds(parse_responses(buffs=observation.text))
            if seconds is not None:
                # A confirmed cooldown replaces any guess, faster or slower (it resets after prestige).
                self.cooldown, self.tightened = seconds, None
                self._retime()
                self._note(f'Capturing every {self.interval:g} s ({self.cooldown:g} s fishing cooldown)')
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
        # Only a confirmed bait is recorded as the trips' bait; an assumed one is kept beside it, so comparisons
        # (which use only recorded bait) are not built on a guess.
        verified = self.bait_status == 'verified'
        tally = assign_bait(parse_fishing(text), self.bait if verified else NOT_RECORDED)
        tally['bait_status'] = self.bait_status
        if self.bait_status == 'assumed':
            tally['assumed_bait'] = self.bait
        seen = self.latest.get('profile')
        if seen is not None:
            # The conditions these trips were fished in, from your latest confirmed /profile this run, so money
            # estimates can compare like with like.
            profile = parse_responses(profile=seen.text)['profile']
            tally['conditions'] = {'biome': profile['biome'], 'rod': profile['rod'], 'pet': profile['pet'],
                                   'profile_observed_at': seen.observed_at}
            buffs = self.latest.get('buffs')
            if buffs is not None:
                tally['conditions'].update(buff_conditions(parse_responses(buffs=buffs.text)),
                                           buffs_observed_at=buffs.observed_at)
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
