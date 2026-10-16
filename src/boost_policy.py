"""Part 7: which emerald boosts to keep running, by the guide's rates and the person's order. Pure: no I/O.

Rates (progression-knowledge §5): Fish20m above 6 Emerald Fish an hour, Treasure20m from 18, Auto30m from 36; all
three from the charm grind on (route.json boosts.always_from) and while the Booster quest is open. Gold Fish are
never spent (saved for Statistician).
"""
import re

BOOSTS = ('Fish20m', 'Treasure20m', 'Auto30m')
COST = {'Fish20m': 6, 'Treasure20m': 6, 'Auto30m': 8}
LABEL = {'Fish20m': 'Fish boost', 'Treasure20m': 'Treasure boost', 'Auto30m': 'Worker'}
DEFAULT_ORDER = BOOSTS


def clean_order(order):
    """`order` as a full ordering of BOOSTS: unknown or repeated names dropped, missing ones added at the end."""
    kept = [name for name in dict.fromkeys(order or ()) if name in BOOSTS]
    return tuple(kept + [name for name in BOOSTS if name not in kept])


def seconds_from_note(note):
    """How long a boost runs, from its action note ('boost on for 20m', 'already boosting, 19m 38s left')."""
    parts = re.findall(r'(\d+)\s*([hms])\b', note or '')
    if not parts:
        return None
    return sum(int(n) * {'h': 3600, 'm': 60, 's': 1}[unit] for n, unit in parts)


def wanted(phase, emerald_per_hour, rules, booster_quest_open):
    """The boosts that should be running."""
    if booster_quest_open or phase in (rules.get('always_from') or ()):
        return set(BOOSTS)
    if emerald_per_hour is None:
        return set()
    result = set()
    if emerald_per_hour > rules.get('fish_min_rate', 6):
        result.add('Fish20m')
    if emerald_per_hour >= rules.get('treasure_min_rate', 18):
        result.add('Treasure20m')
    if emerald_per_hour >= rules.get('worker_min_rate', 36):
        result.add('Auto30m')
    return result


def buys(wanted, order, expiries, now, emerald, held=()):
    """The wanted boosts to buy now, in `order`: not running (expiry passed or unknown) and not held, while the
    Emerald Fish left after the earlier ones cover the cost. Nothing when Emerald Fish on hand is unknown."""
    if emerald is None:
        return []
    result, left = [], emerald
    for name in clean_order(order):
        if name not in wanted or name in held or expiries.get(name, float('-inf')) > now:
            continue
        if COST[name] <= left:
            result.append(name)
            left -= COST[name]
    return result
