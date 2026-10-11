"""Which setup to fish with, and what one trip yields with it (progression part 3, on the part 2 model)."""
import json
from dataclasses import dataclass, replace

from .game_model import NotModelled, cooldown, trip
from .game_model_data import REFERENCE

EXOTIC_KEYS = (('gold', 'Gold'), ('emerald', 'Emerald'), ('lava', 'Lava'), ('diamond', 'Diamond'))
BAIT_COST = {name: entry['cost'] for name, entry in
             json.loads((REFERENCE / 'bait.json').read_text(encoding='utf-8'))['baits'].items()}


@dataclass
class ModelContext:
    data: dict
    learned: dict | None = None
    chest_scales: dict | None = None
    corrections: dict | None = None
    factors: dict | None = None
    overhead: float = 0.5


def _value(result, key):
    item = result.get(key)
    return item.value if item is not None and item.value is not None else 0.0


def _trip(state, setup, ctx):
    try:
        return trip(state.account, setup, ctx.data, ctx.learned, ctx.chest_scales, ctx.corrections)
    except (NotModelled, KeyError):
        return None


def _why(result):
    reasons = set()
    for item in result.values():
        if hasattr(item, 'source') and item.source != 'formula':
            reasons.update(item.why)
    return reasons


def metrics(state, setup, ctx, route, prices, all_boosts):
    """Expected values for one trip with `setup`, boosts blended in by the share of time they can run."""
    base = _trip(state, setup, ctx)
    if base is None:
        return None
    factor = lambda name: (ctx.factors or {}).get(name, 1.0)
    seconds = cooldown(state.account, setup, ctx.data) + ctx.overhead
    rules = route['boosts']
    if all_boosts:
        fish_share = treasure_share = 1.0
    else:
        rate = _value(base, 'emerald') * 3600 / seconds
        fish_share = min(1.0, rate / rules['per_hour']) if rate > rules['fish_min_rate'] else 0.0
        left = rate - fish_share * rules['per_hour']
        treasure_share = min(1.0, left / rules['per_hour']) if rate >= rules['treasure_min_rate'] and left > 0 else 0.0
    fished = _trip(state, replace(setup, fish_boost=True), ctx) if fish_share else base
    treasured = _trip(state, replace(setup, treasure_boost=True), ctx) if treasure_share else base
    if fished is None or treasured is None:
        return None

    def blend(key):
        b = _value(base, key)
        return b + fish_share * (_value(fished, key) - b) + treasure_share * (_value(treasured, key) - b)

    bait_cost = bait_price(state, setup.bait)
    boost_use = (fish_share + treasure_share) * rules['per_hour'] * seconds / 3600
    result = {
        'money': blend('money_fish') * factor('money_fish') + blend('money_chests') * factor('chest_chance') - bait_cost,
        'xp': blend('xp_fish') * factor('xp_fish') + blend('xp_chests') * factor('chest_chance'),
        'charms': blend('charms') * factor('charms'),
        'cooldown': seconds - ctx.overhead, 'seconds': seconds, 'bait_cost': bait_cost,
        'fish_share': fish_share, 'treasure_share': treasure_share, 'setup': setup, 'why': _why(base),
    }
    for key, _ in EXOTIC_KEYS:
        result[key] = blend(key)
    result['emerald'] = max(0.0, result['emerald'] - boost_use)
    return result


def score(m, needs):
    """Minus the seconds until every need is met at these rates (the slowest need decides)."""
    worst = 0.0
    for name, amount in needs.items():
        if amount <= 0:
            continue
        rate = m.get(name, 0.0)
        if rate <= 0:
            return float('-inf')
        worst = max(worst, amount / rate * m['seconds'])
    return -worst


def bait_price(state, bait):
    """What one bait costs, less Bait Efficiency (no bait costs nothing)."""
    if not bait:
        return 0.0
    return BAIT_COST.get(bait, 0) * (1 - 0.05 * state.account.upgrades.get('Bait Efficiency', 0))


def bait_batch(route, biome):
    batches = route['bait']['batch']
    return batches.get(biome, batches['default'])


def bait_options(state, needs, route, prices, biome, affordable=True):
    """The baits the model may choose from: none, and every bait unlocked at this level whose batch for the biome
    (the guide: 200 at a time in River, 500 elsewhere) the money on hand can pay for. The bait in use stays allowed:
    it is restocked from what each trip makes, so a purchase that empties the wallet does not take it away. The
    guide's exclusions stay: never Magnet or Support Bait, and Wise Bait only for an XP goal once the Infinity Rod is
    owned (until then money still matters). With `affordable` False the money on hand is not checked."""
    rules = route['bait']
    batch, money = bait_batch(route, biome), state.money()
    options = [None]
    for name, level in sorted(prices['bait_unlock'].items(), key=lambda item: item[1]):
        if level > state.level or name in rules['never'] or name not in BAIT_COST:
            continue
        if name == rules['xp'] and not (set(needs) == {'xp'} and rules['xp_after_rod'] in state.rods):
            continue
        if not affordable or name == state.setup.bait or bait_price(state, name) * batch <= money:
            options.append(name)
    return options


def _with_bait(state, best, needs, ctx, route, prices, all_boosts):
    """The bait that meets `needs` soonest on this setup, its cost per trip taken off the money it makes. When a
    better bait is unlocked but its batch cannot be paid for yet, `bait_wait` is the money at which it can: the plan
    fishes until then and chooses again."""
    biome = best['setup'].biome
    allowed = bait_options(state, needs, route, prices, biome)
    top = best
    for bait in bait_options(state, needs, route, prices, biome, affordable=False):
        if bait == best['setup'].bait:
            continue
        m = metrics(state, replace(best['setup'], bait=bait), ctx, route, prices, all_boosts)
        if m is None:
            continue
        if bait in allowed and score(m, needs) > score(best, needs):
            best = m
        if score(m, needs) > score(top, needs):
            top = m
    best = dict(best)
    best['bait_wait'] = None
    if top['setup'].bait not in allowed:
        best['bait_wait'] = bait_price(state, top['setup'].bait) * bait_batch(route, biome)
    return best


def _bait(state, needs, phase, route, prices):
    level, rules = state.level, route['bait']
    allowed = lambda name: prices['bait_unlock'].get(name, 10 ** 9) <= level
    if 'charms' in needs and allowed(rules['charm']):
        return rules['charm']   # only when fishing for charms: a purchase saved for in a charm grind uses the level bait
    if set(needs) == {'xp'} and rules['xp_after_rod'] in state.rods and allowed(rules['xp']):
        return rules['xp']
    chosen = None
    for start, name in rules['by_level']:
        if level >= start and allowed(name):
            chosen = name
    return chosen


def choose_setup(state, needs, phase, ctx, route, prices):
    """The setup that meets `needs` soonest: rod and biome by the model (tried with the guide's bait for the level and
    goal), then the bait by the model among those unlocked and affordable, net of its cost, then the pet by its stat
    bonus with all pets at one level (the phase's pet order breaks ties), then the bait once more for that pet."""
    all_boosts = phase in route['boosts']['always_from']
    guide_bait = _bait(state, needs, phase, route, prices)
    biomes = [b for b, level in prices['biome_unlock'].items() if level <= state.level and b in ctx.data['biomes']]
    rods = sorted(state.rods)
    if phase.startswith('charm_grind') and route['charm_grind']['rod'] in state.rods:
        rods = [route['charm_grind']['rod']]
    best = None
    for biome in biomes:
        for rod in rods:
            if biome not in ctx.data['rods'].get(rod, {}).get('biomes', ()):
                continue
            bait = guide_bait if guide_bait in bait_options(state, needs, route, prices, biome) else None
            setup = replace(state.setup, rod=rod, biome=biome, bait=bait, fish_boost=False, treasure_boost=False)
            m = metrics(state, setup, ctx, route, prices, all_boosts)
            if m is not None and (best is None or score(m, needs) > score(best, needs)):
                best = m
    if best is None:
        return None, 'no owned rod is modelled in an unlocked biome'
    best = _with_bait(state, best, needs, ctx, route, prices, all_boosts)
    best = _with_pet(state, best, needs, ctx, route, prices, all_boosts)
    best = _with_bait(state, best, needs, ctx, route, prices, all_boosts)
    return best['setup'], best


def _with_pet(state, best, needs, ctx, route, prices, all_boosts):
    """The owned pet whose stat bonus does most for the goal, judged with every pet at the same level: levels differ
    only by how much each pet has been used, and a pet levels up once it is used. The phase's pet order breaks ties.
    The returned metrics use the chosen pet's real level."""
    if not state.account.pets:
        return best
    goal = 'charms' if 'charms' in needs else ('xp' if set(needs) == {'xp'} else 'money')
    order = route['pet_order'][goal]
    ranked = sorted(state.account.pets, key=lambda p: order.index(p) if p in order else len(order))
    level = max(state.account.pets.values())
    even = state.copy()
    even.account = replace(even.account, pets={pet: level for pet in even.account.pets})
    chosen, chosen_score = None, None
    for pet in ranked:
        m = metrics(even, replace(best['setup'], pet=pet), ctx, route, prices, all_boosts)
        if m is not None and (chosen_score is None or score(m, needs) > chosen_score):
            chosen, chosen_score = pet, score(m, needs)
    if chosen is None:
        return best
    real = metrics(state, replace(best['setup'], pet=chosen), ctx, route, prices, all_boosts)
    return real if real is not None else best
