"""Live player stats: the last synced /profile and /buffs, carried forward trip by trip.

Pure functions, no I/O. A sync reads whatever the replies show (OCR-garbled fields stay unknown). Each trip then
adds its XP (with level-ups from the wiki level table), catches, chest finds and money, and the fish value its
catches add at wiki sell price times the sell multiplier. The next sync replaces the projection with fresh values.
"""
from copy import deepcopy

from .player_planner import xp_required
from .player_state import parse_responses


def synced_stats(profile_text: str, buffs_text: str) -> dict:
    """What a /profile and /buffs reply show, best effort: unreadable fields are None or absent."""
    profile = parse_responses(profile=profile_text or '')
    buffs = parse_responses(buffs=buffs_text or '')['buffs'] if buffs_text else {}
    return {'profile': dict(profile['profile']),
            'inventory': {category: dict(items or {}) for category, items in profile['inventory'].items()},
            'buffs': {name: value for name, value in buffs.items() if value is not None}}


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
    for tally in tallies:
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
                       'trips': len(tallies), 'catches': sum(changes['fish'].values())}}
