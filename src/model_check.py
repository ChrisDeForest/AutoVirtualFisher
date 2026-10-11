"""The game model checked against the game: /buffs compared, correction factors from the fishing logs, fish mixes
and chest rewards learned from them, and the Model tab's view. Kept in <account folder>/model_check.json."""

import json
import re
from collections import deque
from dataclasses import replace
from pathlib import Path

from .catch_check import xp_from_fish
from .game_model import NotModelled, PER_TRIP, Value, buffs, per_hour, trip
from .game_model_inputs import build_inputs
from .player_snapshots import write_json_atomically

BUFF_LABELS = (('sell_price', 'Sell price'), ('fish_catch', 'Fish catch'), ('fish_quality', 'Fish quality'),
               ('treasure_chance', 'Treasure chance'), ('treasure_quality', 'Treasure quality'),
               ('xp_multiplier', 'XP multiplier'), ('fishing_cooldown', 'Fishing cooldown'))
TOLERANCE = 0.005


def hidden_effect(name, ratio, data):
    """A known effect the model cannot see that explains game ÷ model, within half a percent."""
    for effect in data['hidden_effects']:
        if effect['buff'] == name and abs(ratio / effect['factor'] - 1) <= 0.005:
            return effect['name']
    return None


def compare_buffs(model, game, data):
    rows = []
    for name, label in BUFF_LABELS:
        predicted = model[name]
        shown = game.get(name)
        unit = shown.get('unit') if isinstance(shown, dict) else None
        shown = shown.get('value') if isinstance(shown, dict) else None
        if shown is not None and unit == 'percent':  # "+41%" is 1.41x
            shown = 1 + shown / 100
        ok = None if shown is None else abs(shown - predicted.value) <= TOLERANCE + 1e-9
        ratio = shown / predicted.value if shown is not None and predicted.value else None
        rows.append({'name': name, 'label': label, 'game': shown, 'model': predicted.value, 'ok': ok,
                     'correction': 1.0 if ok else ratio,
                     'effect': hidden_effect(name, ratio, data) if ok is False and ratio else None,
                     'source': predicted.source, 'why': list(predicted.why)})
    return rows


def buff_corrections(rows):
    """Game ÷ model per buff, applied to every what-if; 1 where they agree or the game has not shown it."""
    return {row['name']: (row['correction'] if row['ok'] is False and row['correction'] else 1.0) for row in rows}


def corrected_trip(account, setup, game_buffs, data, learned=None, chest_scales=None):
    """(the /buffs comparison, the trip with its buff corrections applied). The comparison is made on the model
    alone; without a /buffs reply every correction is 1."""
    rows = compare_buffs(buffs(account, setup, data), game_buffs or {}, data)
    return rows, trip(account, setup, data, learned, chest_scales, buff_corrections(rows))


MINIMUMS = {'fish': ('trips', 50), 'money_fish': ('trips', 50), 'xp_fish': ('trips', 50),
            'chest_chance': ('chests', 20), 'charms': ('charms', 10)}
WINDOW = 500
MIX_TRIPS = 200
SCALE_CHESTS = 5
FLAG = 0.10
CHARM = re.compile(r'Found (\w+) Charm$')
MONEY = re.compile(r'Chest: \$([\d,]+)$')
XP = re.compile(r'Chest: ([\d,]+) XP$')


def _number(text):
    return int(text.replace(',', ''))


def measure(tally, data, sell_multiplier, biome):
    """What one logged trip shows, or None for a trip left out (several trips in one reply, a fish not caught in
    this biome, no catch). Tiers are the biome's: Salmon is tier 2 in River but tier 1 in Volcanic."""
    catches = tally.get('catches') or {}
    if tally.get('trips') != 1 or not catches:
        return None
    by_name = {tier['fish'].casefold(): (index, tier) for index, tier in enumerate(data['biomes'][biome]['tiers'])}
    if any(name.casefold() not in by_name for name in catches):
        return None
    tiers = [0] * 5
    for name, count in catches.items():
        tiers[by_name[name.casefold()][0]] += count
    detail = (tally.get('trip_details') or [{}])[0]
    events = detail.get('events') or []
    # The trip's XP line includes chest XP, which does not come from the fish (as the catch check has it).
    xp = xp_from_fish(detail)
    if xp is None:
        xp = (tally.get('fishing_xp') or 0) - (tally.get('chest_xp') or 0)
    found = {chest['event']: tier for tier, chest in data['chests'].items()}
    chest_tier, rewards, chests, charms = None, [], 0, 0
    for event in events:
        if event in found:
            chest_tier, chests = found[event], chests + 1
        elif CHARM.match(event):
            charms += 1
        elif chest_tier and (match := MONEY.match(event)):
            rewards.append((chest_tier, 'money', _number(match[1])))
        elif chest_tier and (match := XP.match(event)):
            rewards.append((chest_tier, 'xp', _number(match[1])))
    return {'fish': sum(catches.values()),
            'money_fish': sum(count * by_name[name.casefold()][1]['price'] for name, count in catches.items())
            * sell_multiplier,
            'xp_fish': xp, 'chest_chance': min(chests, 1), 'charms': charms,
            'tiers': tiers, 'chest_rewards': rewards}


def trip_boosted(tally):
    """Whether a logged trip was under a global boost: its "Global boost by" line (fishing_log)."""
    detail = (tally.get('trip_details') or [{}])[0]
    if 'boosted' in detail:
        return bool(detail['boosted'])
    return bool(tally.get('boosted_trips'))


def window_key(setup, predicted, shown=None):
    """What starts a new window: the rod, biome, bait or pet, or the buffs. A buff the /buffs reply shows counts as
    shown, so an event that changes /buffs starts a new window; one not shown (or no reply yet) as predicted."""
    shown = shown or {}
    values = [round(shown[name] if shown.get(name) is not None else predicted['buffs'][name].value, 4)
              for name, _ in BUFF_LABELS]
    return json.dumps([setup.rod, setup.biome, setup.bait, setup.pet, values])


RECENT_CHESTS = 100


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _valid_factors(value):
    return isinstance(value, dict) and all(
        isinstance(factor, dict) and _is_number(factor.get('value')) and _is_number(factor.get('count'))
        for factor in value.values())


def _valid_mix(value):
    return isinstance(value, dict) and all(
        isinstance(key, str) and isinstance(mix, dict) and _is_number(mix.get('trips'))
        and _is_number(mix.get('quality')) and isinstance(mix.get('counts'), list) and len(mix['counts']) == 5
        and all(_is_number(count) for count in mix['counts']) for key, mix in value.items())


def _valid_record(record):
    if not (isinstance(record, dict) and isinstance(record.get('measured'), dict)
            and isinstance(record.get('predicted'), dict)):
        return False
    measured, predicted = record['measured'], record['predicted']
    tiers = measured.get('tiers')
    return (all(_is_number(measured.get(name)) for name in MINIMUMS)
            and all(predicted.get(name) is None or _is_number(predicted.get(name)) for name in MINIMUMS)
            and isinstance(tiers, list) and len(tiers) == 5 and all(_is_number(tier) for tier in tiers))


def _valid_window(value):
    return (isinstance(value, dict) and (value.get('key') is None or isinstance(value['key'], str))
            and isinstance(value.get('trips'), list) and all(_valid_record(record) for record in value['trips']))


def _valid_recent(value):
    return isinstance(value, dict) and all(
        isinstance(chests, list) and all(isinstance(chest, list) and len(chest) == 2
                                         and all(_is_number(part) for part in chest) for chest in chests)
        for chests in value.values())


class ModelTracker:
    """Correction factors, learned fish mixes and chest reward scales, from the trips the model has watched. The
    window is saved with its key, so a restart on the same setup resumes it."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.window, self.window_key = deque(maxlen=WINDOW), None
        # A saved field that is not as written starts fresh, with a note on the Model tab: the model never stops a run.
        self.problem, bad = None, []
        saved = {}
        if self.path is not None and self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                saved, bad = {}, ['the file']
        if not isinstance(saved, dict):
            saved, bad = {}, ['the file']
        fields = {}
        for name, valid in (('factors', _valid_factors), ('mix', _valid_mix), ('window', _valid_window),
                            ('chest_recent', _valid_recent)):
            if name in saved and not valid(saved[name]):
                bad.append(name)
            elif name in saved:
                fields[name] = saved[name]
        if bad:
            self.problem = f'{self.path.name} could not be read ({", ".join(bad)}): started fresh for it.'
        self.saved_factors = fields.get('factors') or {}
        self.mix = fields.get('mix') or {}
        window = fields.get('window') or {}
        if window.get('key'):
            self.window_key = window['key']
            self.window.extend(window['trips'])
        # The most recent chests that gave money, and those that gave XP: (amount, chest multiplier).
        recent = fields.get('chest_recent') or {}
        self.recent = {kind: deque(recent.get(kind) or [], maxlen=RECENT_CHESTS) for kind in ('money', 'xp')}

    def observe(self, tally, setup, predicted, data, sell_multiplier, shown=None):
        """Watch one trip; True when it was used. `shown` holds the /buffs reply's values, where one was read."""
        measured = measure(tally, data, sell_multiplier, setup.biome)
        if measured is None:
            return False
        key = window_key(setup, predicted, shown)
        if key != self.window_key:
            self.window.clear()
            self.window_key = key
        self.window.append({'measured': {name: measured[name] for name in (*MINIMUMS, 'tiers')},
                            'predicted': {name: predicted[name].value for name in MINIMUMS}})
        self._promote()
        mix = self.mix.setdefault(f'{setup.rod}|{setup.biome}', {'trips': 0, 'counts': [0] * 5, 'quality': 0.0})
        mix['trips'] += 1
        mix['counts'] = [a + b for a, b in zip(mix['counts'], measured['tiers'])]
        mix['quality'] += predicted['buffs']['fish_quality'].value
        for tier, kind, amount in measured['chest_rewards']:
            self.recent[kind].append([amount, data['chests'][tier]['multiplier']])
        return True

    def _window_totals(self, name):
        unit = MINIMUMS[name][0]
        measured = sum(record['measured'][name] for record in self.window)
        predicted = sum(record['predicted'][name] or 0 for record in self.window)
        count = len(self.window) if unit == 'trips' else sum(record['measured'][name] for record in self.window)
        return measured, predicted, count

    def _promote(self):
        for name, (_, needed) in MINIMUMS.items():
            measured, predicted, count = self._window_totals(name)
            if count >= needed and predicted:
                self.saved_factors[name] = {'value': measured / predicted, 'count': count}

    def factors(self):
        result = {}
        for name, (unit, needed) in MINIMUMS.items():
            measured, predicted, count = self._window_totals(name)
            saved = self.saved_factors.get(name)
            if count >= needed and predicted:
                value, status = measured / predicted, 'applied'
            elif saved:
                value, status = saved['value'], 'carried from an earlier setup'
            else:
                value, status = 1.0, f'measuring {count}/{needed} {unit}'
            result[name] = {'value': value, 'status': status, 'count': count, 'needed': needed,
                            'flag': abs(value - 1) > FLAG}
        return result

    def learned_mix(self):
        """Base shares per rod and biome, the exact inverse of fish_shares: each upper tier's measured share ÷ fish
        quality, and the bottom tier the rest (fish_shares gives it whatever is left, so its base never matters
        beyond being the rest)."""
        learned = {}
        for key, mix in self.mix.items():
            total = sum(mix['counts'])
            if mix['trips'] < MIX_TRIPS or not total:
                continue
            quality = mix['quality'] / mix['trips']
            top = [count / total / quality for count in mix['counts'][1:]]
            learned[key] = {'shares': [max(0.0, 1 - sum(top)), *top], 'trips': mix['trips']}
        return learned

    def chest_scales(self):
        """Money and XP per unit of chest multiplier, from the last 100 chests that gave each (at least 5): chest
        rewards grow with level, upgrades and prestige. Super crates are not chests and are left out."""
        scales = {}
        for kind, recent in self.recent.items():
            weight = sum(multiplier for _, multiplier in recent)
            scales[kind] = (sum(amount for amount, _ in recent) / weight
                            if len(recent) >= SCALE_CHESTS and weight else None)
        return scales

    def save(self):
        if self.path is None:
            return
        write_json_atomically(self.path.parent, self.path.stem, {
            'schema_version': 2, 'factors': self.saved_factors, 'mix': self.mix,
            'window': {'key': self.window_key, 'trips': list(self.window)},
            'chest_recent': {kind: list(recent) for kind, recent in self.recent.items()}})


ESTIMATED_SECONDS = 'seconds per trip: cooldown + 0.5 s until this run has a rate'
TRIP_LABELS = (('fish', 'Fish'), ('money_fish', 'Money from fish'), ('xp_fish', 'XP from fish'),
               ('chest_chance', 'Chests'), ('charms', 'Charms'), ('gold', 'Gold Fish'),
               ('emerald', 'Emerald Fish'), ('lava', 'Lava Fish'), ('diamond', 'Diamond Fish'),
               ('super_crate', 'Super crates'), ('money_chests', 'Money from chests'), ('xp_chests', 'XP from chests'))


def model_view(account_state, profile, game_buffs, charms_text, tracker, data, trips_per_hour=None,
               global_booster=None):
    """Everything the Model tab shows. Never raises: a problem comes back as 'error'. `global_booster` is whether
    the latest trip was boosted; without a trip yet, the /boosts read decides."""
    empty = {'error': None, 'setup': None, 'buffs': [], 'trip': [], 'mix': {'source': '', 'rows': []},
             'estimates': []}
    built = build_inputs(account_state, profile, charms_text)
    if isinstance(built, str):
        return dict(empty, error=built)
    account, setup = built
    if global_booster is not None:
        setup = replace(setup, global_booster=global_booster)
    try:
        rows, predicted = corrected_trip(account, setup, game_buffs, data, tracker.learned_mix(),
                                         tracker.chest_scales())
    except NotModelled as error:
        return dict(empty, error=str(error))
    factors = tracker.factors()
    if trips_per_hour:
        seconds = Value(3600 / trips_per_hour)
    else:
        seconds = Value(predicted['buffs']['fishing_cooldown'].value + 0.5, 'estimate', (ESTIMATED_SECONDS,))
    hourly = per_hour(predicted, seconds)
    window = list(tracker.window)
    trip_rows = []
    for name, label in TRIP_LABELS:
        value = predicted[name]
        factor = factors.get(name)
        multiplier = factor['value'] if factor else 1.0
        measured = (sum(record['measured'][name] for record in window) / len(window)
                    if window and name in MINIMUMS else None)
        trip_rows.append({'name': name, 'label': label, 'predicted': value.value,
                          'corrected': None if value.value is None else value.value * multiplier,
                          'measured': measured, 'factor': factor['value'] if factor else None,
                          'status': factor['status'] if factor else '', 'flag': bool(factor and factor['flag']),
                          'per_hour': None if hourly[name].value is None else hourly[name].value * multiplier,
                          'source': value.source, 'per_hour_source': hourly[name].source})
    counts = [sum(record['measured']['tiers'][index] for record in window) for index in range(5)]
    total = sum(counts)
    mix_rows = [{'fish': row['fish'], 'predicted': row['share'], 'measured': counts[index] / total if total else None}
                for index, row in enumerate(predicted['shares'])]
    mix_source = (predicted['mix_why'][0] if predicted['mix_why'] else 'the rod\'s own fish mix')
    estimates = list(dict.fromkeys(reason for value in (*predicted['buffs'].values(),
                                                        *(predicted[name] for name in PER_TRIP))
                                   for reason in value.why))
    if not trips_per_hour:
        estimates.append(ESTIMATED_SECONDS)
    if tracker.problem:
        estimates.insert(0, tracker.problem)
    return {'error': None,
            'setup': {'rod': setup.rod, 'biome': setup.biome, 'bait': setup.bait or 'none', 'pet': setup.pet or 'none',
                      'read_at': max((section.get('read_at') or '' for section in
                                      (account_state.get('sections') or {}).values()), default='') or None,
                      'boosts': [label for flag, label in ((setup.fish_boost, 'fish boost'),
                                                           (setup.treasure_boost, 'treasure boost'),
                                                           (setup.global_booster, 'global booster'),
                                                           (setup.personal_booster, 'personal booster')) if flag]},
            'buffs': rows, 'trip': trip_rows, 'mix': {'source': mix_source, 'rows': mix_rows},
            'estimates': estimates}
