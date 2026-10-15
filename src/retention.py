"""Bound generated artifacts within an explicitly supplied directory; never recurse or follow links."""
import os
from pathlib import Path
import re
import tempfile
import threading

# Writes and capacity checks in this process share a lock; no account-sized lock cache is retained.
WRITE_LOCK = threading.RLock()
RECORD_NAME = re.compile(r'\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{8}\.json')


def scoped_directory(folder):
    folder = Path(folder)
    for part in (folder, *folder.parents):
        if part.is_symlink() or part.is_junction():
            raise OSError('Refusing retention through a linked directory')
    return folder.resolve(strict=True)


def owned_files(folder, pattern):
    """Direct, regular owned files only. An unreadable directory raises for critical callers."""
    folder = Path(folder)
    if not folder.exists():
        return []
    scope = scoped_directory(folder)
    result = []
    for path in folder.iterdir():
        if pattern.fullmatch(path.name) and not path.is_symlink() and path.is_file():
            if path.resolve(strict=True).parent == scope:
                result.append(path)
    return result


def capacity_problem(folder, pattern, new_bytes, max_count, max_bytes):
    """Reject an immutable write rather than remove historical data."""
    files = owned_files(folder, pattern)
    size = sum(path.stat().st_size for path in files)
    if len(files) >= max_count or size + new_bytes > max_bytes:
        return (f'Snapshot storage limit reached ({max_count:,} records or {max_bytes // (1024 * 1024):,} MiB '
                'per account). Move older snapshots to a backup folder before saving another snapshot.')
    return ''


def prune_files(folder, pattern, max_count, max_bytes, *, group_stem=False, protected=None):
    """Keep newest owned names under both bounds. Protected records have a separate, unlimited budget.

    Capture pairs count as one group including either orphan suffix. Unreadable protected status must be
    treated as protected by the caller. Removal is limited to individually checked direct files.
    """
    try:
        folder = Path(folder)
        scope = folder.resolve()
        groups = {}
        for path in owned_files(folder, pattern):
            if protected and protected(path):
                continue
            key = path.stem if group_stem else path.name
            groups.setdefault(key, []).append((path, path.stat().st_size))
        count = size = 0
        for key in sorted(groups, reverse=True):
            files = groups[key]
            group_bytes = sum(length for _, length in files)
            if count + 1 <= max(0, max_count) and size + group_bytes <= max(0, max_bytes):
                count += 1
                size += group_bytes
                continue
            for path, _ in files:
                if not path.is_symlink() and path.resolve().parent == scope:
                    path.unlink(missing_ok=True)
    except OSError:
        pass  # optional artifacts never interfere with automation


def trim_lines(path, max_bytes):
    """Atomically retain a bounded tail of complete lines, reading only that tail after the size threshold.

    A line larger than the entire budget is omitted. The file must be a direct regular file, not a link.
    """
    path = Path(path)
    scoped_directory(path.parent)
    if path.is_symlink():
        raise OSError('Refusing to trim a linked file')
    if max_bytes < 0:
        raise ValueError('Negative retention byte limit')
    length = path.stat().st_size
    if length <= max_bytes:
        return False
    with path.open('rb') as source:
        start = max(0, length - max_bytes)
        # The byte before the tail tells us whether its first byte begins a complete line.
        if start:
            source.seek(start - 1)
            boundary = source.read(1) == b'\n'
        else:
            boundary = True
        source.seek(start)
        tail = source.read(max_bytes)
    if not boundary:
        _, _, tail = tail.partition(b'\n')
    if tail and not tail.endswith(b'\n'):
        tail = tail[:tail.rfind(b'\n') + 1]
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, prefix='.' + path.name + '.',
                                         suffix='.tmp', delete=False) as target:
            temporary = Path(target.name)
            target.write(tail)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def prune_html_cache(current, max_count, max_bytes):
    """Only the application's two known HTML cache folders; keep the page currently being parsed."""
    root = Path('src/data/html')
    folders = (root, root / 'pages')
    scopes = {scoped_directory(folder) for folder in folders if folder.exists()}
    current = Path(current)
    if current.is_symlink() or current.resolve().parent not in scopes:
        raise OSError('HTML cache path is outside the known cache folders')
    files = [path for folder in folders for path in owned_files(folder, re.compile(r'.+\.html'))]
    files.sort(key=lambda path: (path != current, -path.stat().st_mtime_ns, path.name))
    count = size = 0
    for path in files:
        file_bytes = path.stat().st_size
        if count + 1 <= max_count and size + file_bytes <= max_bytes:
            count += 1
            size += file_bytes
            continue
        if path == current:
            raise OSError('Current HTML page exceeds the cache storage limit')
        if not path.is_symlink() and path.resolve().parent in scopes:
            path.unlink(missing_ok=True)
