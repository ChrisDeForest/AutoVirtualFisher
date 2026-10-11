"""The planner's tables (progression part 3): prices and route rules, checked when loaded."""
import json
from pathlib import Path

REFERENCE = Path(__file__).resolve().parent / 'reference'
PRICE_TABLES = ('upgrades', 'boats', 'special', 'boosts', 'bait_unlock', 'biome_unlock')
ROUTE_TABLES = ('phases', 'phase_labels', 'standard_rods', 'supporter_bands', 'salesman_before_rod', 'bait',
                'pet_order', 'charm_grind', 'phase_steps', 'boosts', 'special_orders', 'prestige_shop',
                'league_order', 'tie_break', 'no_mrp', 'five_percent', 'full_sell_boats_from')


class PlanDataError(Exception):
    """The planner's tables are missing or malformed."""


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise PlanDataError(f'{Path(path).name} could not be read: {error}') from error


def _rising(name, prices):
    if not prices or any(not isinstance(p, (int, float)) or p <= 0 for p in prices) \
            or any(b < a for a, b in zip(prices, prices[1:])):
        raise PlanDataError(f'prices for {name} must be positive and never fall')


def load_plan_data(prices_path=None, route_path=None) -> dict:
    prices = _read(prices_path or REFERENCE / 'prices.json')
    route = _read(route_path or REFERENCE / 'route.json')
    for table in PRICE_TABLES:
        if table not in prices:
            raise PlanDataError(f'prices.json has no {table} table')
    for table in ROUTE_TABLES:
        if table not in route:
            raise PlanDataError(f'route.json has no {table} table')
    for name, levels in prices['upgrades'].items():
        _rising(name, levels)
    for name, item in prices['special'].items():
        _rising(name, item.get('prices'))
        if item.get('currency') not in ('Gold', 'Emerald', 'Lava', 'Diamond'):
            raise PlanDataError(f'special upgrade {name} has no exotic currency')
    _rising('boats', [boat[1] for boat in prices['boats']])
    if route['phases'][-1] != 'prestige':
        raise PlanDataError('route.json phases must end with prestige')
    return {'prices': prices, 'route': route}
