"""Fish money: wiki base sell prices scaled by the sell multiplier from /buffs."""


def base_value(counts: dict, reference: dict) -> tuple[int, list[str]]:
    """Return (value at wiki base prices, names missing from the wiki table)."""
    total, unknown = 0, []
    for name, count in counts.items():
        row = reference.get(name.casefold())
        if row is None:
            unknown.append(name)
        else:
            total += row['base_sell'] * count
    return total, unknown


def sell_multiplier(state: dict) -> float | None:
    sell = state.get('buffs', {}).get('sell_price')
    if isinstance(sell, dict) and sell.get('unit') == 'multiplier':
        return sell['value']
    return None


def latest_sell_multiplier(history: list[dict]) -> tuple[float, str] | None:
    """Return (multiplier, captured_at) from the newest snapshot that includes /buffs."""
    for snapshot in history:
        multiplier = sell_multiplier(snapshot['state'])
        if multiplier is not None:
            return multiplier, snapshot['captured_at']
    return None


def value_check(history: list[dict], reference: dict) -> dict:
    """Compare each snapshot's Fish Value with base value x sell price from the same snapshot."""
    matches, mismatches = 0, []
    for snapshot in history:
        state = snapshot['state']
        multiplier = sell_multiplier(state)
        fish = state.get('inventory', {}).get('fish')
        observed = state.get('profile', {}).get('fish_value')
        if multiplier is None or not isinstance(fish, dict) or not isinstance(observed, (int, float)):
            continue
        base, unknown = base_value(fish, reference)
        if unknown:
            continue
        # /buffs rounds the multiplier to two decimals, so allow half a hundredth of the base value.
        if abs(observed - base * multiplier) <= base * 0.005 + 1:
            matches += 1
        else:
            mismatches.append({'captured_at': snapshot['captured_at'], 'observed': observed,
                               'expected': round(base * multiplier)})
    return {'matches': matches, 'mismatches': mismatches}
