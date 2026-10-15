"""Local, append-only storage and all-time totals for parsed fishing sessions."""
import json
from pathlib import Path

from .fishing_log import FISHING_SCHEMA_VERSION, known_name, parse_fishing
from .session_index import session_index
from . import storage_limits
from .player_snapshots import account_key, new_record_id, write_json_atomically

DEFAULT_SESSION_ROOT = Path(__file__).parent / 'data' / 'fishing_sessions'
SESSION_SCHEMA_VERSION = 1


def normalize_tally(tally):
    """Fill historical defaults and merge known item aliases without rewriting source records."""
    defaults = parse_fishing('')
    result = dict(tally)
    for field, default in defaults.items():
        value = result.get(field)
        if value is None:
            result[field] = default
        elif isinstance(default, (dict, list)) and not isinstance(value, type(default)):
            raise ValueError(f'{field} has an invalid tally shape')
        elif isinstance(default, (int, float)) and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ValueError(f'{field} must be numeric')
    for field in ('catches', 'finds', 'chest_contents'):
        normalized = {}
        for name, amount in result[field].items():
            if not isinstance(name, str) or not isinstance(amount, (int, float)) or isinstance(amount, bool):
                raise ValueError(f'{field} must map item names to numeric counts')
            canonical = known_name(name)
            normalized[canonical] = normalized.get(canonical, 0) + amount
        result[field] = normalized
    if any(not isinstance(quest, dict) for quest in result['quests_completed']):
        raise ValueError('quests_completed must contain objects')
    result.pop('warnings', None)
    return result


def _read_session(path, account_name):
    session = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(session, dict):
        raise ValueError('session must be an object')
    if session.get('schema_version') != SESSION_SCHEMA_VERSION:
        raise ValueError('unsupported schema version')
    if session.get('account_key') != account_key(account_name):
        raise ValueError('account identity does not match its directory')
    if not isinstance(session.get('tally'), dict) or not session.get('captured_at'):
        raise ValueError('required session fields are missing')
    session['tally'] = normalize_tally(session['tally'])
    return session


def load_sessions(account_name: str, root: Path = DEFAULT_SESSION_ROOT) -> tuple[list[dict], list[str]]:
    """Return the account's sessions newest first, plus warnings for unreadable files."""
    folder = Path(root) / account_key(account_name)
    sessions, warnings = [], []
    if not folder.exists():
        return sessions, warnings
    for path in folder.glob('*.json'):
        try:
            session = _read_session(path, account_name)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            warnings.append(f'{path.name}: {error}')
            continue
        sessions.append(session)
    sessions.sort(key=lambda item: (item['captured_at'], item.get('session_id', '')), reverse=True)
    return sessions, warnings


MANUAL_PROVENANCE = {'source': 'manual', 'identity_status': 'user_attributed'}


def save_session(tally: dict, account_name: str | None, source_fingerprint: str,
                 root: Path = DEFAULT_SESSION_ROOT, provenance: dict | None = None) -> dict:
    """Save a parsed tally. `provenance` says where it came from; by default the person saving it attributed it
    to the account by hand."""
    name = account_name.strip() if isinstance(account_name, str) else ''
    if not name:
        return {'status': 'rejected', 'reason': 'Choose an account before saving a fishing session.'}
    if not tally.get('trips'):
        return {'status': 'rejected', 'reason': 'No fishing trips were found in the pasted results.'}
    folder = Path(root) / account_key(name)
    with session_index(folder, lambda path: _read_session(path, name)) as index:
        warnings = [row[0] for row in index.execute('SELECT warning FROM records WHERE warning IS NOT NULL')]
        row = index.execute('SELECT payload FROM records WHERE fingerprint = ? ORDER BY name DESC LIMIT 1',
                            (source_fingerprint,)).fetchone()
        previous = json.loads(row[0]) if row else None
        if previous:
            return {'status': 'duplicate', 'session': previous, 'warnings': warnings}
        captured_at, session_id = new_record_id()
        stored_tally = normalize_tally(tally)
        session = {
            'schema_version': SESSION_SCHEMA_VERSION,
            'session_id': session_id,
            'captured_at': captured_at,
            'account_name': name,
            'account_key': account_key(name),
            'source_fingerprint': source_fingerprint,
            'tally': stored_tally,
            'provenance': provenance or MANUAL_PROVENANCE,
        }
        files, size = index.execute('SELECT files, bytes FROM usage').fetchone()
        encoded_size = len((json.dumps(session, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        if files + 1 > storage_limits.TRIP_MAX_FILES or size + encoded_size > storage_limits.TRIP_MAX_BYTES:
            raise OSError('Trip history storage limit reached; archive this account history before saving new trips.')
        write_json_atomically(Path(root) / session['account_key'], session_id, session)
        path = folder / f'{session_id}.json'
        stat = path.stat()
        stamp = f'{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_size}'
        index.execute('INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?, ?)',
                      (path.name, stamp, source_fingerprint, json.dumps(session), None))
        index.commit()
        return {'status': 'saved', 'session': session, 'warnings': warnings}


def _merge(total: dict, counts) -> None:
    if isinstance(counts, dict):
        for name, amount in counts.items():
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                total[name] = total.get(name, 0) + amount


def fishing_totals(account_name: str, root: Path = DEFAULT_SESSION_ROOT) -> dict:
    sessions, warnings = load_sessions(account_name, root)
    totals = {'sessions': len(sessions), 'trips': 0, 'boosted_trips': 0, 'catches': {}, 'fishing_xp': 0,
              'finds': {}, 'chest_contents': {}, 'quests_completed': 0, 'quest_money': 0, 'quest_xp': 0,
              'level_ups': 0, 'level_up_money': 0, 'chest_money': 0, 'warnings': warnings}
    for session in sessions:
        tally = session['tally']
        if tally.get('schema_version') != FISHING_SCHEMA_VERSION:
            warnings.append(f'{session.get("session_id", "session")}: unsupported tally version')
            continue
        for field in ('trips', 'boosted_trips', 'fishing_xp', 'level_up_money', 'chest_money'):
            totals[field] += tally.get(field, 0)
        for field in ('catches', 'finds', 'chest_contents'):
            _merge(totals[field], tally.get(field))
        quests = tally.get('quests_completed', [])
        totals['quests_completed'] += len(quests)
        totals['quest_money'] += sum(quest.get('money', 0) for quest in quests)
        totals['quest_xp'] += sum(quest.get('xp', 0) for quest in quests)
        totals['level_ups'] += len(tally.get('level_ups', []))
    trips = totals['trips']
    totals['fish_caught'] = sum(totals['catches'].values())
    totals['fish_per_trip'] = totals['fish_caught'] / trips if trips else None
    totals['xp_per_trip'] = totals['fishing_xp'] / trips if trips else None
    totals['finds_per_100_trips'] = {name: count * 100 / trips for name, count in totals['finds'].items()} if trips else {}
    return totals
