"""Tally pasted Virtual Fisher fishing results. Pure parsing; no file or network access."""
from hashlib import sha256
import re

from .player_state import NUMBER, _clean, _name, _number

FISHING_SCHEMA_VERSION = 1
SKIPPED = (re.compile(r'You must wait .+ before fishing again\.?', re.I),
           re.compile(r'Your cooldown: .+', re.I))


def fingerprint(text: str) -> str:
    """Identify a pasted batch regardless of blank lines and surrounding spaces."""
    lines = [line.strip() for line in text.replace('\r', '').split('\n') if line.strip()]
    return sha256('\n'.join(lines).encode('utf-8')).hexdigest()


def _add(counts, name, amount):
    counts[name] = counts.get(name, 0) + amount


def parse_fishing(text: str) -> dict:
    tally = {'schema_version': FISHING_SCHEMA_VERSION, 'trips': 0, 'boosted_trips': 0, 'catches': {},
             'fishing_xp': 0, 'finds': {}, 'chest_contents': {}, 'charm_levels': {}, 'level_ups': [],
             'level_up_money': 0, 'unlocks': [], 'quests_completed': [], 'bait_ran_out': [],
             'warnings': [], 'unparsed_line_count': 0}
    mode = None
    # A line that is not recognized may be the message author line, which precedes "You caught:".
    pending = None

    def unrecognized(line):
        tally['warnings'].append(f'Unrecognized line: {line}')
        tally['unparsed_line_count'] += 1

    for original in text.splitlines():
        line = _clean(original)
        if not line:
            continue
        if line == 'You caught:':
            pending = None
            tally['trips'] += 1
            mode = 'catch'
            continue
        if pending is not None:
            unrecognized(pending)
            pending = None
        if mode == 'quest_name':
            mode = 'await_reward'
            quest = re.fullmatch(r'(.+?) Tier (\d+)', line)
            if quest:
                tally['quests_completed'].append({'quest': quest[1], 'tier': int(quest[2]),
                                                  'money': 0, 'xp': 0, 'items': {}})
            else:
                unrecognized(line)
            continue
        if mode == 'reward':
            reward = tally['quests_completed'][-1] if tally['quests_completed'] else None
            money = re.fullmatch(rf'\+\$({NUMBER})', line)
            xp = re.fullmatch(rf'\+({NUMBER}) XP', line, re.I)
            item = re.fullmatch(r'(\d+)x (.+)', line)
            if reward is not None and (money or xp or item):
                if money:
                    reward['money'] += _number(money[1])
                elif xp:
                    reward['xp'] += _number(xp[1])
                else:
                    _add(reward['items'], _name(item[2]), int(item[1]))
                continue
            mode = None
        if mode == 'catch':
            catch = re.fullmatch(r'(\d{1,3}(?:,\d{3})+|\d+)\s+(.+)', line)
            if catch and _name(catch[2]):
                _add(tally['catches'], _name(catch[2]), _number(catch[1]))
                continue
            mode = None
        if match := re.fullmatch(rf'\+({NUMBER}) XP', line, re.I):
            tally['fishing_xp'] += _number(match[1])
        elif re.fullmatch(r'Global boost by .+', line, re.I):
            tally['boosted_trips'] += 1
        elif match := re.search(r'LEVEL UP!.*You are now level (\d+)', line, re.I):
            tally['level_ups'].append(int(match[1]))
        elif match := re.fullmatch(rf'\$({NUMBER}) received for leveling up\.?', line, re.I):
            tally['level_up_money'] += _number(match[1])
        elif match := re.match(r'UNLOCKED (.+?)\.(?:\s|$)', line):
            tally['unlocks'].append(match[1])
        elif line == 'QUEST COMPLETE!':
            mode = 'quest_name'
        elif line == 'Reward:' and mode == 'await_reward':
            mode = 'reward'
        elif match := re.fullmatch(rf'You got ({NUMBER}) (.+?) from the chest!?', line, re.I):
            _add(tally['chest_contents'], _name(match[2]), _number(match[1]))
        elif match := re.fullmatch(r'You found (?:an? )?(.+?)\s*!?', line, re.I):
            _add(tally['finds'], _name(match[1]), 1)
        elif match := re.fullmatch(r'Your (.+?) is now level (\d+)!?', line, re.I):
            tally['charm_levels'][_name(match[1])] = int(match[2])
        elif match := re.fullmatch(r'You ran out of (.+?)\.?', line, re.I):
            tally['bait_ran_out'].append(_name(match[1]))
        elif not any(pattern.fullmatch(line) for pattern in SKIPPED):
            pending = line
    if pending is not None:
        unrecognized(pending)
    if not text.strip():
        tally['warnings'].append('Paste fishing results to begin.')
    return tally
