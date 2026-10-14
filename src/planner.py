"""The route to the next prestige (progression part 3): a forward simulation over the plan state."""
import math
import re
import time
from dataclasses import replace

from .actions import Action
from .game_model import max_charm_level
from .plan_economy import charms_for_level, prestige_requirements, xp_to_level
from .plan_forward import fish_forward
from .plan_rules import apply, candidates, charm_exit, phase, rod_band, select_next
from .plan_setup import bait_batch, bait_options, bait_price, choose_setup, metrics


def _needs(state, phase_name, route, action=None):
    """What the setup is chosen for. A purchase is saved for on the money setup, except in a charm grind: there the
    charm setup is fished and the purchase is bought when the money is there, unless it is an entry step or the
    charm rod (`money_first`), which the guide has in place before the grinding starts."""
    charm_phase = phase_name.startswith('charm_grind')
    if action is not None and (not charm_phase or action.money_first):
        return {'money': max(1.0, action.price - state.money())}
    if charm_phase:
        exit_count, _ = charm_exit(state, route)
        return {'charms': max(1.0, exit_count - state.charm_total())}
    requirement = prestige_requirements(state.account.prestige)
    level_250 = route['phase_steps']['charm_grind_2']['level']
    target = level_250 if phase_name == 'xp_grind_2' else requirement[0]
    needs = {'xp': max(1.0, float(xp_to_level(state, target)))}
    if phase_name == 'xp_grind_3':
        needs['money'] = max(0.0, requirement[1] - state.money())
    return needs


def _prestige_shop(state, route):
    shop, target = route['prestige_shop'], state.account.prestige + 1
    owned = state.account.prestige_upgrades
    if target >= shop['route_a_from'] and owned.get('Business Education') and str(target) in shop['route_a']:
        return list(shop['route_a'][str(target)])
    if str(target) in shop['standard']:
        return list(shop['standard'][str(target)])
    items = list(shop['every_fifth_from_20']) if target % 5 == 0 else []
    if target >= 21 and target % 5 == 1:
        items.append('Business Education')
    return items


def _switch_text(old, new):
    parts = []
    for field, label in (('rod', 'rod'), ('biome', 'biome'), ('bait', 'bait'), ('pet', 'pet')):
        before, after = getattr(old, field), getattr(new, field)
        if before != after:
            parts.append(f'{label} {after or "none"}')
    return 'Switch: ' + ', '.join(parts) if parts else None


def _league(state, route, add, notes):
    """The first league upgrade in the route's order that has a price: bought with the Hooks on hand, or listed as
    waiting for Hooks (Hooks earned while fishing are not counted). One whose price was not read is never a step
    (it may cost more than the Hooks on hand): it is named in `notes`. A maxed one (not in league_prices) is passed
    over."""
    unpriced = []
    for name in route['league_order']:
        if name not in state.league_prices:
            continue
        price = state.league_prices[name]
        level = state.account.upgrades.get(name, 0) + 1
        if not price:
            unpriced.append(f'{name} {level}')
            continue
        do = (Action('buy', name, level=level, shop='league', price=price),)
        if state.hooks >= price:
            add('league', f'{name} {level}', price, 'Hooks', why='Hooks on hand', do=do)
            state.hooks -= price
            state.account = replace(state.account, upgrades={**state.account.upgrades, name: level})
        else:
            add('league', f'{name} {level}', price, 'Hooks',
                why=f'waiting for Hooks: {price - state.hooks} more needed ({state.hooks} on hand)', do=do)
        break
    if unpriced:
        notes.add('league prices not read (/shop league): ' + ', '.join(unpriced) + '; left out of the route')


def _until(current, action, charm_target, route, bait_wait=None):
    """Trips still to fish before the next decision: until the purchase is covered, the charm grind's exit is reached,
    a better bait's batch can be paid for (`bait_wait`) or the phase changes, whichever comes first. A hair is added
    so float sums cross the line."""
    def until(st, r):
        if phase(st, route) != current:
            return 0
        parts = []
        if action is not None:
            short = action.price - st.money()
            parts.append(0 if short <= 0 else short / max(r['money'], 1e-9) * (1 + 1e-9) + 1e-9)
        if bait_wait is not None:
            short = bait_wait - st.money()
            parts.append(0 if short <= 0 else short / max(r['money'], 1e-9) * (1 + 1e-9) + 1e-9)
        if charm_target is not None:
            short = charm_target - st.charm_total()
            parts.append(0 if short <= 0 else short / max(r['charms'], 1e-12) * (1 + 1e-9) + 1e-9)
        return min(parts) if parts else 1e18
    return until


def _charm_target(state, phase_name, route):
    """The charm total that ends this charm grind (None outside one). The 1st grind of a split ends at the first exit:
    its charm count, or the total at which Haste reaches the exit level with charms spread evenly over the kinds not
    maxed, whichever comes first (the phase check after the stretch confirms it)."""
    if not phase_name.startswith('charm_grind'):
        return None
    exit_count, _ = charm_exit(state, route)
    rules = route['charm_grind']
    if phase_name != 'charm_grind_1' or state.account.prestige < rules['split_from_prestige']:
        return exit_count
    first = rules['first_exit']
    top = charms_for_level(max_charm_level(state.account.prestige))
    open_kinds = sum(1 for count in state.charm_counts.values() if count < top - 1e-9) or 1
    haste_short = max(0.0, charms_for_level(first['haste']) - state.charm_counts.get('Haste', 0))
    return min(exit_count, first['charms'], state.charm_total() + haste_short * open_kinds)


def _cached_chooser():
    """choose_setup with its results kept for one plan. With one need its amount cannot change the choice (every
    setup is judged by the same rate), so only the need's name is part of the key; the money on hand only through
    which baits it can pay a batch of."""
    cache = {}

    def choose(state, needs, phase_name, ctx, route, prices):
        positive = sorted(name for name, amount in needs.items() if amount > 0)
        amounts = tuple(sorted(needs.items())) if len(positive) > 1 else None
        account = state.account
        baits = tuple(tuple(bait_options(state, needs, route, prices, biome)) for biome in ('River', None))
        key = (phase_name, tuple(sorted(needs)), tuple(positive), amounts, account.level, account.boats,
               frozenset(state.rods), tuple(sorted(account.upgrades.items())), tuple(sorted(account.charms.items())),
               baits)
        if key not in cache:
            cache[key] = choose_setup(state, needs, phase_name, ctx, route, prices)
        return cache[key]
    return choose


# Where each kind of step is bought in the game; a buy step goes by what it buys.
WHERE = {'league': '/shop league', 'special': '/shop special', 'bait': '/shop bait',
         'upgrade': '/shop upgrades', 'rod': '/shop rods', 'boat': '/shop boats'}


def make_plan(state, ctx, plan_data, limits=(3000, 2.0), clock=time.monotonic):
    route, prices = plan_data['route'], plan_data['prices']
    labels = route['phase_labels']
    requirement = prestige_requirements(state.account.prestige)
    plan = {'error': None, 'next': None, 'steps': [], 'prestige': {'reached': False, 'trips': 0, 'seconds': 0,
            'missing': [], 'shop': []}, 'assumptions': set(route.get('estimates', [])), 'cut_short': False}
    if requirement is None:
        plan['error'] = f'P{state.account.prestige} requirements are not known'
        return _finish(plan)
    state = state.copy()
    started, trips, seconds = clock(), 0.0, 0.0
    stop_levels = set(prices['biome_unlock'].values()) | set(prices['bait_unlock'].values()) \
        | {route['phase_steps']['charm_grind_2']['level']}
    steps = plan['steps']
    current = phase(state, route)
    choose = _cached_chooser()
    band = rod_band(state, route)
    if band.get('estimate'):
        plan['assumptions'].add(band['estimate'])
    special_notes = route.get('special_estimates', [])

    def add(kind, action, cost=0, currency='$', sell=0, why='', fished=0.0, where=None, do=()):
        # `do`: the actions the step stands for (part 4 queues them); empty when there is nothing to send.
        steps.append({'phase': current, 'phase_label': labels.get(current, current), 'kind': kind,
                      'action': action, 'cost': cost, 'currency': currency, 'sell': sell, 'trips': fished,
                      'seconds': seconds, 'why': why, 'where': WHERE.get(kind, '') if where is None else where,
                      'do': [item.to_dict() for item in do]})

    _league(state, route, add, plan['assumptions'])
    bait_step = {'bait': object()}                     # the last bait step, finished when the bait changes

    def close_bait():
        """Finish the last bait step: the bait used until this change, the part of it that came from the bait on hand
        (already paid for, so its cost goes back to the money) and what had to be bought."""
        step = bait_step.get('step')
        if step is None:
            return
        name, price = bait_step['bait'], bait_step['price']
        used = math.ceil(trips - bait_step['from'])
        on_hand = min(used, state.bait_stock.get(name, 0))
        if on_hand:
            state.cash += on_hand * price
            state.bait_stock[name] -= on_hand
        bought = used - on_hand
        if bait_step['stock'] and not bought:
            step.update(action=f'Use the {bait_step["stock"]:,} {name} on hand', cost=0)
        step['why'] += (f'; about {used:,} bait until the next bait change'
                        + (f', {on_hand:,} from the bait on hand' if on_hand else '')
                        + (f', {bought:,} bought (${math.ceil(bought * price):,})' if bought else ''))

    while True:
        if len(steps) >= limits[0] or clock() - started > limits[1]:
            plan['cut_short'] = True
            break
        current = phase(state, route)
        if current == 'prestige':
            plan['prestige'].update(reached=True, trips=trips, seconds=seconds, shop=_prestige_shop(state, route))
            break
        _, why = charm_exit(state, route)
        if why and current.startswith('charm_grind'):
            plan['assumptions'].add(why)
        actions = candidates(state, current, route, prices, ctx.data)
        action, reason = select_next(state, current, actions, _needs(state, current, route), ctx, route, prices,
                                     chooser=choose)
        needs = _needs(state, current, route, action)
        setup, m = choose(state, needs, current, ctx, route, prices)
        if setup is None:
            plan['error'] = f'not planned: {m}'
            break
        plan['assumptions'].update(m['why'])
        switch = _switch_text(state.setup, setup)
        if switch:
            changes = tuple(Action(field, getattr(setup, field) or '') for field in ('rod', 'biome', 'bait', 'pet')
                            if getattr(state.setup, field) != getattr(setup, field))
            add('switch', switch, why='best setup for ' + ', '.join(sorted(needs)), do=changes)
        if setup.bait != bait_step['bait']:
            close_bait()
            bait_step.update(bait=setup.bait, step=None)
            if setup.bait:
                price, batch = bait_price(state, setup.bait), bait_batch(route, setup.biome)
                stock = state.bait_stock.get(setup.bait, 0)
                buy = f'buy {setup.bait}, {batch} at a time'
                add('bait', f'Use the {stock:,} {setup.bait} on hand, then {buy}' if stock else 'B' + buy[1:],
                    math.ceil(price * batch),
                    why=f'${price:,.2f} a bait, paid from the money each trip makes; never run out',
                    do=() if stock else (Action('buy', setup.bait, amount=batch, shop='bait',
                                                price=math.ceil(price * batch)),))
                bait_step.update(step=steps[-1], price=price, stock=stock, **{'from': trips})
        state.setup = setup
        all_boosts = current in route['boosts']['always_from']
        cache = {}

        def rates(st, m=m, all_boosts=all_boosts, cache=cache):
            key = (st.level, tuple(sorted(st.account.upgrades.items())), tuple(sorted(st.account.charms.items())))
            if key not in cache:
                cache[key] = metrics(st, st.setup, ctx, route, prices, all_boosts) or {**m, 'money': 0, 'xp': 0}
            return cache[key]

        until = _until(current, action, _charm_target(state, current, route), route, m.get('bait_wait'))
        result = fish_forward(state, rates, until, stop_levels, route=route, prices=prices)
        trips += result['trips']
        seconds += result['seconds']
        for line in result['events']:
            named = re.match(r'(.+?) (\d+) \(', line)              # 'Fish Ovens 3 (60 Lava Fish)'
            add('special', line, why='exotic fish on hand',
                do=(Action('buy', named[1], level=int(named[2]), shop='special'),) if named else ())
            bought = line.split(' (')[0]
            plan['assumptions'].update(note for note in special_notes if bought in note)
        if result['stopped'] == 'stuck':
            plan['error'] = 'not planned: the plan cannot make progress (no money or XP per trip)'
            break
        if result['stopped'] == 'level' or action is None or state.money() < action.price:
            continue                                   # a level or phase changed what comes next
        sell = max(0.0, action.price - state.cash)
        full = action.kind == 'rod' or (action.kind == 'boat' and action.level >= route['full_sell_boats_from'])
        if full:
            sell = state.fish_value
        state.cash += sell
        state.fish_value -= sell
        state.cash -= action.price
        label = f'{action.name} {action.level}' if action.kind == 'upgrade' else action.name
        shop = {'upgrade': 'upgrades', 'rod': 'rods', 'boat': 'boats'}[action.kind]
        add('buy', label, action.price, '$', sell, reason, fished=result['trips'], where=WHERE[action.kind],
            do=(Action('buy', action.name, level=action.level if action.kind == 'upgrade' else None, shop=shop,
                       price=action.price),))
        if action.for_rod and action.for_rod in route.get('salesman_estimates', {}):
            plan['assumptions'].add(route['salesman_estimates'][action.for_rod])
        state = apply(state, action, ctx.data)
    close_bait()
    if not plan['prestige']['reached']:
        level, money, charms = requirement
        missing = []
        if state.level < level:
            missing.append(f'level {level} (the plan reaches {state.level})')
        if state.money() < money:
            missing.append(f'${math.ceil(money - state.money()):,} more money')
        if state.charm_total() < charms:
            missing.append(f'{math.ceil(charms - state.charm_total() - 1e-9)} more charms')
        plan['prestige']['missing'] = missing
    return _finish(plan)


def _finish(plan):
    plan['assumptions'] = sorted(plan['assumptions'])
    plan['next'] = next((s for s in plan['steps'] if s['kind'] != 'league'), None) or (plan['steps'] or [None])[0]
    return plan
