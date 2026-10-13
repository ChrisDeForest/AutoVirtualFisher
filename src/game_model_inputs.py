"""The game model's Account and Setup, built from what the run has read: the saved account state (part 1), the
synced /profile and the /charms reply. Anything not read yet is assumed (level 0) and named."""
from .game_model import CHARM_KINDS, Account, Setup
from .live_stats import parse_charms

NORMAL_UPGRADES = ('Better Fish', 'Salesman', 'Bait Efficiency', 'More Chests', 'Worker Motivation',
                   'Artifact Specialist', 'Experienced', 'Better Chests', 'Better Dailies')
SPECIAL_UPGRADES = ('Fish Ovens', 'Bait Lover', 'Aquatic Expert', 'Worker Extender', 'Ultimate Salesman',
                    'Highly Experienced', 'Boost Booster', 'Statistician', 'Duplicator', 'Charmer')
LEAGUE_UPGRADES = ('Super Crates', 'Bait Helper', 'Pet Helper', 'Worker Crates', 'Fishing Frenzy', 'Duplicator 2.0')
PRESTIGE_UPGRADES = ('International Ties', 'Business Education', 'Fish Whisperer', 'Ancient One', 'Virtual Fisher')


def _values(state, kind):
    return ((state.get('sections') or {}).get(kind) or {}).get('values') or {}


def _unread(state, kind):
    return ((state.get('sections') or {}).get(kind) or {}).get('unread') or []


def _levels(state, kind, names, assumed):
    section = (state.get('sections') or {}).get(kind)
    items = _values(state, kind).get('items') or {}
    levels = {}
    for name in names:
        level = (items.get(name) or {}).get('level')
        if level is not None:
            levels[name] = level
        else:
            levels[name] = 0
            assumed[name] = 'not read yet' if section is None or name in _unread(state, kind) else 'not shown'
    return levels


def _no_none(name):
    """The replies show "None" for no bait or no pet."""
    return name if name and name.upper() != 'NONE' else None


def build_inputs(account_state, profile, charms_text):
    """(Account, Setup), or the reason they cannot be built yet."""
    profile = profile or {}
    level = profile.get('level')
    if level is None:
        return 'The level is not read yet (/profile).'
    prestige = profile.get('prestige') or 0
    rod = profile.get('rod') or _values(account_state, 'rod').get('current')
    biome = profile.get('biome') or _values(account_state, 'biome').get('current')
    if not rod or not biome:
        return 'The rod and biome are not read yet (/profile, /rod, /biome).'
    assumed = {}
    upgrades = {**_levels(account_state, 'shop_upgrades', NORMAL_UPGRADES, assumed),
                **_levels(account_state, 'shop_league', LEAGUE_UPGRADES, assumed)}
    if _values(account_state, 'shop_special'):
        # A read special shop lists what is unlocked; the rest are not unlocked yet (level 0).
        specials = _levels(account_state, 'shop_special', SPECIAL_UPGRADES, assumed)
        upgrades.update(specials)
        for name in SPECIAL_UPGRADES:
            if assumed.get(name) == 'not shown':
                del assumed[name]
    else:
        for name in SPECIAL_UPGRADES:
            upgrades[name] = 0
            if level >= 50:
                assumed[name] = 'the special shop is not read yet'
    for name in NORMAL_UPGRADES + LEAGUE_UPGRADES:
        if name in assumed and assumed[name] == 'not shown':
            del assumed[name]  # a shop that was read and does not list it: not bought
    cap = 1 + prestige // 5
    owned = _values(account_state, 'prestige_shop').get('upgrades') or {}
    prestige_upgrades = {}
    for name in PRESTIGE_UPGRADES:
        if owned.get(name):
            prestige_upgrades[name] = cap
            if prestige >= 5:  # below P5 the cap is 1, so an owned upgrade's level is known
                assumed[name] = f'assumed at the cap ({cap}): /prestige shop does not show the level'
        else:
            prestige_upgrades[name] = 0
            if name not in owned:
                assumed[name] = 'the prestige shop is not read yet'
            elif prestige >= 5:
                assumed[name] = 'its level below the cap is not shown'
    read = parse_charms(charms_text or '')['charms']
    charms = {}
    for kind in CHARM_KINDS:
        found = read.get(f'{kind} Charm')
        charms[kind] = found['level'] if found else 0
        if not found:
            assumed[kind] = '/charms is not read yet' if not charms_text else 'not read from /charms'
    boat_items = _values(account_state, 'shop_boats').get('items') or {}
    boats = sum(1 for item in boat_items.values() if item.get('owned'))
    if not boat_items:
        assumed['boats'] = 'the boat shop is not read yet (/shop boats)'
    pets = {name: info['level'] for name, info in (_values(account_state, 'pet').get('owned') or {}).items()
            if info.get('level') is not None}
    pet = _no_none(profile.get('pet') or _values(account_state, 'pet').get('current'))
    if pet and profile.get('pet_level') is not None:
        pets[pet] = profile['pet_level']
    if pet and pet not in pets:
        assumed['pet'] = f'the level of {pet} is not read yet (/pet)'
    bait = profile.get('bait') if profile.get('bait') else _values(account_state, 'bait').get('current')
    boosts = _values(account_state, 'boosts')
    active = ' '.join(boosts.get('active') or {}).casefold()
    account = Account(level=level, prestige=prestige, charms=charms, upgrades=upgrades,
                      prestige_upgrades=prestige_upgrades, boats=boats, pets=pets, assumed=assumed)
    setup = Setup(rod=rod, biome=biome, bait=_no_none(bait), pet=pet,
                  fish_boost='fish' in active, treasure_boost='treasure' in active,
                  global_booster=bool(boosts.get('global_duration')), personal_booster='personal' in active)
    return account, setup
