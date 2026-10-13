"""This account's saved shapes (2026-10-10) for planner tests."""
from src.plan_state import from_run

CHARMS = ('0/14 - :charm_yellow: Marketing Charm - Sell fish for more. (1 to next tier).\n'
          '1/14 - :charm_pink: Endurance Charm - Catch more fish. (2 to next tier).\n'
          '1/14 - :charm_red: Haste Charm - Fish more often. (2 to next tier).\n'
          '1/14 - :charm_white: Quantity Charm - Increase amount of items found in chests. (1 to next tier).\n'
          '1/14 - :charm_cyan: Worker Charm - Workers catch more fish. (2 to next tier).\n'
          '0/14 - :charm_lblue: Treasure Charm - Catch slightly more treasure. (1 to next tier).\n'
          '2/14 - :charm_green: Quality Charm - Increase quality of treasure. (2 to next tier).\n'
          '1/14 - :charm_blue: Experience Charm - Increase experience from all fish. (1 to next tier).\n'
          '\n11/840 - Charms found.')


def account_state():
    item = lambda level, top: {'level': level, 'max': top, 'price': None}
    return {'sections': {
        'shop_upgrades': {'values': {'items': {
            'Better Fish': item(7, 21), 'Salesman': item(7, 20), 'Bait Efficiency': item(5, 9),
            'More Chests': item(3, 11), 'Worker Motivation': item(1, 12), 'Artifact Specialist': item(3, 7),
            'Experienced': item(0, 5), 'Better Chests': item(0, 5), 'Better Dailies': item(2, 10)}}},
        'rod': {'values': {'current': 'Fiberglass Rod',
                           'owned': ['Supporter Rod', 'Plastic Rod', 'Steel Rod', 'Fiberglass Rod']}},
        'biome': {'values': {'current': 'River', 'unlocked': ['Volcanic'], 'next': 'Ocean', 'next_level': 100}},
        'shop_boats': {'values': {'items': {'Rowboat': {'owned': True}, 'Fishing Boat': {'owned': False}}}},
        'pet': {'values': {'current': 'Dolphin', 'owned': {
            'Puffer': {'level': 30}, 'Dolphin': {'level': 52}, 'Zebrafish': {'level': 61},
            'Axolotl': {'level': 60}, 'Tuna': {'level': 14}}}},
        'prestige_shop': {'values': {'azure_fish': 0, 'upgrades': {
            'International Ties': True, 'Business Education': True, 'Fish Whisperer': True, 'Ancient One': True,
            'Virtual Fisher': False}}},
        'shop_league': {'values': {'items': {
            'Pet Helper': {'level': 0, 'max': 5, 'price': 50}, 'Bait Helper': {'level': 1, 'max': 5, 'price': 100},
            'Super Crates': {'level': 2, 'max': 5, 'price': 150}, 'Worker Crates': {'level': 2, 'max': 5, 'price': 150},
            'Fishing Frenzy': {'level': 0, 'max': 5, 'price': 50},
            'Duplicator 2.0': {'level': 0, 'max': 10, 'price': None}}, 'hooks': 11}},
        'bait': {'values': {'current': None, 'owned': {}}},
    }}


def synced(level=52, balance=442680, fish_value=120000):
    return {'profile': {'level': level, 'prestige': 4, 'balance': balance, 'fish_value': fish_value,
                        'xp_current': 1000, 'xp_required': 20000, 'rod': 'Fiberglass Rod', 'biome': 'River',
                        'bait': None, 'pet': 'Dolphin', 'pet_level': 52},
            'inventory': {'exotic_fish': {'Gold Fish': 40, 'Emerald Fish': 9, 'Lava Fish': 3}}}


def state(**changes):
    built = from_run(account_state(), synced(**changes), CHARMS)
    assert not isinstance(built, str), built
    return built
