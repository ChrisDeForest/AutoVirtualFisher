"""Levels, money and prestige requirements for the planner (docs/progression-model.md §1, §4, §5)."""
import math
from functools import lru_cache

from .game_model import max_charm_level
from .player_planner import load_level_increments, xp_required

# (first level, last level, money for reaching level L)
LEVEL_MONEY = (
    (2, 14, lambda L: 50 * L), (15, 29, lambda L: 100 * L), (30, 39, lambda L: 200 * L),
    (40, 49, lambda L: 400 * L), (50, 62, lambda L: 1600 * L),
    (63, 99, lambda L: math.floor(1.6 * L) * 1000), (100, 149, lambda L: math.floor(6.4 * L) * 1000),
    (150, 199, lambda L: math.floor(2.56 * L) * 10000), (200, 249, lambda L: math.floor(1.024 * L) * 100000),
    (250, 488, lambda L: math.floor(2.048 * L) * 100000), (489, None, lambda L: math.floor(0.2048 * L) * 1000000),
)
PRESTIGE_MONEY = (5e9, 20e9, 50e9, 100e9, 250e9, 500e9,                     # P0-P5
                  1e12, 2e12, 4e12, 8e12, 13e12, 18e12, 25e12, 32e12, 40e12, 50e12, 75e12, 100e12, 125e12,
                  150e12, 200e12, 250e12, 320e12, 450e12, 600e12, 750e12, 900e12)  # P6-P26


def level_up_money(level: int) -> int:
    for first, last, rule in LEVEL_MONEY:
        if level >= first and (last is None or level <= last):
            return int(rule(level))
    return 0


def charms_for_level(level: int) -> int:
    return level * (level + 1) // 2


def level_for_charms(count: float) -> int:
    # the hair goes on before the floor, so a float sum a hair under a triangular number still reaches its level
    return int((math.sqrt(8 * count + 1) - 1) / 2 + 1e-9)


def charm_requirement(prestige: int) -> int:
    return 8 * charms_for_level(max_charm_level(prestige))


def prestige_requirements(prestige: int):
    """(level, money, charms) to prestige from `prestige`, or None beyond the known table (P26)."""
    if prestige >= len(PRESTIGE_MONEY):
        return None
    level = 250 + 50 * prestige if prestige <= 3 else 100 + 100 * prestige
    return level, int(PRESTIGE_MONEY[prestige]), charm_requirement(prestige)


@lru_cache(maxsize=1)
def _increments():
    return tuple(load_level_increments() or ())


def increments() -> list[int]:
    return list(_increments())



@lru_cache(maxsize=1)
def _xp_totals():
    """totals[L] = XP to go from the start of level 1 to the start of level L + 1."""
    totals, running, required = [0], 0, 0
    for step in _increments():
        required += step          # xp_required(level) for the next level
        running += required
        totals.append(running)
    return tuple(totals)


def xp_to_level(state, target: int) -> float:
    """XP still to earn from `state` (state.xp into its level) until reaching level `target`."""
    if target <= state.level:
        return 0.0
    totals = _xp_totals()
    last = len(totals) - 1
    start, end = min(state.level, last), min(target - 1, last)
    between = totals[end] - totals[start]                     # levels level + 1 .. target - 1
    return max(0.0, (xp_required(state.level, increments()) or 0) - state.xp) + between
