"""Fishing forward in the plan: XP, levels and their money, fish, charms and exotic fish (progression part 3)."""
from dataclasses import replace

from .game_model import max_charm_level
from .plan_economy import charms_for_level, increments, level_up_money
from .player_planner import xp_required

EXOTIC = (('gold', 'Gold'), ('emerald', 'Emerald'), ('lava', 'Lava'), ('diamond', 'Diamond'))


def buy_special(state, route, prices):
    """Buy what the exotic fish afford, in the route's order. One line per purchase."""
    lines = []
    for currency, order in route['special_orders'].items():
        for name, target in order:
            item = prices['special'][name]
            while state.level >= item['unlock'] and state.account.upgrades.get(name, 0) < target:
                level = state.account.upgrades.get(name, 0)
                if level >= len(item['prices']) or state.exotic[currency] < item['prices'][level]:
                    break
                state.exotic[currency] -= item['prices'][level]
                state.account = replace(state.account, upgrades={**state.account.upgrades, name: level + 1})
                lines.append(f'{name} {level + 1} ({item["prices"][level]} {currency} Fish)')
            if state.account.upgrades.get(name, 0) < target:
                break  # the order waits for this item
    return lines


def _spread_charms(state, amount):
    top = charms_for_level(max_charm_level(state.account.prestige))
    while amount > 1e-9:
        open_kinds = [k for k, c in state.charm_counts.items() if c < top - 1e-9]
        if not open_kinds:
            break
        share = amount / len(open_kinds)
        room = min(top - state.charm_counts[k] for k in open_kinds)
        step = min(share, room)
        for kind in open_kinds:
            state.charm_counts[kind] += step
        amount -= step * len(open_kinds)
    state.sync_charms()


def _fish(state, m, trips):
    state.fish_value += m['money'] * trips          # fish and chest money alike (the model's money per trip)
    state.xp += m['xp'] * trips
    for key, currency in EXOTIC:
        state.exotic[currency] += m[key] * trips
    _spread_charms(state, m['charms'] * trips)


def _gains_nothing(m):
    return not any(m.get(key, 0) > 0 for key in ('money', 'xp', 'charms', *(k for k, _ in EXOTIC)))


def fish_forward(state, metrics_at, until, stop_levels, route=None, prices=None, max_trips=1e9):
    """Fish trip by trip, levelling up as the XP comes. Stops when `until` is met, at a stop level, or when stuck."""
    trips = seconds = 0.0
    events = []
    table = increments()

    def result(stopped):
        return {'trips': trips, 'seconds': seconds, 'events': events, 'stopped': stopped}

    while trips < max_trips:
        m = metrics_at(state)
        needed = until(state, m)
        if needed <= 0:
            return result('until')
        if _gains_nothing(m):
            return result('stuck')
        required = xp_required(state.level, table) or float('inf')
        to_level = max(0.0, (required - state.xp) / m['xp']) if m['xp'] > 0 else float('inf')
        step = min(needed, to_level, max_trips - trips)
        if step == float('inf'):
            return result('stuck')
        _fish(state, m, step)
        trips += step
        seconds += step * m['seconds']
        if route is not None and prices is not None:
            events += buy_special(state, route, prices)
        if step >= to_level:
            state.xp = max(0.0, state.xp - required)
            state.account = replace(state.account, level=state.level + 1)
            state.cash += level_up_money(state.level)
            if state.level in stop_levels:
                return result('level')
    return result('stuck')
