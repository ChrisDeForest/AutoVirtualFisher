"""Game tables for the game model (src/reference/game_model.json), checked when loaded. Each biome's five fish
tiers come from src/reference/fish.json, cheapest first; a rod's fish-mix base shares are given by fish name, put in
tier order and normalized to sum to 1."""
import json
from pathlib import Path

REFERENCE = Path(__file__).resolve().parent / 'reference'
GAME_DATA_PATH = REFERENCE / 'game_model.json'
FISH_PATH = REFERENCE / 'fish.json'
BIOME_FIELDS = ('index', 'fish_multiplier', 'cooldown_add', 'fish_boost_fish', 'supporter_multiplier', 'lava_drop',
                'diamond_drop')
ROD_FIELDS = ('mean_catch', 'treasure_chance', 'treasure_quality', 'unlock_level', 'price', 'biomes')
CHEST_FIELDS = ('share_per_quality', 'a', 'gold', 'emerald', 'lava', 'diamond', 'charm', 'multiplier', 'event')
TABLES = ('level_sell_multiplier', 'biomes', 'example_shares', 'rods', 'baits', 'pets', 'chest_order', 'chests',
          'hidden_effects')


class GameDataError(Exception):
    """The game data file is missing, unreadable or incomplete."""


def _normalized(shares, where):
    if len(shares) != 5 or sum(shares) <= 0:
        raise GameDataError(f'{where}: five shares with a positive sum are needed.')
    total = sum(shares)
    return [share / total for share in shares]


def _by_tier(shares, tiers, where):
    """A rod's shares in a biome, given by fish name, in the biome's tier order (cheapest first). Matched by name,
    never by position: the wiki's columns are not always in tier order."""
    if not isinstance(shares, dict):
        raise GameDataError(f'{where}: shares must be given by fish name.')
    names = {tier['fish'].casefold(): index for index, tier in enumerate(tiers)}
    unknown = [name for name in shares if name.casefold() not in names]
    if unknown or len(shares) != len(tiers):
        raise GameDataError(f'{where}: the fish {", ".join(unknown) or "given"} do not match the biome\'s fish '
                            f'{", ".join(tier["fish"] for tier in tiers)}.')
    ordered = [0.0] * len(tiers)
    for name, share in shares.items():
        ordered[names[name.casefold()]] = share
    return _normalized(ordered, where)


def load_game_data(path=None, fish_path=None) -> dict:
    try:
        data = json.loads(Path(path or GAME_DATA_PATH).read_text(encoding='utf-8'))
        fish = json.loads(Path(fish_path or FISH_PATH).read_text(encoding='utf-8'))['fish']
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise GameDataError(f'Game data could not be read: {error}') from error
    missing = [table for table in TABLES if table not in data]
    if missing:
        raise GameDataError(f'Game data is missing: {", ".join(missing)}.')
    for name, biome in data['biomes'].items():
        gaps = [key for key in BIOME_FIELDS if key not in biome]
        if gaps:
            raise GameDataError(f'Biome {name} is missing {", ".join(gaps)}.')
        tiers = sorted((row['Base Sell Price'], row['Base XP'], fish_name)
                       for fish_name, row in fish.items() if row.get(name))
        if len(tiers) != 5:
            raise GameDataError(f'Biome {name} has {len(tiers)} fish in fish.json; five are needed.')
        biome['tiers'] = [{'fish': fish_name, 'price': price, 'xp': xp} for price, xp, fish_name in tiers]
    for name, rod in data['rods'].items():
        gaps = [key for key in ROD_FIELDS if key not in rod]
        if gaps:
            raise GameDataError(f'{name} is missing {", ".join(gaps)}.')
        # Shares for a biome the model does not know (Abyss: no fish in fish.json yet) are kept out.
        rod['shares'] = {biome: _by_tier(shares, data['biomes'][biome]['tiers'], f'{name} in {biome}')
                         for biome, shares in rod.get('shares', {}).items() if biome in data['biomes']}
    for name, chest in data['chests'].items():
        gaps = [key for key in CHEST_FIELDS if key not in chest]
        if gaps:
            raise GameDataError(f'The {name} chest is missing {", ".join(gaps)}.')
    if sorted(data['chest_order']) != sorted(data['chests']):
        raise GameDataError('chest_order must name every chest once.')
    data['example_shares'] = _normalized(data['example_shares'], 'example_shares')
    return data
