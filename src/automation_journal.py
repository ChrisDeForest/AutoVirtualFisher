"""Append-only record of every automation action, written before and after each step of sending it.

Each event is an immutable JSON file under `<root>/<run id>/`, published atomically. After a crash, recover()
tells the controller which actions were never handed over (safe to forget), which may have been sent without a
recorded outcome (uncertain: never resent automatically), and which were acknowledged but not yet copied into
the logbook (re-projected idempotently).
"""
import json
from pathlib import Path
from secrets import token_hex
import threading

from .player_snapshots import new_record_id, write_json_atomically

DEFAULT_JOURNAL_ROOT = Path(__file__).parent / 'data' / 'automation_runs'
JOURNAL_SCHEMA_VERSION = 1
STATUS = {'intent': 'intended', 'dispatching': 'dispatching', 'dispatched': 'dispatched',
          'acknowledged': 'acknowledged', 'projected': 'projected', 'uncertain': 'uncertain',
          'abandoned': 'abandoned', 'resolved': 'resolved'}
UNRESOLVED = ('dispatching', 'dispatched', 'uncertain')


class AutomationJournal:
    def __init__(self, root: Path = DEFAULT_JOURNAL_ROOT):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.counters = {}

    def append(self, event: dict) -> None:
        """Write one event. Raises OSError when it cannot be written; the caller must not act as if it was."""
        if not event.get('run_id') or not event.get('type'):
            raise ValueError('A journal event needs a run_id and a type.')
        at, event_id = new_record_id()
        document = {'schema_version': JOURNAL_SCHEMA_VERSION, 'event_id': event_id, 'at': at, 'action_id': None,
                    **event}
        folder = self.root / event['run_id']
        with self.lock:
            if event['run_id'] not in self.counters:
                existing = [int(path.name.split('-', 1)[0]) for path in folder.glob('*.json')
                            if path.name.split('-', 1)[0].isdigit()] if folder.exists() else []
                self.counters[event['run_id']] = max(existing, default=0)
            self.counters[event['run_id']] += 1
            write_json_atomically(folder, f'{self.counters[event["run_id"]]:06d}-{token_hex(4)}', document)

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

    def recover(self) -> dict:
        events, warnings = self.events()
        actions = {}
        for event in events:
            action_id = event.get('action_id')
            if not action_id or event['type'] not in STATUS:
                continue
            action = actions.setdefault(action_id, {'run_id': event['run_id']})
            action['status'] = STATUS[event['type']]
            for field in ('intent', 'result', 'tally', 'reason', 'outcome', 'observation_id', 'sent_at'):
                if field in event:
                    action[field] = event[field]
        by_status = lambda *statuses: [action for action, info in actions.items() if info['status'] in statuses]
        return {'actions': actions, 'never_sent': by_status('intended'), 'unresolved': by_status(*UNRESOLVED),
                'unprojected': [action for action in by_status('acknowledged')
                                if actions[action].get('result') == 'trip'],
                'events': len(events), 'warnings': warnings}
