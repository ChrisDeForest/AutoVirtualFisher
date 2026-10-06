"""Plan levels and quests from saved snapshots, fishing sessions, and the wiki level table."""
from datetime import datetime, timedelta, timezone
import json
from math import ceil
from pathlib import Path
import re

from .fishing_sessions import DEFAULT_SESSION_ROOT, fishing_totals
from .player_snapshots import DEFAULT_SNAPSHOT_ROOT, load_history

LEVEL_REFERENCE_PATH = Path(__file__).parent / 'reference' / 'level_xp.json'
CHEST_NAME = re.compile(r'\b(box|chest|crate)\b', re.I)


def load_level_increments(path: Path = LEVEL_REFERENCE_PATH) -> list[int] | None:
    try:
        increments = json.loads(Path(path).read_text(encoding='utf-8'))['xp_increment']
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    return increments if isinstance(increments, list) else None


def xp_required(level: int, increments: list[int]) -> int | None:
    """XP needed to advance from `level`: the running total of the wiki column through that level."""
    if not increments or level < 1 or level > len(increments):
        return None
    return sum(increments[:level])


def xp_between(level: int, xp_to_next: int, target: int, increments: list[int]) -> int | None:
    """XP needed to go from the current position in `level` to reaching `target`."""
    if target <= level:
        return None
    total = xp_to_next
    for passing in range(level + 1, target):
        requirement = xp_required(passing, increments)
        if requirement is None:
            return None
        total += requirement
    return total


def _trips(amount, rate):
    return ceil(amount / rate) if amount is not None and rate else None


def _model_check(snapshots, increments):
    matches, mismatches = 0, []
    for snapshot in snapshots:
        profile = snapshot['state'].get('profile', {})
        level, observed = profile.get('level'), profile.get('xp_required')
        if not isinstance(level, int) or not isinstance(observed, int):
            continue
        expected = xp_required(level, increments)
        if expected == observed:
            matches += 1
        else:
            mismatches.append({'level': level, 'observed': observed, 'expected': expected})
    return {'matches': matches, 'mismatches': mismatches, 'usable': bool(increments) and not mismatches}


def _quest_estimate(quest, totals, level, xp_to_next, xp_per_trip, increments, usable):
    remaining = quest['goal'] - quest['progress']
    if remaining <= 0:
        return 'complete', None, 'Complete'
    task = quest.get('task') or ''
    trips = totals['trips']
    if re.search(r'\bcatch\b.*\bfish\b', task, re.I):
        rate, unit = totals['fish_per_trip'], 'fish'
    elif re.search(r'\bfind\b.*\bchests?\b', task, re.I):
        found = sum(count for name, count in totals['finds'].items() if CHEST_NAME.search(name))
        rate, unit = (found / trips if trips else None), 'chests'
    elif re.search(r'\blevel up\b', task, re.I):
        if level is None or xp_to_next is None or (remaining > 1 and not usable):
            return 'no_data', None, 'Needs a full snapshot with level and XP'
        needed = xp_between(level, xp_to_next, level + remaining, increments) if remaining > 1 else xp_to_next
        if not xp_per_trip or needed is None:
            return 'no_data', None, 'Needs a saved fishing session'
        return 'estimate', _trips(needed, xp_per_trip), f'{needed:,} XP at {xp_per_trip:,.2f} per trip'
    elif re.search(r'\bpets?\b', task, re.I):
        return 'no_data', None, 'No pet finds in your fishing sessions yet'
    else:
        return 'unknown_task', None, 'This quest type is not estimated yet'
    if not rate:
        return 'no_data', None, f'No {unit} in your fishing sessions yet'
    return 'estimate', _trips(remaining, rate), f'{remaining:,} {unit} at {rate:,.2f} per trip'


def plan(account_name: str, snapshot_root: Path = DEFAULT_SNAPSHOT_ROOT,
         fishing_root: Path = DEFAULT_SESSION_ROOT, target: int | None = None,
         now: datetime | None = None, increments: list[int] | None = None) -> dict:
    increments = load_level_increments() if increments is None else increments
    history, warnings = load_history(account_name, snapshot_root)
    full = [item for item in history if item.get('validity') == 'full']
    totals = fishing_totals(account_name, fishing_root)
    warnings += totals['warnings']
    if not full:
        return {'status': 'needs_snapshot', 'warnings': warnings}
    latest = full[0]
    profile = latest['state'].get('profile', {})
    level, xp_to_next = profile.get('level'), profile.get('xp_to_next_level')
    xp_per_trip = totals['xp_per_trip']
    model = _model_check(full, increments)

    targets = []
    if isinstance(level, int) and isinstance(xp_to_next, (int, float)):
        wanted = [level + 1] + ([level + 5, level + 10] if model['usable'] else [])
        if target and model['usable'] and target > level:
            wanted.append(target)
        for goal in sorted(set(wanted)):
            needed = xp_to_next if goal == level + 1 else xp_between(level, xp_to_next, goal, increments)
            if needed is not None:
                targets.append({'level': goal, 'xp_needed': needed, 'trips': _trips(needed, xp_per_trip)})

    with_quests = next((item for item in history if any(
        item['state'].get('quests', {}).get(group) for group in ('daily', 'special'))), None)
    quests, quests_reset = [], False
    if with_quests:
        state_quests = with_quests['state']['quests']
        for group in ('daily', 'special'):
            for name, quest in (state_quests.get(group) or {}).items():
                status, trips, note = _quest_estimate(quest, totals, level, xp_to_next, xp_per_trip,
                                                      increments, model['usable'])
                quests.append({'group': group, 'name': name, 'progress': quest['progress'], 'goal': quest['goal'],
                               'task': quest.get('task'), 'status': status, 'trips': trips, 'note': note})
        minutes = state_quests.get('resets_in_minutes')
        if minutes is not None:
            reset_at = datetime.fromisoformat(with_quests['captured_at']) + timedelta(minutes=minutes)
            quests_reset = (now or datetime.now(timezone.utc)) >= reset_at
    return {'status': 'ok', 'as_of': latest['captured_at'], 'level': level, 'xp_to_next': xp_to_next,
            'xp_per_trip': xp_per_trip, 'fishing_trips': totals['trips'], 'model': model, 'targets': targets,
            'quests': quests, 'quests_as_of': with_quests['captured_at'] if with_quests else None,
            'quests_reset': quests_reset, 'warnings': warnings}
