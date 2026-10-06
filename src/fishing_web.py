"""HTML for the fishing-results panel and the all-time fishing card on the local web page."""
from html import escape

from .catch_check import check_batch, load_fish_reference
from .fish_value import base_value
from .fishing_log import fingerprint, parse_fishing
from .fishing_sessions import fishing_totals, save_session
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


def _batch(tally):
    trips = tally['trips']
    caught = sum(tally['catches'].values())
    level_ups = ', '.join(str(level) for level in tally['level_ups'])
    return _rows([
        ('Trips', display_value(trips)),
        ('Boosted trips', display_value(tally['boosted_trips'])),
        ('Fish caught', _per_trip(caught, caught / trips if trips else None)),
        ('Catches', _counts(tally['catches'])),
        ('Fishing XP', _per_trip(tally['fishing_xp'], tally['fishing_xp'] / trips if trips else None)),
        ('Finds', _counts(tally['finds'])),
        ('Chest contents', _counts(tally['chest_contents'])),
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


def handle_fishing(text, action, account, accounts, root):
    """Parse a pasted batch and save it when asked. Return (tally, status message)."""
    tally = parse_fishing(text)
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


def fishing_panel(text, tally, message, account_options, biome=None, sell=None):
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
            '<div class="actions"><button type="submit" name="action" value="fishing_read">Read fishing results</button>'
            '<button class="secondary" type="submit" name="action" value="fishing_save">Save session</button></div>'
            f'<p class="note">Only the tally is saved; your pasted text is not. {escape(OVERLAP_NOTE)}</p>'
            f'</form><article class="result"><h2>This batch</h2>{result}</article></div></section>')


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
        ('Quests completed', f'{totals["quests_completed"]:,} (+${totals["quest_money"]:,}, +{totals["quest_xp"]:,} XP)'),
        ('Level-ups', f'{totals["level_ups"]:,} (+${totals["level_up_money"]:,})'),
    ]) + f'<p class="note">{escape(OVERLAP_NOTE)}</p>')
