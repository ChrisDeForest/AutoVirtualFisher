"""Part 6's per-account files: the daily trip index and the profile history, beside account_state.json."""
import json
from pathlib import Path

from .player_snapshots import write_json_atomically
from .progress import empty_index, index_trips, parse_time

INDEX, HISTORY = 'progress_index.json', 'profile_history.jsonl'


def load_index(path):
    """The saved index, or an empty one when it is missing or damaged (it is rebuilt from the trips)."""
    try:
        index = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return empty_index()
    if (isinstance(index, dict) and index.get('version') == 1
            and isinstance(index.get('files'), list) and all(isinstance(name, str) for name in index['files'])
            and isinstance(index.get('days'), dict) and all(isinstance(day, dict) for day in index['days'].values())
            and (index.get('last_at') is None or isinstance(index['last_at'], str))):
        try:
            if index['last_at'] is not None:
                parse_time(index['last_at'])
        except ValueError:
            return empty_index()
        return index
    return empty_index()


def save_index(path, index):
    path = Path(path)
    write_json_atomically(path.parent, path.stem, index)


def build_index(folder, tz=None):
    """(index, unreadable trip files): the saved index extended with trip files it has not counted yet."""
    folder = Path(folder)
    index = load_index(folder / INDEX)
    counted = set(index['files'])
    records, bad = [], 0
    for path in sorted((folder / 'fishing').glob('**/*.json')):
        if path.stem in counted:
            continue
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            record = {'session_id': path.stem, 'captured_at': record['captured_at'],
                      'tally': record.get('tally') or {}}
            index_trips(empty_index(), [record], tz)   # a file that cannot be indexed is bad, not a crash later
            records.append(record)
        except Exception:   # any bad content: skip and count
            bad += 1
    if records:
        index = index_trips(index, records, tz)
        try:
            save_index(folder / INDEX, index)
        except OSError:
            pass   # rebuilt next time
    return index, bad


def append_history(path, entry):
    with Path(path).open('a', encoding='utf-8') as file:
        file.write(json.dumps(entry) + '\n')


def read_history(path):
    """(entries oldest first, bad lines); a missing file is no history."""
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return [], 0
    entries, bad = [], 0
    for line in lines:
        try:
            entry = json.loads(line)
            if isinstance(entry, dict) and 'at' in entry:
                entries.append(entry)
            else:
                bad += 1
        except ValueError:
            bad += 1
    return entries, bad


def _read_event(path):
    """One journal event, or None when it cannot be read (skipped, as AutomationJournal.events does)."""
    from .automation_journal import JOURNAL_SCHEMA_VERSION
    try:
        event = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(event, dict) or event.get('schema_version') != JOURNAL_SCHEMA_VERSION \
                or not event.get('type'):
            return None
    except (OSError, ValueError):
        return None
    return event


def journal_events(journal_root, cache):
    """AutomationJournal(journal_root).events()[0], but a run folder is read again only when its file count
    changed: events are immutable files, so a finished run's folder never changes. `cache` (run folder name ->
    (file count, events)) is kept by the caller between calls; folders gone from disk are dropped from it."""
    root = Path(journal_root)
    folders = sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name) \
        if root.is_dir() else []
    events = []
    for folder in folders:
        files = sorted(folder.glob('*.json'), key=lambda path: path.name)
        cached = cache.get(folder.name)
        if cached is None or cached[0] != len(files):
            cached = cache[folder.name] = (len(files), [e for e in map(_read_event, files) if e is not None])
        events.extend(cached[1])
    for name in set(cache) - {folder.name for folder in folders}:
        del cache[name]
    return events


_DISK_JOURNALS = {}   # account folder -> journal_events cache, for view_from_disk


def view_from_disk(folder, now=None):
    """The Progress view from the account's files alone (no run going: no plan, no live profile)."""
    from datetime import datetime, timezone
    from .progress import progress_view
    folder = Path(folder)
    index, bad_trips = build_index(folder)
    history, bad_lines = read_history(folder / HISTORY)
    events = journal_events(folder / 'journal', _DISK_JOURNALS.setdefault(str(folder.resolve()), {}))
    last = next((h for h in reversed(history) if h.get('kind') == 'profile'), {})
    charms = next((h for h in reversed(history) if h.get('kind') == 'charms'), None)
    notes = [f'{count} {what} could not be read.' for count, what in
             ((bad_trips, 'trip records'), (bad_lines, 'profile history lines')) if count]
    return progress_view(index, history, events, None, last, charms, now or datetime.now(timezone.utc),
                         notes=notes)
