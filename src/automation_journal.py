"""Append-only record of every automation action, written before and after each step of sending it.

Each event is an immutable JSON file under `<root>/<run id>/`, published atomically. After a crash, recover()
tells the controller which actions were never handed over (safe to forget), which may have been sent without a
recorded outcome (uncertain: never resent automatically), and which were acknowledged but not yet copied into
the logbook (re-projected idempotently).
"""
import copy
import json
import os
from pathlib import Path
from secrets import token_hex
import threading

from .player_snapshots import new_record_id, write_json_atomically
from . import storage_limits

DEFAULT_JOURNAL_ROOT = Path(__file__).parent / 'data' / 'automation_runs'
JOURNAL_SCHEMA_VERSION = 1
STATUS = {'intent': 'intended', 'dispatching': 'dispatching', 'dispatched': 'dispatched',
          'acknowledged': 'acknowledged', 'projected': 'projected', 'uncertain': 'uncertain',
          'abandoned': 'abandoned', 'resolved': 'resolved'}
UNRESOLVED = ('dispatching', 'dispatched', 'uncertain')
# journal root -> run name -> (its files' names, sizes and times, its share of recover()); rebuilt when they change.
_RECOVERY = {}
_RECOVERY_LOCK = threading.Lock()


class AutomationJournal:
    def __init__(self, root: Path = DEFAULT_JOURNAL_ROOT):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.counters = {}
        self.usage = None

    def _load_usage(self):
        """Account-wide JSON usage; refresh on run starts and before refusing new work."""
        count = size = 0
        if self.root.exists():
            with os.scandir(self.root) as scan:
                folders = [entry.path for entry in scan if entry.is_dir()]
            for folder in folders:
                with os.scandir(folder) as scan:
                    for entry in scan:
                        if entry.name.endswith('.json') and entry.is_file():
                            try:
                                size += entry.stat().st_size
                                count += 1
                            except FileNotFoundError:
                                continue
        return count, size

    def append(self, event: dict) -> None:
        """Write one event. Raises OSError when it cannot be written; the caller must not act as if it was."""
        if not event.get('run_id') or not event.get('type'):
            raise ValueError('A journal event needs a run_id and a type.')
        at, event_id = new_record_id()
        document = {'schema_version': JOURNAL_SCHEMA_VERSION, 'event_id': event_id, 'at': at, 'action_id': None,
                    **event}
        folder = self.root / event['run_id']
        with self.lock:
            refreshed = self.usage is None or event['type'] == 'run_started'
            if refreshed:
                self.usage = self._load_usage()
            encoded_size = len((json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
            files, size = self.usage
            # Once a command may have been sent, outcomes/recovery must remain durable
            # even above quota. Refuse only steps which start or dispatch new work.
            new_work = event['type'] in ('run_started', 'intent', 'dispatching')
            over_limit = lambda: (files + 1 > storage_limits.JOURNAL_MAX_FILES
                                  or size + encoded_size > storage_limits.JOURNAL_MAX_BYTES)
            if new_work and over_limit():
                if not refreshed:
                    # An operator may have archived history since the last append.
                    # Keep normal appends cheap, but never reject using stale usage.
                    self.usage = self._load_usage()
                    files, size = self.usage
                if over_limit():
                    raise OSError('Action journal storage limit reached; archive this account history before starting new actions.')
            if event['run_id'] not in self.counters:
                existing = [int(path.name.split('-', 1)[0]) for path in folder.glob('*.json')
                            if path.name.split('-', 1)[0].isdigit()] if folder.exists() else []
                self.counters[event['run_id']] = max(existing, default=0)
            self.counters[event['run_id']] += 1
            write_json_atomically(folder, f'{self.counters[event["run_id"]]:06d}-{token_hex(4)}', document)
            self.usage = (files + 1, size + encoded_size)

    def events(self) -> tuple[list[dict], list[str]]:
        """All readable events in order (runs by name, which starts with their start time; events by number)."""
        events, warnings = [], []
        if not self.root.exists():
            return events, warnings
        for path in sorted(self.root.glob('*/*.json'), key=lambda path: (path.parent.name, path.name)):
            try:
                event = json.loads(path.read_text(encoding='utf-8'))
                if event.get('schema_version') != JOURNAL_SCHEMA_VERSION or not event.get('type'):
                    raise ValueError('not a journal event')
            except (OSError, json.JSONDecodeError, ValueError) as error:
                warnings.append(f'{path.parent.name}/{path.name}: {error}')
                continue
            events.append(event)
        return events, warnings

    def _fold_run(self, folder):
        """One run's share of recover(): per action, its last status and the fields its events set, in order."""
        actions, checked, warnings, count = {}, set(), [], 0
        for path in sorted(folder.glob('*.json'), key=lambda path: path.name):
            try:
                event = json.loads(path.read_text(encoding='utf-8'))
                if event.get('schema_version') != JOURNAL_SCHEMA_VERSION or not event.get('type'):
                    raise ValueError('not a journal event')
            except (OSError, json.JSONDecodeError, ValueError) as error:
                warnings.append(f'{path.parent.name}/{path.name}: {error}')
                continue
            count += 1
            if event['type'] == 'journal_damage_resolved':
                checked.update(event.get('files', []))
            action_id = event.get('action_id')
            if not action_id or event['type'] not in STATUS:
                continue
            action = actions.setdefault(action_id, {'run_id': event['run_id']})
            action['status'] = STATUS[event['type']]
            for field in ('intent', 'result', 'tally', 'reason', 'outcome', 'observation_id', 'sent_at'):
                if field in event:
                    action[field] = event[field]
        return {'actions': actions, 'checked': checked, 'warnings': warnings, 'events': count}

    def recover(self) -> dict:
        """What every action's events add up to, over all runs in order. A run whose files are unchanged (names,
        sizes, modification times) is taken from the last recovery in this process instead of read again: every
        Start read the whole journal (90,374 events, 8.9 s live on 2026-10-16, longer each day)."""
        runs = []
        if self.root.exists():
            with os.scandir(self.root) as scan:
                folders = sorted((entry.name, entry.path) for entry in scan if entry.is_dir())
            with _RECOVERY_LOCK:
                cache = _RECOVERY.setdefault(str(self.root.resolve()), {})
                for name, path in folders:
                    with os.scandir(path) as scan:
                        signature = tuple(sorted((entry.name, entry.stat().st_size, entry.stat().st_mtime_ns)
                                                 for entry in scan if entry.name.endswith('.json')))
                    cached = cache.get(name)
                    if cached is None or cached[0] != signature:
                        cached = cache[name] = (signature, self._fold_run(Path(path)))
                    runs.append(cached[1])
                for name in set(cache) - {name for name, _ in folders}:
                    del cache[name]
        actions, checked, warnings, count = {}, set(), [], 0
        for run in runs:
            for action_id, info in run['actions'].items():
                action = actions.setdefault(action_id, {'run_id': info['run_id']})
                action.update({key: value for key, value in info.items() if key != 'run_id'})
            checked |= run['checked']
            warnings += run['warnings']
            count += run['events']
        by_status = lambda *statuses: [action for action, info in actions.items() if info['status'] in statuses]
        # Unreadable records not yet checked by a person. Until they are, nothing in them can be ruled out.
        damaged = [warning.split(':', 1)[0] for warning in warnings if warning.split(':', 1)[0] not in checked]
        # Cached values are shared between recoveries: callers get their own copies of what they act on (never sent,
        # unresolved, unprojected); the rest, only read, share their values.
        acted = set(by_status('intended', 'acknowledged', *UNRESOLVED))
        actions = {action_id: copy.deepcopy(info) if action_id in acted else info for action_id, info in actions.items()}
        return {'actions': actions, 'damaged': damaged, 'never_sent': by_status('intended'), 'unresolved': by_status(*UNRESOLVED),
                'unprojected': [action for action in by_status('acknowledged')
                                if actions[action].get('result') == 'trip'],
                'events': count, 'warnings': warnings}
