"""Sort one big paste (e.g. a range of Discord messages) into the logbook's response types."""
import re

from .player_state import _clean

# " — Yesterday at 9:20 PM", "epicmouse — 10/3/2025 11:03 AM", 24-hour variants, or the
# " — screenshot <file>" headers that ocr_cleanup writes.
HEADER = re.compile(r'^(?P<name>.*?)\s*—\s*(?P<when>(?:Today|Yesterday) at \d{1,2}:\d{2}(?:\s*[AP]M)?'
                    r'|\d{1,2}[/.]\d{1,2}[/.]\d{2,4},? \d{1,2}:\d{2}(?:\s*[AP]M)?|screenshot .+)\s*$', re.I)
BOT_TAGS = ('APP', 'BOT')
PLAYER_KINDS = ('profile', 'buffs', 'prestige_shop', 'pet', 'quests')


def _kind(line):
    """Return the response type a cleaned line identifies, if any."""
    if line.startswith('Inventory of '):
        return 'profile'
    if re.search(r"current multipliers \(including bait\):?$", line, re.I):
        return 'buffs'
    if line == 'Prestige Shop':
        return 'prestige_shop'
    if (line.startswith('Pets are permanent') or line.startswith('Current pet:') or line.startswith('You have no pets!')
            or re.search(r'pet XP per fishing trip', line, re.I)):
        return 'pet'
    if line == 'Quest List' or line.startswith('Quests reset in'):
        return 'quests'
    if line in ('You caught:', 'QUEST COMPLETE!'):
        return 'fishing'
    if line.startswith('Charms are found in') or re.fullmatch(r'[\d,]+\s*/\s*[\d,]+\s*-\s*Charms found\.?', line, re.I):
        return 'charms'
    # Account replies read by automation runs (each by the fixed line it always shows).
    for kind, marker in ACCOUNT_MARKERS:
        if marker.match(line):
            return kind
    return None


ACCOUNT_MARKERS = (
    ('shop_upgrades', re.compile(r'Upgrades in this category are permanent', re.I)),
    ('shop_rods', re.compile(r'Fishing Rod Shop$', re.I)),
    ('rod', re.compile(r'/rod\s*<rod name>', re.I)),
    ('bait', re.compile(r'/bait\s*<bait type>', re.I)),
    ('biome', re.compile(r'Current Biome:', re.I)),
    ('shop_boats', re.compile(r'All boats are permanent', re.I)),
    ('shop_league', re.compile(r'League Shop$', re.I)),
    ('boosts', re.compile(r'Active Boosts$', re.I)),
)


def split_messages(text: str) -> list[dict]:
    """Split on Discord's message headers. Text without headers comes back as one message."""
    messages = []
    current = {'author': None, 'bot': None, 'when': None, 'lines': []}

    def finish():
        if any(line.strip() for line in current['lines']):
            messages.append(current)

    for line in text.replace('\r', '').split('\n'):
        header = HEADER.match(line.strip())
        if not header:
            current['lines'].append(line)
            continue
        name, bot = header['name'].strip(), False
        lines = current['lines']
        while lines and not lines[-1].strip():
            lines.pop()
        if not name:
            # Bot headers span three lines: the name, an APP/BOT tag, then " — <time>".
            if lines and lines[-1].strip() in BOT_TAGS:
                bot = True
                lines.pop()
            if lines:
                name = lines.pop().strip()
        else:
            for tag in BOT_TAGS:
                if name.endswith(' ' + tag):
                    name, bot = name[:-len(tag) - 1].strip(), True
        finish()
        current = {'author': name or None, 'bot': bot, 'when': header['when'], 'lines': []}
    finish()
    return messages


def _blocks(lines):
    """Split headerless text where a different response type begins."""
    blocks, current, kind = [], [], None
    for line in lines:
        found = _kind(_clean(line))
        if found and found != kind and any(entry.strip() for entry in current):
            moved = []
            if found == 'fishing':
                # Keep the fishing author line (e.g. "epicmouse9925") with its result.
                while current and not current[-1].strip():
                    current.pop()
                if current and re.fullmatch(r'\S+', current[-1].strip()) and ':' not in current[-1]:
                    moved = [current.pop()]
            blocks.append((kind, current))
            current = moved
        if found and found != kind:
            kind = found
        current.append(line)
    if any(entry.strip() for entry in current):
        blocks.append((kind, current))
    return blocks


def detect(text: str) -> dict:
    """Return the newest text of each player response, all fishing results joined, and what was skipped."""
    responses = {kind: '' for kind in PLAYER_KINDS}
    fishing, counts, times = [], {}, {}
    skipped_bot = skipped_user = 0
    for message in split_messages(text):
        if message['bot'] is False:
            skipped_user += 1
            continue
        if message['author'] is None:
            pieces = _blocks(message['lines'])
        else:
            kinds = [found for found in (_kind(_clean(line)) for line in message['lines']) if found]
            pieces = [(kinds[0] if kinds else None, message['lines'])]
        for kind, lines in pieces:
            if kind is None or (kind != 'fishing' and kind not in responses):
                # Replies only automation runs read (charms, shops, …) are skipped here as before.
                skipped_bot += 1
                continue
            counts[kind] = counts.get(kind, 0) + 1
            body = '\n'.join(lines).strip('\n')
            if kind == 'fishing':
                fishing.append(body)
            elif kind in responses:
                responses[kind] = body
                times[kind] = message['when']
    return {'responses': responses, 'fishing': '\n\n'.join(fishing), 'counts': counts, 'times': times,
            'skipped_bot': skipped_bot, 'skipped_user': skipped_user}
