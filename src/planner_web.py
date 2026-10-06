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


def _money_rows(money):
    rows = []
    if money['multiplier'] is None:
        rows.append(('Money per trip', 'Paste /buffs to include your sell price', False))
    elif money['per_trip'] is None:
        rows.append(('Money per trip', 'Save a fishing session to estimate it', False))
    else:
        rows.append(('Money per trip', f'${money["per_trip"]:,.2f} (${money["base_per_trip"]:,.2f} at wiki base '
                                       f'prices × {money["multiplier"]:.2f} sell price)', True))
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
            'Estimates count fishing XP only; quest rewards make progress faster.</p>' + _rows(targets)
            + '<h3>Money</h3>'
            + (f'<p class="note">{escape(_value_note(result["money"]["check"]))} Money assumes you sell every fish '
               'you catch.</p>')
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
