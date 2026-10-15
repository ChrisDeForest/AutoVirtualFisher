"""Parse the account replies automation reads: shops, rods, bait, biome, boats, pets, prestige shop, boosts.

Pure functions: reply text in, {'values', 'unread', 'page'} out. 'unread' names what this reply should show but
did not read cleanly, so the read is tried again. Paged shops are judged complete on both pages together
(account_state.section_unread). Nothing is inferred: a value that did not read is absent.
"""
import re

from .paste_detect import SLASH
from .player_state import BAIT_NAMES, _clean, _rod_names, known_bait, known_rod, parse_responses

UPGRADES = ('Better Fish', 'Salesman', 'Bait Efficiency', 'More Chests', 'Worker Motivation', 'Artifact Specialist',
            'Experienced', 'Better Chests', 'Better Dailies')
SPECIAL = ('Fish Ovens', 'Bait Lover', 'Aquatic Expert', 'Worker Extender', 'Ultimate Salesman',
           'Highly Experienced', 'Boost Booster', 'Statistician', 'Duplicator', 'Charmer')
LEAGUE = ('Pet Helper', 'Bait Helper', 'Super Crates', 'Worker Crates', 'Fishing Frenzy', 'Duplicator 2.0')
BOATS = ('Rowboat', 'Fishing Boat', 'Speedboat', 'Pontoon', 'Sailboat', 'Yacht', 'Luxury Yacht', 'Cruise Ship',
         'Goldboat', 'Sky Cruiser', 'Satellite', 'Space Shuttle', 'Cruiser', 'Alien Raft', 'Alien Submarine')
BIOMES = ('River', 'Volcanic', 'Ocean', 'Sky', 'Space', 'Alien')
# Button labels and the shop's own wording: never items, never unread.
BUTTONS = {'back', 'return', 'select', 'selected', 'deselect', 'maxed'}

# OCR may read the slash of "Page 1/2" as a 1 ("Page 112"), l, I or |.
PAGE = re.compile(r'Page\s*(\d+)\s*(?:/|[1lI|](?=\s*\d))\s*(\d+)', re.I)
LEVELLED = re.compile(r'(.+?)\s*\(\s*(\S+)\s*/\s*(\S+)\s*\)$')
MONEY = re.compile(r'^\W*\$\s*([\d,]+)\W*$')
# The Hook icon may read as up to four stray characters ("t.' 150").
HOOK_PRICE = re.compile(r'^\D{0,4}?(\d[\d,]*)$')
NUMBER = re.compile(r'^\d[\d,]*$')


def _lines(text):
    return [line for line in (_clean(raw) for raw in (text or '').splitlines()) if line]


def _number(text):
    return int(text.replace(',', ''))


def _page(lines):
    found = [match for match in map(PAGE.search, lines) if match]
    return (int(found[-1][1]), int(found[-1][2])) if found else (None, None)


def _known(raw, names):
    """A known name for an OCR'd item, ignoring case and an emoji glyph read before it."""
    raw = re.sub(r'^\W+', '', raw).strip().casefold()
    for name in sorted(names, key=len, reverse=True):
        key = name.casefold()
        if raw == key or raw.endswith(' ' + key):
            return name
    return None


def _result(values, unread=(), lines=()):
    return {'values': values, 'unread': list(unread), 'page': _page(lines)}


def _balance(lines, label):
    for line in lines:
        match = re.match(rf'{label}:\s*\$?\s*([\d,]+)', line, re.I)
        if match:
            return _number(match[1])
    return None


def _attach(items, order, price_of):
    """Pair each item with the price (or button) line beside it. On screen the button sits level with its item,
    so OCR may give it just before or just after the name: a price belongs to the item above it when nothing else
    came between them, otherwise to the next item."""
    pending, last, since_last = None, None, 0
    for kind, value in order:
        if kind == 'item':
            last, since_last = value, 0
            if pending is not None:
                price_of(items[value], pending)
                pending = None
            continue
        if kind == 'price':
            if last is not None and since_last == 0 and not items[last].get('_priced'):
                price_of(items[last], value)
            else:
                pending = value
        since_last += 1


def parse_levelled(text, names, currency='money'):
    """A shop whose items read "Name (level/max)" with a price button: upgrades, league shop."""
    lines = _lines(text)
    items, order, unread = {}, [], []
    price_pattern = MONEY if currency == 'money' else HOOK_PRICE
    for line in lines:
        match = LEVELLED.match(line)
        if match and not PAGE.search(line):
            name = _known(match[1], names)
            if name and NUMBER.match(match[2]) and NUMBER.match(match[3]):
                items[name] = {'level': _number(match[2]), 'max': _number(match[3]), 'price': None}
                order.append(('item', name))
            else:
                unread.append(line)
            continue
        price = price_pattern.match(line)
        if price and not re.search(r'balance', line, re.I):
            order.append(('price', _number(price[1])))
        elif line.casefold() == 'maxed':
            order.append(('price', None))
        else:
            order.append(('other', line))

    def set_price(item, value):
        item['price'], item['_priced'] = value, True
    _attach(items, order, set_price)
    for item in items.values():
        item.pop('_priced', None)
    label = 'Your balance' if currency == 'money' else 'Balance'
    values = {'items': items, ('balance' if currency == 'money' else 'hooks'): _balance(lines, label)}
    return _result(values, unread, lines)


def parse_shop_upgrades(text):
    return parse_levelled(text, UPGRADES)


def parse_shop_league(text):
    return parse_levelled(text, LEAGUE, currency='hooks')


def parse_shop_special(text):
    """/shop special: upgrades bought with exotic fish, "Fish Ovens (2/20)". Below the level the shop names ("More
    upgrades unlocked at level 100.") the rest are not listed: not unlocked yet. "Inventory: 49" is the exotic fish
    its prices are in (its icon is not read)."""
    parsed = parse_levelled(text, SPECIAL, currency='hooks')
    values = parsed['values']
    values.pop('hooks', None)
    inventory = re.search(r'Inventory:\s*([\d,]+)', text, re.I)
    more = re.search(r'More upgrades unlocked at level\s*([\d,]+)', text, re.I)
    values['inventory'] = _number(inventory[1]) if inventory else None
    values['more_at_level'] = _number(more[1]) if more else None
    # Only page 1 is read; a page 2 (expected from level 100) is flagged on the page until it is read too.
    values['pages'] = parsed['page'][1]
    return parsed


def parse_rod(text):
    lines = _lines(text)
    current = next((m[1].strip() for m in (re.match(r'Currently using:\s*(.+)$', l, re.I) for l in lines) if m), None)
    if current:
        current = known_rod(current)
    # Each rod line counts when it is closest to one rod in the game data (its icon read as a letter, a misread).
    rods = set(_rod_names())
    owned = list(dict.fromkeys(rod for rod in (known_rod(line) for line in lines
                                               if not line.lower().startswith(('currently', '/rod')))
                               if rod in rods))
    return _result({'current': current, 'owned': owned}, [] if current else ['Rod in use'], lines)


def _owned_bait(line):
    """(bait, count) from an owned-bait line ("<icon> Magic Bait - 1,485"), else None. The icon may read as a stray
    letter or symbol, the dash as an en/em dash or tilde, and the thousands comma as a period or a space."""
    match = re.match(r'^(.*?[A-Za-z])\s*[-–—~]\s*(\d[\d,. ]*)$', line)
    if not match:
        return None
    bait = known_bait(match[1].strip())
    return (bait, int(re.sub(r'\D', '', match[2]))) if bait in BAIT_NAMES else None


def parse_bait(text):
    """The bait in use and how many of each bait are owned ("Worms - 68" lines; a bait with none is not listed, so
    a whole reply with no lines means none owned). `owned` is None when the reply was not whole."""
    lines = _lines(text)
    current = None
    found = False
    owned, other = {}, []
    for line in lines:
        match = re.match(r'Current bait:\s*(.+)$', line, re.I)
        counted = _owned_bait(line)
        if match:
            found = True
            current = None if match[1].strip().upper() == 'NONE' else known_bait(match[1].strip())
        elif counted:
            owned[counted[0]] = counted[1]
        elif not re.match(SLASH + r'?\s*bait\b', line, re.I):
            other.append(line)
    return _result({'current': current, 'owned': owned if found else None, 'other_lines': other},
                   [] if found else ['Bait in use'], lines)


def parse_biome(text):
    lines = _lines(text)
    values = {'current': None, 'unlocked': [], 'next': None, 'next_level': None}
    in_list = False
    for line in lines:
        if match := re.match(r'Current Biome:\s*(\w+)', line, re.I):
            values['current'] = _known(match[1], BIOMES)
        elif re.match(r'Your unlocked biomes', line, re.I):
            in_list = True
        elif match := re.match(r'Next Biome:\s*(\w+)', line, re.I):
            in_list = False
            values['next'] = _known(match[1].split()[-1], BIOMES)
        elif match := re.search(r'UNLOCKED AT LEVEL\s*(\d+)', line, re.I):
            values['next_level'] = int(match[1])
        elif in_list and _known(line, BIOMES):
            values['unlocked'].append(_known(line, BIOMES))
    return _result(values, [] if values['current'] else ['Biome in use'], lines)


def parse_shop_boats(text):
    lines = _lines(text)
    items, unread = {}, []
    for line in lines:
        match = re.match(r'^\W*(.+?)\s*-\s*(OWNED|\$\s*[\d,]+)\W*$', line, re.I)
        if not match:
            continue
        name = _known(match[1], BOATS)
        if name is None:
            # Use the same unique close-name rule as rods/bait; never repair prices or ownership text.
            from .player_state import _known as match_item_name
            candidate = match_item_name(match[1], BOATS)
            name = candidate if candidate in BOATS else None
        if name is None:
            unread.append(line)
            continue
        owned = match[2].upper() == 'OWNED'
        items[name] = {'owned': owned, 'price': None if owned else _number(match[2].lstrip('$').strip())}
    more = next((int(m[1]) for m in (re.search(r'Unlock more boats at Level (\d+)', l, re.I) for l in lines) if m),
                None)
    if not items:
        unread.append('Boats')
    return _result({'items': items, 'balance': _balance(lines, 'Your balance'), 'more_at_level': more}, unread,
                   lines)


def parse_pets(text):
    lines = _lines(text)
    parsed = parse_responses(pet='\n'.join(line for line in lines if line.casefold() not in BUTTONS))
    pets = parsed['pets']
    owned = pets['owned'] or {}
    values = {'current': pets['current_pet'], 'xp_per_trip': pets['xp_per_fishing_trip_average'], 'owned': owned}
    # A line that looks like a pet's ("Name(Lvl N)") but did not read as one is named, never silently dropped: live,
    # Axolotl went missing from every /pet read (2026-10-14).
    looks_like_pet = re.compile(r'\(\s*L\S{0,3}\s*\S{0,3}\d|\bLvl\b', re.I)
    missed = [line for line in parsed['unparsed'].get('pet', []) if looks_like_pet.search(line)]
    missed += [pet['description'] for pet in owned.values()
               if pet.get('description') and looks_like_pet.search(pet['description'])]
    return _result(values, ([] if owned else ['Pets']) + [f'pet line {line!r}' for line in missed], lines)


def parse_prestige_shop(text):
    lines = _lines(text)
    prestige = parse_responses(prestige_shop='\n'.join(lines))['prestige']
    values = {'azure_fish': prestige['azure_fish'], 'upgrades': prestige['upgrades']}
    return _result(values, [] if prestige['azure_fish'] is not None else ['Azure Fish'], lines)


def parse_boosts(text):
    lines = _lines(text)
    values = {'global_duration': None, 'booster': None, 'active': {}}
    for line in lines:
        if match := re.match(r'Global Boost Duration:\s*(.+)$', line, re.I):
            values['global_duration'] = match[1].strip()
        elif match := re.match(r'Current Booster:\s*(.+)$', line, re.I):
            values['booster'] = match[1].strip()
        elif match := re.match(r'^(.+?):\s*(\d+[hms].*)$', line):
            values['active'][match[1].strip()] = match[2].strip()
    return _result(values, (), lines)


PARSERS = {'shop_upgrades': parse_shop_upgrades, 'shop_league': parse_shop_league, 'shop_special': parse_shop_special,
           'rod': parse_rod, 'bait': parse_bait, 'biome': parse_biome, 'shop_boats': parse_shop_boats,
           'pet': parse_pets, 'prestige_shop': parse_prestige_shop, 'boosts': parse_boosts}
