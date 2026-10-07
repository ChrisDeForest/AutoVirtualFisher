"""Turn OCR lines from Discord screenshots into text the smart paste and parsers understand."""
import json
from pathlib import Path
import re

PET_REFERENCE_PATH = Path(__file__).parent / 'reference' / 'pet.json'
BUTTONS = ('Fish Again', 'Sell', 'Return', 'Back', 'Deselect', 'Select', 'Inventory', 'Statistics',
           'Leaderboards', 'Charms', 'Pets')
BUFF_LABELS = ('Sell price', 'Fish catch', 'Fish quality', 'Treasure chance', 'Treasure quality',
               'XP Multiplier', 'Fishing cooldown')
TIME = re.compile(r'\d{1,2}:\d{2}\s*[AP]M', re.I)
DATED_TIME = re.compile(r'(?:Today|Yesterday) at\s+\d{1,2}:\d{2}\s*[AP]M', re.I)
# A message header: the bot name, its APP tag as OCR reads it ("APP", "AP", "VAPP", "V APP"), and/or a time.
# Rejects look-alikes such as the DM title "Virtual Fisher [fish" or the profile panel's "Virtual Fisher#7036".
MESSAGE_HEADER = re.compile(r'Virtual Fisher(?:\s+[V✓]?\s*A?PP?)?'
                            r'(?:\s+(?:(?:Today|Yesterday) at\s+)?\d{1,2}:\d{2}\s*[AP]M)?', re.I)
# Any other author's header: a name, an optional APP tag, and a time. Keeps other bots' and players' messages
# apart from Virtual Fisher's instead of letting them run into the message above.
OTHER_HEADER = re.compile(r'(?P<name>\S.{0,40}?)(?P<tag>\s+[V✓]?\s*A?PP)?\s+(?:(?:Today|Yesterday) at\s+)?'
                          r'(?P<time>\d{1,2}:\d{2}\s*[AP]M)', re.I)
# Discord chrome that can sit inside the chat column: the message box and the slash-command list.
CHROME = re.compile(r'Message\s*[@#].*|[/\[|]\w+')
# Prefix of the lines capture=True adds after a header: who used the command, and when the reply was sent.
META = '↪ '


def _pet_names():
    try:
        return sorted(json.loads(PET_REFERENCE_PATH.read_text(encoding='utf-8'))['pets'], key=len, reverse=True)
    except (OSError, json.JSONDecodeError, KeyError):
        return []


def clean_ocr(lines: list[str], label: str, capture: bool = False) -> str:
    """Return cleaned text with a Discord-style header per message, labelled by the screenshot name.

    With `capture`, each header is followed by META lines saying who used the command ("used by <name>") and
    when the reply was sent ("sent 11:40 AM"), when OCR read them; live capture uses these as evidence.
    """
    pet_names = _pet_names()
    pet_header = (re.compile(r'^.*?\b(' + '|'.join(map(re.escape, pet_names)) + r')\s*\((Lvl .*)$')
                  if pet_names else None)
    cleaned, after_used, in_tip, invoker = [], False, False, None

    def header(name, bot, when):
        nonlocal invoker
        cleaned.extend([name] + (['APP'] if bot else []) + [f' — screenshot {label}'])
        if capture:
            cleaned.extend(([f'{META}used by {invoker}'] if invoker else [])
                           + ([f'{META}sent {when.upper()}'] if when else []))
        invoker = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if used := re.match(r'^(\S+) used\b', line):
            # "epicmouse used" is followed by the command name, unless OCR put both on one line.
            after_used = line.endswith(' used')
            invoker = used[1]
            continue
        if after_used:
            after_used = False
            if re.fullmatch(r'\W*\s*\w+', line):
                continue
        if in_tip and line[:1].islower():
            continue
        in_tip = line.startswith('TIP:')
        if in_tip or TIME.fullmatch(line) or DATED_TIME.fullmatch(line):
            continue
        if re.fullmatch(r'[^\w]*\s*(' + '|'.join(map(re.escape, BUTTONS)) + ')', line):
            continue
        if MESSAGE_HEADER.fullmatch(line):
            header('Virtual Fisher', True, (TIME.search(line) or [None])[0])
            continue
        if CHROME.fullmatch(line):
            continue
        if other := OTHER_HEADER.fullmatch(line):
            header(other['name'], bool(other['tag']), other['time'])
            continue
        invoker = None
        # The game writes "(Lvl 30)"; OCR often reads the "l" as "1" ("Lv130").
        line = re.sub(r'\(Lv[1lI|]\s?(\d+)\)', r'(Lvl \1)', line)
        if pet_header and (match := pet_header.match(line)):
            line = f'{match[1]}({match[2]}'
        line = re.sub(r'^(' + '|'.join(map(re.escape, BUFF_LABELS)) + r')[.,;]\s', r'\1: ', line)
        # Emoji leave short lowercase fragments between a count or label and the name ("160 to Hooks").
        line = re.sub(r'^(\d[\d,]*|[^:]+:)\s+[a-z]{1,2}\s+(?=[A-Z])', r'\1 ', line)
        # ...or punctuation stuck to the name ("5 'cod").
        line = re.sub(r'^(\d[\d,]*)\s+[^\w\s$]+\s*(?=[A-Za-z])', r'\1 ', line)
        line = re.sub(r'^(\d[\d,]*\s+)([a-z])', lambda match: match[1] + match[2].upper(), line)
        cleaned.append(line)
    return '\n'.join(cleaned)
