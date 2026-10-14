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
import copy
import json
import random
import re
import threading
import time

from .account_reads import ACCOUNT_READS, PAGE_TWO, read_of
from .account_replies import PARSERS as ACCOUNT_PARSERS
from .account_state import apply_read, empty_state, load_state, mark_maxed, save_state
from .automation_journal import AutomationJournal
from .automation_models import (ActionIntent, AutomationConfig, CapabilityError, Interrupted, NOT_FOREGROUND,
                                NotDispatched, TransportError)
from .automation_policy import next_action
from .automation_state import AutomationState, apply_observation
from .catch_check import load_fish_reference
from .fishing_sessions import load_sessions
from .game_model import cooldown
from .game_model_data import GameDataError, load_game_data
from .game_model_inputs import build_inputs
from .model_check import ModelTracker, buff_corrections, corrected_trip, model_view, trip_boosted
from .live_stats import (add_unlisted_finds, carry_forward, charm_summary, parse_charms, project, summarize_finds,
                         synced_stats, unread_fields)
from .plan_data import PlanDataError, load_plan_data
from .plan_setup import ModelContext
from .plan_state import from_run
from .planner import make_plan
from .player_planner import load_level_increments
from .player_state import parse_responses
from .progress import progress_view
from .progress_store import HISTORY, append_history, build_index, journal_events, read_history
from .startup_fields import apply_startup, reply_problems, startup_problems
from .fishing_log import parse_fishing
from .fishing_sessions import save_session
from .live_capture import fishing_core_problems, fishing_problems
from .observation import VERIFIED
from .player_snapshots import new_record_id, write_json_atomically
from .review_queue import add_item

# The game writes "You must wait 0.1s before fishing again." (live, Oct 13; OCR read "O.Is"); "2.5 seconds" too.
COOLDOWN_REPLY = re.compile(r'You must wait\s+([\dOIl|.,]+)\s*(?:s|seconds?)\b\.?\s*before fishing again', re.I)


def _cooldown_wait(match):
    """Seconds the game still asked to wait, with OCR's O for 0 and I, l or | for 1; 0 when unreadable."""
    try:
        return float(re.sub(r'[Oo]', '0', re.sub(r'[Il|]', '1', match[1])).replace(',', '.'))
    except ValueError:
        return 0.0
RESOLUTIONS = ('happened', 'not_sent')
STARTUP_ATTEMPTS = 3
STARTUP_RETRY_DELAY = 2.0
# Reads and actions in a row with an unknown outcome (no reply, unreadable, failed hand-over) before a run gives up:
# the game is not answering. An unknown /fish does not count here: /profile is read next and says what happened.
UNKNOWN_LIMIT = 3
# /fish in a row with an unknown outcome before a run gives up, even while /profile answers (the user, 2026-10-14:
# three unknown /fish stopped a run that a /profile read would have carried on).
FISH_UNKNOWN_LIMIT = 10
# Trips with an unknown outcome since /profile was read before it is read again: such a trip most likely happened,
# but its XP and money are not counted, so the level and balance drift until the next read. Read at once: the read
# also tells whether the game is answering at all.
UNCOUNTED_RESYNC = 1
CAPTCHA_REASON = ('Virtual Fisher asked for a captcha. Type /captcha with the code from the image in Discord '
                  '(exact case). Fishing carries on by itself once Virtual Fisher replies to anything again; '
                  'Resume also works.')
PRESTIGE_REASON = 'Ready to prestige: the requirements are met. Prestige by hand, then Resume.'
# After a refusal before any input (Discord not in front, message box not empty), the wait before trying again.
REFUSAL_RETRY_DELAY = 2.0
# Follow the plan (part 5): a step whose action failed or whose outcome is unknown is not queued again for this many
# trips (Do it can still queue it).
HOLD_BACK_TRIPS = 50
# Follow the plan: plans are otherwise only asked for after a read, so something that became affordable while
# fishing would wait for the next resync (100 trips). PlanRunner's 30 s spacing still applies.
FOLLOW_PLAN_TRIPS = 10


_REFERENCES = []


def _references():
    """(wiki level increments, wiki fish table), loaded once."""
    if not _REFERENCES:
        _REFERENCES.extend((load_level_increments(), load_fish_reference() or {}))
    return tuple(_REFERENCES)


def _read_anything(parsed: dict) -> bool:
    """Whether an account reply read at all: it lacks nothing it must show ("Current bait: NONE" is a reading),
    or it showed some value."""
    return not parsed['unread'] or any(value not in (None, {}, [], '') for value in parsed['values'].values())


def trip_spacing(sessions, recent=300, longest=60.0, fewest=20):
    """The median seconds between consecutive logged trips: the newest `recent` gaps under `longest` seconds
    (longer ones are breaks), from one-trip sessions (each saved as its trip happened). None with under `fewest`."""
    times = []
    for session in sessions:
        if (session.get('tally') or {}).get('trips') != 1:
            continue
        try:
            times.append(datetime.fromisoformat(session['captured_at'].replace('Z', '+00:00')).timestamp())
        except (KeyError, AttributeError, TypeError, ValueError):
            continue
    times.sort(reverse=True)
    gaps = [a - b for a, b in zip(times, times[1:]) if 0 < a - b < longest][:recent]
    if len(gaps) < fewest:
        return None
    gaps.sort()
    middle = len(gaps) // 2
    return gaps[middle] if len(gaps) % 2 else (gaps[middle - 1] + gaps[middle]) / 2


def _iso(moment: datetime) -> str:
    return moment.isoformat()


class PlanRunner:
    """Works out plans in a background thread: never in the run's loop. Only the newest request is kept, one plan
    is worked out at a time, and a new one starts at most every `spacing` seconds (the first at once): a request
    inside that window waits, folded with any later ones, until `poll` finds the window over."""

    def __init__(self, compute, spawn=None, clock=time.monotonic, spacing=30.0, label='planner'):
        self.compute, self.label = compute, label    # label: what the error messages name ('The planner failed')
        self.spawn = spawn or (lambda work: threading.Thread(target=work, daemon=True).start())
        self.clock, self.spacing = clock, spacing
        self.lock = threading.Lock()
        self.pending, self.busy, self.started = None, False, None
        self.plan, self.made_at, self.error = None, None, None
        self.requested, self.plan_request = 0, 0

    def request(self, inputs):
        with self.lock:
            self.requested += 1
            self.pending = (self.requested, inputs)    # numbered: the executor tells a fresh plan from a stale one
        self.poll()

    def poll(self):
        """Start the waiting request if nothing is being worked out and the window is over. Never blocks."""
        with self.lock:
            if self.busy or self.pending is None or not self._window_over():
                return
            self.busy = True
        try:
            self.spawn(self._work)
        except Exception as failure:  # a thread that cannot start never stops a run
            with self.lock:
                self.busy, self.pending = False, None
                self.error = f'The {self.label} could not start: {failure}'

    def _window_over(self):
        return self.started is None or self.clock() - self.started >= self.spacing

    def _work(self):
        while True:
            with self.lock:
                if self.pending is None or not self._window_over():
                    self.busy = False   # a request left waiting is started by a later poll
                    return
                (number, inputs), self.pending = self.pending, None
                self.started = self.clock()
            try:
                plan, error = self.compute(inputs), None
            except Exception as failure:  # the planner never stops a run
                plan, error = None, f'The {self.label} failed: {failure}'
            if error is None and isinstance(plan, dict) and plan.get('error') and _good_plan(self.plan):
                error = plan['error']       # a plan that could not be made keeps the last good one on the tab
            with self.lock:
                if error:
                    self.error = error
                    if not _good_plan(self.plan):
                        self.plan = None    # a plan that could not be made never stays up beside a newer failure
                else:
                    self.plan, self.error, self.plan_request = plan, None, number
                    self.made_at = datetime.now(timezone.utc).isoformat(timespec='seconds')

    def view(self):
        with self.lock:
            return {'plan': self.plan, 'made_at': self.made_at, 'error': self.error,
                    'updating': self.busy or self.pending is not None, 'request': self.plan_request}


def _good_plan(plan):
    return isinstance(plan, dict) and not plan.get('error')


def _load_follow(path):
    """Whether Follow the plan was left on for this account; off when never set or unreadable."""
    try:
        return bool(json.loads(Path(path).read_text(encoding='utf-8')).get('follow_plan')) if path else False
    except (OSError, ValueError, AttributeError):
        return False


class AutomationController:
    def __init__(self, transport, config: AutomationConfig, journal: AutomationJournal, fishing_root: Path,
                 review_root: Path, monotonic=time.monotonic, utc=None, policy=next_action, interval=0.2,
                 rng=None, alert=None, state_path=None, plan_spawn=None):
        self.transport, self.config, self.journal, self.policy = transport, config, journal, policy
        # The account as its replies last showed it, saved so it outlives the run (see account_state).
        self.state_path = Path(state_path) if state_path else None
        self.account = load_state(self.state_path) if self.state_path else empty_state()
        # The game model (part 2): checked per trip, shown on the Model tab. Never stops the run.
        try:
            self.model_data, self.model_problem = load_game_data(), None
        except GameDataError as error:
            self.model_data, self.model_problem = None, str(error)
        try:  # a bad model_check.json starts fresh with a note (ModelTracker); anything else never stops a run
            self.model = ModelTracker(self.state_path.parent / 'model_check.json' if self.state_path else None)
        except Exception as error:
            self.model = ModelTracker()
            self.model.problem = f'model_check.json could not be read ({error}): the model started fresh.'
        # The planner (part 3): worked out in a background thread whenever the account changes. Never stops a run.
        try:
            self.plan_data, plan_problem = load_plan_data(), None
        except PlanDataError as error:
            self.plan_data, plan_problem = None, str(error)
        self.planner = PlanRunner(self._compute_plan, plan_spawn, clock=monotonic)  # plan_spawn: tests only
        self.planner.error = plan_problem
        # The Progress view (part 6): the same background runner, at most one view every 10 s. Never stops a run.
        self.progress = PlanRunner(self._compute_progress, plan_spawn, clock=monotonic, spacing=10.0,
                                   label='Progress view')
        self.journal_cache = {}        # run folder -> (file count, events): finished runs are not read again
        # Follow the plan (part 5): kept per account beside the account state, so it outlives restarts.
        self.executor_path = self.state_path.parent / 'executor.json' if self.state_path else None
        self.follow_plan = _load_follow(self.executor_path)
        self.held = {}                 # step label -> the trip from which Follow the plan may queue it again
        # step label -> the trip its queued action last ran at (any result): a read that misses a buy leaves the
        # step ready in the next plan, and a bait batch has no level check, so it is not queued again for a while.
        self.recent = {}
        self.awaiting_reads = None     # the reads the last action queued, until all are applied
        self.settled_request = 0       # the plan request made once they were: plans from it on are fresh
        self.prestige_ready = False    # paused at Ready to prestige
        self.prestige_seen = False     # this prestige already paused once; cleared when the plan leaves prestige
        self.queued_from = -1          # the plan request steps were last queued from: each plan is followed once
        self.model_boosted = None  # whether the latest trip was under a global boost (None: no trip yet)
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
        self.unknown_streak = self.fish_unknown_streak = 0
        self.refused_until, self.refused_reason = float('-inf'), ''
        self.paused = False            # paused by the person: nothing new is sent until resumed
        self.pause_id = 0              # counts pauses; Resume names the pause it ends, so a stale one cannot
        self.captcha = None            # the captcha message that paused the run, until resumed
        self.captchas_seen = set()     # observation ids of captcha messages already acted on
        self.jitter = 0.0              # the random extra wait before the next fish
        self.focus_note = None         # what an interruption left to redo, shown while Discord is not active
        self.focus_lost_at = None      # when Discord lost focus while a reply was awaited
        self.jitters = []              # every extra wait picked this run
        self._reset_stats()

    def _reset_stats(self):
        # Live stats: the last readable /profile and /buffs replies, the trips since the profile was read, and
        # every trip this run (for rates).
        self.synced = {'profile': '', 'buffs': '', 'charms': ''}
        # Part 4: plan steps queued with Do it, carried out one action per gap; lost on restart, so nothing stale acts.
        self.queue = []                # {'id', 'label', 'actions': [Action], 'checked'}
        self.queue_ids = 0
        self.action_log = []           # the last results, newest first: {'label', 'result', 'note'}
        self.pending_action = None     # the action (as a dict) of the act intent being sent or answered
        self.synced_at = {}
        self.sync_tallies, self.run_tallies = [], []
        self.uncounted = 0             # trips since /profile was read whose outcome is unknown (not counted)
        self.charm_tallies = []        # trips since /charms was read: their charms add to its counts
        self.retry_counts = {}         # deferred refresh -> trips since it was last tried
        self.earlier_profile = None    # the projected profile when /profile was last read, for what a read misses
        self.earlier_buffs = {}        # the /buffs values read before, for what a read misses
        self.unread = {}               # refresh -> fields its last reply did not show; read again until complete
        self.unread_trips = {}         # refresh -> trips since that reply
        self.finds_before = None       # finds from earlier automation trips, loaded once per run
        self.logbook_spacing = False   # the logbook's recent trip spacing for the plan (False: not read yet)

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
            self.unknown_streak = self.fish_unknown_streak = 0
            self.refused_until, self.refused_reason = float('-inf'), ''
            self.paused, self.captcha, self.prestige_ready, self.prestige_seen = False, None, False, False
            self.recent = {}               # trips count from 0 again
            self.jitter, self.jitters = 0.0, []
            self.focus_note, self.focus_lost_at = None, None
            self._reset_stats()
            # Read once the profile and cooldown are known, before the first fish.
            self.state = replace(self.state, refresh_due=tuple(
                kind for kind in ('refresh_charms', *self._account_reads()) if kind in self.config.capabilities))
            # Follow the plan waits for these too: a plan from the account saved last run is not followed.
            self.awaiting_reads = set(self.state.refresh_due)
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
            self._request_plan()
            self._request_progress()
            generation, stop_event = self.generation, self.stop_event
        if thread:
            self.thread = threading.Thread(target=self._loop, args=(generation, stop_event), daemon=True)
            self.thread.start()

    def stop(self, reason='Stopped.', timeout=10.0):
        """Stop sending. A command already handed over is not undone; its outcome stays in the journal."""
        cancel_input = getattr(self.transport, 'stop', None)
        if cancel_input is not None:
            cancel_input()  # Available even while another thread holds the controller/send lock.
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
                self.pause_id += 1
                self._safe_event('paused', None)
                self.phase, self.reason = 'PAUSED_BY_YOU', 'Paused. Resume to carry on fishing.'

    def resume(self, pause_id=None):
        """End the pause. `pause_id`, from the Resume button, must name the current pause: a resume sent again
        later (a reload, a restored tab) belongs to an earlier one and is ignored. None: resumed from code."""
        with self.lock:
            if not (self.paused and (pause_id is None or pause_id == self.pause_id)):
                return False
            if self.prestige_ready:
                # Prestiged by hand: the plan stays at prestige until /profile (and the prestige shop) are read.
                self.state = replace(self.state, refresh_due=self.state.refresh_due + tuple(
                    kind for kind in ('refresh_profile', 'refresh_prestige_shop')
                    if kind in self.config.capabilities and kind not in self.state.refresh_due))
            self.paused, self.captcha, self.prestige_ready = False, None, False
            self._safe_event('resumed', None)
            self.phase, self.reason = 'READY', 'Resuming.'
            refocus = getattr(self.transport, 'refocus', None)
            if refocus is not None:
                refocus()   # the message box is clicked again before the next command
            return True

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
            self._poll_plan()
            self._poll_progress()
            dashboard = self._dashboard()
            pending = self.state.pending
            progress = self.progress.view()
            return {'running': self.running, 'paused': self.paused, 'pause_id': self.pause_id,
                    'pending_kind': pending.kind if pending else None,
                    'mode': self.config.mode, 'phase': self.phase, 'reason': self.reason,
                    'run_id': self.state.run_id, 'trips': self.state.trips, 'total_trips': self.total_trips,
                    'pending': pending.action_id if pending else None,
                    'unresolved': sorted(self.unresolved), 'last_result': self.last_result,
                    'next_eligible_in': max(0.0, self.ready_at - self.monotonic()) if self.running else None,
                    'context_ok': self.state.context_ok, 'config': {
                        'account': self.config.account, 'context_id': self.config.context_id,
                        'max_trips': self.config.max_trips, 'max_seconds': self.config.max_seconds},
                    'dashboard': dashboard, 'account': self.account, 'model': self._model_status(dashboard),
                    'plan': self.planner.view(),
                    'progress': {'view': progress['plan'], 'made_at': progress['made_at'],
                                 'error': progress['error']},
                    'queue': [{'id': entry['id'], 'label': entry['label'], 'left': len(entry['actions'])}
                              for entry in self.queue],
                    'action_log': list(self.action_log),
                    'executor': {'follow': self.follow_plan, 'prestige_ready': self.prestige_ready,
                                 'held': [{'label': label, 'until': until} for label, until in self.held.items()
                                          if until > self.state.trips]},
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
            self._poll_plan()
            self._poll_progress()
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
            if self.state.pending is not None and not context.ok and context.reason == NOT_FOREGROUND:
                # Sent; its reply can only be read with Discord active. This time does not count toward the wait.
                if self.focus_lost_at is None:
                    self.focus_lost_at = now
                self.phase = 'WAITING_FOCUS'
                self.reason = (f'{self.state.pending.kind} was sent; its reply is read once Discord is the active '
                               'window again.')
                return
            if self.focus_lost_at is not None:
                if self.sent_at is not None:
                    self.sent_at = (self.sent_at[0] + now - self.focus_lost_at, self.sent_at[1])
                self.focus_lost_at = None
            if self.state.pending is not None:
                sending_seen = getattr(self.transport, 'sending_seen', False)
                base = self.config.reply_timeout if self.state.pending.kind == 'fish' else self.config.read_timeout
                timeout = max(base, 60) if sending_seen else base
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
            if self.paused and self.captcha and self._captcha_cleared():
                # The bot answers nothing else until a captcha is solved: a newer reply means it was. At Ready to
                # prestige the run stays paused there: only Resume (after prestiging by hand) carries on.
                self.captcha = None
                if not self.prestige_ready:
                    self.paused = False
                self._safe_event('captcha_solved', None)
            if self.paused:
                if self.captcha:
                    self.phase, self.reason = 'CAPTCHA', CAPTCHA_REASON
                elif self.prestige_ready:
                    self.phase, self.reason = 'READY_TO_PRESTIGE', PRESTIGE_REASON
                else:
                    self.phase, self.reason = 'PAUSED_BY_YOU', 'Paused. Resume to carry on fishing.'
                return
            if not context_ok and context.reason == NOT_FOREGROUND:
                self.phase = 'WAITING_FOCUS'
                self.reason = 'Waiting for Discord to be the active window.' + (
                    f' {self.focus_note}' if self.focus_note else '')
                return
            if not context_ok:
                self.phase = 'PAUSED'
                self.reason = context.reason or f'Not the configured channel ({context.context_id}).'
                return
            if self.config.max_trips is not None and self.state.trips >= self.config.max_trips:
                self._finish('Bounded run complete: trip limit reached.')
                return
            if now < self.refused_until:
                if self.refused_reason.startswith('Waiting for Discord'):
                    self.phase = 'WAITING_FOCUS'
                    self.reason = self.refused_reason + (f' {self.focus_note}' if self.focus_note else '')
                else:
                    self.phase, self.reason = 'PAUSED', f'{self.refused_reason} Trying again shortly.'
                return
            if now < self.startup_retry_at:
                self.phase = 'RETRYING_STARTUP'
                self.reason = f'Retrying startup read in {self.startup_retry_at - now:.1f} s.'
                return
            try:
                self._follow()
            except Exception as error:   # following the plan never stops a run
                self.planner.error = f'Follow the plan failed: {error}'
            if self.paused:
                return
            # A queued action goes in the gap between trips, without waiting for the fishing cooldown; after one
            # that changes the balance (a sell, a buy) /profile is read first, so the next is checked against it.
            intent = None
            if self.queue and 'act' in self.config.capabilities and self.state.pending is None \
                    and not self.state.uncertain and 'refresh_profile' not in self.state.refresh_due:
                intent = self._action_intent()
            if intent is None:
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
                                                                'preconditions': list(intent.preconditions),
                                                                **({'arguments': list(intent.arguments)}
                                                                   if intent.kind == 'act' else {})})
                self.state = replace(self.state, sequence=self.state.sequence + 1)
                self._event('dispatching', intent.action_id)
            except OSError as error:
                return self._journal_failed(f'Could not record the {intent.kind} command, so it was not sent: {error}')
            # Reserved before the lock is released: a concurrent step sees it and sends nothing.
            self.in_flight = intent
            self.pending_action = intent.arguments[0] if intent.kind == 'act' else None
            if intent.kind == 'act':
                self.phase, self.reason = 'SENDING', intent.preconditions[0]
            self.state = replace(self.state, pending=intent)
        # The hand-over happens outside the lock so stop() is never blocked behind it; it is recorded either way.
        try:
            receipt, failure = self.transport.send(intent), None
        except Interrupted as error:
            receipt, failure = None, ('interrupted', str(error))
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
            if failure and (failure[0] == 'interrupted' or failure[1] == NOT_FOREGROUND):
                # Discord lost focus mid-command: nothing was sent. The same command is chosen again, from a clean
                # message box, as soon as Discord is the active window (see the context check in tick).
                self._safe_event('interrupted', intent.action_id, reason=failure[1])
                self.focus_note = failure[1] if failure[0] == 'interrupted' else None
                # Retried calmly, not every tick, if the window check itself still passes.
                self.refused_until = self.monotonic() + REFUSAL_RETRY_DELAY
                self.refused_reason = 'Waiting for Discord to be the active window.'
                self.phase = 'WAITING_FOCUS'
                self.reason = 'Waiting for Discord to be the active window.' + (
                    f' {self.focus_note}' if self.focus_note else '')
                return
            if failure and failure[0] == 'refused' and intent.kind in PAGE_TWO:
                # Page 2's arrow was not found: nothing was clicked. Page 2 is read again with its read later.
                self._safe_event('abandoned', intent.action_id, reason=failure[1])
                read = read_of(intent.kind)
                self.state = replace(self.state, refresh_due=tuple(k for k in self.state.refresh_due
                                                                   if k != intent.kind))
                self.unread[read], self.unread_trips[read] = [f'Page 2 ({failure[1]})'], 0
                return
            if failure and failure[0] == 'refused' and intent.kind == 'act' and 'option box' in failure[1]:
                # Nothing was sent, and typing it again would only repeat it: the step is given up.
                self._safe_event('abandoned', intent.action_id, reason=failure[1])
                self._finish_action(intent.arguments[0], 'failed', failure[1])
                return
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
            self.focus_note = None
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
            if observation.kind != pending.expected_reply_kind and pending.kind != 'act' and not (
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

    def queue_step(self, label, actions):
        """Queue a plan step's actions (Do it). They run one per gap between trips; the queue is lost on restart."""
        with self.lock:
            return self._enqueue(label, actions)

    def _enqueue(self, label, actions):
        from .actions import Action
        self.queue_ids += 1
        self.queue.append({'id': self.queue_ids, 'label': label, 'checked': False,
                           'actions': [Action.from_dict(item) for item in actions]})
        return self.queue_ids

    def set_follow(self, on):
        """Turn Follow the plan on or off; saved for this account. Steps already queued stay queued."""
        with self.lock:
            self.follow_plan = bool(on)
            if self.executor_path:
                try:
                    write_json_atomically(self.executor_path.parent, self.executor_path.stem,
                                          {'follow_plan': self.follow_plan})
                except OSError as error:
                    self._safe_event('state_not_saved', None, reason=str(error))

    def _follow(self):
        """Follow the plan (part 5): with nothing queued or waiting, queue every ready step of a plan made after the
        last action's reads (or the run's startup reads) came back, once per plan. Held-back steps are left out."""
        if not self.follow_plan or self.queue or self.state.pending is not None or self.state.uncertain:
            return
        if startup_problems(self.state, 'profile') or startup_problems(self.state, 'buffs'):
            return    # the run's /profile and /buffs come first (start() also waits for its account reads)
        if self.awaiting_reads is not None:
            # Page 2 of a paged read (refresh_special_page2) still belongs to its read.
            if self.awaiting_reads & {read_of(kind) for kind in self.state.refresh_due}:
                return
            self.awaiting_reads, self.settled_request = None, self.planner.requested
        view = self.planner.view()
        plan = view['plan']
        if not _good_plan(plan) or view['request'] < self.settled_request or view['request'] <= self.queued_from:
            return
        if plan.get('phase_now') == 'prestige':
            if not self.prestige_seen:     # once per prestige: Resume (after prestiging by hand) carries on
                self.prestige_seen = self.prestige_ready = self.paused = True
                self.pause_id += 1
                self._safe_event('paused', None, reason='ready to prestige')
                self.phase, self.reason = 'READY_TO_PRESTIGE', PRESTIGE_REASON
            return
        self.prestige_seen = False
        self.queued_from = view['request']
        for step in list(plan.get('steps') or []) + list(plan.get('league') or []):
            label = step.get('action')
            if step.get('status') == 'ready' and step.get('do') and self.held.get(label, 0) <= self.state.trips \
                    and (label not in self.recent or self.state.trips - self.recent[label] >= HOLD_BACK_TRIPS):
                self._enqueue(label, step['do'])

    def unqueue(self, queue_id):
        """Remove a queued step; False when it is no longer queued."""
        with self.lock:
            kept = [entry for entry in self.queue if entry['id'] != queue_id]
            removed, self.queue = len(kept) != len(self.queue), kept
            return removed

    def _log_action(self, label, result, note):
        self.action_log = ([{'label': label, 'result': result, 'note': note}] + self.action_log)[:6]

    def _action_intent(self):
        """The next queued action as an intent, after the check against the latest reads; None when nothing is to
        be sent now. A buy short of cash, with enough fish to cover it, gets a Sell first."""
        from .actions import Action, check
        profile = self._synced()['profile']
        while self.queue:
            entry = self.queue[0]
            if not entry['actions']:
                self.queue.pop(0)
                continue
            action = entry['actions'][0]
            why = check(action, self.account.get('sections'), profile)
            if why and why.startswith('not enough money') and not entry['checked'] and \
                    (profile.get('balance') or 0) + (profile.get('fish_value') or 0) >= action.price:
                entry['actions'].insert(0, Action('sell'))
                action, why = entry['actions'][0], None
            if why:
                self._log_action(entry['label'], 'skipped', why)
                self.queue.pop(0)
                continue
            entry['checked'] = True
            return ActionIntent(action_id=f'{self.state.run_id}-{self.state.sequence + 1:05d}-act', kind='act',
                                context_id=self.state.context_id, created_at=self.utc().isoformat(),
                                expected_reply_kind='action', arguments=(action.to_dict(),),
                                preconditions=(f'{action.label()} ({entry["label"]})',))
        return None

    def _finish_action(self, action, result, note):
        """Record an action's result, move its step on (a step whose action did not succeed is given up), and queue
        the reads it changes."""
        from .actions import Action, reads_after
        action = Action.from_dict(action or {'act': 'sell'})
        entry = self.queue[0] if self.queue else None
        if entry is not None and entry['actions'] and entry['actions'][0] == action:
            entry['actions'].pop(0)
            # A sell whose reply was not read still goes on to its buy: the buy is checked against the new /profile.
            if (result != 'done' and action.act != 'sell') or not entry['actions']:
                self.queue.pop(0)
        label = entry['label'] if entry else action.label()
        self._log_action(label, result, note)
        self.recent[label] = self.state.trips     # Follow the plan leaves it a while, whatever the reads show
        if result in ('failed', 'unknown') and action.act != 'sell':
            self.held[label] = self.state.trips + HOLD_BACK_TRIPS    # Follow the plan leaves it for a while
        # Follow the plan waits for these reads, then for a plan made after them.
        self.awaiting_reads = {kind for kind in reads_after(action) if kind in self.config.capabilities}
        self.pending_action = None
        due = tuple(kind for kind in reads_after(action) if kind in self.config.capabilities
                    and kind not in self.state.refresh_due)
        self.state = replace(self.state, refresh_due=self.state.refresh_due + due)
        self._request_progress()

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
        self.pause_id += 1
        self.phase, self.reason = 'CAPTCHA', CAPTCHA_REASON
        if self.alert:
            try:
                self.alert(CAPTCHA_REASON + '\n\n' + observation.text)
            except Exception:
                pass  # the dashboard still shows it

    def _acknowledge(self, action_id, kind, observation, this_run):
        wait = COOLDOWN_REPLY.search(observation.text)
        tally = None
        note = None
        problems = []
        if kind in ACCOUNT_READS or kind in PAGE_TWO:
            parsed = ACCOUNT_PARSERS[observation.kind](observation.text)
            problems = [] if _read_anything(parsed) else ['Nothing could be read from this reply.']
            result = 'unreadable' if problems else 'refreshed'
        elif kind == 'refresh_charms':
            # Any charm line read is worth keeping; a reply missing one is read again soon (see unread_fields).
            problems = [] if parse_charms(observation.text)['charms'] else ['No charm line could be read.']
            result = 'unreadable' if problems else 'refreshed'
        elif kind == 'act':
            from .actions import Action, outcome
            action = Action.from_dict(self.pending_action or {'act': 'sell'})
            result, note = outcome(action, observation.text)
            if note == 'already maxed' and action.level:
                # Remembered, so the plan stops offering it (the shop's own max can be higher).
                self.account = mark_maxed(self.account, action.name, action.level - 1)
                if self.state_path:
                    try:
                        save_state(self.state_path, self.account)
                    except OSError as error:
                        self._safe_event('state_not_saved', None, reason=str(error))
                self._request_plan()
            self._finish_action(self.pending_action, 'done' if result == 'done' else 'failed', note)
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
                        tally=tally, **({'note': note} if note else {}))
        except OSError as error:
            self.unresolved[action_id] = 'Its reply was seen but could not be recorded.'
            self._sync_uncertain()
            return self._journal_failed(f'Could not record the reply to {action_id}: {error}')
        self.acknowledged.add(action_id)
        self.unknown_streak = 0
        if kind == 'fish':
            self.fish_unknown_streak = 0   # only a confirmed /fish ends a run of unknown ones
        self.last_result = {'action_id': action_id, 'result': result, 'at': observation.observed_at}
        now = self.monotonic()
        if result == 'cooldown':
            self.ready_at = now + _cooldown_wait(wait) + self.config.cooldown_margin + self.jitter
        elif result == 'unreadable' and (kind in ACCOUNT_READS or kind in PAGE_TWO):
            # Fishing never waits on it; the whole read (both pages) is tried again later.
            self.state = replace(self.state, refresh_due=tuple(k for k in self.state.refresh_due if k != kind))
            self._defer(read_of(kind), observation, problems)
        elif result == 'refreshed' and (kind in ACCOUNT_READS or kind in PAGE_TWO):
            self._refreshed_account(kind, observation, parsed)
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
            self._keep_history(observation)
            self.synced_at[observation.kind] = observation.observed_at
            if observation.kind == 'profile':
                self.sync_tallies, self.uncounted = [], 0
            if observation.kind == 'charms':
                self.charm_tallies = []
            self._request_plan()
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
                self._follow_replan()
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
                    kind for kind in ('refresh_profile', 'refresh_buffs', 'refresh_charms', *self._account_reads())
                    if kind in self.config.capabilities))
            # A milestone level-up raises a multiplier at once ("10% INCREASED SELLING PRICE FOR LEVELING UP!"):
            # /buffs is read again at the next gap.
            if (tally.get('level_bonuses') and 'refresh_buffs' in self.config.capabilities
                    and 'refresh_buffs' not in self.state.refresh_due):
                self.state = replace(self.state, refresh_due=self.state.refresh_due + ('refresh_buffs',))
            self._count_retries()
            self._project(action_id, tally)
            self._model_observe(tally)
            if this_run:
                self._follow_replan()     # after the trip is counted, so the plan starts from it

    def _follow_replan(self):
        """While following the plan, ask for one every FOLLOW_PLAN_TRIPS trips. Not while an action's reads are
        awaited or steps are queued: a plan asked for then is never taken as fresh (see _follow)."""
        if self.follow_plan and self.state.trips % FOLLOW_PLAN_TRIPS == 0 and self.awaiting_reads is None \
                and not self.queue and self.pending_action is None:
            self._request_plan()

    def _model_observe(self, tally):
        """Let the model watch one new trip with the setup it was fished with."""
        if self.model_data is None:
            return
        try:
            synced = self._synced()
            built = build_inputs(self.account, synced['profile'], self.synced['charms'])
            if isinstance(built, str):
                return
            account, setup = built
            # A global boost is counted from the trip's own "Global boost by" line, not the last /boosts read.
            self.model_boosted = trip_boosted(tally)
            setup = replace(setup, global_booster=self.model_boosted)
            # Predicted with the corrections from the latest /buffs comparison (1 until /buffs is compared). Measured
            # money stays on the game's sell price, so the money factor no longer carries the sell price ratio.
            rows, predicted = corrected_trip(account, setup, synced['buffs'], self.model_data,
                                             self.model.learned_mix(), self.model.chest_scales())
            # The comparison's game values are already converted ("+91%" is 1.91x).
            shown = {row['name']: row['game'] for row in rows if row['game'] is not None}
            if self.model.observe(tally, setup, predicted, self.model_data,
                                  shown.get('sell_price') or predicted['buffs']['sell_price'].value, shown):
                self.model.save()
                self.model_problem = None  # only once a trip was actually used
        except Exception as error:  # the model never stops a run
            self.model_problem = f'The model could not use a trip: {error}'

    def _model_status(self, dashboard):
        empty = {'error': None, 'setup': None, 'buffs': [], 'trip': [], 'mix': {'source': '', 'rows': []},
                 'estimates': []}
        if self.model_data is None:
            return dict(empty, error=self.model_problem or 'The game data is not loaded.')
        try:
            synced = self._synced()
            view = model_view(self.account, synced['profile'], synced['buffs'], self.synced['charms'], self.model,
                              self.model_data, (dashboard.get('per_hour') or {}).get('trips'), self.model_boosted)
        except Exception as error:  # the model never breaks the page
            return dict(empty, error=f'The model failed: {error}')
        if self.model_problem and not view['error']:
            view['estimates'] = [self.model_problem, *view['estimates']]
        return view

    # --- the planner (part 3): inputs snapshotted here, the plan worked out in PlanRunner's thread ---

    def _plan_inputs(self, dashboard=None):
        """A snapshot of what the plan needs, taken under the lock; the plan itself is worked out elsewhere."""
        synced = self._synced()
        tracker = self.model
        trips_per_hour = ((dashboard or {}).get('per_hour') or {}).get('trips')
        return {'account': copy.deepcopy(self.account), 'synced': copy.deepcopy(synced),
                'charms': self.synced['charms'], 'factors': {k: v['value'] for k, v in tracker.factors().items()},
                'learned': copy.deepcopy(tracker.learned_mix()), 'scales': copy.deepcopy(tracker.chest_scales()),
                'trips_per_hour': trips_per_hour, 'buffs': dict(synced['buffs']),
                'charms_since': dict(summarize_finds(self.charm_tallies)['charms']),
                'logbook_spacing': self._logbook_spacing()}

    def _logbook_spacing(self):
        """The median time between recent trips in the logbook (seconds), read once per run; None when too few."""
        if self.logbook_spacing is False:
            try:
                sessions = load_sessions(self.config.account, self.roots['fishing'])[0]
            except OSError:
                sessions = []
            self.logbook_spacing = trip_spacing(sessions)
        return self.logbook_spacing

    def _compute_plan(self, inputs):
        if self.plan_data is None or self.model_data is None:
            return {'error': self.planner.error or self.model_problem or 'The game data is not loaded.',
                    'steps': [], 'next': None, 'prestige': {}, 'assumptions': [], 'cut_short': False}
        state = from_run(inputs['account'], inputs['synced'], inputs['charms'], inputs.get('charms_since'))
        if isinstance(state, str):
            return {'error': f'not planned: {state}', 'steps': [], 'next': None, 'prestige': {},
                    'assumptions': [], 'cut_short': False}
        rows, _ = corrected_trip(state.account, state.setup, inputs['buffs'], self.model_data)
        corrections = buff_corrections(rows)
        # seconds per trip: this run's rate; before it has one, the logbook's recent trip spacing (an estimate)
        overhead, note = 0.5, 'seconds per trip: cooldown + 0.5 s until this run has a rate'
        rate = inputs['trips_per_hour']
        spacing = 3600 / rate if rate else inputs.get('logbook_spacing')
        if spacing:
            overhead = max(0.5, spacing - cooldown(state.account, state.setup, self.model_data))
            if not rate:
                note = (f"seconds per trip: cooldown + {overhead:.1f} s from the logbook's recent trip spacing "
                        f"(median {spacing:.1f} s) until this run has a rate")
        ctx = ModelContext(self.model_data, inputs['learned'], inputs['scales'], corrections, inputs['factors'],
                           overhead)
        plan = make_plan(state, ctx, self.plan_data)
        if not rate:
            plan['assumptions'] = sorted(set(plan['assumptions']) | {note})
        return plan

    def _poll_plan(self):
        """Start a plan request left waiting by PlanRunner's spacing. Never blocks or stops the run."""
        try:
            self.planner.poll()
        except Exception as error:
            self.planner.error = f'The plan could not be started: {error}'

    def _request_plan(self):
        try:
            self.planner.request(self._plan_inputs(self._dashboard()))
        except Exception as error:  # never stops a run
            self.planner.error = f'The plan could not be requested: {error}'

    # --- progress (part 6): history kept as the run reads, the view worked out in its own PlanRunner ---

    def _keep_history(self, observation):
        """Part 6: each readable /profile and /charms read, one line in profile_history.jsonl. Never stops a run."""
        if not self.state_path or observation.kind not in ('profile', 'charms'):
            return
        try:
            if observation.kind == 'profile':
                read = parse_responses(profile=observation.text)['profile']
                entry = {'level': read.get('level'), 'prestige': read.get('prestige'), 'xp': read.get('xp_current'),
                         'balance': read.get('balance'), 'fish_value': read.get('fish_value')}
            else:
                read = parse_charms(observation.text)
                entry = {'found': read.get('found'), 'goal': read.get('goal')}
            append_history(self.state_path.parent / HISTORY,
                           {'at': observation.observed_at, 'kind': observation.kind, **entry})
            self._request_progress()
        except Exception as error:   # an unwritable file or an odd reply never stops a run
            self._safe_event('state_not_saved', None, reason=f'Profile history: {error}')

    def _request_progress(self):
        try:
            self.progress.request({'plan': (self.planner.view() or {}).get('plan'),
                                   'profile': self._synced()['profile'],
                                   'charms': parse_charms(self.synced['charms']) if self.synced['charms'] else None,
                                   'running_id': self.state.run_id})
        except Exception as error:   # never stops a run
            self.progress.error = f'Progress could not be requested: {error}'

    def _poll_progress(self):
        """Start a progress request left waiting by its runner's spacing. Never blocks or stops the run."""
        try:
            self.progress.poll()
        except Exception as error:
            self.progress.error = f'Progress could not be started: {error}'

    def _compute_progress(self, inputs):
        if not self.state_path:
            return None
        folder = self.state_path.parent
        index, bad_trips = build_index(folder)
        history, bad_lines = read_history(folder / HISTORY)
        events = journal_events(self.journal.root, self.journal_cache)
        notes = [f'{count} {what} could not be read.' for count, what in
                 ((bad_trips, 'trip records'), (bad_lines, 'profile history lines')) if count]
        return progress_view(index, history, events, inputs['plan'], inputs['profile'], inputs['charms'],
                             datetime.now(timezone.utc), inputs['running_id'], notes)

    def _project(self, action_id, tally):
        """Copy an acknowledged trip into the logbook. Idempotent: the session is keyed by the action id."""
        try:
            save_session(tally, self.config.account, f'automation:{action_id}', self.roots['fishing'],
                         provenance={'source': 'automation', 'mode': self.config.mode, 'action_id': action_id,
                                     'identity_status': VERIFIED})
            self._event('projected', action_id)
        except OSError as error:
            self._journal_failed(f'Could not save trip {action_id} to the logbook: {error}')
            return
        self._request_progress()

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
        # What the chat's bottom read as when the reply was given up on: live, replies went unseen for minutes with
        # nothing saved to tell why (2026-10-14).
        screen = list(getattr(self.transport, 'last_lines', None) or [])[-12:]
        forget = getattr(self.transport, 'forget', None)
        if forget is not None:
            forget()
        self._safe_event('resolved', intent.action_id, outcome='unknown', reason=reason,
                         **({'screen': screen} if screen else {}))
        if intent.kind == 'act':
            # It may have happened: never sent again; the reads it changes (and /profile) are done again.
            self._finish_action(intent.arguments[0], 'unknown', reason)
        add_item(self.roots['review'], self.config.account, 'automation', f'{intent.kind}: {reason}',
                 [f'This {intent.kind} may or may not have reached the game; the run carried on.', reason]
                 + (['The bottom of the chat read: ' + ' | '.join(screen)] if screen else []),
                 blocks_actions=False)
        self.last_result = {'action_id': intent.action_id, 'result': 'unknown', 'at': _iso(self.utc())}
        if intent.kind == 'fish':
            self.fish_unknown_streak += 1
        else:
            self.unknown_streak += 1
        if self.unknown_streak >= UNKNOWN_LIMIT or self.fish_unknown_streak >= FISH_UNKNOWN_LIMIT:
            count = UNKNOWN_LIMIT if self.unknown_streak >= UNKNOWN_LIMIT else FISH_UNKNOWN_LIMIT
            what = 'reads or actions' if count == UNKNOWN_LIMIT else '/fish commands'
            stopped = f'{count} {what} in a row had no confirmed outcome, so the run stopped. Last: {reason}'
            try:
                self._finish(stopped)
            except OSError:
                self.running = False
            self.failed = stopped
            self.phase, self.reason = 'FAILED', stopped
            return
        if intent.kind == 'fish':
            self.uncounted += 1
            if (self.uncounted >= UNCOUNTED_RESYNC and 'refresh_profile' in self.config.capabilities
                    and 'refresh_profile' not in self.state.refresh_due):
                self.state = replace(self.state, refresh_due=self.state.refresh_due + ('refresh_profile',))
        self.ready_at = max(self.ready_at, self.monotonic() + (self.state.cooldown_seconds or 0)
                            + self.config.cooldown_margin + self.jitter)

    def _captcha_cleared(self):
        check = getattr(self.transport, 'captcha_cleared', None)
        try:
            return bool(check and check())
        except Exception:
            return False

    def _account_reads(self):
        return tuple(kind for kind in ACCOUNT_READS if kind in self.config.capabilities)

    def _refreshed_account(self, kind, observation, parsed):
        """Apply an account reply and save the state. Page 1 of a paged read queues page 2 next; a read that
        still lacks something (after both pages) is read again every few trips."""
        self.account = apply_read(self.account, observation.kind, parsed, observation.observed_at, observation.text)
        if self.state_path:
            try:
                save_state(self.state_path, self.account)
            except OSError as error:
                self._safe_event('state_not_saved', None, reason=str(error))
        read = read_of(kind)
        due = tuple(k for k in self.state.refresh_due if k != kind)
        page_two = f'{kind}_page2'
        total = (parsed.get('page') or (None, None))[1]
        listed = total is not None and total > 1 if ACCOUNT_READS[read].paged_when_listed else total != 1
        if page_two in PAGE_TWO and page_two in self.config.capabilities and listed:
            self.state = replace(self.state, refresh_due=(page_two,) + tuple(k for k in due if k != page_two))
            return
        self.state = replace(self.state, refresh_due=due,
                             deferred=tuple(k for k in self.state.deferred if k != read))
        self.retry_counts.pop(read, None)
        unread = self.account['sections'][observation.kind]['unread']
        if unread:
            self.unread[read], self.unread_trips[read] = list(unread), 0
        else:
            self.unread.pop(read, None)
            self.unread_trips.pop(read, None)
        self._request_plan()

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
            self.logbook_spacing = trip_spacing(sessions)   # the same read serves the plan's seconds per trip
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
                     trips_since_sync=len(self.sync_tallies), uncounted_since_sync=self.uncounted,
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
        cancel_input = getattr(self.transport, 'stop', None)
        if cancel_input is not None:
            cancel_input()
        self.failed = reason
        self.phase, self.reason = 'FAILED', reason

    def _finish(self, reason):
        cancel_input = getattr(self.transport, 'stop', None)
        if cancel_input is not None:
            cancel_input()
        self._event('run_stopped', reason=reason)
        self.running = False
        self.generation += 1
        self.stop_event.set()
        self.phase, self.reason = 'STOPPED', reason
