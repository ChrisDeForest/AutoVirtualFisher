"""Optional independent, local OCR of reply bodies. No input or game interaction."""
import os
from math import floor, ceil
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .automation_state import refresh_problems
from .ocr_cleanup import BUTTONS, MESSAGE_HEADER, OTHER_HEADER, clean_ocr
from .paste_detect import _kind
from .player_state import parse_responses


def _identity(line, expected):
    line, expected = _possessive(line), _possessive(expected)
    return line.startswith(expected) and re.fullmatch(r'\W*', line[len(expected):]) is not None


def _possessive(line):
    return re.sub(r"[’‘•\ufffd]s(?= current multipliers \(including bait\):?$)", "'s", line)


def chrome_rows(boxes, extra_controls=()):
    """A recognized control identifies its visual row, including garbled neighboring buttons."""
    if not boxes:
        return set()
    left, right = min(b['left'] for b in boxes), max(b['right'] for b in boxes)
    padding = 3 * max(b['bottom'] - b['top'] for b in boxes)
    def level(box, control):
        return (abs((box['top'] + box['bottom'] - control['top'] - control['bottom']) / 2)
                <= min(box['bottom'] - box['top'], control['bottom'] - control['top']) / 2)

    def beside_item(control):
        # A shop's Select button sits level with its item, far to the right of the name: part of the reply.
        # The button's text may sit a little lower than the name's, so any vertical overlap counts.
        gap = 10 * (control['bottom'] - control['top'])
        return any(box['top'] < control['bottom'] and control['top'] < box['bottom'] and box['text'].strip() not in BUTTONS and box['right'] < control['left'] - gap
                   for box in boxes)

    controls = [b for b in [*boxes, *extra_controls] if b['text'].strip() in BUTTONS
                and left <= b['left'] <= right + padding]
    controls = [control for control in controls if not beside_item(control)]
    return {index for index, box in enumerate(boxes) if any(level(box, control) for control in controls)}


def _numbers(lines):
    return tuple(token.replace(',', '').replace('$', '').lstrip('+') for token in re.findall(
        r'[+-]?\$?\d+(?:,\d{3})*(?:\.\d+)?', '\n'.join(lines)))


def _peer_punctuation(line):
    # Observed Windows OCR reads the terminal period as a dash. Leading signs remain intact.
    if line.endswith('-') and (line.startswith(('Balance: ', 'Fish Value: ', 'Fishing cooldown: '))
                              or re.fullmatch(r'(?:P\d+\s+)?Level .+XP to next level-', line)
                              or re.fullmatch(r'Currently using .+ Rod-', line)):
        return line[:-1] + '.'
    return line


def _buff_units(peer, primary):
    """Correct the observed terminal x→1 glyph only after an independent exact amount read."""
    expected = {}
    for line in primary:
        label, separator, value = line.partition(':')
        if separator and re.fullmatch(r'[+-]?\d+(?:\.\d+)?x\.?', value.strip()):
            expected[label.strip().casefold()] = value.strip().rstrip('.')[:-1]
    result = []
    for line in peer:
        label, separator, value = line.partition(':')
        amount = expected.get(label.strip().casefold())
        if separator and amount is not None and value.strip().rstrip('.') == amount + '1':
            line = label + ': ' + amount + 'x'
        result.append(line)
    return result


def buff_field_candidates(primary, alternate, peers):
    """Choose a field read only when another engine corroborates its exact numeric value and unit."""
    candidate = list(primary)
    for index, line in enumerate(primary[1:], 1):
        label, separator, _ = line.partition(':')
        if not separator:
            continue
        choices = [line] + [other for other in alternate
                            if other.partition(':')[0].strip().casefold() == label.strip().casefold()]
        for choice in choices:
            parsed = parse_responses(buffs=choice)
            if parsed['warnings'] or parsed['unparsed']['buffs'] or not any(parsed['buffs'].values()):
                continue
            supported = False
            for peer in peers:
                for other in peer:
                    normalized = _peer_punctuation(_buff_units([other], [choice])[0])
                    peer_parsed = parse_responses(buffs=normalized)
                    if (not peer_parsed['warnings'] and not peer_parsed['unparsed']['buffs']
                            and peer_parsed['buffs'] == parsed['buffs']):
                        supported = True
            if supported:
                candidate[index] = choice
                break
    return candidate


def profile_field_candidates(primary, alternate, peers):
    """Replace an inventory line only when alternate OCR and a peer agree on its category/name/count."""
    alternate_state = parse_responses(profile='\n'.join(clean_ocr(alternate, 'reply').splitlines()))
    peer_states = [parse_responses(profile='\n'.join(clean_ocr(peer, 'reply').splitlines())) for peer in peers]
    candidate = list(primary)
    category = None
    headings = {'Fish Inventory': 'fish', 'Exotic Fish': 'exotic_fish', 'Special': 'special'}
    for index, line in enumerate(primary):
        cleaned = clean_ocr([line], 'reply')
        if cleaned in headings:
            category = headings[cleaned]
            continue
        match = re.fullmatch(r'(\d[\d,]*) (.+)', cleaned)
        if category is None or match is None:
            continue
        name = match[2]
        for other in alternate:
            other_clean = clean_ocr([other], 'reply')
            replacement = re.fullmatch(r'(\d[\d,]*) ' + re.escape(name), other_clean, re.I)
            if replacement is None:
                continue
            value = int(replacement[1].replace(',', ''))
            # Parsed inventory keys retain the reference name's case.
            supported = lambda state: any(k.casefold() == name.casefold() and v == value
                                          for k, v in (state['inventory'][category] or {}).items())
            if supported(alternate_state) and any(supported(state) for state in peer_states):
                candidate[index] = other_clean
                break
    return candidate


def profile_level_peers(primary, peers, extra_lines):
    """Supplement a missed level line with a separate pixel read corroborated by the primary."""
    wanted = parse_responses(profile='\n'.join(clean_ocr(primary, 'reply').splitlines()))['profile']
    fields = ('prestige', 'level', 'xp_current', 'xp_required')
    result = list(peers)
    for line in extra_lines:
        # Observed separator period after level; no digit is added, removed or guessed.
        line = re.sub(r'^(P\d+\s+)?Level (\d+)\.(?=\d[\d,]*/)', r'\1Level \2, ', line)
        if not re.fullmatch(r'(?:P\d+\s+)?Level \d+,\s*\d[\d,]*/\d[\d,]* XP to next level\.', line):
            continue
        parsed = parse_responses(profile=line)['profile']
        if any(parsed[field] is None or parsed[field] != wanted[field] for field in fields):
            continue
        for peer in peers:
            indices = [i for i, text in enumerate(peer) if re.match(r'^(?:P\d+\s+)?Level ', text)]
            if len(indices) == 1:
                copy = list(peer)
                copy[indices[0]] = line
                result.append(copy)
    return result


def field_consensus(kind, identity_line, field, reads):
    """Agree on one required field from rereads of its line, given as (engine, text). Returns (value, line).

    Every readable value must be the same; unreadable reads neither count nor conflict. Preparations of one
    engine are not independent (two thresholds once agreed on 47,710 for 47,210), so agreement needs both
    engines, or three preparations of Tesseract."""
    from .startup_fields import reply_fields
    found = []
    for engine, text in reads:
        line = _peer_punctuation(re.sub(r'^[|¦!\s]+', '', text.strip()))
        value = reply_fields(kind, identity_line + '\n' + line).get(field) if line else None
        if value is not None:
            found.append((engine, value, line))
    if len({value for _, value, _ in found}) != 1:
        return None
    engines = [engine for engine, _, _ in found]
    if not ('windows' in engines and 'tesseract' in engines or engines.count('tesseract') >= 3):
        return None
    return found[0][1], found[0][2]


def repair_balance_field(original, primary, peers):
    """Recover only a missing balance from independent engines, preserving all other fields."""
    from .startup_fields import reply_fields
    original_fields = reply_fields('profile', '\n'.join(original))
    if 'balance' in original_fields or not original_fields.get('account_name'):
        return None
    cleaned = clean_ocr(primary, 'reply').splitlines()
    fields = reply_fields('profile', '\n'.join(cleaned))
    if fields.get('account_name') != original_fields['account_name'] or 'balance' not in fields:
        return None
    for peer in peers:
        peer_fields = reply_fields('profile', '\n'.join(
            _peer_punctuation(line) for line in clean_ocr(peer, 'reply').splitlines()))
        if (peer_fields.get('account_name') == fields['account_name']
                and peer_fields.get('balance') == fields['balance']):
            replacement = next(line for line in cleaned if line.casefold().startswith('balance:'))
            return [replacement if line.casefold().startswith('balance:') else line for line in original]
    return None


def agreed_reply(kind, identity_line, primary, peers):
    """Accept a complete independent read only with agreeing identity, values, and item names."""
    if kind == 'fishing':
        from .live_capture import fishing_problems
        from .fishing_log import parse_fishing
        def usable(lines):
            cleaned = clean_ocr(lines, 'reply').splitlines()
            return cleaned if cleaned and cleaned[0] == 'You caught:' and not fishing_problems('\n'.join(cleaned)) else None
        cleaned = usable(primary)
        if cleaned is None:
            return None
        tally = parse_fishing('\n'.join(cleaned))
        for peer in peers:
            other = usable(peer)
            if other is not None and parse_fishing('\n'.join(other)) == tally:
                return cleaned
        return None
    if not primary or not _identity(primary[0], identity_line):
        return None
    identity_line = _possessive(identity_line)
    primary = [identity_line, *primary[1:]]
    cleaned = clean_ocr(primary, 'reply').splitlines()
    if refresh_problems(kind, '\n'.join(cleaned)):
        return None
    state = parse_responses(**{kind: '\n'.join(cleaned)})
    for peer in peers:
        if kind == 'buffs':
            peer = _buff_units(peer, primary)
        if not peer or not _identity(peer[0], identity_line) or _numbers(primary) != _numbers(peer):
            continue
        peer = [_possessive(peer[0]), *peer[1:]]
        peer_clean = [_peer_punctuation(line) for line in clean_ocr(peer, 'reply').splitlines()]
        if refresh_problems(kind, '\n'.join(peer_clean)):
            continue
        peer_state = parse_responses(**{kind: '\n'.join(peer_clean)})
        if kind == 'buffs' and peer_state['buffs'] != state['buffs']:
            continue
        if kind == 'profile':
            actual_rod, peer_rod = state['profile']['rod'], peer_state['profile']['rod']
            # The rod emoji's specific observed Z glyph may precede an otherwise exact name.
            if actual_rod is not None and peer_rod == 'Z ' + actual_rod:
                peer_state['profile']['rod'] = actual_rod
            if peer_state['profile'] != state['profile'] or peer_state['inventory'] != state['inventory']:
                continue
        return cleaned
    return None


REQUIRED = {'profile': ('account_name', 'balance', 'level'), 'buffs': ('username', 'cooldown_seconds')}
# Where each rereadable field is written. The identity line is the reply's anchor and is never reread here.
FIELD_LINES = {'balance': re.compile(r'balance\b', re.I), 'level': re.compile(r'(?:P\d+\s+)?Level\b', re.I),
               'cooldown_seconds': re.compile(r'fishing cooldown\b', re.I)}
# (threshold, scale) of the inverted crops given to Tesseract. A 100 threshold misread 47,210 as 47,710.
FIELD_PREPARATIONS = ((None, 3), (None, 4), (140, 3), (180, 3))


def _tesseract(executable, path, psm):
    read = subprocess.run([executable, str(path), 'stdout', '--psm', str(psm)],
                          capture_output=True, text=True, encoding='utf-8', timeout=5,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return [line.strip() for line in read.stdout.splitlines() if line.strip()] if read.returncode == 0 else None


def repair_required_fields(kind, body, image, executable, output, limits):
    """Reread each missing required field from a crop of its own line; None when nothing was recovered.

    `limits` is (top, bottom) of the reply body, so a crop never reaches the next message."""
    from .ocr_image import contrast_region
    from .screenshot_ocr import read_region_lines
    from .startup_fields import reply_fields
    if Path(image).suffix.lower() != '.bmp':
        return None
    lines = [b['text'] for b in body]
    identity = _possessive(lines[0].strip())
    fields = reply_fields(kind, '\n'.join(lines))
    changed = False
    for field in REQUIRED[kind]:
        if field in fields or field not in FIELD_LINES:
            continue
        found = [i for i, b in enumerate(body) if i and FIELD_LINES[field].match(b['text'].strip())]
        if len(found) != 1:
            continue
        index = found[0]
        box = body[index]
        height = max(1, box['bottom'] - box['top'])
        top = max(limits[0], body[index - 1]['bottom'], floor(box['top'] - 0.4 * height))
        bottom = min(limits[1], body[index + 1]['top'] if index + 1 < len(body) else limits[1],
                     ceil(box['bottom'] + 0.4 * height))
        region = (max(0, floor(box['left'] - 2 * height)), top, ceil(box['right'] + 10 * height), bottom)
        reads = []
        for threshold, scale in FIELD_PREPARATIONS:
            contrast_region(image, output, region, scale=scale, threshold=threshold)
            read = _tesseract(executable, output, 7)
            if read:
                reads.append(('tesseract', ' '.join(read)))
        reads += [('windows', ' '.join(read_region_lines(image, *region, scale))) for scale in (2, 3)]
        agreed = field_consensus(kind, identity, field, reads)
        if agreed is not None:
            lines[index] = agreed[1]
            changed = True
    return lines if changed else None


def repair_reply_regions(boxes, image, image_height, extra_controls=()):
    """Reread profile/buffs bodies, keeping original invocation/header evidence untouched."""
    executable = shutil.which('tesseract')
    if not executable:
        return [b['text'] for b in boxes]
    from .screenshot_ocr import read_region_lines, save_region
    skipped = chrome_rows(boxes, extra_controls)
    result, index = [], 0
    while index < len(boxes):
        if index in skipped:
            index += 1
            continue
        anchor = boxes[index]
        kind = _kind(anchor['text'].strip())
        if kind not in ('profile', 'buffs', 'fishing'):
            result.append(anchor['text'])
            index += 1
            continue
        end = index + 1
        while end < len(boxes):
            text = boxes[end]['text'].strip()
            if (end in skipped or MESSAGE_HEADER.fullmatch(text) or OTHER_HEADER.fullmatch(text) or re.match(r'^\S+ used\b', text)
                    or text in BUTTONS or text.startswith('Message')):
                break
            end += 1
        body = boxes[index:end]
        from .startup_fields import reply_fields
        required = ('account_name', 'balance', 'level') if kind == 'profile' else ('username', 'cooldown_seconds')
        fields = reply_fields(kind, '\n'.join(b['text'] for b in body))
        if kind == 'fishing':
            from .live_capture import fishing_problems
            ready = not fishing_problems('\n'.join(b['text'] for b in body))
        else:
            ready = all(key in fields for key in required)
        if ready:
            # Optional OCR noise cannot justify expensive rereads once startup is readable.
            result += [b['text'] for b in body]
            index = end
            continue
        height = max(1, anchor['bottom'] - anchor['top'])
        # Pad the body for emoji and missed words. Never include the next message.
        region = (max(0, round((anchor['left'] - 2 * height) / 10) * 10),
                  max(0, floor(anchor['top'] - height / 2)),
                  round((max(b['right'] for b in body) + 13 * height) / 10) * 10,
                  min(image_height, ceil(body[-1]['bottom'] + height / 2),
                      floor(boxes[end]['top']) if end < len(boxes) else image_height))
        repaired = None
        handle, temporary = tempfile.mkstemp(suffix='.png')
        os.close(handle)
        output = Path(temporary)
        try:
            if kind in REQUIRED:
                # Required fields first, each from its own line: optional inventory noise is not reread.
                fielded = repair_required_fields(kind, body, image, executable, output,
                                                 (region[1], region[3]))
                if fielded is not None and all(key in reply_fields(kind, '\n'.join(fielded))
                                               for key in REQUIRED[kind]):
                    result += fielded
                    index = end
                    continue
            if Path(image).suffix.lower() == '.bmp':
                from .ocr_image import contrast_region
                # Tesseract reads light-on-dark Discord text poorly; it read nothing of a balance
                # line that the inverted crop of the same pixels read exactly.
                contrast_region(image, output, region, threshold=None)
            else:
                save_region(image, output, region, 3)
            primary = _tesseract(executable, output, 6)
            if primary is not None:
                all_peers = []
                # A one-pixel crop phase can drop a word in Windows OCR. Bounded padding
                # variations retain the same reply; every accepted read still needs full semantic agreement.
                for offset in (0, 1, -1):
                    peer_region = (region[0], max(0, region[1] + offset), region[2], region[3])
                    peers = [read_region_lines(image, *peer_region, scale) for scale in (2, 3)]
                    all_peers += peers
                    repaired = agreed_reply(kind, anchor['text'].strip(), primary, peers)
                    if repaired is not None:
                        break
                if repaired is None and kind == 'buffs' and Path(image).suffix.lower() == '.bmp':
                    from .ocr_image import contrast_region
                    # Keep the original field layout and identity; a contrasted crop supplies
                    # alternate field reads, never inferred decimal points or magnitudes.
                    contrast_region(image, output, region, threshold=180)
                    alternate_read = subprocess.run([executable, str(output), 'stdout', '--psm', '6'],
                        capture_output=True, text=True, encoding='utf-8', timeout=5,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    if alternate_read.returncode == 0:
                        alternate = [line.strip() for line in alternate_read.stdout.splitlines() if line.strip()]
                        candidate = buff_field_candidates(primary, alternate, all_peers)
                        repaired = agreed_reply(kind, anchor['text'].strip(), candidate, all_peers)
                if repaired is None and kind == 'profile' and Path(image).suffix.lower() == '.bmp':
                    from .ocr_image import contrast_region
                    from .screenshot_ocr import read_image_boxes
                    contrast_region(image, output, region, threshold=140)
                    extra_peer = [b['text'] for b in read_image_boxes(output)]
                    alternate_read = subprocess.run([executable, str(output), 'stdout', '--psm', '6'],
                        capture_output=True, text=True, encoding='utf-8', timeout=5,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    if alternate_read.returncode == 0:
                        alternate = [line.strip() for line in alternate_read.stdout.splitlines() if line.strip()]
                        candidate = profile_field_candidates(primary, alternate, all_peers)
                        supplemented = profile_level_peers(candidate, all_peers, extra_peer)
                        repaired = agreed_reply(kind, anchor['text'].strip(), candidate, supplemented)
                if repaired is None and kind == 'profile':
                    repaired = repair_balance_field([b['text'] for b in body], primary, all_peers)
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            # An unavailable/failed independent read cannot change the original observation.
            pass
        finally:
            output.unlink(missing_ok=True)
        result += repaired if repaired is not None else [b['text'] for b in body]
        index = end
    return result
