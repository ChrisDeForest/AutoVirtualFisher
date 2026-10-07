"""The state an automated action may rely on: each value with how recently, and whose, it was observed.

This is separate from the logbook. A snapshot's save time is not evidence that its values are current, so a
section counts as fresh only when it was observed, confirmed as the configured player's, and is recent enough.
Pasted (user-attributed) and version 1 snapshots have no observation time and are unknown here.
"""
from datetime import datetime

from .observation import VERIFIED
from .player_snapshots import SECTIONS

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
