"""Record which bait each fishing trip used and compare baits across saved sessions."""
from copy import deepcopy
import json
from pathlib import Path
from statistics import median

from .catch_check import check_batch

BAIT_REFERENCE_PATH = Path(__file__).parent / 'reference' / 'bait.json'
NOT_RECORDED = ''
# The bait shop says "Bait is consumed PER CAST": one bait per fishing trip.
BAIT_PER_TRIP = 1
# Below this many trips, per-trip averages swing too much to compare baits fairly.
FEW_TRIPS = 20


def load_bait_reference(path: Path = BAIT_REFERENCE_PATH) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))['baits']
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def assign_bait(tally: dict, bait: str | None) -> dict:
    """Return a copy of the tally with each trip's bait; bait ends after the trip where it ran out."""
    baited = deepcopy(tally)
    if bait == NOT_RECORDED:
        return baited
    baited['bait'] = bait
    current = bait
    for trip in baited.get('trip_details', []):
        trip['bait'] = current
        if current and any(event.startswith('Ran out of ') for event in trip.get('events', [])):
            current = None
    return baited


def bait_cost_per_trip(bait: str | None, bait_reference: dict | None) -> float | None:
    """Wiki price × one bait per cast; 0 with no bait; None when the price is not known (never assumed zero)."""
    if bait is None:
        return 0
    for known, row in (bait_reference or {}).items():
        if known.casefold() == bait.casefold() and isinstance(row.get('cost'), (int, float)):
            return row['cost'] * BAIT_PER_TRIP
    return None


def _canonical(name, bait_reference):
    if name is None:
        return 'No bait'
    for known in bait_reference or {}:
        if known.casefold() == name.casefold():
            return known
    return name


def compare_baits(sessions: list[dict], fish_reference: dict | None, bait_reference: dict | None,
                  sell: float | None = None) -> dict:
    """Per-bait results. Gross value is the catch at wiki base prices × `sell`; net subtracts the bait's cost
    and is None whenever the sell price or bait cost is unknown. Sessions mix boosts, pets, and biomes, so these
    describe history, not a recommendation."""
    groups, not_recorded = {}, 0
    for session in sessions:
        details = session['tally'].get('trip_details', [])
        checked = check_batch(details, fish_reference) if fish_reference is not None else None
        for index, trip in enumerate(details):
            if 'bait' not in trip:
                not_recorded += 1
                continue
            group = groups.setdefault(_canonical(trip['bait'], bait_reference),
                                      {'trips': 0, 'boosted': 0, 'fish': 0, 'ratios': [], 'value': 0, 'finds': 0})
            group['trips'] += 1
            group['boosted'] += bool(trip.get('boosted'))
            group['fish'] += sum(trip['catches'].values())
            group['finds'] += sum(event.startswith('Found ') for event in trip.get('events', []))
            if checked:
                entry = checked['trips'][index]
                group['value'] += entry['base_value']
                if entry['ratio']:
                    group['ratios'].append(entry['ratio'])
    rows = []
    for bait, group in sorted(groups.items(), key=lambda item: -item[1]['trips']):
        trips = group['trips']
        base = group['value'] / trips if fish_reference is not None else None
        gross = base * sell if base is not None and sell else None
        cost = bait_cost_per_trip(None if bait == 'No bait' else bait, bait_reference)
        rows.append({'bait': bait, 'trips': trips, 'boosted_trips': group['boosted'],
                     'gross_value_per_trip': gross, 'bait_cost_per_trip': cost,
                     'net_value_per_trip': gross - cost if gross is not None and cost is not None else None,
                     'fish_per_trip': group['fish'] / trips,
                     'xp_ratio': median(group['ratios']) if group['ratios'] else None,
                     'base_value_per_trip': group['value'] / trips if fish_reference is not None else None,
                     'finds_per_100_trips': group['finds'] * 100 / trips,
                     'wiki': (bait_reference or {}).get(bait), 'few_trips': trips < FEW_TRIPS})
    return {'rows': rows, 'not_recorded_trips': not_recorded}
