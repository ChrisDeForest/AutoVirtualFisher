"""The account as its replies last showed it: one section per reply kind, each with when it was read.

Pure merge functions plus load/save. Nothing is inferred: a section holds only values replies showed. A newer
reply replaces the values it shows; values it did not show (a garbled line, the other page) keep their earlier
read. Paged shops are complete only when both pages have every item.
"""
import json
from pathlib import Path

from .account_replies import LEAGUE, UPGRADES
from .player_snapshots import write_json_atomically

# Shops whose items are all known: complete only when each one has been read.
COMPLETE_ITEMS = {'shop_upgrades': UPGRADES, 'shop_league': LEAGUE}
# Replies shown whole on one page: a whole one replaces the section (the bait in use and every bait owned).
WHOLE_REPLIES = {'bait'}


def empty_state() -> dict:
    return {'sections': {}}


def load_state(path) -> dict:
    try:
        state = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return empty_state()
    return state if isinstance(state.get('sections'), dict) else empty_state()


def mark_maxed(state: dict, name: str, level: int) -> dict:
    """The state with `name` known to stop at `level`: the game answered a buy of the next level with "You already
    have that upgrade maxed." while the shop still listed a higher max (Artifact Specialist 5/7, 2026-10-14)."""
    return {**state, 'maxed': {**(state.get('maxed') or {}), name: level}}


def mark_owned(state: dict, action) -> dict:
    """Keep the ownership shown by an already-owned reply to this specific dispatched buy."""
    kind = {'boats': 'shop_boats', 'rods': 'rod', 'pets': 'pet', 'upgrades': 'shop_upgrades',
            'special': 'shop_special', 'league': 'shop_league'}.get(action.shop)
    if action.act != 'buy' or kind is None:
        return state
    sections = dict(state.get('sections', {}))
    old = sections.get(kind, {'values': {}, 'pages': {}})
    values = dict(old.get('values') or {})
    if kind == 'rod':
        values['owned'] = list(dict.fromkeys([*(values.get('owned') or []), action.name]))
    elif kind == 'pet':
        values['owned'] = {**(values.get('owned') or {}),
                           action.name: (values.get('owned') or {}).get(action.name, {})}
    else:
        items = dict(values.get('items') or {})
        item = dict(items.get(action.name) or {})
        item['owned'] = True
        items[action.name] = item
        values['items'] = items
    sections[kind] = {**old, 'values': values}
    return {**state, 'sections': sections}


def save_state(path, state: dict) -> None:
    path = Path(path)
    write_json_atomically(path.parent, path.stem, state)


def _merge(old: dict, new: dict) -> dict:
    merged = dict(old)
    for key, value in new.items():
        if key == 'items':
            merged['items'] = {**old.get('items', {}), **value}
        elif value is not None or key not in old:
            merged[key] = value
    return merged


def section_unread(kind: str, values: dict, parser_unread=()) -> list[str]:
    """What a section still lacks: every known item of a paged shop (and its balance of hooks), else whatever
    the latest reply's parser reported."""
    names = COMPLETE_ITEMS.get(kind)
    if names is None:
        return list(parser_unread)
    missing = [name for name in names if name not in values.get('items', {})]
    if kind == 'shop_league' and values.get('hooks') is None:
        missing.append('Hooks')
    return missing


def apply_read(state: dict, kind: str, parsed: dict, observed_at: str, text: str | None = None) -> dict:
    """The state with this reply applied. `parsed` is an account_replies parser result; `text`, the reply as read,
    is kept per page so a misread can be looked at later."""
    sections = dict(state.get('sections', {}))
    old = sections.get(kind, {'values': {}, 'pages': {}})
    values = _merge(old.get('values', {}), parsed['values'])
    if kind in WHOLE_REPLIES and not parsed.get('unread'):
        values = dict(parsed['values'])   # a whole reply's None is a reading ("Current bait: NONE"), not a gap
    if kind == 'rod':
        # Rods remain owned when OCR misses a line in a later /rod reply.
        values['owned'] = list(dict.fromkeys([*(old.get('values', {}).get('owned') or []),
                                             *(parsed['values'].get('owned') or [])]))
    if kind == 'pet' and parsed['values'].get('owned'):
        # Pets are permanent: a /pet read that missed a pet's line (Axolotl, live 2026-10-14) never loses it.
        values['owned'] = {**(old.get('values', {}).get('owned') or {}), **parsed['values']['owned']}
    page, _ = parsed.get('page') or (None, None)
    pages = dict(old.get('pages', {}))
    pages[str(page or 1)] = observed_at
    texts = dict(old.get('texts', {}))
    if text is not None:
        texts[str(page or 1)] = text
    sections[kind] = {'values': values, 'unread': section_unread(kind, values, parsed.get('unread', ())),
                      'read_at': observed_at, 'pages': pages, **({'texts': texts} if texts else {})}
    return {**state, 'sections': sections}
