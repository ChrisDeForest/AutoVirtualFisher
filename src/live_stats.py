"""Live player stats: the last synced /profile and /buffs, carried forward trip by trip.

Pure functions, no I/O. A sync reads whatever the replies show (OCR-garbled fields stay unknown). Each trip then
adds its XP (with level-ups from the wiki level table), catches, chest finds and money, and the fish value its
catches add at wiki sell price times the sell multiplier. The next sync replaces the projection with fresh values.
"""
from copy import deepcopy
import re

from .fishing_log import CHARM_NAMES, EXOTIC_FISH, known_name
from .player_planner import xp_required
from .player_state import _clean, parse_responses

# Read only when the whole line did not parse: observed "P4 Level 31, xp to next level." with the numbers lost.
LEVEL_ONLY = re.compile(r'(?:P(\d+)\s+)?Level (\d+),', re.I)
PET = re.compile(r'Pet:\s*(.*?)\s*\(Level (\d+)\)', re.I)
# Fields a later read that could not see them keeps from the earlier read; the XP ones only on the same level.
CARRIED = ('prestige', 'xp_current', 'xp_required', 'xp_to_next_level', 'rod', 'biome', 'pet', 'pet_level')


def synced_stats(profile_text: str, buffs_text: str) -> dict:
    """What a /profile and /buffs reply show, best effort: unreadable fields are None or absent."""
    parsed = parse_responses(profile=profile_text or '')
    profile = dict(parsed['profile'])
    lines = [_clean(line) for line in (profile_text or '').splitlines()]
    if profile.get('level') is None:
        found = [match for match in map(LEVEL_ONLY.match, lines) if match]
        if len(found) == 1:
            profile['level'] = int(found[0][2])
            if found[0][1] is not None and profile.get('prestige') is None:
                profile['prestige'] = int(found[0][1])
    pets = [match for match in map(PET.match, lines) if match]
    profile['pet_level'] = int(pets[0][2]) if len(pets) == 1 else None
    if profile.get('pet') and profile['pet_level'] is not None:
        # The parser keeps "Dolphin (Level 51)" whole; shown here as name and level.
        profile['pet'] = re.sub(r'\s*\(Level \d+\)$', '', profile['pet'], flags=re.I) or None
    buffs = parse_responses(buffs=buffs_text or '')['buffs'] if buffs_text else {}
    return {'profile': profile,
            'inventory': {category: dict(items or {}) for category, items in parsed['inventory'].items()},
            'buffs': {name: value for name, value in buffs.items() if value is not None}}


PROFILE_PARTS = (('balance', 'Balance'), ('level', 'Level'), ('xp_current', 'XP progress'), ('rod', 'Rod'),
                 ('biome', 'Biome'), ('fish_value', 'Fish value'))
BUFF_PARTS = (('sell_price', 'Sell price'), ('fish_catch', 'Fish catch'), ('fish_quality', 'Fish quality'),
              ('treasure_chance', 'Treasure chance'), ('treasure_quality', 'Treasure quality'),
              ('xp_multiplier', 'XP multiplier'), ('fishing_cooldown', 'Fishing cooldown'))


def unread_fields(kind: str, text: str) -> list[str]:
    """What this one /profile, /buffs or /charms reply does not show readably. A pet counts once the reply has a
    pet line. A /charms reply needs all eight charm lines, and its total must agree with them."""
    if kind == 'charms':
        parsed = parse_charms(text)
        missing = [name for name in CHARM_NAMES if name not in parsed['charms']]
        if not missing and parsed['found'] != sum(charm['counted'] for charm in parsed['charms'].values()):
            missing.append('Charms found')
        return missing
    if kind == 'buffs':
        buffs = synced_stats('', text)['buffs']
        return [label for field, label in BUFF_PARTS if field not in buffs]
    profile = synced_stats(text, '')['profile']
    missing = [label for field, label in PROFILE_PARTS if profile.get(field) is None]
    if any(re.match(r'Pet:', _clean(line), re.I) for line in text.splitlines()) and (
            not profile.get('pet') or profile.get('pet_level') is None) and profile.get('pet') != 'None':
        missing.append('Pet')
    return missing


def carry_forward(profile: dict, earlier: dict | None) -> dict:
    """A fresh read, with what it could not see kept from the earlier projected profile. XP carries only while
    the level (and prestige) still match; a pet's name only while its level does."""
    if not earlier:
        return profile
    same_level = (profile.get('level') is not None and profile['level'] == earlier.get('level')
                  and profile.get('prestige') in (None, earlier.get('prestige')))
    same_pet = profile.get('pet_level') in (None, earlier.get('pet_level'))
    result = dict(profile)
    for field in CARRIED:
        if result.get(field) is not None or earlier.get(field) is None:
            continue
        if field.startswith('xp') and not same_level or field == 'pet' and not same_pet:
            continue
        result[field] = earlier[field]
    return result


def level_after(level: int, xp_current: int, gained: int, increments: list[int]) -> tuple[int, int, int | None]:
    """(level, XP into it, XP it requires) after gaining XP. Remaining XP carries into the next level."""
    xp = xp_current + gained
    required = xp_required(level, increments)
    while required is not None and xp >= required:
        xp -= required
        level += 1
        required = xp_required(level, increments)
    return level, xp, required


def _multiplier(buffs: dict, name: str) -> float | None:
    value = buffs.get(name)
    return value['value'] if isinstance(value, dict) and value.get('unit') == 'multiplier' else None


def project(synced: dict, tallies: list[dict], increments: list[int] | None, fish_reference: dict) -> dict:
    """The synced stats after these trips (each a parse_fishing tally), with this run's changes."""
    profile = deepcopy(synced['profile'])
    inventory = deepcopy(synced['inventory'])
    inventory.setdefault('fish', {})
    changes = {'fish': {}, 'other': {}}
    sell = _multiplier(synced['buffs'], 'sell_price')
    xp_gained = money = value_added = 0
    sources = {'chests': {'money': 0, 'xp': 0, 'other': {}}, 'quests': {'money': 0, 'xp': 0, 'other': {}},
               'level_ups': {'money': 0}}
    for tally in tallies:
        sources['chests']['money'] += tally['chest_money']
        sources['chests']['xp'] += tally['chest_xp']
        for name, count in tally['chest_contents'].items():
            sources['chests']['other'][name] = sources['chests']['other'].get(name, 0) + count
        for quest in tally['quests_completed']:
            sources['quests']['money'] += quest['money']
            sources['quests']['xp'] += quest['xp']
            for name, count in quest['items'].items():
                sources['quests']['other'][name] = sources['quests']['other'].get(name, 0) + count
        sources['level_ups']['money'] += tally['level_up_money']
        trip_xp = tally['fishing_xp'] + sum(quest['xp'] for quest in tally['quests_completed'])
        xp_gained += trip_xp
        money += (tally['chest_money'] + tally['level_up_money']
                  + sum(quest['money'] for quest in tally['quests_completed']))
        if profile.get('level') is not None and profile.get('xp_current') is not None and increments:
            level, xp, required = level_after(profile['level'], profile['xp_current'], trip_xp, increments)
            if tally['level_ups'] and max(tally['level_ups']) != level:
                # The game said which level it reached; trust it over the table.
                level, xp, required = max(tally['level_ups']), 0, xp_required(max(tally['level_ups']), increments)
            profile.update(level=level, xp_current=xp, xp_required=required,
                           xp_to_next_level=None if required is None else required - xp)
        for name, count in tally['catches'].items():
            row = fish_reference.get(name.casefold())
            name = row['name'] if row else name
            inventory['fish'][name] = inventory['fish'].get(name, 0) + count
            changes['fish'][name] = changes['fish'].get(name, 0) + count
            if row and sell is not None:
                value_added += count * row['base_sell'] * sell
        for name, count in tally['chest_contents'].items():
            category = next((key for key, items in inventory.items()
                             if any(known.casefold() == name.casefold() for known in items)),
                            'exotic_fish' if name in EXOTIC_FISH else 'special')
            items = inventory.setdefault(category, {})
            known = next((key for key in items if key.casefold() == name.casefold()), name)
            items[known] = items.get(known, 0) + count
            changes['other'][known] = changes['other'].get(known, 0) + count
    if profile.get('balance') is not None:
        profile['balance'] += money
    if profile.get('fish_value') is not None and sell is not None:
        profile['fish_value'] += round(value_added)
    return {'profile': profile, 'inventory': inventory, 'buffs': synced['buffs'], 'changes': changes,
            'gained': {'xp': xp_gained, 'money': money, 'fish_value': round(value_added),
                       'trips': len(tallies), 'catches': sum(changes['fish'].values()), 'sources': sources}}


CONTAINER = re.compile(r'\b(chest|crate|box)\b', re.I)


def summarize_finds(tallies, earlier: dict | None = None) -> dict:
    """Chests by type, special fish from chests, charms found and the latest charm levels, added onto `earlier`."""
    finds = deepcopy(earlier) if earlier else {'chests': {}, 'special_fish': {}, 'charms': {}, 'other': {},
                                                'charm_levels': {}}
    for tally in tallies:
        for name, count in tally.get('finds', {}).items():
            name = known_name(name)
            group = ('charms' if name.casefold().endswith('charm') else
                     'chests' if CONTAINER.search(name) else 'other')
            finds[group][name] = finds[group].get(name, 0) + count
        for name, count in tally.get('chest_contents', {}).items():
            name = known_name(name)
            group = 'special_fish' if name.casefold().endswith('fish') else 'other'
            finds[group][name] = finds[group].get(name, 0) + count
        charms = [known_name(name) for name in tally.get('finds', {}) if known_name(name).casefold().endswith('charm')]
        for name, level in tally.get('charm_levels', {}).items():
            if name.casefold() == 'charm' and len(charms) == 1:
                name = charms[0]  # saved before the parser named it: the charm found that trip
            # Levels only rise; the highest the game reported stands.
            finds['charm_levels'][name] = max(level, finds['charm_levels'].get(name, 0))
    return finds


# Charms: the first one found reaches level 1, and each level after needs as many more as its number (level 2 needs
# 2 more, level 3 needs 3). Eight charms, each to level 14, is the 840 needed to prestige.
CHARM_TYPES, CHARM_MAX_LEVEL = 8, 14


def charms_for_level(level: int) -> int:
    """Charms of one kind needed to reach this level from none."""
    return level * (level + 1) // 2


CHARM_GOAL = CHARM_TYPES * charms_for_level(CHARM_MAX_LEVEL)


def charm_progress(found: int, reported_level: int | None = None) -> dict:
    """One charm's level and progress from how many were found here. A higher level reported by the game (charms
    found before tracking began) raises it; the count is then at least what that level needs."""
    counted = min(found, charms_for_level(CHARM_MAX_LEVEL))
    level = 0
    while level < CHARM_MAX_LEVEL and charms_for_level(level + 1) <= counted:
        level += 1
    at_least = reported_level is not None and reported_level > level
    if at_least:
        level = min(reported_level, CHARM_MAX_LEVEL)
        counted = charms_for_level(level)
    return {'found': found, 'counted': counted, 'level': level, 'into': counted - charms_for_level(level),
            'needed': None if level >= CHARM_MAX_LEVEL else level + 1, 'at_least': at_least}


CHARM_LINE = re.compile(r'(\d+)\s*/\s*14\s*-.*?\b([A-Za-z]+)\s+Charm\b.*?(?:\((\d+)\s+to next tier\))?\W*$', re.I)
CHARMS_FOUND = re.compile(r'([\d,]+)\s*/\s*([\d,]+)\s*-\s*Charms found', re.I)


def parse_charms(text: str) -> dict:
    """A /charms reply: each charm's level and charms counted ("2/14 - Quality Charm - ... (2 to next tier)" is
    level 2, with 2 of the 3 for level 3 still to find: 1 + 2 + 1 = 4), and the "11/840 - Charms found" total.
    A line whose level or charms to next tier cannot be read is left out."""
    charms, found, goal = {}, None, None
    for line in (_clean(line) for line in (text or '').splitlines()):
        if total := CHARMS_FOUND.search(line):
            found, goal = int(total[1].replace(',', '')), int(total[2].replace(',', ''))
            continue
        match = CHARM_LINE.search(line)
        if not match:
            continue
        name = known_name(f'{match[2]} Charm')
        level = int(match[1])
        if name not in CHARM_NAMES or level > CHARM_MAX_LEVEL:
            continue
        if level == CHARM_MAX_LEVEL:
            counted = charms_for_level(level)
        elif match[3] is not None and 1 <= int(match[3]) <= level + 1:
            counted = charms_for_level(level) + level + 1 - int(match[3])
        else:
            continue
        charms[name] = {'level': level, 'counted': counted}
    return {'charms': charms, 'found': found, 'goal': goal}


def charm_summary(finds: dict, synced: dict | None = None, since: dict | None = None) -> dict:
    """Every charm's progress and the total toward the 840 goal. With a /charms reply (`synced`), its counts plus
    the charms found since (`since`); without one, or for a line it did not show, the charms logged in `finds`."""
    levels = finds.get('charm_levels', {})
    read = (synced or {}).get('charms', {})
    counts = {name: finds.get('charms', {}).get(name, 0) for name in finds.get('charms', {})}
    counts.update({name: charm['counted'] + (since or {}).get(name, 0) for name, charm in read.items()})
    names = set(counts) | {name for name in levels if name.casefold() != 'charm'}
    charms = {name: charm_progress(counts.get(name, 0), levels.get(name)) for name in names}
    return {'charms': charms, 'total': sum(charm['counted'] for charm in charms.values()), 'goal': CHARM_GOAL,
            'types': sum(charm['counted'] > 0 for charm in charms.values()), 'type_count': CHARM_TYPES,
            'from_reply': bool(read)}


def add_unlisted_finds(inventory: dict, found: dict, listed: dict | None = None) -> dict:
    """The inventory with fish found in chests that the profile does not list (`listed`, by default the
    inventory itself) counted from the finds instead. Below level 50 the profile leaves out Lava and Diamond Fish,
    which super crates give to prestiged players."""
    listed = inventory if listed is None else listed
    names = {name.casefold() for items in listed.values() for name in (items or {})}
    result = {key: dict(items or {}) for key, items in inventory.items()}
    for name, count in found.items():
        if name.casefold() not in names:
            result.setdefault('exotic_fish', {})[name] = count
    return result
