"""Read only the fields needed by the fishing policy, independently of optional profile text."""
from dataclasses import replace
import math
import re
from .player_state import _clean, parse_responses
from .observation import VERIFIED


def reply_fields(kind, text):
    lines = [_clean(line) for line in text.splitlines() if line.strip()]
    if kind == 'profile':
        names = [parse_responses(profile=line)['profile']['account_name']
                 for line in lines if line.startswith('Inventory of ')]
        if len(names) != 1 or not names[0]:
            return {}
        fields = {'account_name': names[0]}
        balance_lines = [line for line in lines if line.lower().startswith('balance:')]
        if len(balance_lines) == 1:
            value = parse_responses(profile=balance_lines[0])['profile']['balance']
            if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                fields['balance'] = value
        level_lines = [line for line in lines if re.match(r'^(?:P\d+\s+)?Level\b', line, re.I)]
        if len(level_lines) == 1:
            line = level_lines[0]
            match = re.match(r'^(?:P(\d+)\s+)?Level (\d+)(?=,|$)', line, re.I)
            value = int(match[2]) if match else parse_responses(profile=line)['profile']['level']
            if isinstance(value, int) and value >= 0:
                fields['level'] = value
        return fields
    if kind == 'buffs':
        # Observed native OCR reads the apostrophe as "•" (and other quote glyphs); the name is unchanged.
        identities = [line for line in lines if re.fullmatch(r".+['’‘•�]s current multipliers \(including bait\):?", line, re.I)]
        if len(identities) != 1:
            return {}
        fields = {'username': re.split(r"['’‘•�]s current multipliers", identities[0], flags=re.I)[0]}
        cooldown_lines = [line for line in lines if line.lower().startswith('fishing cooldown:')]
        if len(cooldown_lines) == 1:
            # Observed: the closing period read as "_" after the unit. No digit is changed.
            line = re.sub(r'(\ds)_$', r'\1.', cooldown_lines[0])
            value = parse_responses(buffs=line)['buffs']['fishing_cooldown']
            if isinstance(value, dict) and value['unit'] == 'seconds' and math.isfinite(value['value']) and value['value'] > 0:
                fields['cooldown_seconds'] = value['value']
        return fields
    return {}


def startup_problems(state, kind):
    if kind == 'profile':
        profile = state.profile or {}
        return [label + ' has not been read.' for key, label in
                (('account_name', 'Account name'), ('balance', 'Balance'), ('level', 'Level'))
                if profile.get(key) is None]
    if kind == 'buffs':
        return [] if state.cooldown_seconds is not None else ['Fishing cooldown has not been read.']
    return ['Expected a profile or buffs reply.']


def apply_startup(state, observation):
    if (observation.identity_status != VERIFIED or observation.observation_id in state.applied
            or observation.context_id != state.context_id):
        return state
    fields = reply_fields(observation.kind, observation.text)
    if observation.kind == 'profile':
        if fields.get('account_name', '').casefold() != state.account.casefold():
            return state
        profile = {**(state.profile or {}), **fields}
        provenance = {**state.startup_observed_at,
                      **{key: observation.observed_at for key in fields}}
        return replace(state, profile=profile, profile_observed_at=observation.observed_at,
                       startup_observed_at=provenance, applied=state.applied | {observation.observation_id})
    if observation.kind == 'buffs' and state.username and fields.get('username', '').casefold() == state.username.casefold():
        if 'cooldown_seconds' in fields:
            return replace(state, cooldown_seconds=fields['cooldown_seconds'], buffs_observed_at=observation.observed_at,
                           startup_observed_at={**state.startup_observed_at, 'cooldown_seconds': observation.observed_at},
                           applied=state.applied | {observation.observation_id})
    return state
