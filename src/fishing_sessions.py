"""Local, append-only storage and all-time totals for parsed fishing sessions."""
import json
from pathlib import Path

from .fishing_log import FISHING_SCHEMA_VERSION
from .player_snapshots import account_key, new_record_id, write_json_atomically

DEFAULT_SESSION_ROOT = Path(__file__).parent / 'data' / 'fishing_sessions'
SESSION_SCHEMA_VERSION = 1


def load_sessions(account_name: str, root: Path = DEFAULT_SESSION_ROOT) -> tuple[list[dict], list[str]]:
    """Return the account's sessions newest first, plus warnings for unreadable files."""
    folder = Path(root) / account_key(account_name)
    sessions, warnings = [], []
    if not folder.exists():
        return sessions, warnings
    for path in folder.glob('*.json'):
        try:
            session = json.loads(path.read_text(encoding='utf-8'))
            if session.get('schema_version') != SESSION_SCHEMA_VERSION:
                raise ValueError('unsupported schema version')
            if session.get('account_key') != account_key(account_name):
                raise ValueError('account identity does not match its directory')
            if not isinstance(session.get('tally'), dict) or not session.get('captured_at'):
                raise ValueError('required session fields are missing')
        except (OSError, json.JSONDecodeError, ValueError) as error:
            warnings.append(f'{path.name}: {error}')
            continue
        sessions.append(session)
    sessions.sort(key=lambda item: (item['captured_at'], item.get('session_id', '')), reverse=True)
    return sessions, warnings


def save_session(tally: dict, account_name: str | None, source_fingerprint: str,
                 root: Path = DEFAULT_SESSION_ROOT) -> dict:
    name = account_name.strip() if isinstance(account_name, str) else ''
    if not name:
        return {'status': 'rejected', 'reason': 'Choose an account before saving a fishing session.'}
    if not tally.get('trips'):
        return {'status': 'rejected', 'reason': 'No fishing trips were found in the pasted results.'}
    sessions, warnings = load_sessions(name, root)
    previous = next((item for item in sessions if item.get('source_fingerprint') == source_fingerprint), None)
    if previous:
        return {'status': 'duplicate', 'session': previous, 'warnings': warnings}
    captured_at, session_id = new_record_id()
    stored_tally = {field: value for field, value in tally.items() if field != 'warnings'}
    session = {
        'schema_version': SESSION_SCHEMA_VERSION,
        'session_id': session_id,
        'captured_at': captured_at,
        'account_name': name,
        'account_key': account_key(name),
        'source_fingerprint': source_fingerprint,
        'tally': stored_tally,
    }
    write_json_atomically(Path(root) / session['account_key'], session_id, session)
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
              'level_ups': 0, 'level_up_money': 0, 'warnings': warnings}
    for session in sessions:
        tally = session['tally']
        if tally.get('schema_version') != FISHING_SCHEMA_VERSION:
            warnings.append(f'{session.get("session_id", "session")}: unsupported tally version')
            continue
        for field in ('trips', 'boosted_trips', 'fishing_xp', 'level_up_money'):
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
