"""Run the fishing loop: choose an action, journal it, send it, confirm its reply, update state, wait.

At most one command is pending at a time. Every command is journaled before it is handed over, and its
hand-over and reply after. A command whose outcome is not known (no reply in time, a failed hand-over, a reply
without evidence of which command it answers, an unreadable fishing result) is noted in Needs review without
blocking; the run waits one cooldown and carries on, and UNKNOWN_LIMIT in a row stop it. A later run is never
blocked by an earlier one. Confirmed read-only startup replies
that fail OCR are retried up to three attempts, with a delay, unless a retry reads exactly as the last one did
(the same pixels give the same misread); they never establish state until valid.
"""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import random
import re
import threading
import time

from .automation_journal import AutomationJournal
from .automation_models import AutomationConfig, CapabilityError, NotDispatched, TransportError
from .automation_policy import next_action
from .automation_state import AutomationState, apply_observation
from .catch_check import load_fish_reference
from .fishing_sessions import load_sessions
from .live_stats import (add_unlisted_finds, carry_forward, charm_summary, parse_charms, project, summarize_finds,
                         synced_stats, unread_fields)
from .player_planner import load_level_increments
from .startup_fields import apply_startup, reply_problems, startup_problems
from .fishing_log import parse_fishing
from .fishing_sessions import save_session
from .live_capture import fishing_core_problems, fishing_problems
from .observation import VERIFIED
from .player_snapshots import new_record_id
from .review_queue import add_item

COOLDOWN_REPLY = re.compile(r'You must wait ([\d.]+) seconds? before fishing again', re.I)
RESOLUTIONS = ('happened', 'not_sent')
STARTUP_ATTEMPTS = 3
STARTUP_RETRY_DELAY = 2.0
# Unknown outcomes in a row (no reply, unreadable, failed hand-over) before a run gives up.
UNKNOWN_LIMIT = 3
CAPTCHA_REASON = ('Virtual Fisher asked for a captcha. Type /captcha with the code from the image in Discord '
                  '(exact case), then press Resume.')
# After a refusal before any input (Discord not in front, message box not empty), the wait before trying again.
REFUSAL_RETRY_DELAY = 2.0


_REFERENCES = []


def _references():
    """(wiki level increments, wiki fish table), loaded once."""
    if not _REFERENCES:
        _REFERENCES.extend((load_level_increments(), load_fish_reference() or {}))
    return tuple(_REFERENCES)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


class AutomationController:
    def __init__(self, transport, config: AutomationConfig, journal: AutomationJournal, fishing_root: Path,
                 review_root: Path, monotonic=time.monotonic, utc=None, policy=next_action, interval=0.2,
                 rng=None, alert=None):
        self.transport, self.config, self.journal, self.policy = transport, config, journal, policy
        self.roots = {'fishing': Path(fishing_root), 'review': Path(review_root)}
        self.monotonic = monotonic
        self.utc = utc or (lambda: datetime.now(timezone.utc))
        self.interval = interval
        self.rng = rng or random.Random()
        self.alert = alert             # called with a message when the game asks for a captcha
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
        self.unknown_streak = 0
        self.refused_until, self.refused_reason = float('-inf'), ''
        self.paused = False            # paused by the person: nothing new is sent until resumed
        self.captcha = None            # the captcha message that paused the run, until resumed
        self.captchas_seen = set()     # observation ids of captcha messages already acted on
        self.jitter = 0.0              # the random extra wait before the next fish
        self.jitters = []              # every extra wait picked this run
        self._reset_stats()

    def _reset_stats(self):
        # Live stats: the last readable /profile and /buffs replies, the trips since the profile was read, and
        # every trip this run (for rates).
        self.synced = {'profile': '', 'buffs': '', 'charms': ''}
        self.synced_at = {}
        self.sync_tallies, self.run_tallies = [], []
        self.charm_tallies = []        # trips since /charms was read: their charms add to its counts
        self.retry_counts = {}         # deferred refresh -> trips since it was last tried
        self.earlier_profile = None    # the projected profile when /profile was last read, for what a read misses
        self.earlier_buffs = {}        # the /buffs values read before, for what a read misses
        self.unread = {}               # refresh -> fields its last reply did not show; read again until complete
        self.unread_trips = {}         # refresh -> trips since that reply
        self.finds_before = None       # finds from earlier automation trips, loaded once per run

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
            self.unknown_streak = 0
            self.refused_until, self.refused_reason = float('-inf'), ''
            self.paused, self.captcha = False, None
            self.jitter, self.jitters = 0.0, []
            self._reset_stats()
            if 'refresh_charms' in self.config.capabilities:
                # Read once the profile and cooldown are known, before the first fish.
                self.state = replace(self.state, refresh_due=('refresh_charms',))
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

    def pause(self):
        """Send nothing new until resumed. A command already sent still has its reply read."""
        with self.lock:
            if self.running and not self.paused:
                self.paused = True
                self._safe_event('paused', None)
                self.phase, self.reason = 'PAUSED_BY_YOU', 'Paused. Resume to carry on fishing.'

    def resume(self):
        with self.lock:
            if self.paused:
                self.paused, self.captcha = False, None
                self._safe_event('resumed', None)
                self.phase, self.reason = 'READY', 'Resuming.'

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
            return {'running': self.running, 'paused': self.paused,
                    'pending_kind': pending.kind if pending else None,
                    'mode': self.config.mode, 'phase': self.phase, 'reason': self.reason,
                    'run_id': self.state.run_id, 'trips': self.state.trips, 'total_trips': self.total_trips,
                    'pending': pending.action_id if pending else None,
                    'unresolved': sorted(self.unresolved), 'last_result': self.last_result,
                    'next_eligible_in': max(0.0, self.ready_at - self.monotonic()) if self.running else None,
                    'context_ok': self.state.context_ok, 'config': {
                        'account': self.config.account, 'context_id': self.config.context_id,
                        'max_trips': self.config.max_trips, 'max_seconds': self.config.max_seconds},
                    'dashboard': self._dashboard(),
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
                    self._move_on(self.state.pending, f'No confirmed reply within {timeout:g} s.'
                                  + (f' {detail}' if detail else ''))
                    if not self.running or self.failed:
                        return
                else:
                    self.phase = 'WAITING_DELIVERY' if sending_seen else 'WAITING_REPLY'
                    self.reason = ('Discord displayed Sending command; retrying reply capture without resending.'
                                   if sending_seen else f'Waiting for the reply to {self.state.pending.kind}.')
                    return
            if self.unresolved:
                self.phase, self.reason = 'NEEDS_REVIEW', self._unresolved_text()
                return
            if self.paused:
                if self.captcha:
                    self.phase, self.reason = 'CAPTCHA', CAPTCHA_REASON
                else:
                    self.phase, self.reason = 'PAUSED_BY_YOU', 'Paused. Resume to carry on fishing.'
                return
            if not context_ok:
                self.phase = 'PAUSED'
                self.reason = context.reason or f'Not the configured channel ({context.context_id}).'
                return
            if self.config.max_trips is not None and self.state.trips >= self.config.max_trips:
                self._finish('Bounded run complete: trip limit reached.')
                return
            if now < self.refused_until:
                self.phase, self.reason = 'PAUSED', f'{self.refused_reason} Trying again shortly.'
                return
            if now < self.startup_retry_at:
                self.phase = 'RETRYING_STARTUP'
                self.reason = f'Retrying startup read in {self.startup_retry_at - now:.1f} s.'
                return
            if self.last_trip_at is not None and self.state.cooldown_seconds is not None:
                # Also covers a trip reconciled after a restart, before this run had seen /buffs.
                self.ready_at = max(self.ready_at, self.last_trip_at + self.state.cooldown_seconds
                                    + self.config.cooldown_margin + self.jitter)
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
        except NotDispatched as error:
            receipt, failure = None, ('refused', str(error))
        except CapabilityError as error:
            receipt, failure = None, ('abandoned', str(error))
        except TransportError as error:
            receipt, failure = None, ('uncertain', f'The hand-over failed ({error}); it may or may not have '
                                                   'reached the game. Not resent.')
        with self.lock:
            self.in_flight = None
            if failure or generation != self.generation:
                self.state = replace(self.state, pending=None)
            if failure and failure[0] == 'refused':
                # Nothing was typed: pause and try again once Discord is ready. The run is not over.
                self._safe_event('abandoned', intent.action_id, reason=failure[1])
                self.refused_until = self.monotonic() + REFUSAL_RETRY_DELAY
                self.refused_reason = failure[1]
                self.phase, self.reason = 'PAUSED', f'{failure[1]} Trying again shortly.'
                return
            if failure and failure[0] == 'abandoned':
                self._safe_event('abandoned', intent.action_id, reason=failure[1])
                self.failed = failure[1]
                self.phase, self.reason = 'FAILED', failure[1]
                return
            if failure:
                self._move_on(intent, failure[1])
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
            if intent.kind == 'fish':
                self._pick_jitter()

    def _pick_jitter(self):
        """A new random extra wait for the next fish: a band chosen by its share, then a step within it."""
        bands = self.config.jitter_bands
        if not bands:
            self.jitter = 0.0
        else:
            _, low, high, step = self.rng.choices(bands, weights=[band[0] for band in bands])[0]
            self.jitter = round(low + self.rng.randint(0, round((high - low) / step)) * step, 6)
        self.jitters.append(self.jitter)

    # --- replies ---

    def _observe(self, observation):
        correlation = observation.correlation
        if observation.kind == 'captcha':
            return self._captcha(observation)
        if observation.identity_status != VERIFIED or (
                self.config.context_id and observation.context_id != self.config.context_id):
            return
        pending = self.state.pending
        if correlation is None:
            if pending is not None and observation.kind == pending.expected_reply_kind \
                    and observation.observed_at >= self.sent_at[1]:
                # It looks like the answer, but nothing ties it to this command (it could be an older one).
                self._move_on(pending, 'A reply arrived without evidence of which command it answers.')
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

    def _captcha(self, observation):
        """The game asked for a captcha: the command it answers did not run. Pause, note it, and alert the person.
        Any player's captcha pauses the run; resuming is always the person's choice."""
        if self.captcha is not None or observation.observation_id in self.captchas_seen:
            return
        self.captchas_seen.add(observation.observation_id)
        pending = self.state.pending
        if pending is not None and observation.correlation == pending.action_id:
            self.state = replace(self.state, pending=None)
            self.acknowledged.add(pending.action_id)
        self._safe_event('captcha', pending.action_id if pending else None, observation_id=observation.observation_id,
                         text=observation.text)
        add_item(self.roots['review'], self.config.account, 'captcha', observation.text,
                 ['Virtual Fisher asked for a captcha. The run paused; nothing more was sent.'],
                 identity_status=observation.identity_status, observation_id=observation.observation_id,
                 blocks_actions=False)
        self.paused, self.captcha = True, observation.text
        self.phase, self.reason = 'CAPTCHA', CAPTCHA_REASON
        if self.alert:
            try:
                self.alert(CAPTCHA_REASON + '\n\n' + observation.text)
            except Exception:
                pass  # the dashboard still shows it

    def _acknowledge(self, action_id, kind, observation, this_run):
        wait = COOLDOWN_REPLY.search(observation.text)
        tally = None
        problems = []
        if kind == 'refresh_charms':
            # Any charm line read is worth keeping; a reply missing one is read again soon (see unread_fields).
            problems = [] if parse_charms(observation.text)['charms'] else ['No charm line could be read.']
            result = 'unreadable' if problems else 'refreshed'
        elif kind != 'fish':
            # A resync or retry must read on its own; at startup, fields read across replies add up.
            problems = (reply_problems(observation.kind, observation.text) if kind in self.state.refresh_due
                        else startup_problems(self.state, observation.kind))
            result = 'unreadable' if problems else 'refreshed'
        elif wait:
            result = 'cooldown'
        elif fishing_core_problems(observation.text):
            result = 'unreadable'
        else:
            # The catch and XP read cleanly: the trip counts even if an extra line (an event, a reward) did not.
            result = 'trip'
            parsed = parse_fishing(observation.text)
            tally = {field: value for field, value in parsed.items() if field != 'warnings'}
            if parsed['warnings']:
                add_item(self.roots['review'], self.config.account, 'fishing', observation.text,
                         ['Some lines of this result could not be read; the rest was counted.'] + parsed['warnings'],
                         identity_status=observation.identity_status, observation_id=observation.observation_id,
                         blocks_actions=False)
        try:
            self._event('acknowledged', action_id, observation_id=observation.observation_id, result=result,
                        tally=tally)
        except OSError as error:
            self.unresolved[action_id] = 'Its reply was seen but could not be recorded.'
            self._sync_uncertain()
            return self._journal_failed(f'Could not record the reply to {action_id}: {error}')
        self.acknowledged.add(action_id)
        self.unknown_streak = 0
        self.last_result = {'action_id': action_id, 'result': result, 'at': observation.observed_at}
        now = self.monotonic()
        if result == 'cooldown':
            self.ready_at = now + float(wait[1]) + self.config.cooldown_margin + self.jitter
        elif result == 'unreadable' and kind in self.state.refresh_due:
            # A resync or retry that could not be read: fishing never waits on it; it is tried again later.
            self._defer(kind, observation, problems)
        elif result == 'unreadable' and kind != 'fish':
            # Delivery is confirmed: repeating this read-only command cannot duplicate a fishing trip.
            attempts = self.startup_failures.get(kind, 0) + 1
            self.startup_failures[kind] = attempts
            self.startup_problems[kind] = problems
            # The same pixels give the same misread: another send cannot read differently.
            repeated = self.startup_texts.get(kind) == observation.text
            self.startup_texts[kind] = observation.text
            exhausted = attempts >= STARTUP_ATTEMPTS or not this_run or repeated
            if exhausted:
                self.startup_retry_at = float('-inf')
                self._defer(kind, observation, problems)
                self._safe_event('deferred', action_id, command=kind, attempts=attempts, repeated=repeated,
                                 problems=problems)
            else:
                self.startup_retry_at = now + STARTUP_RETRY_DELAY
                self._safe_event('startup_retry_scheduled', action_id, command=kind,
                                 attempt=attempts, problems=problems, delay=STARTUP_RETRY_DELAY)
        elif result == 'refreshed':
            if observation.kind == 'profile' and self.synced['profile']:
                self.earlier_profile = self._synced_profile(project_trips=True)
            if observation.kind == 'buffs' and self.synced['buffs']:
                self.earlier_buffs = self._synced()['buffs']
            self.synced[observation.kind] = observation.text
            self.synced_at[observation.kind] = observation.observed_at
            if observation.kind == 'profile':
                self.sync_tallies = []
            if observation.kind == 'charms':
                self.charm_tallies = []
            # OCR can lose one field while the rest reads; that reply is read again soon, until nothing is missing.
            unread = unread_fields(observation.kind, observation.text)
            if unread:
                self.unread[kind], self.unread_trips[kind] = unread, 0
            else:
                self.unread.pop(kind, None)
                self.unread_trips.pop(kind, None)
            self.state = replace(self.state, refresh_due=tuple(k for k in self.state.refresh_due if k != kind),
                                 deferred=tuple(k for k in self.state.deferred if k != kind))
            self.retry_counts.pop(kind, None)
            self.startup_problems.pop(kind, None)
            self.startup_texts.pop(kind, None)
            self.startup_retry_at = float('-inf')
        elif result == 'unreadable':
            reasons = fishing_problems(observation.text)
            # The trip happened; only its catch could not be read. Noted, and the run carries on.
            add_item(self.roots['review'], self.config.account, 'fishing', observation.text,
                     ['Automation could not read this result exactly; it was not added to the logbook.'] + reasons,
                     identity_status=observation.identity_status, observation_id=observation.observation_id,
                     blocks_actions=False)
            self.last_trip_at = self.sent_at[0] if this_run else now
            if this_run:
                self.state = replace(self.state, trips=self.state.trips + 1)
            self._count_retries()
        elif result == 'trip':
            self.total_trips += 1
            # A reconciled trip's hand-over time is not known to this run; when it was seen is a safe bound.
            self.last_trip_at = self.sent_at[0] if this_run else now
            if this_run:
                self.state = replace(self.state, trips=self.state.trips + 1)
            self.sync_tallies.append(tally)
            self.charm_tallies.append(tally)
            self.run_tallies.append(tally)
            if (self.config.resync_trips and len(self.sync_tallies) >= self.config.resync_trips
                    and not self.state.refresh_due):
                self.state = replace(self.state, refresh_due=tuple(
                    kind for kind in ('refresh_profile', 'refresh_buffs', 'refresh_charms')
                    if kind in self.config.capabilities))
            self._count_retries()
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

    def _move_on(self, intent, reason):
        """An unknown outcome is noted in Needs review (non-blocking) and settled in the journal. The run waits a
        full cooldown, so a command that did reach the game is not followed too soon, then carries on."""
        self.state = replace(self.state, pending=None,
                             refresh_due=tuple(k for k in self.state.refresh_due if k != intent.kind))
        forget = getattr(self.transport, 'forget', None)
        if forget is not None:
            forget()
        self._safe_event('resolved', intent.action_id, outcome='unknown', reason=reason)
        add_item(self.roots['review'], self.config.account, 'automation', f'{intent.kind}: {reason}',
                 [f'This {intent.kind} may or may not have reached the game; the run carried on.', reason],
                 blocks_actions=False)
        self.last_result = {'action_id': intent.action_id, 'result': 'unknown', 'at': _iso(self.utc())}
        self.unknown_streak += 1
        if self.unknown_streak >= UNKNOWN_LIMIT:
            stopped = f'{UNKNOWN_LIMIT} commands in a row had no confirmed outcome, so the run stopped. Last: {reason}'
            try:
                self._finish(stopped)
            except OSError:
                self.running = False
            self.failed = stopped
            self.phase, self.reason = 'FAILED', stopped
            return
        self.ready_at = max(self.ready_at, self.monotonic() + (self.state.cooldown_seconds or 0)
                            + self.config.cooldown_margin + self.jitter)

    def _defer(self, kind, observation, problems):
        """Set an unreadable /profile or /buffs aside: noted in Needs review, tried again every few trips. Without
        a cooldown, fishing goes on at an assumed one that "You must wait" replies correct."""
        self.state = replace(self.state, refresh_due=tuple(k for k in self.state.refresh_due if k != kind),
                             deferred=tuple(dict.fromkeys(self.state.deferred + (kind,))))
        self.retry_counts[kind] = 0
        if kind == 'refresh_buffs' and self.state.cooldown_seconds is None:
            self.state = replace(self.state, cooldown_seconds=self.config.assumed_cooldown)
        add_item(self.roots['review'], self.config.account, observation.kind or 'unknown', observation.text,
                 [f'This {observation.kind or "reply"} could not be read; fishing goes on and it will be tried again '
                  f'in {self.config.retry_unread_trips} trips.'] + problems,
                 identity_status=observation.identity_status, observation_id=observation.observation_id,
                 blocks_actions=False)

    def _count_retries(self):
        """After each trip, queue any set-aside refresh whose turn has come."""
        for kind in self.unread:
            # A read that missed a field is read again every few trips until every field is filled.
            self.unread_trips[kind] = self.unread_trips.get(kind, 0) + 1
            if self.unread_trips[kind] >= self.config.retry_unread_trips and kind not in self.state.refresh_due:
                self.unread_trips[kind] = 0
                self.state = replace(self.state, refresh_due=self.state.refresh_due + (kind,))
        for kind in self.state.deferred:
            self.retry_counts[kind] = self.retry_counts.get(kind, 0) + 1
            if self.retry_counts[kind] >= self.config.retry_unread_trips and kind not in self.state.refresh_due:
                self.retry_counts[kind] = 0
                self.state = replace(self.state, refresh_due=self.state.refresh_due + (kind,))

    def _synced(self):
        """The last /profile and /buffs as read, with what a read missed kept from the read before."""
        synced = synced_stats(self.synced['profile'], self.synced['buffs'])
        synced['profile'] = carry_forward(synced['profile'], self.earlier_profile)
        synced['buffs'] = {**self.earlier_buffs, **synced['buffs']}
        return synced

    def _synced_profile(self, project_trips=False):
        synced = self._synced()
        if not project_trips:
            return synced['profile']
        increments, fish = _references()
        return project(synced, self.sync_tallies, increments, fish)['profile']

    def _dashboard(self):
        """The synced stats carried forward over the trips since, plus this run's totals and hourly rates."""
        increments, fish = _references()
        synced = self._synced()
        stats = project(synced, self.sync_tallies, increments, fish)
        run = project(synced, self.run_tallies, increments, fish)['gained']
        elapsed = max(1.0, self.monotonic() - self.started_at) if self.started_at is not None else None
        per_hour = {key: run[key] * 3600 / elapsed for key in ('trips', 'xp', 'money', 'fish_value', 'catches')}             if elapsed else {}
        to_next = stats['profile'].get('xp_to_next_level')
        if self.finds_before is None:
            try:
                sessions = load_sessions(self.config.account, self.roots['fishing'])[0]
            except OSError:
                sessions = []
            self.finds_before = summarize_finds([session['tally'] for session in reversed(sessions)])
        stats['finds'] = {'run': summarize_finds(self.run_tallies),
                          'all': summarize_finds(self.run_tallies, self.finds_before)}
        if self.synced['profile']:
            # Below level 50 the profile leaves out Lava and Diamond Fish; they are counted from finds instead.
            stats['inventory'] = add_unlisted_finds(stats['inventory'], stats['finds']['all']['special_fish'],
                                                    listed=synced['inventory'])
            stats['unlisted'] = sorted(set(stats['inventory'].get('exotic_fish', {}))
                                       - {name for items in synced['inventory'].values() for name in items})
        recent = [{'catches': dict(tally['catches']), 'xp': tally['fishing_xp'],
                   'events': list(tally['trip_details'][0]['events']) if tally.get('trip_details') else []}
                  for tally in reversed(self.run_tallies[-8:])]
        stats.update(run=run, per_hour=per_hour, synced_at=dict(self.synced_at), recent=recent,
                     trips_since_sync=len(self.sync_tallies),
                     next_sync_in=(max(0, self.config.resync_trips - len(self.sync_tallies))
                                   if self.config.resync_trips else None),
                     next_level_in_seconds=(to_next * 3600 / per_hour['xp']
                                            if to_next is not None and per_hour.get('xp') else None),
                     unread={kind.removeprefix('refresh_'): {
                         'fields': list(fields),
                         'retry_in': max(0, self.config.retry_unread_trips - self.unread_trips.get(kind, 0))}
                         for kind, fields in self.unread.items()},
                     charms=charm_summary(stats['finds']['all'], parse_charms(self.synced['charms']),
                                          summarize_finds(self.charm_tallies)['charms'],
                                          prestige=stats['profile'].get('prestige')))
        return stats

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
