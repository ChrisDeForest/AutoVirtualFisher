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


def _pet_names():
    try:
        return sorted(json.loads(PET_REFERENCE_PATH.read_text(encoding='utf-8'))['pets'], key=len, reverse=True)
    except (OSError, json.JSONDecodeError, KeyError):
        return []


def clean_ocr(lines: list[str], label: str) -> str:
    """Return cleaned text with a Discord-style header per bot message, labelled by the screenshot name."""
    pet_names = _pet_names()
    pet_header = (re.compile(r'^.*?\b(' + '|'.join(map(re.escape, pet_names)) + r')\s*\((Lvl .*)$')
                  if pet_names else None)
    cleaned, after_used, in_tip = [], False, False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if re.match(r'^\S+ used\b', line):
            # "epicmouse used" is followed by the command name, unless OCR put both on one line.
            after_used = line.endswith(' used')
            continue
        if after_used:
            after_used = False
            if re.fullmatch(r'\W*\s*\w+', line):
                continue
        if in_tip and line[:1].islower():
            continue
        in_tip = line.startswith('TIP:')
        if in_tip or TIME.fullmatch(line):
            continue
        if re.fullmatch(r'[^\w]*\s*(' + '|'.join(map(re.escape, BUTTONS)) + ')', line):
            continue
        if line.startswith('Virtual Fisher'):
            cleaned += ['Virtual Fisher', 'APP', f' — screenshot {label}']
            continue
        # The game writes "(Lvl 30)"; OCR often reads the "l" as "1" ("Lv130").
        line = re.sub(r'\(Lv[1lI|]\s?(\d+)\)', r'(Lvl \1)', line)
        if pet_header and (match := pet_header.match(line)):
            line = f'{match[1]}({match[2]}'
        line = re.sub(r'^(' + '|'.join(map(re.escape, BUFF_LABELS)) + r')[.,;]\s', r'\1: ', line)
        # Emoji leave short lowercase fragments between a count or label and the name ("160 to Hooks").
        line = re.sub(r'^(\d[\d,]*|[^:]+:)\s+[a-z]{1,2}\s+(?=[A-Z])', r'\1 ', line)
        line = re.sub(r'^(\d[\d,]*\s+)([a-z])', lambda match: match[1] + match[2].upper(), line)
        cleaned.append(line)
    return '\n'.join(cleaned)
