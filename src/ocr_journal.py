"""Scoped OCR text audit: one compressed stream per run, no images or retained reads."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import gzip
import json
from pathlib import Path
import re
from secrets import token_hex
import threading
import time
from . import storage_limits as limits
from .retention import scoped_directory

_OBSERVER = ContextVar('ocr_journal_observer', default=None)
_LOG_NAME = re.compile(r'ocr_reads(?:\.\d+-[0-9a-f]{8})?\.jsonl\.gz')


@contextmanager
def ocr_scope(journal, **metadata):
    """Attach actual OCR passes in this capture (including asyncio tasks) to its run."""
    if journal is None:
        yield
        return
    context = journal.begin_capture()
    observer = (journal, {'capture_id': token_hex(8), **context, **metadata}) if context is not None else None
    token = _OBSERVER.set(observer)
    try:
        yield
    finally:
        _OBSERVER.reset(token)
        if context is not None:
            journal.end_capture()


def record_ocr_read(engine, lines, **metadata):
    observer = _OBSERVER.get()
    if observer is not None:
        journal, capture = observer
        journal.record({'engine': engine, 'lines': list(lines or []), **capture, **metadata})


def trace_windows_read(read):
    """Observe the result without retaining the WinRT engine, bitmap, stream, or image."""
    @wraps(read)
    async def traced(path, scale, region=None):
        if _OBSERVER.get() is None:
            return await read(path, scale, region)
        started = time.perf_counter()
        try:
            result = await read(path, scale, region)
        except Exception as error:
            record_ocr_read('windows', [], scale=scale, region=region, error=str(error),
                            duration_ms=round((time.perf_counter()-started)*1000,3))
            raise
        record_ocr_read('windows', [line['text'] for line in result], scale=scale, region=region,
                        duration_ms=round((time.perf_counter()-started)*1000,3))
        return result
    return traced


class OcrJournal:
    """Bounded-memory gzip JSONL writer. Logging failures never change input or OCR outcomes.

    Each read is flushed, but only the action journal has crash-durable fsync semantics.
    A process crash can leave a partial compressed tail; completed runs close cleanly.
    """
    def __init__(self, root, context):
        self.root, self.context = Path(root), context
        self._lock = threading.Lock()
        self._stream = self._run_id = self._path = None
        self._run_archive_bytes = self._account_archive_bytes = 0
        self._closed = False
        self._captures = 0
        self.error = None

    def record(self, read):
        with self._lock:
            if self.error or (self._closed and not self._captures):
                return
            try:
                run_id = read.get('run_id')
                if not run_id:
                    return  # Inspection has no run and performs no journaling.
                if not isinstance(run_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',run_id) or run_id in ('.','..'):
                    raise ValueError('Invalid OCR run id')
                if run_id != self._run_id:
                    self._close_stream()
                    folder = self.root/run_id
                    ancestor = self.root
                    while not ancestor.exists():
                        ancestor = ancestor.parent
                    scoped_directory(ancestor)
                    folder.mkdir(parents=True,exist_ok=True)
                    scoped_directory(folder)
                    self._path = folder/'ocr_reads.jsonl.gz'
                    if self._path.is_symlink():
                        raise OSError('Refusing OCR logging through a linked file')
                    self._stream = gzip.open(self._path,'at',encoding='utf-8',compresslevel=3)
                    self._run_id = run_id
                    self._prune_logs(self._path)
                record = {'schema_version':1, 'type':'ocr_read',
                          'at':datetime.now(timezone.utc).isoformat(), **read}
                self._stream.write(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n')
                self._stream.flush()
                size = self._stream.buffer.fileobj.tell()
                if size >= limits.OCR_SEGMENT_MAX_BYTES:
                    path = self._path
                    self._close_stream()
                    rotated = path.with_name(f'ocr_reads.{time.time_ns()}-{token_hex(4)}.jsonl.gz')
                    path.replace(rotated)
                    self._prune_logs()
                elif (self._run_archive_bytes + size > limits.OCR_RUN_MAX_BYTES
                      or self._account_archive_bytes + size > limits.OCR_ACCOUNT_MAX_BYTES):
                    self._prune_logs(self._path)
            except Exception as error:
                self.error = str(error)
                self._close_stream()

    def _close_stream(self):
        stream, self._stream = self._stream, None
        self._run_id = None
        self._path = None
        if stream is not None:
            try:
                stream.close()
            except (OSError,ValueError):
                pass

    def _prune_logs(self, active=None):
        """Only owned OCR files inside the account journal can be removed."""
        if not self.root.exists():
            return
        scope = scoped_directory(self.root)
        files = []
        for path in self.root.glob('*/ocr_reads*.gz'):
            if not _LOG_NAME.fullmatch(path.name) or path.is_symlink():
                continue
            if scoped_directory(path.parent).parent != scope:
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            files.append((stat.st_mtime_ns,path,stat.st_size))
        files.sort(key=lambda item:(item[0],item[1].name))
        run = active.parent if active is not None else None
        if run is not None:
            own = [item for item in files if item[1].parent == run]
            used = sum(item[2] for item in own)
            for _,path,size in own:
                if used <= limits.OCR_RUN_MAX_BYTES:
                    break
                if path == active:
                    continue
                path.unlink(missing_ok=True)
                files = [item for item in files if item[1] != path]
                used -= size
        else:
            for folder in {item[1].parent for item in files}:
                own = [item for item in files if item[1].parent == folder]
                used = sum(item[2] for item in own)
                for _,path,size in own:
                    if used <= limits.OCR_RUN_MAX_BYTES:
                        break
                    path.unlink(missing_ok=True)
                    files = [item for item in files if item[1] != path]
                    used -= size
        used = sum(item[2] for item in files)
        for _,path,size in list(files):
            if used <= limits.OCR_ACCOUNT_MAX_BYTES:
                break
            if path == active:
                continue
            path.unlink(missing_ok=True)
            files = [item for item in files if item[1] != path]
            used -= size
        if active is not None:
            active_size = next((item[2] for item in files if item[1] == active),0)
            if active_size > min(limits.OCR_RUN_MAX_BYTES,limits.OCR_ACCOUNT_MAX_BYTES):
                raise OSError('One OCR read exceeds the configured OCR log storage limit.')
            self._run_archive_bytes = sum(item[2] for item in files if item[1] != active and item[1].parent == active.parent)
            self._account_archive_bytes = sum(item[2] for item in files if item[1] != active)

    def begin_capture(self):
        with self._lock:
            if self._closed or self.error:
                return None
            try:
                context = self.context()
                context = {'run_id':context} if isinstance(context,str) else dict(context or {})
                if not context.get('run_id'):
                    return None
            except Exception as error:
                self.error = str(error)
                return None
            self._captures += 1
            return context

    def end_capture(self):
        with self._lock:
            self._captures -= 1
            if self._closed and not self._captures:
                self._close_stream()
                try:
                    self._prune_logs()
                except OSError as error:
                    self.error = str(error)

    def close(self):
        with self._lock:
            self._closed = True
            if not self._captures:
                self._close_stream()
                try:
                    self._prune_logs()
                except OSError as error:
                    self.error = str(error)

    def resume(self):
        with self._lock:
            self._closed, self.error = False, None
