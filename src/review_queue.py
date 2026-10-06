"""Messages the live capture could not process cleanly, kept locally so the parsers can be improved."""
import json
from pathlib import Path
import re

from .player_snapshots import new_record_id, write_json_atomically

DEFAULT_REVIEW_ROOT = Path(__file__).parent / 'data' / 'review_queue'
RECORD_ID = re.compile(r'^[0-9TZ.]+-[0-9a-f]{8}$')


def add_item(root: Path, account: str, kind: str, text: str, reasons: list[str]) -> dict:
    captured_at, item_id = new_record_id()
    item = {'id': item_id, 'captured_at': captured_at, 'account': account, 'kind': kind, 'text': text,
            'reasons': reasons}
    write_json_atomically(Path(root), item_id, item)
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


def dismiss(root: Path, item_id: str) -> bool:
    """Delete one queued item. Only well-formed ids are accepted, so no other file can be touched."""
    if not RECORD_ID.fullmatch(item_id or ''):
        return False
    path = Path(root) / f'{item_id}.json'
    if not path.exists():
        return False
    path.unlink()
    return True
