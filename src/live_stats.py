"""Live player stats: the last synced /profile and /buffs, carried forward trip by trip.

Pure functions, no I/O. A sync reads whatever the replies show (OCR-garbled fields stay unknown). Each trip then
adds its XP (with level-ups from the wiki level table), catches, chest finds and money, and the fish value its
catches add at wiki sell price times the sell multiplier. The next sync replaces the projection with fresh values.
"""
from copy import deepcopy
import re

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
                             if any(known.casefold() == name.casefold() for known in items)), 'special')
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
            group = ('charms' if name.casefold().endswith('charm') else
                     'chests' if CONTAINER.search(name) else 'other')
            finds[group][name] = finds[group].get(name, 0) + count
        for name, count in tally.get('chest_contents', {}).items():
            group = 'special_fish' if name.casefold().endswith('fish') else 'other'
            finds[group][name] = finds[group].get(name, 0) + count
        finds['charm_levels'].update(tally.get('charm_levels', {}))
    return finds
