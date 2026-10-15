"""Messages the live capture could not process cleanly, kept locally so the parsers can be improved."""
import json
from pathlib import Path
import re

from .player_snapshots import new_record_id, write_json_atomically
from . import storage_limits as sl
from .retention import WRITE_LOCK, RECORD_NAME, prune_files

DEFAULT_REVIEW_ROOT = Path(__file__).parent / 'data' / 'review_queue'
RECORD_ID = re.compile(r'^[0-9TZ.]+-[0-9a-f]{8}$')


def add_item(root: Path, account: str, kind: str, text: str, reasons: list[str],
             identity_status: str | None = None, observation_id: str | None = None,
             evidence_id: str | None = None, blocks_actions: bool = True) -> dict:
    """Queue an item. With `evidence_id` (what was captured, not just the reply text), an item already queued
    for the same evidence is returned with 'duplicate': True instead of adding another."""
    if evidence_id:
        existing = next((item for item in list_items(root)[0] if item.get('evidence_id') == evidence_id), None)
        if existing:
            return {**existing, 'duplicate': True}
    captured_at, item_id = new_record_id()
    item = {'id': item_id, 'captured_at': captured_at, 'account': account, 'kind': kind, 'text': text,
            'reasons': reasons, 'identity_status': identity_status, 'observation_id': observation_id,
            'evidence_id': evidence_id, 'blocks_actions': blocks_actions}
    def protected(path):
        try:
            return json.loads(path.read_text(encoding='utf-8')).get('blocks_actions', True) is not False
        except (OSError, ValueError, AttributeError):
            return True
    with WRITE_LOCK:
        write_json_atomically(Path(root), item_id, item)
        prune_files(root, RECORD_NAME, sl.REVIEW_MAX_FILES, sl.REVIEW_MAX_BYTES, protected=protected)
    return item


def list_items(root: Path) -> tuple[list[dict], list[str]]:
    """Return queued items newest first, plus warnings for unreadable files."""
    items, warnings = [], []
    folder = Path(root)
    if not folder.exists():
        return items, warnings
    for path in folder.glob('*.json'):
        try:
            item = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(item.get('text'), str) or not isinstance(item.get('reasons'), list):
                raise ValueError('required fields are missing')
        except (OSError, json.JSONDecodeError, ValueError) as error:
            warnings.append(f'{path.name}: {error}')
            continue
        items.append(item)
    items.sort(key=lambda item: item.get('id', ''), reverse=True)
    return items, warnings


def blocking_items(root: Path, account: str | None) -> int:
    """Queued items that leave the account's state uncertain until a person dismisses or fixes them. Items
    without the flag (queued before it existed) count as blocking."""
    items, _ = list_items(root)
    return sum(item.get('blocks_actions', True) is not False
               and (item.get('account') or '').casefold() == (account or '').casefold() for item in items)


def dismiss(root: Path, item_id: str) -> bool:
    """Delete one queued item. Only well-formed ids are accepted, so no other file can be touched."""
    if not RECORD_ID.fullmatch(item_id or ''):
        return False
    path = Path(root) / f'{item_id}.json'
    if not path.exists():
        return False
    path.unlink()
    return True
