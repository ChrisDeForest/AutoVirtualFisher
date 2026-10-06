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


def planner_card(account, snapshot_root, fishing_root, target=None):
    try:
        result = plan(account, snapshot_root, fishing_root, target=target)
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
            'Estimates count fishing XP only; quest rewards make progress faster.</p>' + _rows(targets))
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
