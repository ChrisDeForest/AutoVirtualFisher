"""HTML for the fishing-results panel and the all-time fishing card on the local web page."""
from html import escape

from .bait_compare import NOT_RECORDED, assign_bait, compare_baits, load_bait_reference
from .catch_check import check_batch, load_fish_reference
from .fish_value import base_value
from .fishing_log import fingerprint, parse_fishing
from .fishing_sessions import fishing_totals, load_sessions, save_session
from .player_state import display_value

OVERLAP_NOTE = ('Pasting the exact same batch twice is detected and ignored. Batches that only partly '
                'overlap cannot be detected, so paste each fishing run once.')


def _counts(counts, value_format=display_value):
    return ', '.join(f'{name} {value_format(count)}' for name, count in counts.items()) or 'None'


def _per_trip(total, rate):
    return display_value(total) + (f' ({rate:,.2f} per trip)' if rate is not None else '')


def _rows(pairs):
    return '<dl>' + ''.join(f'<div class="stat"><dt>{escape(label)}</dt><dd class="known">{escape(text)}</dd></div>'
                            for label, text in pairs) + '</dl>'


def _quest(quest):
    rewards = ([f'+${quest["money"]:,}'] if quest.get('money') else []) + \
              ([f'+{quest["xp"]:,} XP'] if quest.get('xp') else []) + \
              [f'{item} ×{count}' for item, count in quest.get('items', {}).items()]
    return f'{quest["quest"]} tier {quest["tier"]}: ' + (', '.join(rewards) or 'no reward listed')


def _warnings(warnings):
    if not warnings:
        return ''
    return ('<aside class="warnings"><h3>Needs a look</h3><ul>'
            + ''.join(f'<li>{escape(warning)}</li>' for warning in warnings) + '</ul></aside>')


def _bait_label(tally):
    if 'bait' not in tally:
        return 'Not recorded'
    return tally['bait'] or 'No bait'


def bait_choice(value):
    """Map the form's bait value to assign_bait(): '' is not recorded, 'none' is no bait."""
    if value == 'none':
        return None
    for known in load_bait_reference() or {}:
        if known.casefold() == value.casefold():
            return known
    return value or NOT_RECORDED


def bait_options(selected):
    choices = [('', 'Not recorded'), ('none', 'No bait')] + [(name, name) for name in load_bait_reference() or {}]
    return ''.join(f'<option value="{escape(value)}"{" selected" if value == selected else ""}>{escape(label)}</option>'
                   for value, label in choices)


def _batch(tally):
    trips = tally['trips']
    caught = sum(tally['catches'].values())
    level_ups = ', '.join(str(level) for level in tally['level_ups'])
    return _rows([
        ('Trips', display_value(trips)),
        ('Bait', _bait_label(tally)),
        ('Boosted trips', display_value(tally['boosted_trips'])),
        ('Fish caught', _per_trip(caught, caught / trips if trips else None)),
        ('Catches', _counts(tally['catches'])),
        ('Fishing XP', _per_trip(tally['fishing_xp'], tally['fishing_xp'] / trips if trips else None)),
        ('Finds', _counts(tally['finds'])),
        ('Chest contents', _counts(tally['chest_contents'])),
        ('Chest money', f'${tally.get("chest_money", 0):,}'),
        ('Charm levels', _counts(tally['charm_levels'])),
        ('Level-ups', f'{level_ups} (+${tally["level_up_money"]:,})' if level_ups else 'None'),
        ('Unlocks', ', '.join(tally['unlocks']) or 'None'),
        ('Quests completed', '; '.join(_quest(quest) for quest in tally['quests_completed']) or 'None'),
        ('Ran out of bait', ', '.join(tally['bait_ran_out']) or 'None'),
    ])


def _xp_check(tally, biome, sell=None):
    result = check_batch(tally.get('trip_details', []), load_fish_reference(), biome)
    if result is None:
        return '<h3>XP check</h3><p class="status">The wiki fish table (src/reference/fish.json) is unavailable.</p>'
    if not result['trips']:
        return ''
    rows = []
    for trip in result['trips']:
        notes = list(trip['events'])
        if trip['jump']:
            notes.insert(0, f'▲ ratio changed from {trip["previous_ratio"]:.2f}× to {trip["ratio"]:.2f}×')
        if trip['unknown']:
            notes.append('Not in the wiki fish table: ' + ', '.join(trip['unknown']))
        if trip['off_biome']:
            notes.append(f'Wiki says not caught in {biome}: ' + ', '.join(trip['off_biome']))
        ratio = f'{trip["ratio"]:.2f}×' if trip['ratio'] else '—'
        value = f'${round(trip["base_value"] * (sell or 1)):,}'
        xp = display_value(trip['xp']) if trip['xp'] is not None else '—'
        row_class = ' class="jump"' if trip['jump'] else ''
        rows.append(f'<tr{row_class}><td>{trip["number"]}</td>'
                    f'<td>{escape(display_value(trip["base_xp"]))}</td><td>{escape(xp)}</td><td>{ratio}</td>'
                    f'<td>{value}</td><td>{escape("; ".join(notes))}</td></tr>')
    biome_note = (f'Biome: {biome}, from the latest snapshot.' if biome
                  else 'Choose an account to check catches against its biome.')
    return ('<h3>XP check</h3><p class="note">Ratio is the XP the game gave divided by the wiki base XP of '
            'the catch, so a steady ratio is your overall XP multiplier and ▲ marks a change beyond rounding. '
            f'{escape(biome_note)}</p>'
            '<div class="table-scroll"><table class="trips"><thead><tr><th>Trip</th><th>Base XP</th><th>XP</th>'
            '<th>Ratio</th><th>Value</th><th>Notes</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
            + _value_summary(result['base_value'], sell))


def _value_summary(base, sell):
    if sell is None:
        return (f'<p class="note">Base sell value of this batch: ${base:,} at wiki base prices; your sale '
                'multiplier is not applied. Choose an account whose snapshots include /buffs to use it.</p>')
    return (f'<p class="note">Sell value of this batch: ${round(base * sell):,} at your sell price of {sell:.2f}× '
            f'(${base:,} at wiki base prices).</p>')


def handle_fishing(text, action, account, accounts, root, bait=''):
    """Parse a pasted batch and save it when asked. Return (tally, status message)."""
    tally = assign_bait(parse_fishing(text), bait_choice(bait))
    if action != 'fishing_save':
        return tally, ''
    if account and account not in accounts:
        return tally, 'Choose an existing account before saving a fishing session.'
    try:
        result = save_session(tally, account, fingerprint(text), root)
    except OSError as error:
        return tally, f'Fishing session could not be saved: {error}'
    if result['status'] == 'saved':
        return tally, f'Fishing session saved for {account}.'
    if result['status'] == 'duplicate':
        return tally, f'This batch was already saved for {account} on {result["session"]["captured_at"]}; nothing new was saved.'
    return tally, f'Not saved: {result["reason"]}'


def fishing_panel(text, tally, message, account_options, biome=None, sell=None, bait=''):
    if tally is None:
        result = '<p class="status">Waiting for fishing results</p>'
    else:
        status = f'<p class="save-status">{escape(message)}</p>' if message else ''
        result = ('<p class="status">Tallied from your pasted results</p>' + status + _warnings(tally['warnings'])
                  + _batch(tally) + _xp_check(tally, biome, sell))
    return ('<section class="fishing"><div class="layout"><form method="post" action="/">'
            '<h2>Fishing results</h2>'
            '<p class="note">Paste one or more fishing results, including any quest completions. '
            'Cooldown messages are skipped.</p>'
            '<label for="fishing">Fishing results</label>'
            f'<textarea id="fishing" name="fishing" spellcheck="false" placeholder="Paste fishing results here…">{escape(text)}</textarea>'
            '<label for="fishing_account">Save to account</label>'
            f'<select id="fishing_account" name="account"><option value="">Choose an account…</option>{account_options}</select>'
            '<label for="fishing_bait">Bait used</label>'
            f'<select id="fishing_bait" name="bait">{bait_options(bait)}</select>'
            '<p class="note">Trips after a "You ran out of …" line count as no bait.</p>'
            '<div class="actions"><button type="submit" name="action" value="fishing_read">Read fishing results</button>'
            '<button class="secondary" type="submit" name="action" value="fishing_save">Save session</button></div>'
            f'<p class="note">Only the tally is saved; your pasted text is not. {escape(OVERLAP_NOTE)}</p>'
            f'</form><article class="result"><h2>This batch</h2>{result}</article></div></section>')


WIKI_EFFECTS = (('fish_catch_percent', 'fish catch'), ('fish_quality_percent', 'fish quality'),
                ('treasure_chance_percent', 'treasure chance'), ('treasure_quality_percent', 'treasure quality'),
                ('xp_percent', 'XP'), ('pet_catch_chance_percent', 'pet catch chance'),
                ('pet_effectiveness_percent', 'pet effectiveness'), ('pet_xp_percent', 'pet XP'))


def _signed_percent(value):
    return ('+' if value > 0 else '−') + f'{abs(value):g}%'


def _wiki_effects(wiki):
    if not wiki:
        return '—'
    parts = [f'+{wiki["extra_fish"]} fish'] if wiki.get('extra_fish') else []
    parts += [f'{label} {_signed_percent(wiki[field])}' for field, label in WIKI_EFFECTS if wiki.get(field)]
    return ', '.join(parts) or 'no listed effect'


def bait_card(account, root, sell=None):
    try:
        sessions, _ = load_sessions(account, root)
    except OSError as error:
        return _warnings([f'Fishing sessions unavailable: {error}'])
    result = compare_baits(sessions, load_fish_reference(), load_bait_reference(), sell=sell)
    if not result['rows']:
        return ('<h3>Bait comparison</h3><p class="status">Save fishing sessions with "Bait used" set to compare '
                'baits.</p>')
    rows = []
    for row in result['rows']:
        ratio = f'{row["xp_ratio"]:.2f}×' if row['xp_ratio'] else '—'
        value = ('—' if row['base_value_per_trip'] is None
                 else f'${round(row["base_value_per_trip"] * (sell or 1)):,}')
        cost = '?' if row['bait_cost_per_trip'] is None else f'${row["bait_cost_per_trip"]:,.0f}'
        net = '?' if row['net_value_per_trip'] is None else f'${round(row["net_value_per_trip"]):,}'
        rows.append(f'<tr><td>{escape(row["bait"])}{" *" if row["few_trips"] else ""}</td>'
                    f'<td>{row["trips"]:,} ({row["boosted_trips"]:,} boosted)</td><td>{row["fish_per_trip"]:.2f}</td>'
                    f'<td>{ratio}</td><td>{value}</td><td>{cost}</td><td>{net}</td><td>{row["finds_per_100_trips"]:.2f}</td>'
                    f'<td>{escape(_wiki_effects(row["wiki"]))}</td></tr>')
    value_basis = f'at your sell price of {sell:.2f}×' if sell else 'at wiki base prices'
    notes = [f'Gross value per trip is {value_basis}. Net subtracts the bait (wiki price, one per cast) and needs '
             'your sell price; ? means unknown, not free. Rows marked * have fewer than 20 trips, so treat them as '
             'rough.',
             'Global boosts, purchased boosts, charms, and pets change these numbers too; compare batches fished '
             'under similar conditions.']
    skipped = result['not_recorded_trips']
    if skipped:
        notes.append(f'{skipped:,} trip{"" if skipped == 1 else "s"} from sessions saved without a bait '
                     f'{"is" if skipped == 1 else "are"} left out.')
    return ('<h3>Bait comparison</h3><div class="table-scroll"><table class="trips"><thead><tr><th>Bait</th>'
            '<th>Trips</th><th>Fish per trip</th><th>XP ratio</th><th>Gross value per trip</th><th>Bait cost</th>'
            '<th>Net per trip</th><th>Finds per 100 trips</th>'
            '<th>Wiki says</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
            + ''.join(f'<p class="note">{escape(note)}</p>' for note in notes))


def fishing_card(account, root):
    try:
        totals = fishing_totals(account, root)
    except OSError as error:
        return _warnings([f'Fishing sessions unavailable: {error}'])
    if not totals['sessions']:
        return '<h3>Fishing (all time)</h3><p class="status">No fishing sessions saved for this account yet.</p>'
    rate = lambda value: f'{value:,.2f}'
    return ('<h3>Fishing (all time)</h3>' + _warnings(totals['warnings']) + _rows([
        ('Sessions', display_value(totals['sessions'])),
        ('Trips', f'{totals["trips"]:,} ({totals["boosted_trips"]:,} boosted)'),
        ('Fish caught', _per_trip(totals['fish_caught'], totals['fish_per_trip'])),
        ('Catches', _counts(totals['catches'])),
        ('Fishing XP', _per_trip(totals['fishing_xp'], totals['xp_per_trip'])),
        ('Finds per 100 trips', _counts(totals['finds_per_100_trips'], rate)),
        ('Chest contents', _counts(totals['chest_contents'])),
        ('Chest money', f'${totals.get("chest_money", 0):,}'),
        ('Quests completed', f'{totals["quests_completed"]:,} (+${totals["quest_money"]:,}, +{totals["quest_xp"]:,} XP)'),
        ('Level-ups', f'{totals["level_ups"]:,} (+${totals["level_up_money"]:,})'),
    ]) + f'<p class="note">{escape(OVERLAP_NOTE)}</p>')
