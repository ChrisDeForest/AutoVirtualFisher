"""The route's phases and what may be bought in each, ordered by MRP (progression part 3)."""
import math
from dataclasses import dataclass, replace

from .plan_economy import charm_requirement, level_for_charms, prestige_requirements
from .plan_setup import choose_setup, metrics, score


@dataclass
class Action:
    kind: str            # 'upgrade', 'rod' or 'boat'
    name: str
    level: int | None
    price: float
    reason: str = ''
    forced: bool = False
    money_first: bool = False   # in a charm grind: saved for on the money setup (entry steps, the charm rod)
    for_rod: str | None = None  # a Salesman level bought just before this rod


def charm_exit(state, route):
    rules, prestige = route['charm_grind'], state.account.prestige
    exits = rules['exits']
    if str(prestige) in exits:
        return exits[str(prestige)], rules.get('exit_estimates', {}).get(str(prestige))
    count = math.ceil(charm_requirement(prestige) * rules['later_share'])
    return count, (f'P{prestige} charm exit is not in the guides: {count} uses the P4 share of the requirement '
                   f'(640 of 840)')


def _first_exit_done(state, route):
    rules = route['charm_grind']['first_exit']
    return state.charm_total() >= rules['charms'] or level_for_charms(state.charm_counts.get('Haste', 0)) >= rules['haste']


def phase(state, route):
    requirement = prestige_requirements(state.account.prestige)
    if requirement and state.level >= requirement[0] and state.money() >= requirement[1] \
            and state.charm_total() >= requirement[2]:
        return 'prestige'
    rules = route['charm_grind']
    split = state.account.prestige >= rules['split_from_prestige']
    exit_count, _ = charm_exit(state, route)
    level_250 = route['phase_steps']['charm_grind_2']['level']
    if rules['rod'] not in state.rods:
        return 'xp_grind_1' if state.level < level_250 else ('charm_grind_2' if state.charm_total() < exit_count
                                                             else 'xp_grind_3')
    first_done = _first_exit_done(state, route) if split else state.charm_total() >= exit_count
    if not first_done and state.level < level_250:
        return 'charm_grind_1'
    if state.level < level_250:
        return 'xp_grind_2'
    if state.charm_total() < exit_count:
        return 'charm_grind_2'
    return 'xp_grind_3'


def mrp(price, k):
    return price / (1 - 1 / k) if k > 1 else math.inf


def rod_band(state, route):
    if 'Supporter Rod' not in state.rods:
        return {'rods': route['standard_rods']}
    for band in route['supporter_bands']:
        if band['from'] <= state.account.prestige and (band['to'] is None or state.account.prestige <= band['to']):
            return band
    return {'rods': []}


def _next_rod(state, route, data):
    band = rod_band(state, route)
    for rod in band['rods']:
        if rod in state.rods:
            continue
        only = band.get('only_at', {}).get(rod)
        if only is not None and state.account.prestige not in only:
            continue
        other = band.get('skip_if_affordable', {}).get(rod)
        if other and other not in state.rods and state.money() >= data['rods'][other]['price']:
            continue
        return rod
    return None


def _upgrade(state, name, prices, target=None, reason='', forced=False, money_first=False):
    level = state.account.upgrades.get(name, 0)
    table = prices['upgrades'][name]
    if level >= min(len(table), state.maxed.get(name, len(table))) or (target is not None and level >= target):
        return None
    return Action('upgrade', name, level + 1, table[level], reason, forced, money_first)


def _next_boat(state, prices, reason='', forced=False, money_first=False):
    if state.account.boats >= len(prices['boats']):
        return None
    name, price, unlock = prices['boats'][state.account.boats]
    if unlock > state.level:
        return None
    return Action('boat', name, state.account.boats + 1, price, reason, forced, money_first)


def candidates(state, phase_name, route, prices, data):
    """What may be bought next: forced steps first, then the pool ranked by MRP. The next band rod is offered in XP
    grinds only (a charm grind buys no rod but its own); its Salesman level is added by select_next only when the rod
    itself is the purchase."""
    forced, pool = [], []
    charm_phase = phase_name.startswith('charm_grind')
    rod = _next_rod(state, route, data)
    rod_price = data['rods'][rod]['price'] if rod else None
    if rod and not charm_phase and data['rods'][rod]['unlock_level'] <= state.level:
        pool.append(Action('rod', rod, None, rod_price, 'next rod'))
    charm_rod = route['charm_grind']['rod']
    if (phase_name == 'xp_grind_1' or charm_phase) and charm_rod not in state.rods             and data['rods'][charm_rod]['unlock_level'] <= state.level:
        # the first XP grind lasts until the charm grind's rod; it is not judged by money
        forced.append(Action('rod', charm_rod, None, data['rods'][charm_rod]['price'],
                             f'{charm_rod} for the charm grind', True, money_first=True))
        pool = [a for a in pool if a.name != charm_rod]
    if charm_phase:
        # the guide checks these before a charm grind starts; the 2nd grind applies them too when the 1st was skipped
        entry = route['charm_grind']
        for name, target in entry['entry'].items():
            step = _upgrade(state, name, prices, target, f'charm grind entry: {name} {target}', True, True)
            if step:
                forced.append(step)
        if state.account.boats < entry['entry_boats']:
            boat = _next_boat(state, prices, f'charm grind entry: boat {entry["entry_boats"]}', True, True)
            if boat:
                forced.append(boat)
    steps = route['phase_steps'].get(phase_name, {})
    for name, target in steps.get('upgrades', {}).items():
        step = _upgrade(state, name, prices, target, f'{name} {target} in this phase', True)
        if step:
            forced.append(step)
    boats_target = steps.get('boats')
    boat_name = steps.get('boat')
    haste = level_for_charms(state.charm_counts.get('Haste', 0))
    if boat_name and haste >= steps.get('skip_boat_if_haste', math.inf):
        boat_name = None
    if boat_name:
        boats_target = [b[0] for b in prices['boats']].index(boat_name) + 1
    if boats_target and state.account.boats < boats_target:
        boat = _next_boat(state, prices, f'boat {boats_target} in this phase', True)
        if boat:
            forced.append(boat)
    for name in steps.get('rods', []):
        if name not in state.rods and data['rods'][name]['unlock_level'] <= state.level:
            forced.append(Action('rod', name, None, data['rods'][name]['price'], f'{name} in this phase', True))
    limit = route['five_percent']
    for name in prices['upgrades']:
        if name in route['no_mrp']:
            continue
        step = _upgrade(state, name, prices)
        if step is None:
            continue
        if rod_price and step.price > limit['share'] * rod_price and name not in limit['exceptions']:
            continue
        pool.append(step)
    boat = _next_boat(state, prices)
    if boat:
        pool.append(boat)
    dailies = _upgrade(state, 'Better Dailies', prices, None, 'Better Dailies: bigger daily rewards', True)
    return ([dailies] if dailies else []) + forced + pool


def apply(state, action, data):
    """The state after buying `action` (no money is taken here)."""
    after = state.copy()
    if action.kind == 'upgrade':
        after.account = replace(after.account, upgrades={**after.account.upgrades, action.name: action.level})
    elif action.kind == 'boat':
        after.account = replace(after.account, boats=action.level)
    elif action.kind == 'rod':
        after.rods.add(action.name)
    return after


def _salesman_first(state, action, reason, route, prices):
    """A rod's Salesman level, bought just before the rod (the guide's "buy just before use"), else the rod."""
    if action is None or action.kind != 'rod':
        return action, reason
    target = route['salesman_before_rod'].get(action.name)
    step = target and _upgrade(state, 'Salesman', prices, target, f'Salesman {target} before {action.name}', True,
                               action.money_first)
    if not step:
        return action, reason
    step.for_rod = action.name
    return step, step.reason


def select_next(state, phase_name, actions, needs, ctx, route, prices, chooser=None):
    """The next purchase: the first forced one, else the lowest MRP (within 5%, the tie-break order). A rod whose
    Salesman level is not reached yet gives that Salesman level first. `chooser` replaces choose_setup (the planner
    passes a cached one)."""
    action, reason = _select(state, phase_name, actions, ctx, route, prices, chooser)
    return _salesman_first(state, action, reason, route, prices)


def _select(state, phase_name, actions, ctx, route, prices, chooser):
    choose = chooser or choose_setup
    for action in actions:
        if action.forced:
            return action, action.reason
    goal_needs = {'charms': 1.0} if phase_name.startswith('charm_grind') else {'money': 1.0}
    _, now = choose(state, goal_needs, phase_name, ctx, route, prices)
    if isinstance(now, str):
        return None, now
    rated, unseen = [], set()
    all_boosts = phase_name in route['boosts']['always_from']
    for action in actions:
        bought = apply(state, action, ctx.data)
        if action.kind == 'rod':
            _, after = choose(bought, goal_needs, phase_name, ctx, route, prices)
        else:
            # an upgrade or boat is judged on the setup in use: re-choosing the setup for each would cost a full
            # search per candidate, and these purchases do not change which rod or biome is best
            after = metrics(bought, now['setup'], ctx, route, prices, all_boosts)
        if after is None or isinstance(after, str):
            continue
        k = score(now, goal_needs) / score(after, goal_needs) if score(after, goal_needs) else math.inf
        value = mrp(action.price, k)
        if value == math.inf and action.kind == 'rod' and 'charms' not in goal_needs:
            # a rod the guide lists but the model shows no gain from (e.g. a River-only rod while the Supporter Rod
            # fishes further out): ranked at its price, the lowest MRP a purchase can have, so it is bought when
            # cheap rather than holding back the rods after it and the 5% rule its price sets
            value = action.price
            unseen.add(id(action))
        if value < math.inf:
            rated.append((value, action))
    if not rated:
        return None, 'nothing in this phase raises the goal'
    rated.sort(key=lambda pair: pair[0])
    best = rated[0][0]
    order = route['tie_break']
    close = [a for v, a in rated if v <= best * 1.05]
    token = lambda a: 'boat' if a.kind == 'boat' else a.name
    close.sort(key=lambda a: order.index(token(a)) if token(a) in order else len(order))
    chosen = close[0]
    value = next(v for v, a in rated if a is chosen)
    if id(chosen) in unseen:
        return chosen, (f'MRP ${value:,.0f}: next rod in the guide, ranked at its price (the model shows no '
                        f'{next(iter(goal_needs))} gain from it)')
    return chosen, f'MRP ${value:,.0f}'
