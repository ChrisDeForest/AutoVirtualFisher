"""Local, append-only persistence for parsed player states."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from secrets import token_hex

from .player_state import display_value


DEFAULT_SNAPSHOT_ROOT = Path(__file__).parent / 'data' / 'player_snapshots'
SNAPSHOT_SCHEMA_VERSION = 1
# Parser warnings that quote a pasted line look like "<section>: <reason>: <line>".
LINE_WARNING = re.compile(r'^(profile|buffs): ([^:]+): .*$', re.DOTALL)


def validate_full_state(state: dict) -> tuple[bool, list[str]]:
    profile = state.get('profile', {})
    reasons = []
    if not isinstance(profile.get('account_name'), str) or not profile['account_name'].strip():
        reasons.append('Account name is missing.')
    balance = profile.get('balance')
    if isinstance(balance, bool) or not isinstance(balance, (int, float)) or balance < 0:
        reasons.append('Balance must be a nonnegative number.')
    level = profile.get('level')
    if isinstance(level, bool) or not isinstance(level, int) or level < 0:
        reasons.append('Level must be a nonnegative integer.')
    return not reasons, reasons


def account_key(name: str) -> str:
    canonical = name.strip().casefold()
    ascii_name = canonical.encode('ascii', 'ignore').decode('ascii')
    slug = re.sub(r'[^a-z0-9]+', '-', ascii_name).strip('-') or 'account'
    digest = sha256(canonical.encode('utf-8')).hexdigest()[:8]
    return f'{slug}-{digest}'


def _without_pasted_text(state: dict) -> dict:
    """Copy a parsed state, replacing quoted pasted lines with counts and redacted warnings."""
    stored = deepcopy(state)
    unparsed = stored.pop('unparsed', None)
    if isinstance(unparsed, dict):
        stored['unparsed_line_counts'] = {section: len(lines) for section, lines in unparsed.items()
                                          if isinstance(lines, list)}
    if isinstance(stored.get('warnings'), list):
        stored['warnings'] = [LINE_WARNING.sub(r'\1: \2 (line omitted)', warning) if isinstance(warning, str) else warning
                              for warning in stored['warnings']]
    return stored


def _canonical_state(state: dict) -> str:
    comparable = deepcopy(state)
    if isinstance(comparable.get('warnings'), list):
        comparable['warnings'] = sorted(comparable['warnings'])
    return json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def load_history(account_name: str, root: Path = DEFAULT_SNAPSHOT_ROOT,
                 limit: int | None = None) -> tuple[list[dict], list[str]]:
    folder = Path(root) / account_key(account_name)
    snapshots = []
    warnings = []
    if not folder.exists():
        return snapshots, warnings
    for path in folder.glob('*.json'):
        try:
            snapshot = json.loads(path.read_text(encoding='utf-8'))
            if snapshot.get('schema_version') != SNAPSHOT_SCHEMA_VERSION:
                raise ValueError('unsupported schema version')
            if snapshot.get('account_key') != account_key(account_name):
                raise ValueError('account identity does not match its directory')
            if not isinstance(snapshot.get('state'), dict) or not snapshot.get('captured_at'):
                raise ValueError('required snapshot fields are missing')
        except (OSError, json.JSONDecodeError, ValueError) as error:
            warnings.append(f'{path.name}: {error}')
            continue
        snapshots.append(snapshot)
    snapshots.sort(key=lambda item: (item['captured_at'], item.get('snapshot_id', '')), reverse=True)
    return (snapshots[:limit] if limit is not None else snapshots), warnings


def list_accounts(root: Path = DEFAULT_SNAPSHOT_ROOT) -> tuple[list[str], list[str]]:
    accounts = {}
    warnings = []
    root = Path(root)
    if not root.exists():
        return [], []
    for path in root.glob('*/*.json'):
        try:
            snapshot = json.loads(path.read_text(encoding='utf-8'))
            if snapshot.get('schema_version') != SNAPSHOT_SCHEMA_VERSION:
                raise ValueError('unsupported schema version')
            name = snapshot.get('account_name')
            if not isinstance(name, str) or not name.strip():
                raise ValueError('account name is missing')
            if path.parent.name != account_key(name):
                raise ValueError('account identity does not match its directory')
        except (OSError, json.JSONDecodeError, ValueError) as error:
            warnings.append(f'{path.name}: {error}')
            continue
        accounts[path.parent.name] = name
    return sorted(accounts.values(), key=str.casefold), warnings


def save_snapshot(state: dict, root: Path = DEFAULT_SNAPSHOT_ROOT, mode: str = 'automatic',
                  account_name: str | None = None) -> dict:
    if mode not in ('automatic', 'manual'):
        return {'status': 'rejected', 'reason': 'Save mode must be automatic or manual.'}
    parsed_name = state.get('profile', {}).get('account_name')
    explicit_name = account_name.strip() if isinstance(account_name, str) else None
    if parsed_name and explicit_name and parsed_name.casefold() != explicit_name.casefold():
        return {'status': 'rejected', 'reason': 'Selected account does not match the parsed profile account.'}
    chosen_name = parsed_name or explicit_name
    if not chosen_name:
        return {'status': 'rejected', 'reason': 'Choose an account before saving a partial snapshot.'}

    state = _without_pasted_text(state)
    full, reasons = validate_full_state(state)
    if mode == 'automatic' and not full:
        return {'status': 'rejected', 'reason': ' '.join(reasons)}
    validity = 'full' if full else 'partial'
    history, history_warnings = load_history(chosen_name, root)
    if mode == 'automatic':
        latest_full = next((item for item in history if item.get('validity') == 'full'), None)
        if latest_full and _canonical_state(latest_full['state']) == _canonical_state(state):
            return {'status': 'unchanged', 'snapshot': latest_full, 'warnings': history_warnings}

    captured_at = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    snapshot_id = captured_at.replace(':', '').replace('-', '') + '-' + token_hex(4)
    key = account_key(chosen_name)
    snapshot = {
        'schema_version': SNAPSHOT_SCHEMA_VERSION,
        'snapshot_id': snapshot_id,
        'captured_at': captured_at,
        'account_name': chosen_name,
        'account_key': key,
        'save_mode': mode,
        'validity': validity,
        'state': state,
        'parser_warnings': list(state.get('warnings', [])),
    }
    folder = Path(root) / key
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f'{snapshot_id}.json'
    temporary = folder / f'.{snapshot_id}.tmp'
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {'status': 'saved', 'snapshot': snapshot, 'warnings': history_warnings}


def latest_full_snapshots(account_name: str, root: Path = DEFAULT_SNAPSHOT_ROOT,
                          count: int = 2) -> tuple[list[dict], list[str]]:
    history, warnings = load_history(account_name, root)
    return [item for item in history if item.get('validity') == 'full'][:count], warnings


def _scalar_change(older, newer) -> dict:
    numeric = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool)
    if not numeric(older) or not numeric(newer):
        return {'status': 'unknown'}
    return {'status': 'ok', 'from': older, 'to': newer, 'delta': newer - older}


def _inventory_change(older, newer) -> dict:
    if not isinstance(older, dict) or not isinstance(newer, dict):
        return {'status': 'unknown'}
    deltas = {}
    for item in sorted(older.keys() & newer.keys(), key=str.casefold):
        change = _scalar_change(older[item], newer[item])
        if change['status'] == 'ok':
            deltas[item] = change['delta']
    return {'status': 'ok', 'deltas': deltas}


def _pet_change(older, newer) -> dict:
    if not isinstance(older, dict) or not isinstance(newer, dict):
        return {'status': 'unknown'}
    changes = {}
    for pet in sorted(older.keys() & newer.keys(), key=str.casefold):
        if not isinstance(older[pet], dict) or not isinstance(newer[pet], dict):
            continue
        changes[pet] = {
            'level': _scalar_change(older[pet].get('level'), newer[pet].get('level')),
            'xp_current': _scalar_change(older[pet].get('xp_current'), newer[pet].get('xp_current')),
        }
    return {'status': 'ok', 'pets': changes}


def compare_latest(account_name: str, root: Path = DEFAULT_SNAPSHOT_ROOT) -> dict:
    snapshots, warnings = latest_full_snapshots(account_name, root, count=2)
    newer = snapshots[0] if snapshots else None
    older = snapshots[1] if len(snapshots) > 1 else None
    if older is None:
        return {'status': 'needs_another_full_snapshot', 'older': None, 'newer': newer,
                'changes': {}, 'warnings': warnings}
    old_state, new_state = older['state'], newer['state']
    old_profile, new_profile = old_state.get('profile', {}), new_state.get('profile', {})
    old_inventory, new_inventory = old_state.get('inventory', {}), new_state.get('inventory', {})
    old_bait, new_bait = old_profile.get('bait'), new_profile.get('bait')
    if old_bait is None or new_bait is None:
        bait = {'status': 'unknown'}
    elif old_bait != new_bait:
        bait = {'status': 'changed', 'from': old_bait, 'to': new_bait}
    else:
        quantity = _scalar_change(old_profile.get('bait_quantity'), new_profile.get('bait_quantity'))
        bait = ({'status': 'ok', 'bait': new_bait, 'delta': quantity['delta']}
                if quantity['status'] == 'ok' else {'status': 'unknown'})
    changes = {
        field: _scalar_change(old_profile.get(field), new_profile.get(field))
        for field in ('balance', 'level', 'xp_current', 'fish_value')
    }
    changes['bait'] = bait
    changes['inventory'] = {
        category: _inventory_change(old_inventory.get(category), new_inventory.get(category))
        for category in ('fish', 'exotic_fish', 'special')
    }
    changes['pets'] = _pet_change(old_state.get('pets', {}).get('owned'),
                                  new_state.get('pets', {}).get('owned'))
    return {'status': 'ok', 'older': older, 'newer': newer, 'changes': changes,
            'warnings': warnings}


def _signed(value) -> str:
    return f'{value:+,}'


def describe_changes(changes: dict) -> list[tuple[str, str | None]]:
    """Turn compare_latest() changes into (label, text) rows; text is None when unknown."""
    def scalar(change):
        if change['status'] != 'ok':
            return None
        return f'{_signed(change["delta"])} ({display_value(change["from"])} -> {display_value(change["to"])})'

    rows = [(label, scalar(changes[field])) for field, label in (
        ('balance', 'Balance'), ('level', 'Level'), ('xp_current', 'Current XP'), ('fish_value', 'Fish value'))]
    bait = changes['bait']
    if bait['status'] == 'ok':
        rows.append(('Bait', f'{bait["bait"]}: {_signed(bait["delta"])}'))
    elif bait['status'] == 'changed':
        rows.append(('Bait', f'Changed from {bait["from"]} to {bait["to"]}'))
    else:
        rows.append(('Bait', None))
    for category, change in changes['inventory'].items():
        label = category.replace('_', ' ').capitalize()
        if change['status'] != 'ok':
            rows.append((label, None))
            continue
        moved = [f'{item} {_signed(delta)}' for item, delta in change['deltas'].items() if delta]
        rows.append((label, ', '.join(moved) or 'No change'))
    if changes['pets']['status'] != 'ok':
        rows.append(('Pets', None))
    for pet, change in changes['pets'].get('pets', {}).items():
        parts = [f'{label} {_signed(change[field]["delta"])}' if change[field]['status'] == 'ok' else f'{label} unknown'
                 for field, label in (('level', 'Lvl'), ('xp_current', 'XP'))]
        rows.append((f'Pet: {pet}', ', '.join(parts)))
    return rows
