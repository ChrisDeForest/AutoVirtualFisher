"""What the planner simulates: the account, money, fish, exotic fish and charms (progression part 3)."""
import copy
import dataclasses
from dataclasses import dataclass, field

from .game_model import CHARM_KINDS, Account, Setup, max_charm_level
from .game_model_inputs import build_inputs
from .live_stats import parse_charms
from .plan_economy import charms_for_level, level_for_charms

EXOTIC = (('Gold', 'Gold Fish'), ('Emerald', 'Emerald Fish'), ('Lava', 'Lava Fish'), ('Diamond', 'Diamond Fish'))


@dataclass
class PlanState:
    account: Account
    setup: Setup
    rods: set
    xp: float
    cash: float
    fish_value: float
    exotic: dict
    charm_counts: dict
    hooks: int = 0
    league_prices: dict = field(default_factory=dict)
    bait_stock: dict = field(default_factory=dict)   # bait on hand by name, from the last /bait read
    maxed: dict = field(default_factory=dict)        # the level the game said an upgrade is maxed at, by name

    @property
    def level(self) -> int:
        return self.account.level

    def money(self) -> float:
        return self.cash + self.fish_value

    def charm_total(self) -> float:
        return sum(self.charm_counts.values())

    def copy(self) -> 'PlanState':
        return copy.deepcopy(self)

    def sync_charms(self) -> None:
        levels = {kind: level_for_charms(self.charm_counts.get(kind, 0)) for kind in CHARM_KINDS}
        self.account = dataclasses.replace(self.account, charms=levels)


def _values(account_state, kind):
    return ((account_state or {}).get('sections', {}).get(kind) or {}).get('values') or {}


def _charm_counts(account, charms_text, since):
    """Charms of each kind: the /charms reply's counts with the partial progress to the next tier ("(2 to next
    tier)"), plus the charms the run has found since that read (`since`, by charm name). A kind the reply did not
    show is counted from its level. Capped at the prestige's max."""
    read = parse_charms(charms_text or '')['charms']
    top = charms_for_level(max_charm_level(account.prestige))
    counts = {}
    for kind in CHARM_KINDS:
        name = f'{kind} Charm'
        counted = (read.get(name) or {}).get('counted')
        if counted is None:
            counted = charms_for_level(account.charms.get(kind, 0))
        counts[kind] = float(min(top, counted + (since or {}).get(name, 0)))
    return counts


def from_run(account_state, synced, charms_text, since=None):
    """The plan state from the run's reads, or a reason it cannot be planned yet. `since`: the charms found since
    /charms was read, by name (the Run tab adds the same ones)."""
    profile = (synced or {}).get('profile') or {}
    built = build_inputs(account_state, profile, charms_text)
    if isinstance(built, str):
        return built
    account, setup = built
    if profile.get('balance') is None:
        return 'the balance has not been read from /profile yet'
    rods = set(_values(account_state, 'rod').get('owned') or [])
    rods.add(setup.rod)
    exotic_read = ((synced or {}).get('inventory') or {}).get('exotic_fish') or {}
    league = _values(account_state, 'shop_league')
    state = PlanState(
        account=account, setup=setup, rods=rods, xp=float(profile.get('xp_current') or 0),
        cash=float(profile['balance']), fish_value=float(profile.get('fish_value') or 0),
        exotic={short: int(exotic_read.get(name) or 0) for short, name in EXOTIC},
        charm_counts=_charm_counts(account, charms_text, since),
        hooks=int(league.get('hooks') or 0),
        # a maxed league upgrade is left out; one whose price was not read stays in with None
        league_prices={name: item.get('price') for name, item in (league.get('items') or {}).items()
                       if (item.get('max') is None or (item.get('level') or 0) < item['max'])
                       and (item.get('level') or 0) < (account_state or {}).get('maxed', {}).get(name, float('inf'))},
        maxed=dict((account_state or {}).get('maxed') or {}),
        bait_stock={name: int(count) for name, count in (_values(account_state, 'bait').get('owned') or {}).items()
                    if count})
    if since:
        state.sync_charms()   # charms found since the read may have reached a new level
    return state
