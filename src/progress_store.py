"""Part 6's per-account files: the daily trip index and the profile history, beside account_state.json."""
import json
import os
import threading
from copy import deepcopy
from pathlib import Path

from .player_snapshots import write_json_atomically
from .progress import empty_index, index_trips, journal_digest, parse_time

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
    from . import storage_limits as sl
    from .retention import WRITE_LOCK, trim_lines
    with WRITE_LOCK:
        with Path(path).open('a', encoding='utf-8') as file:
            file.write(json.dumps(entry) + '\n')
        trim_lines(path, sl.PROFILE_HISTORY_MAX_BYTES)


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


# What progress.journal_digest reads of an event, all that is kept of it: its kind, time and ids, a stop's or an
# outcome's reason, result and note, an act's intent, and the tally counts of a trip. Replies and OCR are dropped.
KEPT = ('type', 'at', 'run_id', 'action_id', 'reason', 'result', 'note')
TALLY = ('trips', 'fishing_xp', 'finds', 'chest_contents', 'pets_found', 'charm_levels')
MAX_CACHED_RUNS = 4096        # runs whose digest is kept, the newest by name (a run's folder is named by its start)
MAX_CACHED_EVENTS = 100_000   # the newest run's events kept per file, so a run that is going reads only new files
MAX_JOURNALS = 8
_JOURNALS = {}                # journal folder -> {'runs': {run folder: entry}, 'lock'}: the controller and the
_JOURNALS_LOCK = threading.Lock()   # page's disk view of an account share one


def _slim(event):
    if event is None:
        return None
    kept = {key: event[key] for key in KEPT if key in event}
    intent, tally = event.get('intent'), event.get('tally')
    if event['type'] == 'intent' and isinstance(intent, dict) and intent.get('kind') == 'act':
        kept['intent'] = {key: intent[key] for key in ('kind', 'arguments', 'preconditions') if key in intent}
    if event['type'] == 'acknowledged' and isinstance(tally, dict):
        kept['tally'] = {key: tally[key] for key in TALLY if key in tally}
    return kept


def _scan(folder):
    """({.json file name: (mtime, size, ctime)}, a signature of them all) for one run's folder; unreadable: none.
    Any file added, removed, replaced or edited changes the signature."""
    stamps, mixed = {}, 0
    try:
        with os.scandir(folder) as scan:
            for entry in scan:
                if not entry.name.endswith('.json'):
                    continue
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                stamps[entry.name] = stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns)
                mixed ^= hash((entry.name, stamp))
    except OSError:
        pass
    return stamps, (len(stamps), mixed)


def _run_entry(folder, previous, keep_files):
    """One run's cache entry: {'signature', 'digest', 'files'}. Its digest is worked out again only when its files
    changed; `files` (file name -> (stamp, slim event)), kept for the newest run only, lets a change read just the
    new or changed files. Any other run that changed is read whole again (rare: a run that is over is not written)."""
    stamps, signature = _scan(folder)
    previous = previous if isinstance(previous, dict) else {}
    known = previous.get('files') if isinstance(previous.get('files'), dict) else {}
    if previous.get('signature') == signature and isinstance(previous.get('digest'), dict):
        return previous if keep_files or not known else {**previous, 'files': None}
    files = {}
    for name in sorted(stamps):
        cached = known.get(name)
        if not (isinstance(cached, tuple) and len(cached) == 2 and cached[0] == stamps[name]):
            cached = (stamps[name], _slim(_read_event(folder / name)))
        files[name] = cached
    digest = journal_digest([event for _, event in files.values() if event is not None])
    return {'signature': signature, 'digest': digest,
            'files': files if keep_files and len(files) <= MAX_CACHED_EVENTS else None}


def read_journal(journal_root, cache=None):
    """The journal for the Progress view: progress.journal_digest of every run, joined ({'runs', 'acts'}). From the
    cache shared by everything reading this folder (or `cache`, run folder -> entry, when given): a run is worked
    out again only when its files changed. Runs past MAX_CACHED_RUNS (the oldest) are read each time. Unreadable
    files are skipped, as AutomationJournal.events does. The result is the caller's own copy."""
    root = Path(journal_root)
    if cache is None:
        with _JOURNALS_LOCK:
            key = str(root.resolve())
            if key not in _JOURNALS and len(_JOURNALS) >= MAX_JOURNALS:
                del _JOURNALS[next(iter(_JOURNALS))]
            shared = _JOURNALS.setdefault(key, {'runs': {}, 'lock': threading.Lock()})
        with shared['lock']:
            return deepcopy(_read_journal(root, shared['runs']))
    return deepcopy(_read_journal(root, cache))


def _read_journal(root, cache):
    try:
        folders = sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name)
    except OSError:
        folders = []
    kept = {folder.name for folder in folders[-MAX_CACHED_RUNS:]}
    newest = folders[-1].name if folders else None
    runs, acts = [], []
    for folder in folders:
        entry = _run_entry(folder, cache.get(folder.name), folder.name == newest)
        if folder.name in kept:
            cache[folder.name] = entry
        runs += entry['digest']['runs']
        acts += entry['digest']['acts']
    for name in set(cache) - kept:
        del cache[name]
    return {'runs': runs, 'acts': acts}


def view_from_disk(folder, now=None):
    """The Progress view from the account's files alone (no run going: no plan, no live profile). The money bar
    takes the last fish value read when the last /profile read had none."""
    from datetime import datetime, timezone
    from .progress import progress_view
    folder = Path(folder)
    index, bad_trips = build_index(folder)
    history, bad_lines = read_history(folder / HISTORY)
    journal = read_journal(folder / 'journal')
    profiles = [h for h in history if h.get('kind') == 'profile']
    last = dict(profiles[-1]) if profiles else {}
    if profiles and not isinstance(last.get('fish_value'), (int, float)):
        last['fish_value'] = next((h['fish_value'] for h in reversed(profiles)
                                   if isinstance(h.get('fish_value'), (int, float))), None)
    charms = next((h for h in reversed(history) if h.get('kind') == 'charms'), None)
    notes = [f'{count} {what} could not be read.' for count, what in
             ((bad_trips, 'trip records'), (bad_lines, 'profile history lines')) if count]
    return progress_view(index, history, journal, None, last, charms, now or datetime.now(timezone.utc),
                         notes=notes)
