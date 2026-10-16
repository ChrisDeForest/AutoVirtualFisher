"""The state an automated action may rely on: each value with how recently, and whose, it was observed.

This is separate from the logbook. A snapshot's save time is not evidence that its values are current, so a
section counts as fresh only when it was observed, confirmed as the configured player's, and is recent enough.
Pasted (user-attributed) and version 1 snapshots have no observation time and are unknown here.
"""
from dataclasses import dataclass, replace, field
from datetime import datetime

from .automation_models import ActionIntent
from .observation import VERIFIED, Observation
from .player_snapshots import SECTIONS, validate_full_state
from .player_state import parse_responses
from .startup_fields import cooldown_written

# Initial conservative default from the automation design: money decisions need /profile and /buffs seen within
# the last 30 seconds.
FINANCIAL_MAX_AGE_SECONDS = 30
FINANCIAL_SECTIONS = ('profile', 'buffs')
# The latest capture must be at most this many capture intervals old (and at least this many seconds allowed).
CAPTURE_MAX_INTERVALS, CAPTURE_MIN_SECONDS = 3, 10


def _moment(text):
    try:
        moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except (AttributeError, TypeError, ValueError):
        return None
    return moment if moment.tzinfo else None


def section_freshness(snapshot: dict | None, section: str, now: datetime, max_age: float) -> dict:
    """Return {'status': 'fresh'|'stale'|'unknown', 'observed_at', 'age_seconds', 'reason'} for one section."""
    meta = ((snapshot or {}).get('sections') or {}).get(section)
    unknown = {'status': 'unknown', 'observed_at': None, 'age_seconds': None}
    if snapshot is None:
        return {**unknown, 'reason': 'No snapshot saved yet.'}
    if meta is None:
        if 'sections' not in snapshot:
            return {**unknown, 'reason': f'{section} observation time was not recorded (older snapshot).'}
        return {**unknown, 'reason': f'{section} has not been observed.'}
    observed = _moment(meta.get('observed_at'))
    if observed is None:
        return {**unknown, 'reason': f'{section} was pasted by hand, so when it was observed is unknown.'}
    age = (now - observed).total_seconds()
    result = {'observed_at': meta['observed_at'], 'age_seconds': age}
    if meta.get('identity_status') != VERIFIED:
        return {**result, 'status': 'unknown', 'reason': f'{section} was not confirmed as your reply.'}
    if age < 0 or age > max_age:
        return {**result, 'status': 'stale', 'reason': f'{section} was observed {age:,.0f} s ago.'}
    return {**result, 'status': 'fresh', 'reason': ''}


def _capture_blockers(live, now):
    if not live:
        return ['Live capture has not been started.']
    if not live.get('running'):
        return ['Live capture is stopped.']
    blockers = []
    if live.get('capture_status') != 'ok':
        blockers.append(f'The latest capture could not be read ({live.get("capture_status") or "none yet"}).')
    captured = _moment(live.get('captured_at'))
    limit = max(CAPTURE_MAX_INTERVALS * (live.get('interval') or 0), CAPTURE_MIN_SECONDS)
    if captured is None or not 0 <= (now - captured).total_seconds() <= limit:
        blockers.append('No recent capture: what is on screen now is unknown.')
    context = live.get('context') or {}
    if context.get('status') != 'verified':
        blockers.append('The Discord channel is not verified in the latest capture '
                        f'({context.get("status") or "unknown"}).')
    alignment = live.get('alignment') or {}
    if alignment.get('status') != 'certain':
        blockers.append('Chat alignment is not certain: ' + (alignment.get('reason') or 'no capture placed yet.'))
    if live.get('unresolved'):
        blockers.append(f'{live["unresolved"]} review item(s) about your replies are waiting; until they are '
                        'dismissed or fixed, what happened is unknown.')
    return blockers


def automation_view(snapshot: dict | None, now: datetime, max_age: float = FINANCIAL_MAX_AGE_SECONDS,
                    live: dict | None = None) -> dict:
    """Return section freshness, bait knowledge, and whether actions are allowed, with the reasons they are not.

    `live` is LiveCapture.status(). Every action needs capture running, its latest capture recent, readable,
    and in the verified channel, a certain chat alignment, and no review item about your replies waiting.
    """
    sections = {section: section_freshness(snapshot, section, now, max_age) for section in SECTIONS}
    blockers = _capture_blockers(live, now)
    actions_allowed = not blockers
    for section in FINANCIAL_SECTIONS:
        if sections[section]['status'] != 'fresh':
            blockers.append(f'Refresh {section}: {sections[section]["reason"]}')
    financial_ready = not blockers
    bait = (live or {}).get('bait_state') or {'status': 'unknown', 'bait': None}
    if bait.get('status') != 'verified':
        # Only bait decisions need this; other money decisions do not depend on the bait in use.
        blockers.append('Refresh profile or /bait before buying bait: the bait in use is not confirmed.')
    return {'sections': sections, 'bait': bait, 'actions_allowed': actions_allowed,
            'financial_ready': financial_ready, 'bait_ready': financial_ready and bait.get('status') == 'verified',
            'blockers': blockers}


@dataclass(frozen=True)
class AutomationState:
    """What the controller knows. Observations change it only through apply_observation(); the controller sets
    the action fields (pending, uncertain, trips, sequence) as it journals actions."""
    run_id: str
    account: str
    username: str | None
    context_id: str | None
    context_ok: bool = False
    profile: dict | None = None
    profile_observed_at: str | None = None
    cooldown_seconds: float | None = None
    buffs_observed_at: str | None = None
    pending: ActionIntent | None = None
    # Why no action may be sent until a person resolves it (an unanswered or unreadable command), else None.
    uncertain: str | None = None
    trips: int = 0
    sequence: int = 0
    applied: frozenset = frozenset()
    startup_observed_at: dict = field(default_factory=dict)
    # Refreshes queued by a periodic resync, in order ('refresh_profile', 'refresh_buffs').
    refresh_due: tuple = ()
    # Refreshes that could not be read; fishing goes on without them and they are tried again every few trips.
    deferred: tuple = ()


def refresh_problems(kind: str | None, text: str) -> list[str]:
    """Why a /profile or /buffs reply cannot establish state; empty when it reads cleanly. A profile needs a
    readable account name, balance, and level; /buffs needs the fishing cooldown. Any parser warning or line the
    parser did not understand counts too: a misread line may hide a misread value."""
    if kind not in ('profile', 'buffs'):
        return [f'Expected a profile or /buffs reply, got {kind or "an unrecognized reply"}.']
    parsed = parse_responses(**{kind: text})
    problems = list(parsed['warnings'])
    if parsed['unparsed'][kind]:
        problems.append('Lines not understood: ' + ' | '.join(parsed['unparsed'][kind]))
    if kind == 'profile':
        problems += validate_full_state(parsed)[1]
    else:
        cooldown = parsed['buffs']['fishing_cooldown']
        if not (isinstance(cooldown, dict) and cooldown.get('unit') == 'seconds'):
            problems.append('The fishing cooldown was not read.')
        elif not any(cooldown_written(line) for line in text.splitlines()):
            problems.append('The fishing cooldown has no decimal point: most likely a misread ("2.0s" as "20s").')
    return problems


def apply_observation(state: AutomationState, observation: Observation) -> AutomationState:
    """Return the state with a verified, cleanly read profile or /buffs reply applied. Rejected observations
    (another player, unknown identity, another channel, unreadable values) and ones already applied return the
    state unchanged."""
    if observation.observation_id in state.applied or observation.identity_status != VERIFIED:
        return state
    if state.context_id and observation.context_id and observation.context_id != state.context_id:
        return state
    if observation.kind in ('profile', 'buffs') and refresh_problems(observation.kind, observation.text):
        return state
    applied = state.applied | {observation.observation_id}
    if observation.kind == 'profile':
        profile = parse_responses(profile=observation.text)['profile']
        return replace(state, profile=profile, profile_observed_at=observation.observed_at, applied=applied)
    if observation.kind == 'buffs':
        cooldown = parse_responses(buffs=observation.text)['buffs']['fishing_cooldown']
        if isinstance(cooldown, dict) and cooldown.get('unit') == 'seconds':
            return replace(state, cooldown_seconds=cooldown['value'], buffs_observed_at=observation.observed_at,
                           applied=applied)
    return replace(state, applied=applied)
