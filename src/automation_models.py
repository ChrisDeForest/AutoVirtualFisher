"""Contracts shared by the automation policy, transports, and controller (automation plan, Task 2)."""
from dataclasses import dataclass, field

# The reply each action expects.
from .account_reads import ALL_READS, reply_of

EXPECTED_REPLY = {'refresh_profile': 'profile', 'refresh_buffs': 'buffs', 'refresh_charms': 'charms', 'fish': 'fishing',
                  'act': 'action',
                  **{kind: reply_of(kind) for kind in ALL_READS}}


@dataclass(frozen=True)
class ActionIntent:
    action_id: str
    kind: str                      # 'refresh_profile', 'refresh_buffs', 'refresh_charms', or 'fish'
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
    reply_timeout: float = 15.0    # seconds without a correlated reply before a /fish becomes uncertain
    read_timeout: float = 20.0     # the same for everything else (reads, page 2, actions): longer replies (user)
    cooldown_margin: float = 0.0   # seconds added to the verified cooldown (the random jitter is added too)
    # A random extra wait before each fish: (share of fish, from, to, step) in seconds, ends included.
    jitter_bands: tuple = ((0.75, 0.0, 0.25, 0.025), (0.175, 0.25, 0.5, 0.05), (0.075, 0.5, 0.75, 0.05))
    mode: str = 'simulation'       # the separate native CLI explicitly selects 'live'
    capabilities: tuple = field(default=('refresh_profile', 'refresh_buffs', 'refresh_charms', *ALL_READS, 'fish',
                                         'act'))
    resync_trips: int | None = 100   # /profile, /buffs and /charms are read again after this many trips
    retry_unread_trips: int = 10     # a reply missing anything is read again after this many trips
    assumed_cooldown: float = 5.0    # seconds, while /buffs is unread ("You must wait" replies correct it)


class TransportError(Exception):
    """The transport could not hand the command over. Whether it reached the game is unknown."""


class CapabilityError(Exception):
    """The transport does not support this action; raised before anything is dispatched."""


class NotDispatched(CapabilityError):
    """Refused before any input reached the game (wrong context, no baseline, stopped): certainly not sent."""


class Interrupted(NotDispatched):
    """Discord stopped being the active window before the command was sent (before the final Enter, or before a
    click): nothing reached the game. It is tried again, from a clean message box, once Discord is active."""


class FocusLost(TransportError):
    """Discord stopped being the active window before an input event; raised by the input driver."""


# The context reason when Discord is not the active window.
NOT_FOREGROUND = 'Discord must remain in the foreground.'
