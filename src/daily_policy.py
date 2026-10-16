"""Part 8: when /daily is due, from the last reply. Pure: no I/O.

Times are UTC. A reply with a streak or a cooldown sets the next try; any other reply counts as unknown and waits for
the next 00:00 UTC. The level hold keeps the run near 20, 50 and 100 so the daily claim is not missed by a few minutes.
"""
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone

HOLD_LEVELS = (20, 50, 100)
HOLD_SECONDS = 300


@dataclass
class DailyState:
    last_claim: datetime | None = None
    next_due: datetime | None = None
    streak: int | None = None
    unknown_day: date | None = None

    def to_dict(self):
        return {'last_claim': self.last_claim.isoformat() if self.last_claim else None,
                'next_due': self.next_due.isoformat() if self.next_due else None,
                'streak': self.streak,
                'unknown_day': self.unknown_day.isoformat() if self.unknown_day else None}

    @classmethod
    def from_dict(cls, d):
        try:
            last_claim = datetime.fromisoformat(d['last_claim']) if d.get('last_claim') else None
            next_due = datetime.fromisoformat(d['next_due']) if d.get('next_due') else None
            unknown_day = date.fromisoformat(d['unknown_day']) if d.get('unknown_day') else None
            streak = d.get('streak')
            if last_claim is not None and last_claim.tzinfo is None or next_due is not None and next_due.tzinfo is None:
                return cls()
            return cls(last_claim, next_due, None if streak is None else int(streak), unknown_day)
        except (AttributeError, KeyError, TypeError, ValueError):
            return cls()


def _utc(now):
    return now.astimezone(timezone.utc)


def _next_midnight(now):
    return datetime.combine(_utc(now).date() + timedelta(days=1), time(), tzinfo=timezone.utc)


def _seconds_from_note(note):
    parts = re.findall(r'(\d+)\s*([hms])\b', note or '')
    if not parts:
        return None
    return sum(int(n) * {'h': 3600, 'm': 60, 's': 1}[unit] for n, unit in parts)


def due(state, now):
    now = _utc(now)
    if state.unknown_day == now.date():
        return False
    if state.next_due is not None:
        return now >= state.next_due
    return state.last_claim is None or state.last_claim.astimezone(timezone.utc).date() != now.date()


def apply(state, result, note, now, sent_at=None):
    """`sent_at`: when the /daily was sent. A claim counts from then, so a reply read just after 00:00 UTC still
    counts for the day it was sent; a cooldown or unclear reply counts from `now`, when it was read."""
    now = _utc(now)
    streak = re.search(r'streak\s+(\d+)', note or '')
    if result == 'done' and streak:
        claimed = _utc(sent_at) if sent_at is not None else now
        return replace(state, last_claim=claimed, streak=int(streak.group(1)), next_due=_next_midnight(claimed),
                       unknown_day=None)
    if result == 'done' and 'on cooldown' in (note or ''):
        left = _seconds_from_note(note)
        if left is not None:
            return replace(state, next_due=now + timedelta(seconds=left), unknown_day=None)
    return replace(state, unknown_day=now.date(), next_due=_next_midnight(now))


def held_for_level(level, seconds_to_next):
    return level + 1 in HOLD_LEVELS and seconds_to_next is not None and seconds_to_next <= HOLD_SECONDS
