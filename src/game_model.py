"""The game model (progression part 2): what /buffs would show and what one trip yields, for an account and a
setup. Formulas follow docs/progression-model.md and the buff calculator vB1.13.4 (cell refs in comments). Every
number carries a source: formula, measured, estimate or not modelled."""
from dataclasses import dataclass, field
from math import floor

SOURCES = ('formula', 'measured', 'estimate', 'not modelled')  # best first
CHARM_KINDS = ('Marketing', 'Endurance', 'Haste', 'Quantity', 'Worker', 'Treasure', 'Quality', 'Experience')


@dataclass(frozen=True)
class Value:
    value: float | None
    source: str = 'formula'
    why: tuple = ()


def worst(*values):
    """The source and reasons a result built from these values carries: the weakest source wins."""
    source = max((value.source for value in values), key=SOURCES.index, default='formula')
    why = tuple(dict.fromkeys(reason for value in values for reason in value.why))
    return source, why


@dataclass(frozen=True)
class Account:
    level: int
    prestige: int
    charms: dict = field(default_factory=dict)
    upgrades: dict = field(default_factory=dict)
    prestige_upgrades: dict = field(default_factory=dict)
    clan: dict = field(default_factory=dict)
    boats: int = 0
    pets: dict = field(default_factory=dict)
    official_server: bool = False
    assumed: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Setup:
    rod: str
    biome: str
    bait: str | None = None
    pet: str | None = None
    fish_boost: bool = False
    treasure_boost: bool = False
    global_booster: bool = False
    personal_booster: bool = False
    prestige_buff: bool = False
    event: bool = False


class NotModelled(Exception):
    """The setup names something the game data does not know."""


# The inputs each buff is built from: an assumed one makes the buff an estimate.
BUFF_INPUTS = {
    'sell_price': ('Salesman', 'Fish Ovens', 'Ultimate Salesman', 'Statistician', 'Marketing', 'clan',
                   'International Ties', 'Business Education', 'Virtual Fisher', 'level'),
    'fish_catch': ('Aquatic Expert', 'Statistician', 'Endurance', 'clan', 'International Ties', 'Fish Whisperer',
                   'Bait Lover', 'Bait Helper', 'Boost Booster', 'Fishing Frenzy', 'pet'),
    'fish_quality': ('Better Fish', 'Statistician', 'International Ties', 'Virtual Fisher', 'Bait Lover',
                     'Bait Helper', 'pet'),
    'treasure_chance': ('More Chests', 'Statistician', 'Treasure', 'International Ties', 'Bait Lover', 'Bait Helper',
                        'Boost Booster', 'Fishing Frenzy', 'pet'),
    'treasure_quality': ('Better Chests', 'Statistician', 'Quality', 'International Ties', 'Bait Lover',
                         'Bait Helper', 'clan', 'pet'),
    'xp_multiplier': ('Experienced', 'Highly Experienced', 'Statistician', 'Experience', 'clan',
                      'International Ties', 'Ancient One', 'Virtual Fisher', 'Bait Lover', 'Bait Helper', 'pet'),
    'fishing_cooldown': ('boats', 'Haste'),
    'treasure_quantity': ('Artifact Specialist', 'Quantity'),
    'charm_quantity': ('Charmer', 'prestige'),
}
# The inputs fish caught takes beyond fish catch (calculator G38, F38, P23, P24, B55).
FISH_INPUTS = ('boats', 'Bait Lover', 'Bait Helper', 'Boost Booster', 'Fishing Frenzy', 'Duplicator',
               'Duplicator 2.0')
# The inputs every chest number takes beyond treasure chance and quality (calculator J45).
CHEST_INPUTS = ('Super Crates',)


def round_half_up(value):
    """Google Sheets ROUND: half away from zero (Python's round is banker's)."""
    return floor(value + 0.5)


def max_charm_level(prestige):  # calc K6
    return 10 + prestige if prestige < 100 else 2 * prestige - 88


def _up(account, name):
    return account.upgrades.get(name, 0)


def _pu(account, name):
    return account.prestige_upgrades.get(name, 0)


def _charm(account, name):
    return account.charms.get(name, 0)


def level_sell_multiplier(level, data):  # calc K7
    multiplier = 1.0
    for start, value in data['level_sell_multiplier']:
        if level >= start:
            multiplier = value
    return multiplier


def marketing(level):  # calc G6
    return 1.05 ** level if level <= 50 else 1.05 ** 50 * (1 + 0.05 * (level - 50))


def bait_strength(account):  # calc C18 × C29
    return (1 + 0.15 * _up(account, 'Bait Lover')) * (1 + 0.1 * _up(account, 'Bait Helper'))


def _bait(setup, data, stat):
    return data['baits'][setup.bait].get(stat, 0.0) if setup.bait else 0.0


def bait_effect(stat, account, setup, data):  # calc P6:P10
    k = _bait(setup, data, stat)
    strength = bait_strength(account)
    return 1 + k * strength if k > 0 else (1 + k / strength if k < 0 else 1.0)


def pet_effect(stat, account, setup, data):  # calc P16:P20, with P11
    if setup.pet is None:
        return 1.0
    k = data['pets'][setup.pet].get(stat, 0.0)
    effectiveness = 1 + _bait(setup, data, 'pet_effectiveness') * bait_strength(account)
    return 1 + k * (0.1 + 0.0075 * account.pets.get(setup.pet, 0)) * effectiveness


def cooldown(account, setup, data):  # calc B50
    raw = (3.5 + data['biomes'][setup.biome]['cooldown_add'] - 0.25 * account.boats
           - 0.05 * _charm(account, 'Haste'))
    return max(2.0, min(raw, 5.0))


def check_known(setup, data):
    for kind, name, table in (('rod', setup.rod, 'rods'), ('biome', setup.biome, 'biomes'),
                              ('bait', setup.bait, 'baits'), ('pet', setup.pet, 'pets')):
        if name is not None and name not in data[table]:
            raise NotModelled(f'Not modelled: unknown {kind} {name}.')


def _rod_note(setup, data):
    """A rod the game data does not list for the biome is still modelled, but everything it yields is an estimate:
    the wiki's lists may be incomplete, and the game accepted the setup."""
    if setup.biome in data['rods'][setup.rod]['biomes']:
        return Value(None)
    return Value(None, 'estimate', (f'{setup.rod} is not listed for {setup.biome}',))


def _labelled(value, inputs, account, setup=None, data=None, stat=None):
    """The value, an estimate when an assumed input has an effect on it with this setup."""
    why = tuple(f'{name}: {account.assumed[name]}' for name in inputs
                if name in account.assumed and (setup is None or _has_effect(name, stat, setup, data)))
    return Value(value, 'estimate' if why else 'formula', why)


# The bait and pet key each buff reads; 'bonus_fish' stands for fish caught's own inputs.
STAT_KEYS = {'fish_catch': 'fish_catch', 'fish_quality': 'fish_quality', 'treasure_chance': 'treasure_chance',
             'treasure_quality': 'treasure_quality', 'xp_multiplier': 'xp'}


def _has_effect(name, stat, setup, data):
    """Whether an input changes this stat with this setup: Bait Lover and Bait Helper only through the bait (or
    its pet effectiveness), boost upgrades only with their boost, the pet's level only for what the pet gives."""
    bait = data['baits'][setup.bait] if setup.bait else {}
    pet = data['pets'][setup.pet] if setup.pet else {}
    if name in ('Bait Lover', 'Bait Helper'):
        return bool(bait.get(stat) or (pet.get(stat) and bait.get('pet_effectiveness')))
    if name in ('Boost Booster', 'Fishing Frenzy'):
        if stat in ('bonus_fish', 'fish_catch'):  # calc P23, P24; B39 with the wiki's Fishing Frenzy term
            return setup.fish_boost
        if stat == 'treasure_chance':  # calc B41
            return setup.treasure_boost
    if name == 'pet':
        return bool(pet.get(stat))
    return True


def buffs(account, setup, data, corrections=None):
    """The /buffs multipliers and cooldown, plus treasure and charm quantity (calculator B38:B50). Each buff is
    multiplied by its buff correction (game ÷ model from /buffs) when one is given."""
    check_known(setup, data)
    stat = 1 + 0.02 * _up(account, 'Statistician')
    ties = 1 + 0.1 * _pu(account, 'International Ties')
    fisher = _pu(account, 'Virtual Fisher')
    booster, frenzy = _up(account, 'Boost Booster'), _up(account, 'Fishing Frenzy')
    clan = account.clan
    values = {
        'sell_price': ((1 + 0.05 * _up(account, 'Salesman')) * (1 + 0.05 * _up(account, 'Fish Ovens'))
                       * (1 + 0.15 * _up(account, 'Ultimate Salesman')) * stat * marketing(_charm(account, 'Marketing'))
                       * (1 + 0.1 * clan.get('sell price', 0)) * ties * (1 + 0.4 * _pu(account, 'Business Education'))
                       * (1 + 0.2 * fisher) * level_sell_multiplier(account.level, data)),  # B38
        'fish_catch': ((1 + 0.05 * _up(account, 'Aquatic Expert')) * stat * (1 + 0.025 * _charm(account, 'Endurance'))
                       * (1 + 0.05 * clan.get('fish catch', 0)) * ties * (1 + 0.25 * _pu(account, 'Fish Whisperer'))
                       * bait_effect('fish_catch', account, setup, data)
                       * pet_effect('fish_catch', account, setup, data)
                       # Fish boost: + 0.02 per Fishing Frenzy level from the miraheze Temporary_Buff page (the
                       # calculator has none; the Upgrade page's +0.05 is not used).
                       * (1.4 + 0.02 * booster + 0.02 * frenzy if setup.fish_boost else 1)
                       * (1.05 if account.official_server else 1) * 0.95),  # B39
        'fish_quality': ((1 + 0.05 * _up(account, 'Better Fish')) * stat * ties * (1 + 0.4 * fisher)
                         * bait_effect('fish_quality', account, setup, data)
                         * pet_effect('fish_quality', account, setup, data)),  # B40
        'treasure_chance': ((1 + 0.05 * _up(account, 'More Chests')) * stat * (1 + 0.025 * _charm(account, 'Treasure'))
                            * ties * bait_effect('treasure_chance', account, setup, data)
                            * pet_effect('treasure_chance', account, setup, data)
                            * (1.5 + 0.05 * (booster + frenzy) if setup.treasure_boost else 1)),  # B41
        'treasure_quality': ((1 + 0.1 * _up(account, 'Better Chests')) * stat * (1 + 0.025 * _charm(account, 'Quality'))
                             * ties * bait_effect('treasure_quality', account, setup, data)
                             * pet_effect('treasure_quality', account, setup, data)
                             * (1.5 if setup.event else 1) * clan.get('treasure quality', 1)),  # B43
        # Highly Experienced is +15% per level on the miraheze Upgrade page (the calculator has +20%).
        'xp_multiplier': ((1 + 0.1 * _up(account, 'Experienced')) * (1 + 0.15 * _up(account, 'Highly Experienced'))
                          * stat * (1 + 0.05 * _charm(account, 'Experience')) * (1 + 0.05 * clan.get('xp', 0))
                          * ties * (1 + 0.35 * _pu(account, 'Ancient One')) * (1 + 0.2 * fisher)
                          * bait_effect('xp', account, setup, data) * pet_effect('xp', account, setup, data)),  # B44
        'fishing_cooldown': cooldown(account, setup, data),  # B50
        'treasure_quantity': 1 + 0.1 * _up(account, 'Artifact Specialist') + 0.05 * _charm(account, 'Quantity'),  # B46
        'charm_quantity': (1 + 0.025 * _up(account, 'Charmer')) * (1 + 0.1 * (account.prestige // 5)),  # B47
    }
    corrections = corrections or {}
    return {name: _labelled(value * corrections.get(name, 1.0), BUFF_INPUTS[name], account, setup, data,
                            STAT_KEYS.get(name))
            for name, value in values.items()}


def fish_shares(base, quality):
    """Calc F41:F45: best tier first, each capped by what is left. The bottom tier takes the rest, as a catch is
    always all fish; for quality 1 and above this is F41's own cap, below 1 F41 would leave shares summing to less."""
    shares = [0.0] * len(base)
    left = 1.0
    for index in reversed(range(1, len(base))):
        shares[index] = min(base[index] * quality, left)
        left -= shares[index]
    shares[0] = left
    return shares


def base_shares(setup, data, learned=None):
    """(base shares of the five tiers, source, why). Measured shares win, then the rod's own, then the known rod in
    that biome with the closest mean catch, then the calculator's example shares."""
    key = f'{setup.rod}|{setup.biome}'
    if learned and key in learned:
        found = learned[key]
        return found['shares'], 'measured', (f'fish mix measured over {found["trips"]:,} trips',)
    rods = data['rods']
    if setup.biome in rods[setup.rod]['shares']:
        return rods[setup.rod]['shares'][setup.biome], 'formula', ()
    candidates = [name for name, rod in rods.items() if setup.biome in rod['shares']]
    if candidates:
        mean = rods[setup.rod]['mean_catch']
        nearest = min(candidates, key=lambda name: (abs(rods[name]['mean_catch'] - mean), name))
        return (rods[nearest]['shares'][setup.biome], 'estimate',
                (f'fish mix of {setup.rod} in {setup.biome} estimated from {nearest}',))
    return (data['example_shares'], 'estimate',
            (f'fish mix of {setup.rod} in {setup.biome} estimated from the calculator example',))


def trip(account, setup, data, learned=None, chest_scales=None, corrections=None):
    """Expected values for one fishing trip (calculator rows 38-56), with the buff corrections applied."""
    found = buffs(account, setup, data, corrections)
    rod, biome = data['rods'][setup.rod], data['biomes'][setup.biome]
    booster, frenzy = _up(account, 'Boost Booster'), _up(account, 'Fishing Frenzy')
    # Bait bonus fish (calc P12): the miraheze Bait page multiplies Bait Lover and Bait Helper together, and the Rod
    # page's catch formula rounds the result ("CNT += BA (rounded)"), half away from zero.
    bait_bonus = round_half_up((data['baits'][setup.bait].get('bonus_fish', 0) if setup.bait else 0)
                               * bait_strength(account))
    supporter = biome['supporter_multiplier'] if rod.get('scales_with_biome') else 1.0  # L32
    boosters = 1 + (0.5 if setup.global_booster else 0) + (0.75 if setup.personal_booster else 0)  # P27
    before_boost = ((found['fish_catch'].value * rod['mean_catch'] + account.boats + bait_bonus)
                    * supporter * biome['fish_multiplier'] * boosters)  # G38
    boost_fish = biome['fish_boost_fish'] * 2 + 1 + frenzy + booster if setup.fish_boost else 0  # P23
    boost_multiplier = 1.05 + 0.01 * (booster + frenzy) if setup.fish_boost else 1  # P24
    duplication = 0.02 * _up(account, 'Duplicator') + 0.03 * _up(account, 'Duplicator 2.0')  # B55
    caught = ((before_boost + boost_fish) * boost_multiplier * (1.5 if setup.event else 1)
              * (1.5 if setup.prestige_buff else 1) * (1 + duplication))  # F38
    base, mix_source, mix_why = base_shares(setup, data, learned)
    shares = fish_shares(base, found['fish_quality'].value)
    tiers = biome['tiers']
    mix = Value(None, mix_source, mix_why)
    listed = _rod_note(setup, data)
    fish = Value(caught, *worst(found['fish_catch'], _labelled(None, FISH_INPUTS, account, setup, data, 'bonus_fish'),
                                listed))
    money = Value(found['sell_price'].value * caught * sum(s * t['price'] for s, t in zip(shares, tiers)),
                  *worst(found['sell_price'], found['fish_catch'], found['fish_quality'], mix, listed))  # G41:G45
    xp = Value(found['xp_multiplier'].value * caught * sum(s * t['xp'] for s, t in zip(shares, tiers)),
               *worst(found['xp_multiplier'], found['fish_catch'], found['fish_quality'], mix, listed))  # H41:H45
    result = {'buffs': found, 'fish': fish, 'shares': [{'fish': t['fish'], 'share': s} for s, t in zip(shares, tiers)],
              'mix_source': mix_source, 'mix_why': mix_why, 'money_fish': money, 'xp_fish': xp}
    result.update(chests(account, setup, data, found, chest_scales))
    return result


PER_TRIP = ('fish', 'money_fish', 'xp_fish', 'chest_chance', 'super_crate', 'gold', 'emerald', 'lava', 'diamond',
            'charms', 'money_chests', 'xp_chests')


def chests(account, setup, data, found, chest_scales=None):
    """Chests, exotic fish, charms and super crates per trip (calculator B42, J39:J50, K39:K44, M46:P53, N40:N44,
    L53:L56), as the calculator has them."""
    rod, biome = data['rods'][setup.rod], data['biomes'][setup.biome]
    table = data['chests']
    quality = found['treasure_quality'].value * rod['treasure_quality']  # C43
    chance = min(1.0, found['treasure_chance'].value * rod['treasure_chance'])  # B42
    supers = _up(account, 'Super Crates')
    super_chance = supers * 0.00025 * quality  # J45
    left, tiers = 1.0, {}
    for tier in data['chest_order']:  # K39:K44, best tier first
        share = min(table[tier]['share_per_quality'] * quality, left)
        left -= share
        tiers[tier] = share * chance * (1 - super_chance)  # J39:J44
    quantity = found['treasure_quantity'].value
    lava_drop, diamond_drop = biome['lava_drop'], biome['diamond_drop']

    def drops(kind, extra=1.0):
        if extra <= 0:
            return 0.0
        return sum((table[t]['a'] * quantity * extra + 1) * table[t][kind] * tiers[t] for t in tiers)
    exotic_chance = 0.5 - 0.05 * supers if supers else 0.0  # J49
    money_chance = 0.05 - 0.01 * supers if supers else 0.0  # J48
    from_super = (12 * quantity + 1) * super_chance * exotic_chance + exotic_chance * super_chance  # M52 + L48*J45
    gold = drops('gold') + from_super  # N40
    emerald = drops('emerald') + from_super  # N41
    lava = drops('lava', lava_drop) + from_super  # N42
    diamond = drops('diamond', diamond_drop) + from_super  # N43
    charm_quantity = found['charm_quantity'].value
    c47, c48 = round_half_up(charm_quantity), round_half_up(3 * charm_quantity)
    artifact_cq = 0.1875 * (c47 ** 2 - 2 * c47 * charm_quantity - c48 ** 2 + 6 * c48 * charm_quantity) / charm_quantity  # B48
    d47, d48 = round_half_up(2.5 * charm_quantity), round_half_up(7.5 * charm_quantity)
    super_cq = (d47 ** 2 - 5 * d47 * charm_quantity - d48 ** 2 + 15 * d48 * charm_quantity) / (10 * charm_quantity)  # B49
    slots = sum(1 for kind in CHARM_KINDS if _charm(account, kind) < max_charm_level(account.prestige)) / 8  # B54
    unlocked = 1 if account.level >= 20 else 0  # J12
    super_charm = 0.45 + 0.06 * supers  # C30
    charms = (unlocked * charm_quantity * slots * (0.02 * tiers['rare'] + 0.25 * tiers['epic']
                                                    + 0.4 * tiers['legendary'])
              + unlocked * slots * (artifact_cq * tiers['artifact'] * 0.75 + super_cq * super_charm * super_chance))  # N44
    item_chance = (sum(table[t]['gold'] * tiers[t] for t in tiers) + sum(table[t]['emerald'] * tiers[t] for t in tiers)
                   + (1 if biome['index'] >= 2 else 0) * sum(table[t]['lava'] * tiers[t] for t in tiers)
                   + (1 if biome['index'] >= 3 else 0) * sum(table[t]['diamond'] * tiers[t] for t in tiers)
                   + unlocked * (sum(table[t]['charm'] * tiers[t] for t in tiers)
                                 + (0.45 + 0.06 * supers if supers else 0) * super_chance))  # J53:J55, L53:L54
    money_or_xp = 0.5 * (1 - super_chance) * (chance - item_chance) + super_chance * money_chance  # L56
    total = sum(tiers.values())
    mean_multiplier = sum(tiers[t] * table[t]['multiplier'] for t in tiers) / total if total else 0.0
    chest_inputs = _labelled(None, CHEST_INPUTS, account)
    listed = _rod_note(setup, data)
    sources = worst(found['treasure_chance'], found['treasure_quality'], chest_inputs, listed)
    exotic = worst(found['treasure_chance'], found['treasure_quality'], found['treasure_quantity'], chest_inputs,
                   listed)
    scales = chest_scales or {}

    def rewards(kind):
        scale = scales.get(kind)
        if scale is None:
            return Value(None, *worst(Value(None, 'not modelled', (f'chest {kind} is not fitted from the logs yet',)),
                                      listed))
        return Value(money_or_xp * mean_multiplier * scale,
                     *worst(Value(None, 'estimate', (f'chest {kind} fitted from the logs',)), listed))
    return {'chest_chance': Value(total, *sources), 'chest_tiers': tiers,
            'super_crate': Value(super_chance, *sources),
            'gold': Value(gold, *exotic), 'emerald': Value(emerald, *exotic), 'lava': Value(lava, *exotic),
            'diamond': Value(diamond, *exotic), 'charms': Value(charms, *worst(found['treasure_chance'],
                                                                             found['treasure_quality'],
                                                                             found['charm_quantity'], chest_inputs,
                                                                             listed)),
            'money_chests': rewards('money'), 'xp_chests': rewards('xp')}


def per_hour(result, seconds_per_trip):
    """Per-trip values as hourly rates. Seconds per trip may be a Value: an estimated one makes every rate an
    estimate."""
    seconds = seconds_per_trip if isinstance(seconds_per_trip, Value) else Value(seconds_per_trip)
    trips = 3600 / seconds.value
    return {name: Value(None if result[name].value is None else result[name].value * trips,
                        *worst(result[name], seconds)) for name in PER_TRIP}
