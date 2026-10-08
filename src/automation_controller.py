"""Run the fishing loop: choose an action, journal it, send it, confirm its reply, update state, wait.

At most one command is unresolved at a time. Every command is journaled before it is handed over, and its
hand-over and reply after. A command whose outcome is not known (no reply in time, a failed or unrecorded
hand-over, a reply without evidence of which command it answers, an unreadable fishing result) is never resent: it
becomes uncertain and this run stops sending until a person resolves it. A later run is never blocked by it:
start() notes an earlier run's uncertain commands in Needs review and still counts any reply to them it sees. Confirmed read-only startup replies
that fail OCR are retried up to three attempts, with a delay, unless a retry reads exactly as the last one did
(the same pixels give the same misread); they never establish state until valid.
"""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import re
import threading
import time

from .automation_journal import AutomationJournal
from .automation_models import AutomationConfig, CapabilityError, TransportError
from .automation_policy import next_action
from .automation_state import AutomationState, apply_observation
from .startup_fields import apply_startup, startup_problems
from .fishing_log import parse_fishing
from .fishing_sessions import save_session
from .live_capture import fishing_problems
from .observation import VERIFIED
from .player_snapshots import new_record_id
from .review_queue import add_item

COOLDOWN_REPLY = re.compile(r'You must wait ([\d.]+) seconds? before fishing again', re.I)
RESOLUTIONS = ('happened', 'not_sent')
STARTUP_ATTEMPTS = 3
STARTUP_RETRY_DELAY = 2.0


def _iso(moment: datetime) -> str:
    return moment.isoformat()


class AutomationController:
    def __init__(self, transport, config: AutomationConfig, journal: AutomationJournal, fishing_root: Path,
                 review_root: Path, monotonic=time.monotonic, utc=None, policy=next_action, interval=0.2):
        self.transport, self.config, self.journal, self.policy = transport, config, journal, policy
        self.roots = {'fishing': Path(fishing_root), 'review': Path(review_root)}
        self.monotonic = monotonic
        self.utc = utc or (lambda: datetime.now(timezone.utc))
        self.interval = interval
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.generation = 0
        self.running = False
        self.state = AutomationState(run_id='', account=config.account, username=config.username,
                                     context_id=config.context_id)
        self.phase, self.reason = 'STOPPED', 'Not started.'
        self.unresolved = {}           # action id -> why its outcome is unknown (this run; blocks sending)
        self.carried = {}              # the same, from an earlier run: noted in Needs review, never blocks
        self.acknowledged = set()      # action ids whose reply has been accepted
        self.failed = None             # a journal failure: nothing more is sent until restart
        self.in_flight = None          # the command being handed over right now, if any
        self.sent_at = None            # (monotonic, UTC ISO) of the pending command's hand-over
        self.delivery_wait_logged = set()
        self.ready_at = float('-inf')  # monotonic time the next fish may be sent
        self.last_trip_at = None       # monotonic time of the latest confirmed trip (its hand-over, or when seen)
        self.started_at = None
        self.last_result = None
        self.total_trips = 0
        self.startup_failures = {}
        self.startup_problems = {}
        self.startup_texts = {}        # command -> the text of its last unreadable reply
        self.startup_retry_at = float('-inf')

    # --- run control ---

    def start(self, thread=False):
        """Begin a run. Recovers the journal first: never-sent intents are dropped, interrupted projections are
        redone, and possibly-sent commands stay uncertain (and block sending) until their reply is seen or a
        person resolves them."""
        with self.lock:
            if self.running:
                return
            recovered = self.journal.recover()
            _, run_id = new_record_id()
            self.state = AutomationState(run_id=run_id, account=self.config.account, username=self.config.username,
                                         context_id=self.config.context_id)
            self.generation += 1
            self.stop_event = threading.Event()
            self.running, self.failed, self.started_at = True, None, self.monotonic()
            self.startup_failures, self.startup_problems, self.startup_texts = {}, {}, {}
            self.startup_retry_at = float('-inf')
            self.acknowledged = {action for action, info in recovered['actions'].items()
                                 if info['status'] in ('acknowledged', 'projected')}
            self.total_trips = sum(info.get('result') == 'trip' for info in recovered['actions'].values())
            self._event('run_started', mode=self.config.mode, max_trips=self.config.max_trips,
                        max_seconds=self.config.max_seconds)
            damaged = recovered['damaged']
            for action in recovered['never_sent']:
                self._event('abandoned', action, reason='Journaled but never handed over (restart).')
            for action in recovered['unprojected']:
                self._project(action, recovered['actions'][action]['tally'])
            # An earlier run's unknown outcome never blocks this run: it is noted once in Needs review and settled
            # in the journal. A reply to it that is still visible is still counted (see _observe).
            self.unresolved, self.carried = {}, {}
            for action in recovered['unresolved']:
                info = recovered['actions'][action]
                reason = info.get('reason') or 'It may have been sent before the app stopped; its outcome is unknown.'
                kind = (info.get('intent') or {}).get('kind', 'command')
                self._event('resolved', action, outcome='unknown', reason='Carried over from an earlier run: ' + reason)
                add_item(self.roots['review'], self.config.account, 'automation', f'{kind}: {reason}',
                         [f'From an earlier run: this {kind} may or may not have reached the game. Check Discord '
                          'and enter any trip by hand.', reason], blocks_actions=False)
                self.carried[action] = reason
            if damaged:
                self._event('journal_damage_resolved', None, outcome='unknown', files=damaged)
                add_item(self.roots['review'], self.config.account, 'automation', '\n'.join(damaged),
                         [f'{len(damaged)} journal record(s) from an earlier run could not be read, so a command may '
                          'have been sent without its record. Check Discord and enter any trip by hand.'],
                         blocks_actions=False)
            self.phase, self.reason = ('NEEDS_REVIEW', self._unresolved_text()) if self.unresolved else (
                'BOOTSTRAPPING', 'Starting.')
            generation, stop_event = self.generation, self.stop_event
        if thread:
            self.thread = threading.Thread(target=self._loop, args=(generation, stop_event), daemon=True)
            self.thread.start()

    def stop(self, reason='Stopped.', timeout=10.0):
        """Stop sending. A command already handed over is not undone; its outcome stays in the journal."""
        with self.lock:
            if self.running:
                self._event('run_stopped', reason=reason)
            self.running = False
            self.generation += 1
            self.phase, self.reason = 'STOPPED', reason
            self.stop_event.set()
            worker = self.thread
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout)

    def resolve(self, action_id: str, outcome: str) -> bool:
        """A person settles an uncertain command: 'happened' (the game acted; any trip is entered by hand from
        Needs review) or 'not_sent'. Fishing resumes when nothing is left unresolved."""
        if outcome not in RESOLUTIONS:
            raise ValueError(f'Outcome must be one of {RESOLUTIONS}.')
        with self.lock:
            if action_id not in self.unresolved:
                return False
            self._event('resolved', action_id, outcome=outcome)
            del self.unresolved[action_id]
            self.acknowledged.add(action_id)
            self._sync_uncertain()
            return True

    def status(self) -> dict:
        with self.lock:
            pending = self.state.pending
            return {'running': self.running, 'mode': self.config.mode, 'phase': self.phase, 'reason': self.reason,
                    'run_id': self.state.run_id, 'trips': self.state.trips, 'total_trips': self.total_trips,
                    'pending': pending.action_id if pending else None,
                    'unresolved': sorted(self.unresolved), 'last_result': self.last_result,
                    'next_eligible_in': max(0.0, self.ready_at - self.monotonic()) if self.running else None,
                    'context_ok': self.state.context_ok, 'config': {
                        'account': self.config.account, 'context_id': self.config.context_id,
                        'max_trips': self.config.max_trips, 'max_seconds': self.config.max_seconds},
                    'startup': {'failures': dict(self.startup_failures),
                                'problems': dict(self.startup_problems),
                                'confirmed_fields': dict(self.state.startup_observed_at)}}

    def _loop(self, generation, stop_event):
        while not stop_event.is_set():
            began = self.monotonic()
            self.tick(generation)
            if stop_event.wait(max(0.0, self.interval - (self.monotonic() - began))):
                break

    # --- one step ---

    def tick(self, generation=None):
        with self.lock:
            if not self.running or (generation is not None and generation != self.generation):
                return
            generation = self.generation
            if self.in_flight is not None:
                # Another step is handing a command over right now; nothing else may be chosen or sent.
                self.phase, self.reason = 'SENDING', f'Handing over {self.in_flight.kind}.'
                return
            now = self.monotonic()
            if self.config.max_seconds is not None and now - self.started_at >= self.config.max_seconds:
                self._finish('Bounded run complete: time limit reached.')
                return
            context = self.transport.inspect_context()
            context_ok = context.ok and (not self.config.context_id or context.context_id == self.config.context_id)
            self.state = replace(self.state, context_ok=context_ok)
            for observation in self.transport.poll():
                self._observe(observation)
            # Native OCR may take seconds; waits must use the time after the capture.
            now = self.monotonic()
            if self.failed:
                self.phase, self.reason = 'FAILED', self.failed
                return
            if self.state.pending is not None:
                sending_seen = getattr(self.transport, 'sending_seen', False)
                timeout = max(self.config.reply_timeout, 60) if sending_seen else self.config.reply_timeout
                if sending_seen and self.state.pending.action_id not in self.delivery_wait_logged:
                    self.delivery_wait_logged.add(self.state.pending.action_id)
                    self._safe_event('delivery_wait', self.state.pending.action_id,
                                     reason='Discord displayed Sending command; retrying capture, not sending again.',
                                     timeout=timeout)
                if now - self.sent_at[0] > timeout:
                    detail = context.reason
                    self._uncertain(self.state.pending.action_id,
                                    f'No confirmed reply within {timeout:g} s; not resent.'
                                    + (f' {detail}' if detail else ''))
                else:
                    self.phase = 'WAITING_DELIVERY' if sending_seen else 'WAITING_REPLY'
                    self.reason = ('Discord displayed Sending command; retrying reply capture without resending.'
                                   if sending_seen else f'Waiting for the reply to {self.state.pending.kind}.')
                    return
            if self.unresolved:
                self.phase, self.reason = 'NEEDS_REVIEW', self._unresolved_text()
                return
            if not context_ok:
                self.phase = 'PAUSED'
                self.reason = context.reason or f'Not the configured channel ({context.context_id}).'
                return
            if self.config.max_trips is not None and self.state.trips >= self.config.max_trips:
                self._finish('Bounded run complete: trip limit reached.')
                return
            if now < self.startup_retry_at:
                self.phase = 'RETRYING_STARTUP'
                self.reason = f'Retrying startup read in {self.startup_retry_at - now:.1f} s.'
                return
            if self.last_trip_at is not None and self.state.cooldown_seconds is not None:
                # Also covers a trip reconciled after a restart, before this run had seen /buffs.
                self.ready_at = max(self.ready_at, self.last_trip_at + self.state.cooldown_seconds
                                    + self.config.cooldown_margin)
            if now < self.ready_at:
                self.phase, self.reason = 'WAITING_COOLDOWN', f'Next fish in {self.ready_at - now:.1f} s.'
                return
            intent = self.policy(self.state, self.config, self.utc())
            if intent is None:
                self.phase, self.reason = 'READY', 'Nothing to do.'
                return
            try:
                self._event('intent', intent.action_id, intent={'kind': intent.kind, 'context_id': intent.context_id,
                                                                'created_at': intent.created_at,
                                                                'expected_reply_kind': intent.expected_reply_kind,
                                                                'preconditions': list(intent.preconditions)})
                self.state = replace(self.state, sequence=self.state.sequence + 1)
                self._event('dispatching', intent.action_id)
            except OSError as error:
                return self._journal_failed(f'Could not record the {intent.kind} command, so it was not sent: {error}')
            # Reserved before the lock is released: a concurrent step sees it and sends nothing.
            self.in_flight = intent
            self.state = replace(self.state, pending=intent)
        # The hand-over happens outside the lock so stop() is never blocked behind it; it is recorded either way.
        try:
            receipt, failure = self.transport.send(intent), None
        except CapabilityError as error:
            receipt, failure = None, ('abandoned', str(error))
        except TransportError as error:
            receipt, failure = None, ('uncertain', f'The hand-over failed ({error}); it may or may not have '
                                                   'reached the game. Not resent.')
        with self.lock:
            self.in_flight = None
            if failure or generation != self.generation:
                self.state = replace(self.state, pending=None)
            if failure and failure[0] == 'abandoned':
                self._safe_event('abandoned', intent.action_id, reason=failure[1])
                self.failed = failure[1]
                self.phase, self.reason = 'FAILED', failure[1]
                return
            if failure:
                self._safe_event('uncertain', intent.action_id, reason=failure[1])
                self.unresolved[intent.action_id] = failure[1]
                self._sync_uncertain()
                return
            try:
                self._event('dispatched', intent.action_id, sent_at=receipt.sent_at, detail=receipt.detail)
            except OSError as error:
                self.state = replace(self.state, pending=None)
                self.unresolved[intent.action_id] = 'Sent, but the hand-over could not be recorded.'
                self._sync_uncertain()
                return self._journal_failed(f'Sent the {intent.kind} command but could not record it: {error}')
            if generation != self.generation:
                # Stopped (and perhaps restarted) while this command was being handed over.
                self.unresolved[intent.action_id] = 'Sent while the run was stopping; its outcome is unknown.'
                self._safe_event('uncertain', intent.action_id, reason=self.unresolved[intent.action_id])
                return
            self.sent_at = (self.monotonic(), receipt.sent_at)
            self.phase, self.reason = 'WAITING_REPLY', f'Waiting for the reply to {intent.kind}.'

    # --- replies ---

    def _observe(self, observation):
        correlation = observation.correlation
        if observation.identity_status != VERIFIED or (
                self.config.context_id and observation.context_id != self.config.context_id):
            return
        pending = self.state.pending
        if correlation is None:
            if pending is not None and observation.kind == pending.expected_reply_kind \
                    and observation.observed_at >= self.sent_at[1]:
                # It looks like the answer, but nothing ties it to this command (it could be an older one).
                self._uncertain(pending.action_id, 'A reply arrived without evidence of which command it answers.')
            return
        if correlation in self.acknowledged:
            return
        if pending is not None and correlation == pending.action_id:
            if observation.observed_at < self.sent_at[1]:
                return  # seen before this command was sent: cannot be its reply
            if observation.kind != pending.expected_reply_kind and not (
                    pending.kind == 'fish' and COOLDOWN_REPLY.search(observation.text)):
                return
            self.state = apply_startup(self.state, observation) if observation.kind in ('profile', 'buffs') else apply_observation(self.state, observation)
            self.state = replace(self.state, pending=None)
            self._acknowledge(pending.action_id, pending.kind, observation, this_run=True)
        elif correlation in self.unresolved or correlation in self.carried:
            # A reply to a command whose outcome was unknown (from before a restart, or after a timeout).
            kind = 'fish' if observation.kind in ('fishing', None) else f'refresh_{observation.kind}'
            self.unresolved.pop(correlation, None)
            self.carried.pop(correlation, None)
            self._acknowledge(correlation, kind, observation, this_run=False)
            self._sync_uncertain()

    def _acknowledge(self, action_id, kind, observation, this_run):
        wait = COOLDOWN_REPLY.search(observation.text)
        tally = None
        problems = []
        if kind != 'fish':
            problems = startup_problems(self.state, observation.kind)
            result = 'unreadable' if problems else 'refreshed'
        elif wait:
            result = 'cooldown'
        elif fishing_problems(observation.text):
            result = 'unreadable'
        else:
            result = 'trip'
            tally = {field: value for field, value in parse_fishing(observation.text).items() if field != 'warnings'}
        try:
            self._event('acknowledged', action_id, observation_id=observation.observation_id, result=result,
                        tally=tally)
        except OSError as error:
            self.unresolved[action_id] = 'Its reply was seen but could not be recorded.'
            self._sync_uncertain()
            return self._journal_failed(f'Could not record the reply to {action_id}: {error}')
        self.acknowledged.add(action_id)
        self.last_result = {'action_id': action_id, 'result': result, 'at': observation.observed_at}
        now = self.monotonic()
        if result == 'cooldown':
            self.ready_at = now + float(wait[1]) + self.config.cooldown_margin
        elif result == 'unreadable' and kind != 'fish':
            # Delivery is confirmed: repeating this read-only command cannot duplicate a fishing trip.
            attempts = self.startup_failures.get(kind, 0) + 1
            self.startup_failures[kind] = attempts
            self.startup_problems[kind] = problems
            # The same pixels give the same misread: another send cannot read differently.
            repeated = self.startup_texts.get(kind) == observation.text
            self.startup_texts[kind] = observation.text
            exhausted = attempts >= STARTUP_ATTEMPTS or not this_run or repeated
            add_item(self.roots['review'], self.config.account, observation.kind or 'unknown', observation.text,
                     [f'Automation could not read this {observation.kind or "reply"} reliably.'] + problems,
                     identity_status=observation.identity_status, observation_id=observation.observation_id,
                     blocks_actions=exhausted)
            if exhausted:
                self.unresolved[action_id] = (f'Its {observation.kind or "reply"} could not be read after '
                                              f'{attempts} attempt(s)'
                                              + (' (the same unreadable text each time)' if repeated else '')
                                              + f': {"; ".join(problems)}; see Needs review.')
                self._safe_event('uncertain', action_id, reason=self.unresolved[action_id])
                self._sync_uncertain()
            else:
                self.startup_retry_at = now + STARTUP_RETRY_DELAY
                self._safe_event('startup_retry_scheduled', action_id, command=kind,
                                 attempt=attempts, problems=problems, delay=STARTUP_RETRY_DELAY)
        elif result == 'refreshed':
            self.startup_problems.pop(kind, None)
            self.startup_texts.pop(kind, None)
            self.startup_retry_at = float('-inf')
        elif result == 'unreadable':
            reasons = fishing_problems(observation.text)
            add_item(self.roots['review'], self.config.account, 'fishing', observation.text,
                     ['Automation could not read this result exactly; enter the trip by hand.'] + reasons,
                     identity_status=observation.identity_status, observation_id=observation.observation_id)
            self.unresolved[action_id] = 'Its fishing result could not be read exactly (see Needs review).'
            self._safe_event('uncertain', action_id, reason=self.unresolved[action_id])
            self._sync_uncertain()
        elif result == 'trip':
            self.total_trips += 1
            # A reconciled trip's hand-over time is not known to this run; when it was seen is a safe bound.
            self.last_trip_at = self.sent_at[0] if this_run else now
            if this_run:
                self.state = replace(self.state, trips=self.state.trips + 1)
            self._project(action_id, tally)

    def _project(self, action_id, tally):
        """Copy an acknowledged trip into the logbook. Idempotent: the session is keyed by the action id."""
        try:
            save_session(tally, self.config.account, f'automation:{action_id}', self.roots['fishing'],
                         provenance={'source': 'automation', 'mode': self.config.mode, 'action_id': action_id,
                                     'identity_status': VERIFIED})
            self._event('projected', action_id)
        except OSError as error:
            self._journal_failed(f'Could not save trip {action_id} to the logbook: {error}')

    # --- helpers (called with the lock held) ---

    def _event(self, kind, action_id=None, **fields):
        self.journal.append({'run_id': self.state.run_id, 'type': kind, 'action_id': action_id,
                             'at_utc': _iso(self.utc()), **fields})

    def _safe_event(self, kind, action_id, **fields):
        try:
            self._event(kind, action_id, **fields)
        except OSError as error:
            self._journal_failed(f'Could not record {kind} for {action_id}: {error}')

    def _uncertain(self, action_id, reason):
        self.state = replace(self.state, pending=None)
        self.unresolved[action_id] = reason
        self._safe_event('uncertain', action_id, reason=reason)
        self._sync_uncertain()

    def _sync_uncertain(self):
        self.state = replace(self.state, uncertain=self._unresolved_text() if self.unresolved else None)
        if self.unresolved:
            self.phase, self.reason = 'NEEDS_REVIEW', self._unresolved_text()

    def _unresolved_text(self):
        return ' '.join(f'{action}: {reason}' for action, reason in sorted(self.unresolved.items()))

    def _journal_failed(self, reason):
        self.failed = reason
        self.phase, self.reason = 'FAILED', reason

    def _finish(self, reason):
        self._event('run_stopped', reason=reason)
        self.running = False
        self.generation += 1
        self.stop_event.set()
        self.phase, self.reason = 'STOPPED', reason
