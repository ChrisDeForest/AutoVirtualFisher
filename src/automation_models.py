"""Contracts shared by the automation policy, transports, and controller (automation plan, Task 2)."""
from dataclasses import dataclass, field

# The reply each action expects.
EXPECTED_REPLY = {'refresh_profile': 'profile', 'refresh_buffs': 'buffs', 'fish': 'fishing'}


@dataclass(frozen=True)
class ActionIntent:
    action_id: str
    kind: str                      # 'refresh_profile', 'refresh_buffs', or 'fish'
    context_id: str | None
    created_at: str                # UTC ISO time
    expected_reply_kind: str
    arguments: tuple = ()
    preconditions: tuple = ()      # human-readable facts the policy relied on


@dataclass(frozen=True)
class DispatchReceipt:
    """Local evidence that a transport handed the command over. It is not evidence the game acted on it."""
    action_id: str
    sent_at: str                   # UTC ISO time
    detail: str = ''


@dataclass(frozen=True)
class ContextStatus:
    ok: bool
    context_id: str | None
    reason: str = ''


@dataclass(frozen=True)
class AutomationConfig:
    account: str                   # display name ("Inventory of <name>")
    username: str | None           # Discord username (fishing results' author line)
    context_id: str | None         # the channel the run is pinned to
    max_trips: int | None = None   # bounded runs stop after this many confirmed trips...
    max_seconds: float | None = None  # ...or this long, whichever comes first
    reply_timeout: float = 20.0    # seconds without a correlated reply before the action becomes uncertain
    cooldown_margin: float = 0.5   # seconds added to the verified cooldown
    mode: str = 'simulation'       # the separate native CLI explicitly selects 'live'
    capabilities: tuple = field(default=('refresh_profile', 'refresh_buffs', 'fish'))
    resync_trips: int | None = 100   # /profile and /buffs are read again after this many trips
    retry_unread_trips: int = 10     # an unreadable /profile or /buffs is tried again after this many trips
    assumed_cooldown: float = 5.0    # seconds, while /buffs is unread ("You must wait" replies correct it)


class TransportError(Exception):
    """The transport could not hand the command over. Whether it reached the game is unknown."""


class CapabilityError(Exception):
    """The transport does not support this action; raised before anything is dispatched."""


class NotDispatched(CapabilityError):
    """Refused before any input reached the game (wrong context, no baseline, stopped): certainly not sent."""
