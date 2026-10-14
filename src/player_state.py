"""Parse pasted bot responses without connecting to Discord.

Includes formats transcribed from October 5, 2026 /profile and /buffs screenshots.
Unknown lines are retained; numbers never default to zero.
"""
import re
import json
from functools import cache
from pathlib import Path

PROFILE_FIELDS = ('account_name', 'balance', 'level', 'prestige', 'xp_current', 'xp_required',
                  'xp_to_next_level', 'rod', 'biome', 'bait', 'bait_quantity', 'pet', 'fish_value')
BUFF_FIELDS = ('sell_price', 'fish_catch', 'fish_quality', 'treasure_chance',
               'treasure_quality', 'xp_multiplier', 'fishing_cooldown')
PRESTIGE_UPGRADES = ('International Ties', 'Business Education', 'Fish Whisperer',
                     'Ancient One', 'Virtual Fisher')
ALIASES = {'current biome': 'biome', 'current bait': 'bait', 'current pet': 'pet',
           'xp to next level': 'xp_to_next_level', 'xp': 'xp_multiplier',
           'cooldown': 'fishing_cooldown'}
NUMBER = r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
PET_REFERENCE_PATH = Path(__file__).parent / 'reference' / 'pet.json'
PET_STAT_NAMES = {
    'fish_catch_pct': 'Fish catch',
    'fish_quality_pct': 'Fish quality',
    'treasure_quality_pct': 'Treasure quality',
    'treasure_chance_pct': 'Treasure chance',
    'xp_pct': 'XP',
}


def _number(text):
    if not re.fullmatch(NUMBER, text):
        raise ValueError('Expected a number')
    value = float(text.replace(',', ''))
    return int(value) if value.is_integer() else value


def _clean(line):
    line = re.sub(r'<a?:\w+:\d+>', '', line)
    line = re.sub(r':[\w-]+:', '', line)
    return line.replace('**', '').replace('__', '').replace('`', '').strip().lstrip('> ').strip()


def _name(value):
    return re.sub(r'^[^\w]+', '', value).strip().removesuffix('.')


# Longest first: a bait is matched by its ending, and "Artifact Magnet" ends in "Magnet".
BAIT_NAMES = ('Artifact Magnet', 'Support Bait', 'Magic Bait', 'Wise Bait', 'Leeches', 'Magnet', 'Worms', 'Fish')


def known_bait(name):
    """The bait a read name stands for, its icon misread as up to two stray characters ("r' Fish" is Fish);
    a name that is no known bait is returned unchanged."""
    for bait in BAIT_NAMES:
        if name.casefold().endswith(bait.casefold()):
            prefix = name[:len(name) - len(bait)].strip()
            if len(prefix) <= 2 and not re.search(r'[a-z]{2}', prefix, re.I):
                return bait
    return name


# A misread name counts as the known name it is closest to when it is this close (difflib ratio) and closer to it
# than to any other by the margin. Live: "Wcanic" for Volcanic is 0.71; names that are no misread are 0.5 or less.
CLOSE, MARGIN = 0.7, 0.15


def _known(name, names):
    """The known name a read name stands for: its icon misread as up to two stray characters before it, a stray
    mark after it, or a close misread of it. Otherwise the name as read."""
    from difflib import SequenceMatcher
    trimmed = re.sub(r'[\s\W_]+$', '', name)
    for known in sorted(names, key=len, reverse=True):
        if trimmed.casefold().endswith(known.casefold()):
            prefix = trimmed[:len(trimmed) - len(known)].strip()
            if len(prefix) <= 2 and not re.search(r'[a-z]{2}', prefix, re.I):
                return known
    word = re.sub(r'^\W*\S{1,2}\s+(?=\w{3})|^\W+', '', trimmed)
    ratios = sorted(((SequenceMatcher(None, word.casefold(), known.casefold()).ratio(), known) for known in names),
                    reverse=True)
    if ratios and ratios[0][0] >= CLOSE and (len(ratios) == 1 or ratios[0][0] - ratios[1][0] >= MARGIN):
        return ratios[0][1]
    return name


def known_biome(name):
    """The biome a read name stands for ("i Volcanic" and "Wcanic" are Volcanic), else the name as read."""
    from .catch_check import BIOMES
    return _known(name, BIOMES)


def known_rod(name):
    """The rod a read name stands for, from every rod in game_model.json: its icon misread before it or a mark
    after it ("J Supporter Rod", "Z golden."), " Rod" lost, or a close misread. A name close to two rods, or to
    none, is returned as read."""
    rods = _rod_names()
    found = _known(name, rods)
    if found in rods:
        return found
    # Matched on the name without " Rod" too ("golden" is Golden Rod), never when two rods share that word.
    stems = {}
    for rod in rods:
        stems.setdefault(rod.removesuffix(' Rod'), []).append(rod)
    stem = _known(name, [word for word, owners in stems.items() if len(owners) == 1])
    return stems[stem][0] if stem in stems and len(stems[stem]) == 1 else name


@cache
def _rod_names():
    """Read once: every /profile read names the rod."""
    try:
        from .game_model_data import load_game_data
        return list(load_game_data()['rods'])
    except Exception:  # without the game data a rod is kept as read
        return []


def known_pet(name):
    """The pet a read name stands for ("V Puffer", "Puffer _" and "Pufter" are Puffer), else the name as read."""
    return _known(name, list((_pet_reference() or {}).get('pets', {})))


def _pet_reference():
    try:
        return json.loads(PET_REFERENCE_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def _pet_bonuses(pet_name, level):
    reference = _pet_reference()
    if reference is None:
        return None
    pet = reference.get('pets', {}).get(pet_name)
    if pet is None:
        return None
    bonuses = {}
    for field, label in PET_STAT_NAMES.items():
        maximum = pet.get(field, 0)
        if not maximum:
            continue
        formula = reference.get('pet_buffs', {}).get(f'+{maximum:g}%')
        match = re.fullmatch(r'([\d.]+)\+([\d.]+)x', formula or '')
        if match is None:
            continue
        value = round(float(match[1]) + float(match[2]) * level, 2)
        bonuses[label] = {'value': value, 'unit': 'percent'}
    return bonuses


def _parse_quests(text, result):
    quests = result['quests']
    group, quest = 'daily', None
    for original in text.splitlines():
        line = _clean(original).rstrip('.').rstrip()
        if not line or line == 'Quest List' or line.startswith('Quests have multiple tiers'):
            continue
        if line.rstrip(':').upper() == 'SPECIAL DAILY QUEST':
            group, quest = 'special', None
            continue
        reset = re.fullmatch(r'Quests reset in\s*(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?', line, re.I)
        if reset and any(reset.groups()):
            days, hours, minutes = (int(part or 0) for part in reset.groups())
            quests['resets_in_minutes'] = (days * 24 + hours) * 60 + minutes
            quest = None
            continue
        header = re.fullmatch(rf'(.+?)\s+-\s+({NUMBER})/({NUMBER})', line)
        if header and _name(header[1]):
            quests[group] = quests[group] or {}
            quest = {'progress': _number(header[2]), 'goal': _number(header[3]), 'task': None}
            quests[group][_name(header[1])] = quest
            continue
        if quest is not None and quest['task'] is None:
            quest['task'] = line
            continue
        result['unparsed']['quests'].append(original)
        result['warnings'].append(f'quests: Unrecognized line: {line}')
    if text.strip() and quests['daily'] is None and quests['special'] is None:
        result['warnings'].append('quests: No quests were found.')


def quest_summary(quests):
    """Return 'Name progress/goal, ...' for a quest group, or None when the group is unknown."""
    if quests is None:
        return None
    return ', '.join(f'{name} {display_value(quest["progress"])}/{display_value(quest["goal"])}'
                     for name, quest in quests.items())


def parse_responses(profile='', buffs='', prestige_shop='', pet='', quests=''):
    """Return a serializable snapshot. No file writes or network requests."""
    result = {'schema_version': 1,
              'profile': dict.fromkeys(PROFILE_FIELDS),
              'buffs': dict.fromkeys(BUFF_FIELDS),
              'inventory': dict.fromkeys(('fish', 'exotic_fish', 'special')),
              'prestige': {'azure_fish': None,
                           'upgrades': dict.fromkeys(PRESTIGE_UPGRADES),
                           'upgrades_bought': None,
                           'azure_fish_earned_at_least': None,
                           'inferred_prestige_minimum': None,
                           'source': 'unknown'},
              'pets': {'xp_per_fishing_trip_average': None, 'status': None,
                       'current_pet': None, 'current_pet_bonuses': None, 'owned': None},
              'quests': {'daily': None, 'special': None, 'resets_in_minutes': None},
              'warnings': [], 'unparsed': {'profile': [], 'buffs': [], 'prestige_shop': [], 'pet': [],
                                           'quests': []}}
    seen = {}
    conflicts = set()

    def assign(section, field, value):
        key = (section, field)
        if key in conflicts:
            return
        if key in seen and seen[key] != value:
            result[section][field] = None
            conflicts.add(key)
            result['warnings'].append(f'Conflicting {section} values for {field}; left unknown.')
        else:
            seen[key] = value
            result[section][field] = value

    for section, raw in [('profile', profile), ('buffs', buffs)]:
        inventory_section = None
        for original in raw.splitlines():
            line = _clean(original).rstrip('.').rstrip()
            if not line:
                continue
            if section == 'profile':
                if line.startswith('Inventory of '):
                    account_name = line.removeprefix('Inventory of ').strip()
                    if account_name:
                        assign(section, 'account_name', account_name)
                    continue
                headings = {'Fish Inventory': 'fish', 'Exotic Fish': 'exotic_fish', 'Special': 'special'}
                if line in headings:
                    inventory_section = headings[line]
                    result['inventory'][inventory_section] = {}
                    continue
                progress = re.fullmatch(rf'(?:P(\d+)\s+)?Level (\d+),\s*({NUMBER})/({NUMBER}) XP to next level', line, re.I)
                if progress:
                    current, required = _number(progress[3]), _number(progress[4])
                    if 0 <= current <= required and isinstance(current, int) and isinstance(required, int):
                        if progress[1] is not None:
                            assign(section, 'prestige', int(progress[1]))
                        assign(section, 'level', int(progress[2]))
                        assign(section, 'xp_current', current)
                        assign(section, 'xp_required', required)
                        assign(section, 'xp_to_next_level', required - current)
                        continue
                if line.startswith('Currently using '):
                    line = 'Rod: ' + line.removeprefix('Currently using ')
                item = re.fullmatch(r'(\d+(?:,\d{3})*)\s+(.+)', line)
                if inventory_section and item:
                    name = _name(item[2])
                    items = result['inventory'][inventory_section]
                    if name and name not in items:
                        items[name] = _number(item[1])
                        continue
                    result['warnings'].append(f'profile: Duplicate or empty inventory item: {line}')
                    result['unparsed']['profile'].append(original)
                    continue
            elif re.fullmatch(r".+['’]s current multipliers \(including bait\):", line, re.I):
                continue
            level = re.fullmatch(rf'(?:P(\d+)\s+)?L(\d+),?\s+({NUMBER})\s+XP to next level', line, re.I)
            if section == 'profile' and level:
                if level[1] is not None:
                    assign(section, 'prestige', int(level[1]))
                assign(section, 'level', int(level[2]))
                assign(section, 'xp_to_next_level', _number(level[3]))
                continue
            if section == 'buffs' and ':' not in line:
                # Observed OCR: "XP Multiplier. 1.251", the colon read as a full stop.
                misread = re.fullmatch(r'([A-Za-z ]+?)\s*[.;,]\s*(\d.*)', line)
                if misread:
                    line = f'{misread[1]}: {misread[2]}'
            label, separator, value = line.partition(':')
            field = ALIASES.get(label.strip().lower(), label.strip().lower().replace(' ', '_'))
            try:
                if not separator or field not in result[section]:
                    raise ValueError('Unrecognized line')
                value = value.strip()
                if section == 'profile':
                    if field in ('balance', 'level', 'prestige', 'xp_current', 'xp_required',
                                 'xp_to_next_level', 'bait_quantity', 'fish_value'):
                        value = _number(value.removeprefix('$') if field in ('balance', 'fish_value') else value)
                        if value < 0 or (field != 'balance' and not isinstance(value, int)):
                            raise ValueError('Expected a nonnegative whole number')
                    elif not value:
                        raise ValueError('Missing value')
                    else:
                        value = _name(value)
                        if not value:
                            raise ValueError('Missing equipment name')
                        if field == 'pet' and re.fullmatch(r'Level \d+\)?', value, re.I):
                            # Observed OCR: "Pet: (Level 51)", the emoji and name lost. The pet stays unknown.
                            continue
                        if field == 'rod':
                            # Live: "J Supporter Rod", "Z golden." (the icon as a letter, " Rod" lost).
                            value = known_rod(value)
                        if field == 'biome':
                            value = known_biome(value)
                        if field == 'pet':
                            named = re.fullmatch(r'(.+?)(\s*\(Level \d+\)?)', value, re.I)
                            value = known_pet(named[1]) + named[2] if named else known_pet(value)
                        if field == 'bait':
                            stock = re.fullmatch(r'(.+?)\s+\((\d+(?:,\d{3})*)\)', value)
                            if stock:
                                value = stock[1]
                                assign(section, 'bait_quantity', _number(stock[2]))
                            value = known_bait(value)
                else:
                    match = re.fullmatch(rf'({NUMBER})\s*(%|x|s|seconds?)', value, re.I)
                    prefix = re.fullmatch(rf'x\s*({NUMBER})', value, re.I)
                    # The game shows multipliers with two decimals and an x; observed OCR read "1.25x" as "1.251".
                    misread_x = re.fullmatch(r'(\d+\.\d{2})[1lI|]', value)
                    if prefix:
                        amount, unit = _number(prefix[1]), 'multiplier'
                    elif match:
                        amount = _number(match[1])
                        unit = 'percent' if match[2] == '%' else ('multiplier' if match[2].lower() == 'x' else 'seconds')
                    elif misread_x and field != 'fishing_cooldown':
                        amount, unit = _number(misread_x[1]), 'multiplier'
                    else:
                        raise ValueError('Expected a percentage, multiplier, or seconds')
                    if (field == 'fishing_cooldown') != (unit == 'seconds'):
                        raise ValueError('Unexpected unit for this field')
                    if unit in ('seconds', 'multiplier') and amount < 0:
                        raise ValueError('Expected a nonnegative value')
                    value = {'value': amount, 'unit': unit}
                assign(section, field, value)
            except ValueError as error:
                result['unparsed'][section].append(original)
                result['warnings'].append(f'{section}: {error}: {line}')
    upgrades = result['prestige']['upgrades']
    for original in prestige_shop.splitlines():
        line = _clean(original).rstrip('.').rstrip()
        if not line or line == 'Prestige Shop' or line.startswith('Spend your Azure Fish'):
            continue
        # Observed: OCR reads the bold count "0" as the letter O.
        balance = re.fullmatch(rf'You have:\s*({NUMBER}|O)(?:\s+Azure Fish)?\s*', line, re.I)
        if balance:
            result['prestige']['azure_fish'] = 0 if balance[1].upper() == 'O' else _number(balance[1])
            continue
        matched = False
        for upgrade in PRESTIGE_UPGRADES:
            if line.startswith(upgrade + ' -') or re.search(rf':\s*{re.escape(upgrade)}\s*-', line):
                upgrades[upgrade] = 'MAXED' in line.upper()
                matched = True
                break
        if not matched:
            result['unparsed']['prestige_shop'].append(original)
    if prestige_shop.strip():
        if result['prestige']['azure_fish'] is None:
            result['warnings'].append('prestige shop: Azure Fish balance was not found.')
        missing_upgrades = [name for name, status in upgrades.items() if status is None]
        if missing_upgrades:
            result['warnings'].append('prestige shop: Upgrade status unknown for ' + ', '.join(missing_upgrades) + '.')
        if result['prestige']['azure_fish'] is not None:
            observed_bought = sum(status is True for status in upgrades.values())
            earned = result['prestige']['azure_fish'] + observed_bought
            result['prestige']['azure_fish_earned_at_least'] = earned
            result['prestige']['inferred_prestige_minimum'] = earned
            if not missing_upgrades:
                result['prestige']['upgrades_bought'] = observed_bought
    current_owned_pet = None
    for original in pet.splitlines():
        line = _clean(original).rstrip('.').rstrip()
        if not line or line.startswith('Pets are permanent') or line.startswith('Pets are slightly more common'):
            continue
        average_xp = re.fullmatch(rf'\+?({NUMBER})\s+pet XP per fishing trip \(average\)', line, re.I)
        if average_xp:
            value = _number(average_xp[1])
            if isinstance(value, int) and value >= 0:
                result['pets']['xp_per_fishing_trip_average'] = value
                continue
        current = re.fullmatch(r'Current pet:\s*(.+)', line, re.I)
        if current:
            result['pets']['current_pet'] = known_pet(_name(current[1]))
            continue
        if re.fullmatch(r'You have no pets!.*', line, re.I):
            result['pets']['status'] = 'No pets'
            continue
        owned = re.fullmatch(r'(.+?)\s*\(Lvl\s*(\d+)\)', line, re.I)
        if owned:
            name = _name(owned[1])
            if name:
                result['pets']['owned'] = result['pets']['owned'] or {}
                result['pets']['owned'][name] = {'level': int(owned[2]), 'description': None,
                                                  'xp_current': None, 'xp_required': None,
                                                  'xp_to_next_level': None}
                current_owned_pet = name
                result['pets']['status'] = 'Pet collection imported'
                continue
        progress = re.fullmatch(rf'({NUMBER})/({NUMBER}) XP to next level', line, re.I)
        if progress and current_owned_pet:
            current, required = _number(progress[1]), _number(progress[2])
            if isinstance(current, int) and isinstance(required, int) and 0 <= current <= required:
                owned_pet = result['pets']['owned'][current_owned_pet]
                owned_pet['xp_current'] = current
                owned_pet['xp_required'] = required
                owned_pet['xp_to_next_level'] = required - current
                continue
        if current_owned_pet:
            owned_pet = result['pets']['owned'][current_owned_pet]
            if owned_pet['description'] is None:
                owned_pet['description'] = line
                continue
        result['unparsed']['pet'].append(original)
    if pet.strip() and result['pets']['status'] is None:
        result['warnings'].append('pet: No supported pet status was found.')
    current_pet = result['pets']['current_pet']
    owned_pets = result['pets']['owned']
    if current_pet and owned_pets and current_pet in owned_pets:
        result['pets']['current_pet_bonuses'] = _pet_bonuses(current_pet, owned_pets[current_pet]['level'])
    direct_prestige = result['profile']['prestige']
    if direct_prestige is not None:
        result['prestige']['source'] = 'profile'
    elif result['prestige']['inferred_prestige_minimum'] is not None:
        result['prestige']['source'] = 'inferred minimum'
    _parse_quests(quests, result)
    if not any(text.strip() for text in (profile, buffs, prestige_shop, pet, quests)):
        result['warnings'].append('Paste a profile, buffs, prestige shop, pet, or quests response to begin.')
    return result


def display_value(value):
    if value is None:
        return 'Unknown'
    if isinstance(value, bool):
        return 'Maxed' if value else 'Not bought'
    if isinstance(value, dict) and 'unit' in value:
        suffix = {'percent': '%', 'multiplier': 'x', 'seconds': ' s'}[value['unit']]
        return f"{value['value']:,}{suffix}"
    if isinstance(value, dict):
        return ', '.join(f'{name}: {display_value(count)}' for name, count in value.items()) or 'Empty'
    return f'{value:,}' if isinstance(value, (int, float)) else str(value)
