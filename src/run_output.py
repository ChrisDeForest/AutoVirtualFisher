"""Readable console updates mirrored to the latest five local run logs."""
from datetime import datetime
from pathlib import Path
import re
import sys
from .player_snapshots import new_record_id
from . import storage_limits as sl
from .retention import WRITE_LOCK, owned_files, prune_files, trim_lines

DEFAULT_RUNS = Path(__file__).resolve().parent.parent / 'docs' / 'runs'
LOG_NAME = re.compile(r'^avf-run-\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{8}\.log$')


class RunOutput:
    def __init__(self, root=DEFAULT_RUNS, run_id=None, console=None):
        self.console = console if console is not None else sys.stdout
        self.previous = self.last_result = None
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / f'avf-run-{run_id or new_record_id()[1]}.log'
        if not LOG_NAME.fullmatch(self.path.name):
            raise ValueError('Invalid run log name')
        self.file = self.path.open('x', encoding='utf-8')
        # Existing owned logs are brought under the same per-file bound; unrelated files are untouched.
        for old in owned_files(self.root, LOG_NAME):
            if old == self.path:
                continue
            try:
                trim_lines(old, sl.RUN_LOG_MAX_BYTES)
            except OSError:
                pass
        prune_files(self.root, LOG_NAME, sl.RUN_LOG_MAX_FILES,
                    sl.RUN_LOG_MAX_FILES * sl.RUN_LOG_MAX_BYTES)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.file.close()

    def say(self, message):
        line = f'[{datetime.now().astimezone():%H:%M:%S}] {message}'
        print(line, file=self.console, flush=True)
        with WRITE_LOCK:
            self.file.write(line + '\n')
            self.file.flush()
            if self.file.tell() > sl.RUN_LOG_MAX_BYTES:
                # Windows requires the writer to release its handle before an atomic replacement.
                self.file.close()
                try:
                    trim_lines(self.path, sl.RUN_LOG_MAX_BYTES)
                finally:
                    self.file = self.path.open('a', encoding='utf-8')

    def update(self, status):
        result = status.get('last_result') or {}
        action = result.get('action_id')
        if action and action != self.last_result:
            self.last_result = action
            if result.get('result') == 'refreshed':
                command = '/profile' if action.endswith('refresh_profile') else '/buffs'
                self.say(f'{command} confirmed.')
            elif result.get('result') == 'trip':
                self.say(f'Trip {status["trips"]} saved.')
        phase = status['phase']
        key = (phase, status.get('pending'), status.get('trips'))
        if key == self.previous:
            return
        self.previous = key
        if phase == 'WAITING_REPLY':
            pending = status.get('pending') or ''
            command = next((cmd for kind, cmd in (('refresh_profile', '/profile'), ('refresh_buffs', '/buffs'))
                            if pending.endswith(kind)), None)
            self.say(f'Reading {command}...' if command else 'Fishing...')
        elif phase == 'WAITING_DELIVERY':
            self.say('Discord shows Sending command… Waiting and retrying reply capture (up to 60 seconds); command not resent.')
        elif phase == 'RETRYING_STARTUP':
            startup = status.get('startup') or {}
            problems = startup.get('problems') or {}
            for kind, reasons in problems.items():
                command = '/profile' if kind == 'refresh_profile' else '/buffs'
                attempts = (startup.get('failures') or {}).get(kind, 0)
                self.say(f'{command}: {" ".join(reasons)} Retrying in 2 seconds (attempt {attempts + 1}/3).')
        elif phase in ('NEEDS_REVIEW', 'FAILED', 'PAUSED'):
            reason = re.sub(r'^\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{8}-\d+-[a-z_]+:\s*', '', status.get('reason') or '')
            label = {'NEEDS_REVIEW': 'Needs review', 'FAILED': 'Failed', 'PAUSED': 'Paused'}[phase]
            self.say(f'{label}: {reason}')
            if phase == 'NEEDS_REVIEW':
                for action in status.get('unresolved', []):
                    if status.get('run_id') and not action.startswith(status['run_id'] + '-'):
                        self.say('Earlier run is blocking startup; no new commands were sent.')
                    self.say(f'Action needing review: {action}')
                self.say('After checking the saved reply, resolve with --resolve ACTION_ID --outcome happened (or not_sent if it was never sent).')
        elif phase == 'STOPPED':
            trips = status.get('trips', 0)
            reason = status.get('reason', '')
            suffix = f' {reason}' if reason and reason != 'Stopped.' else ''
            self.say(f'Stopped. {trips} trip{"" if trips == 1 else "s"} saved this run.{suffix}')
