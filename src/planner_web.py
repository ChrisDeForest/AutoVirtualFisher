"""HTML for the level and quest planner card on the local web page."""
from html import escape

from .player_planner import plan


def _rows(pairs):
    return '<dl>' + ''.join(f'<div class="stat"><dt>{escape(label)}</dt>'
                            f'<dd class="{"known" if known else "unknown"}">{escape(text)}</dd></div>'
                            for label, text, known in pairs) + '</dl>'


def _model_note(model):
    if model['mismatches']:
        first = model['mismatches'][0]
        expected = f'{first["expected"]:,}' if first['expected'] is not None else 'unknown'
        return (f'XP model disagrees with a snapshot (level {first["level"]}: game showed {first["observed"]:,} XP, '
                f'model expected {expected}), so only the next level is projected.')
    if model['matches']:
        return f'XP model matches {model["matches"]} of your snapshots.'
    return ('XP model not yet confirmed by your snapshots; paste a /profile that shows current/required XP '
            'to check it.')


def _dollars(amount):
    return f'${round(amount):,}'


def _strategy_text(strategy):
    if strategy['missing_conditions']:
        return (False, 'Needs your ' + ', '.join(strategy['missing_conditions'])
                + ' from /profile to pick comparable trips')
    shown = ', '.join(value or 'no bait' for value in strategy['conditions'].values())
    if not strategy['trips']:
        return False, f'No saved trips fished with {shown} yet ({strategy["excluded_trips"]:,} other trips left out)'
    gross = 'unknown' if strategy['gross_per_trip'] is None else f'${strategy["gross_per_trip"]:,.2f}'
    net = 'unknown' if strategy['net_per_trip'] is None else f'${strategy["net_per_trip"]:,.2f}'
    return True, (f'Gross {gross}, net {net} per trip from {strategy["trips"]:,} recent trips with {shown} '
                  f'({strategy["excluded_trips"]:,} other trips left out)')


def _money_rows(money):
    rows = []
    if money['multiplier'] is None:
        rows.append(('Gross catch value per trip', 'Paste /buffs to include your sell price', False))
    elif money['per_trip'] is None:
        rows.append(('Gross catch value per trip', 'Save a fishing session to estimate it', False))
    else:
        rows.append(('Gross catch value per trip', f'${money["per_trip"]:,.2f} (${money["base_per_trip"]:,.2f} at '
                                                   f'wiki base prices × {money["multiplier"]:.2f} sell price)', True))
    cost = money['bait_cost_per_trip']
    if cost is not None:
        rows.append(('Bait cost per trip', f'${cost:,.2f} (' + (f'{money["bait"]} at its wiki price, one per cast)'
                                                                 if money['bait'] else 'no bait)'), True))
    else:
        rows.append(('Bait cost per trip', f'Unknown: no wiki price for {money["bait"]}' if money['bait']
                     else 'Unknown: your profile does not show your bait', False))
    if money['net_per_trip'] is not None:
        rows.append(('Net per trip', f'${money["net_per_trip"]:,.2f} (gross minus bait)', True))
    else:
        rows.append(('Net per trip', 'Unknown until both the gross value and bait cost are known', False))
    if money['gross_per_hour'] is not None:
        rows.append(('Gross per hour', f'At most ${money["gross_per_hour"]:,.0f} (one trip per cooldown, '
                                       'no pauses)', True))
    known, text = _strategy_text(money['strategy'])
    rows.append(('Same conditions as now', text, known))
    target = money['target']
    if target:
        held = 'balance + unsold fish' if target['includes_unsold_fish'] else 'balance'
        if not target['needed']:
            text = f'Already there ({held}: {_dollars(target["have"])})'
        else:
            text = (f'{_dollars(target["needed"])} more ({held}: {_dollars(target["have"])}) · '
                    + (f'about {target["trips"]:,} trips' if target['trips'] is not None
                       else 'needs money per trip to estimate trips'))
        rows.append((f'Reach {_dollars(target["amount"])}', text, True))
    return rows


def _value_note(check):
    if not check:
        return ''
    if check['mismatches']:
        first = check['mismatches'][0]
        return (f'Fish Value disagrees with wiki base prices × sell price in a snapshot (game showed '
                f'{_dollars(first["observed"])}, expected {_dollars(first["expected"])}), so money estimates may be off.')
    if check['matches']:
        return f'Fish Value matches wiki base prices × your sell price in {check["matches"]} of your snapshots.'
    return ''


def _age(seconds):
    if seconds < 90:
        return f'{seconds:.0f} s ago'
    if seconds < 5400:
        return f'{seconds / 60:.0f} min ago'
    if seconds < 172_800:
        return f'{seconds / 3600:.0f} h ago'
    return f'{seconds / 86400:.0f} days ago'


def _ages_note(ages):
    """'Profile seen 3 min ago · Buffs pasted, observation time unknown'; sections never observed are left out."""
    parts = []
    for section in ('profile', 'buffs', 'quests'):
        age = ages.get(section, {})
        if age.get('age_seconds') is not None:
            parts.append(f'{section.capitalize()} seen {_age(age["age_seconds"])}')
        elif 'not been observed' not in age.get('reason', 'not been observed'):
            parts.append(f'{section.capitalize()}: observation time unknown')
    return ' · '.join(parts)


def planner_card(account, snapshot_root, fishing_root, target=None, money_target=None):
    try:
        result = plan(account, snapshot_root, fishing_root, target=target, money_target=money_target)
    except OSError as error:
        return f'<p class="status">Planner unavailable: {escape(str(error))}</p>'
    if result['status'] != 'ok':
        return '<h3>Planner</h3><p class="status">Save a full snapshot to plan levels and quests.</p>'
    rate = result['xp_per_trip']
    basis = (f'Based on the snapshot from {result["as_of"]} and {result["fishing_trips"]:,} saved fishing trips '
             f'({rate:,.2f} fishing XP per trip).' if rate else
             f'Based on the snapshot from {result["as_of"]}. No fishing sessions saved yet.')
    targets = [(f'Level {item["level"]}',
                f'{item["xp_needed"]:,} XP · ' + (f'about {item["trips"]:,} trips' if item['trips'] is not None
                                                 else 'save a fishing session to estimate trips'),
                True) for item in result['targets']]
    html = ('<h3>Planner</h3>'
            f'<p class="note">{escape(basis)} {escape(_model_note(result["model"]))} '
            'Estimates count fishing XP only; quest rewards make progress faster.</p>'
            + (f'<p class="note">{escape(_ages_note(result["section_ages"]))}</p>'
               if _ages_note(result['section_ages']) else '')
            + _rows(targets)
            + '<h3>Money</h3>'
            + (f'<p class="note">{escape(_value_note(result["money"]["check"]))} Gross value assumes you sell every '
               'fish you catch and averages all your saved trips, whatever bait, biome, boosts, or pets they had; '
               'the same-conditions row uses only comparable trips. Unknown costs are shown as unknown, not '
               'zero.</p>')
            + _rows(_money_rows(result['money'])))
    if result['quests']:
        if result['quests_reset']:
            html += (f'<p class="status">These quests were pasted at {escape(result["quests_as_of"])} and have reset '
                     'since. Paste /quests again for current progress.</p>')
        quests = []
        for quest in result['quests']:
            label = f'{quest["name"]} ({quest["progress"]:,}/{quest["goal"]:,})'
            if quest['status'] == 'estimate':
                quests.append((label, f'about {quest["trips"]:,} trips ({quest["note"]})', True))
            else:
                quests.append((label, quest['note'], quest['status'] == 'complete'))
        html += '<h3>Quests</h3>' + _rows(quests)
    return html
