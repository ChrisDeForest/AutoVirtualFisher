"""Who a Virtual Fisher reply belongs to, and the envelope a reply read from Discord is stored in.

Identity is explicit configuration: the display name (shown as "Inventory of <name>" and "<name> used /command")
and the Discord username (the author line of fishing results and "<username>'s current multipliers") are kept as
separate fields and never inferred from each other, however similar they look.
"""
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re

from .player_snapshots import write_json_atomically

VERIFIED, UNKNOWN, MISMATCH = 'verified', 'unknown', 'mismatch'
# Saved by hand from the logbook: the person who saved it said whose it is; nothing on screen confirmed it.
USER_ATTRIBUTED = 'user_attributed'
BOT_NAME = 'Virtual Fisher'
DEFAULT_IDENTITY_PATH = Path(__file__).parent / 'data' / 'identities' / 'identities.json'


@dataclass(frozen=True)
class Identity:
    display_name: str
    username: str | None = None


@dataclass(frozen=True)
class Observation:
    observation_id: str
    capture_id: str
    observed_at: str
    context_id: str | None
    kind: str | None
    recipient: str | None
    text: str
    identity_status: str
    reason: str = ''
    # Evidence tying this reply to one dispatched action (its action id), when the transport can supply it.
    # Reply kind alone never correlates: an identical older reply would look the same.
    correlation: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def recipients(kind: str | None, lines: list[str], invoked_by: str | None = None) -> list[tuple[str, str]]:
    """Return (name, identity field) pairs that say whose reply this is."""
    from .player_state import _clean
    found = [(invoked_by, 'display_name')] if invoked_by else []
    cleaned = [_clean(line) for line in lines]
    if kind == 'profile':
        for line in cleaned:
            if line.startswith('Inventory of '):
                name = line.removeprefix('Inventory of ').strip()
                if name:
                    found.append((name, 'display_name'))
                break
    elif kind == 'buffs':
        for line in cleaned:
            match = re.fullmatch(r"(.+?)['’]s current multipliers \(including bait\):?", line, re.I)
            if match:
                found.append((match[1].strip(), 'username'))
                break
    elif kind == 'fishing' and 'You caught:' in cleaned:
        # The fishing result names its player on the line above "You caught:".
        index = cleaned.index('You caught:')
        if index and re.fullmatch(r'\S+', cleaned[index - 1]) and ':' not in cleaned[index - 1]:
            found.append((cleaned[index - 1], 'username'))
    return found


def check_identity(identity: Identity | None, named: list[tuple[str, str]]) -> tuple[str, str | None, str]:
    """Return (identity status, recipient shown, reason) for the names a reply carries.

    Any name that differs from its configured field is a mismatch. Otherwise one confirmed name verifies the
    reply. A reply without names, or whose names cannot be checked because that field is not configured, is
    unknown: the account chosen on the page is not evidence of whose reply it is.
    """
    if not named:
        return UNKNOWN, None, 'The reply does not name a player, so it cannot be confirmed as yours.'
    verified = None
    for name, field in named:
        expected = getattr(identity, field, None) if identity else None
        if not expected:
            continue
        if name.casefold() != expected.casefold():
            label = 'display name' if field == 'display_name' else 'Discord username'
            return MISMATCH, name, f'The reply is for {name}, not your {label} {expected}.'
        verified = name
    if verified:
        return VERIFIED, verified, ''
    shown = named[0][0]
    return UNKNOWN, shown, f'The reply names {shown}, but that name is not configured, so it cannot be confirmed.'


def load_identity(display_name: str, path: Path = DEFAULT_IDENTITY_PATH) -> Identity | None:
    try:
        saved = json.loads(Path(path).read_text(encoding='utf-8'))
        entry = saved['identities'][display_name.casefold()]
        return Identity(entry['display_name'], entry.get('username'))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return None


def save_identity(identity: Identity, path: Path = DEFAULT_IDENTITY_PATH) -> None:
    path = Path(path)
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(saved.get('identities'), dict):
            raise ValueError
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        saved = {'identities': {}}
    saved['identities'][identity.display_name.casefold()] = asdict(identity)
    write_json_atomically(path.parent, path.stem, saved)
