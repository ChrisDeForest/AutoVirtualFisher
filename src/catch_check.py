"""Compare fishing trips with the wiki's fish table: XP ratio, biome fit, and base sell value."""
import json
from pathlib import Path

FISH_REFERENCE_PATH = Path(__file__).parent / 'reference' / 'fish.json'
BIOMES = ('River', 'Volcanic', 'Ocean', 'Sky', 'Space', 'Alien')
# Ratios wobble because the game rounds XP; beyond that rounding, a change this large is a real shift.
JUMP_ALLOWANCE = 0.02


def load_fish_reference(path: Path = FISH_REFERENCE_PATH) -> dict | None:
    try:
        fish = json.loads(Path(path).read_text(encoding='utf-8'))['fish']
        return {name.casefold(): {'name': name, 'base_xp': row['Base XP'], 'base_sell': row['Base Sell Price'],
                                  'biomes': {biome for biome in BIOMES if row.get(biome)}}
                for name, row in fish.items()}
    except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return None


def check_batch(trip_details: list[dict], reference: dict | None, biome: str | None = None) -> dict | None:
    """Return per-trip XP ratios against wiki base XP, with flags; None without a reference table."""
    if reference is None:
        return None
    trips, previous = [], None
    for number, trip in enumerate(trip_details, 1):
        base_xp = base_value = 0
        unknown, off_biome = [], []
        for name, count in trip['catches'].items():
            row = reference.get(name.casefold())
            if row is None:
                unknown.append(name)
                continue
            base_xp += row['base_xp'] * count
            base_value += row['base_sell'] * count
            if biome and biome not in row['biomes']:
                off_biome.append(name)
        # The trip's XP line includes chest XP, which does not scale with the catch.
        xp = trip['xp'] - trip.get('chest_xp', 0) if trip.get('xp') is not None else None
        ratio = xp / base_xp if xp and base_xp and not unknown else None
        jump = bool(ratio and previous and abs(ratio - previous['ratio']) >
                    1 / base_xp + 1 / previous['base_xp'] + JUMP_ALLOWANCE)
        entry = {'number': number, 'base_xp': base_xp, 'xp': xp, 'ratio': ratio, 'jump': jump,
                 'previous_ratio': previous['ratio'] if jump else None, 'unknown': unknown,
                 'off_biome': off_biome, 'base_value': base_value, 'boosted': trip.get('boosted', False),
                 'events': trip.get('events', [])}
        trips.append(entry)
        if ratio:
            previous = entry
    return {'trips': trips, 'base_value': sum(trip['base_value'] for trip in trips), 'biome': biome}
