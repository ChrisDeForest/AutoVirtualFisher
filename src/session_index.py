"""Rebuildable session metadata; JSON files are always the source of truth."""
from contextlib import contextmanager
import json
import errno
import os
import sqlite3
import time

from . import storage_limits


@contextmanager
def session_index(folder, read_session):
    """Serialize writers across processes, including index recovery and JSON publication.

    Stat every source file to detect external edits/deletions, but only parse changed files.
    The lock lives separately so rebuilding a damaged database cannot split writer locks.
    """
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / '.sessions.lock').open('a+b') as lock:
        lock.seek(0, 2)
        if not lock.tell():
            lock.write(b'0')
            lock.flush()
        if os.name == 'nt':
            import msvcrt
            deadline = time.monotonic() + 30
            while True:
                try:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    # Windows reports contention as EACCES/EDEADLK (winerror 33).
                    # Persistent/invalid-handle failures must never hang a save.
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        database = folder / '.sessions.sqlite3'
        connection = None
        try:
            for attempt in range(2):
                try:
                    connection = sqlite3.connect(database)
                    page_size = connection.execute('PRAGMA page_size').fetchone()[0]
                    max_pages = storage_limits.SESSION_INDEX_MAX_BYTES // page_size
                    if max_pages < 4:
                        raise OSError('Session index storage limit reached; archive account history before continuing.')
                    connection.execute(f'PRAGMA max_page_count = {max_pages}')
                    if connection.execute('PRAGMA page_count').fetchone()[0] > max_pages:
                        raise OSError('Session index storage limit reached; archive account history before continuing.')
                    connection.execute('CREATE TABLE IF NOT EXISTS records '
                                       '(name TEXT PRIMARY KEY, stamp TEXT, fingerprint TEXT, payload TEXT, warning TEXT)')
                    connection.execute('CREATE INDEX IF NOT EXISTS fingerprints ON records(fingerprint)')
                    connection.execute('CREATE TABLE IF NOT EXISTS usage (files INTEGER, bytes INTEGER)')
                    rows = {row[0]: row[1] for row in connection.execute('SELECT name, stamp FROM records')}
                    break
                except sqlite3.DatabaseError as error:
                    if connection is not None:
                        connection.close()
                        connection = None
                    if attempt or getattr(error, 'sqlite_errorcode', None) not in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                        raise
                    database.unlink(missing_ok=True)
                    for suffix in ('-journal', '-wal', '-shm'):
                        database.with_name(database.name + suffix).unlink(missing_ok=True)
            present = set()
            total_bytes = 0
            with os.scandir(folder) as scan:
                files = [entry for entry in scan if entry.name.endswith('.json')]
            for entry in files:
                path = folder / entry.name
                try:
                    stat = entry.stat()
                except FileNotFoundError:
                    continue
                present.add(path.name)
                total_bytes += stat.st_size
                stamp = f'{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_size}'
                if rows.get(path.name) == stamp:
                    continue
                try:
                    record = read_session(path)
                    values = (record.get('source_fingerprint'), json.dumps(record), None)
                except (OSError, ValueError, TypeError, AttributeError) as error:
                    values = (None, None, f'{path.name}: {error}')
                connection.execute('INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?, ?)',
                                   (path.name, stamp, *values))
            connection.executemany('DELETE FROM records WHERE name = ?', [(name,) for name in rows.keys() - present])
            connection.execute('DELETE FROM usage')
            connection.execute('INSERT INTO usage VALUES (?, ?)', (len(present), total_bytes))
            connection.commit()
            yield connection
        except sqlite3.Error as error:
            raise OSError(f'Session index storage limit or disk error; archive account history or free disk space: {error}') from error
        finally:
            if connection is not None:
                connection.close()
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
