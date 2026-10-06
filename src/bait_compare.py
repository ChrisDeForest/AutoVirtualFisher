"""Record which bait each fishing trip used and compare baits across saved sessions."""
from copy import deepcopy
import json
from pathlib import Path
from statistics import median

from .catch_check import check_batch

BAIT_REFERENCE_PATH = Path(__file__).parent / 'reference' / 'bait.json'
NOT_RECORDED = ''
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


def _canonical(name, bait_reference):
    if name is None:
        return 'No bait'
    for known in bait_reference or {}:
        if known.casefold() == name.casefold():
            return known
    return name


def compare_baits(sessions: list[dict], fish_reference: dict | None, bait_reference: dict | None) -> dict:
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
        rows.append({'bait': bait, 'trips': trips, 'boosted_trips': group['boosted'],
                     'fish_per_trip': group['fish'] / trips,
                     'xp_ratio': median(group['ratios']) if group['ratios'] else None,
                     'base_value_per_trip': group['value'] / trips if fish_reference is not None else None,
                     'finds_per_100_trips': group['finds'] * 100 / trips,
                     'wiki': (bait_reference or {}).get(bait), 'few_trips': trips < FEW_TRIPS})
    return {'rows': rows, 'not_recorded_trips': not_recorded}
