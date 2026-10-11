"""Parse the account replies automation reads: shops, rods, bait, biome, boats, pets, prestige shop, boosts.

Pure functions: reply text in, {'values', 'unread', 'page'} out. 'unread' names what this reply should show but
did not read cleanly, so the read is tried again. Paged shops are judged complete on both pages together
(account_state.section_unread). Nothing is inferred: a value that did not read is absent.
"""
import re

from .paste_detect import SLASH
from .player_state import _clean, parse_responses

UPGRADES = ('Better Fish', 'Salesman', 'Bait Efficiency', 'More Chests', 'Worker Motivation', 'Artifact Specialist',
            'Experienced', 'Better Chests', 'Better Dailies')
LEAGUE = ('Pet Helper', 'Bait Helper', 'Super Crates', 'Worker Crates', 'Fishing Frenzy', 'Duplicator 2.0')
BOATS = ('Rowboat', 'Fishing Boat', 'Speedboat', 'Pontoon', 'Sailboat', 'Yacht', 'Luxury Yacht', 'Cruise Ship',
         'Goldboat', 'Sky Cruiser', 'Satellite', 'Space Shuttle', 'Cruiser', 'Alien Raft', 'Alien Submarine')
BIOMES = ('River', 'Volcanic', 'Ocean', 'Sky', 'Space', 'Alien')
# Longest first: an owned-bait line is matched by its ending, and "Artifact Magnet" ends in "Magnet".
BAITS = ('Artifact Magnet', 'Support Bait', 'Magic Bait', 'Wise Bait', 'Leeches', 'Magnet', 'Worms', 'Fish')
# Button labels and the shop's own wording: never items, never unread.
BUTTONS = {'back', 'return', 'select', 'selected', 'deselect', 'maxed'}

# OCR may read the slash of "Page 1/2" as a 1 ("Page 112"), l, I or |.
PAGE = re.compile(r'Page\s*(\d+)\s*(?:/|[1lI|](?=\s*\d))\s*(\d+)', re.I)
LEVELLED = re.compile(r'(.+?)\s*\(\s*(\S+)\s*/\s*(\S+)\s*\)$')
MONEY = re.compile(r'^\W*\$\s*([\d,]+)\W*$')
HOOK_PRICE = re.compile(r'^\D{0,3}?(\d[\d,]*)$')
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
    for name in names:
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


def parse_shop_rods(text):
    lines = _lines(text)
    items, order = {}, []
    for line in lines:
        rod = re.match(r'^\W*([A-Z][a-z]+) Rod$', line)
        if rod and line != 'Fishing Rod Shop':
            name = f'{rod[1]} Rod'
            items[name] = {'owned': False, 'selected': False, 'price': None}
            order.append(('item', name))
        elif line.casefold() in ('select', 'selected'):
            order.append(('price', line.casefold()))
        elif MONEY.match(line) and not re.search(r'balance', line, re.I):
            order.append(('price', _number(MONEY.match(line)[1])))
        else:
            order.append(('other', line))

    def set_status(item, value):
        item['_priced'] = True
        if value in ('select', 'selected'):
            item['owned'], item['selected'] = True, value == 'selected'
        else:
            item['price'] = value
    _attach(items, order, set_status)
    for item in items.values():
        # Every rod shows a price, Select or Selected. A rod with neither read is not known to be owned: OCR cleanup
        # drops a line that is only a button word (Select), and a rod the player cannot afford has its price on a
        # dimmed button OCR misses (Lava and Magma Rod were once read as owned). /rod lists the rods owned.
        if (not item.pop('_priced', None) or item['price'] is None) and not item['owned']:
            item['owned'] = None
    more =next((int(m[1]) for m in (re.search(r'More rods unlocked at level (\d+)', l, re.I) for l in lines) if m),
                None)
    return _result({'items': items, 'balance': _balance(lines, 'Your balance'), 'more_at_level': more}, (), lines)


def parse_rod(text):
    lines = _lines(text)
    current = next((m[1].strip() for m in (re.match(r'Currently using:\s*(.+)$', l, re.I) for l in lines) if m), None)
    # A rod's icon may read as one stray letter ("J Supporter Rod").
    owned = [match[1] for match in (re.match(r'^\W*(?:[A-Za-z]\s+)?([A-Z][a-z]+ Rod)$', line) for line in lines
                                    if not line.lower().startswith('currently')) if match]
    return _result({'current': current, 'owned': owned}, [] if current else ['Rod in use'], lines)


def _owned_bait(line):
    """(bait, count) from an owned-bait line ("<icon> Magic Bait - 1,485"), else None. The icon may read as a stray
    letter or symbol, the dash as an en/em dash or tilde, and the thousands comma as a period or a space."""
    match = re.match(r'^(.*?[A-Za-z])\s*[-–—~]\s*(\d[\d,. ]*)$', line)
    if not match:
        return None
    name = match[1].strip()
    for bait in BAITS:   # longest first, so "Artifact Magnet" is not read as "Magnet"
        if name.lower().endswith(bait.lower()):
            prefix = name[:len(name) - len(bait)].strip()
            if len(prefix) <= 2 and not re.search(r'[a-z]{2}', prefix, re.I):
                return bait, int(re.sub(r'\D', '', match[2]))
    return None


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
            current = None if match[1].strip().upper() == 'NONE' else match[1].strip()
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
    pets = parse_responses(pet='\n'.join(line for line in lines if line.casefold() not in BUTTONS))['pets']
    owned = pets['owned'] or {}
    values = {'current': pets['current_pet'], 'xp_per_trip': pets['xp_per_fishing_trip_average'], 'owned': owned}
    return _result(values, [] if owned else ['Pets'], lines)


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


PARSERS = {'shop_upgrades': parse_shop_upgrades, 'shop_league': parse_shop_league, 'shop_rods': parse_shop_rods,
           'rod': parse_rod, 'bait': parse_bait, 'biome': parse_biome, 'shop_boats': parse_shop_boats,
           'pet': parse_pets, 'prestige_shop': parse_prestige_shop, 'boosts': parse_boosts}
