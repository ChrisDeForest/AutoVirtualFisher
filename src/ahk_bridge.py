"""One owned AHK process and one bounded primitive at a time; never replays."""
import base64
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import queue
import subprocess
import threading
import time
from typing import Callable
from .ahk_runtime import AhkRuntime, hidden_process_options

READY_TIMEOUT = 3.0
PRIMITIVE_TIMEOUT = 2.0
CANCEL_POLL = .05
OPERATIONS = {'probe','target','text','key','select_all','click','shutdown'}
KEYS = {'Enter','Tab','Escape','Backspace'}

@dataclass(frozen=True)
class AhkTarget:
    window: int
    renderer: int

@dataclass(frozen=True)
class AhkReply:
    request_id: int
    status: str
    code: str
    input_state: str
    message: str

class AhkBridgeError(RuntimeError):
    def __init__(self, message, input_state='unknown', code='ipc'):
        super().__init__(message)
        self.input_state, self.code = input_state, code

def parse_reply(line, request_id):
    try:
        fields = line.rstrip('\r\n').split('\t')
        if len(fields) != 6 or fields[0] != '1' or int(fields[1]) != request_id:
            raise ValueError('wrong protocol or request ID')
        if fields[2] not in ('ok','error') or fields[4] not in ('not_started','posted','unknown'):
            raise ValueError('invalid response state')
        message = base64.b64decode(fields[5], validate=True).decode('utf-8')
        return AhkReply(request_id, fields[2], fields[3], fields[4], message)
    except (ValueError, UnicodeError) as error:
        raise AhkBridgeError(f'Invalid AHK response: {error}', code='protocol') from error

class AhkBridge:
    def __init__(self, runtime: AhkRuntime):
        self.runtime = runtime
        self._process = None
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._cancelled = threading.Event()
        self._responses = queue.Queue()
        self._stderr = deque(maxlen=20)
        self._next_id = 0
        self._inflight_target = None
        self._cleanup_error = ''
        self._cleanup_pending = False

    def _command(self):
        return [str(self.runtime.executable), '/ErrorStdOut',
                str(Path(__file__).parent.joinpath('ahk/discord_input.ahk').resolve())]

    def start(self) -> None:
        with self._lock:
            with self._state_lock:
                if self._cleanup_pending or self._cleanup_error:
                    raise AhkBridgeError(self._cleanup_error or 'Discord keyboard cleanup is still running.',
                                         'not_started', 'cleanup')
                if self._process is not None:
                    if self._process.poll() is None and not self._cancelled.is_set():
                        return
                    raise AhkBridgeError('AHK worker is unavailable; close before explicit restart.', 'not_started')
                self._cancelled.clear()
                self._responses = responses = queue.Queue()
                self._stderr = stderr = deque(maxlen=20)
                self._next_id = 0
                try:
                    process = subprocess.Popen(self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                               shell=False, close_fds=True, **hidden_process_options())
                except OSError as error:
                    raise AhkBridgeError(f'Could not start AHK: {error}', 'not_started') from error
                self._process = process
            def read_stdout():
                try:
                    while True:
                        line = process.stdout.readline(16385)
                        if not line:
                            break
                        responses.put(line)
                except (OSError, ValueError, UnicodeError):
                    pass
                finally:
                    responses.put(None)
            def read_stderr():
                try:
                    for line in process.stderr:
                        stderr.append(line[:1000].rstrip())
                except (OSError, ValueError, UnicodeError):
                    pass
            threading.Thread(target=read_stdout, daemon=True).start()
            threading.Thread(target=read_stderr, daemon=True).start()
            try:
                reply = self._request('probe', None, None, '', lambda: False, READY_TIMEOUT)
                if reply.message != f'{self.runtime.version}\t{self.runtime.bitness}' or reply.input_state != 'not_started':
                    raise AhkBridgeError('AHK worker compatibility mismatch.', 'not_started', 'protocol')
            except Exception:
                self.cancel_input()
                raise

    def request(self, operation: str, target: AhkTarget | None, point: tuple[int,int] | None,
                payload: str, cancelled: Callable[[],bool]) -> AhkReply:
        if operation not in OPERATIONS:
            raise ValueError('Unknown AHK operation')
        if operation == 'key' and payload not in KEYS:
            raise ValueError('Unsupported AHK key')
        if len(payload.encode('utf-8')) > 1024 or any(ord(c)<32 or ord(c)==127 for c in payload):
            raise ValueError('AHK text must be a bounded literal single line')
        with self._lock:
            return self._request(operation, target, point, payload, cancelled, PRIMITIVE_TIMEOUT)

    def _request(self, operation, target, point, payload, cancelled, timeout):
        posted = False
        with self._state_lock:
            process, responses = self._process, self._responses
            if cancelled() or self._cancelled.is_set() or process is None or process.poll() is not None:
                raise AhkBridgeError('AHK input stopped or unavailable.', 'not_started')
            self._next_id += 1
            request_id = self._next_id
            window, renderer = (target.window, target.renderer) if target else (0,0)
            x,y = point or (0,0)
            body = base64.b64encode(payload.encode('utf-8')).decode('ascii')
            line = f'1\t{request_id}\t{operation}\t{window}\t{renderer}\t{x}\t{y}\t{body}\n'
            self._inflight_target = target if operation in ('text', 'key', 'select_all') else None
            try:
                posted = True
                process.stdin.write(line)
                process.stdin.flush()
            except (OSError, ValueError) as error:
                write_error = error
            else:
                write_error = None
        try:
            if write_error:
                raise AhkBridgeError(f'AHK pipe failed: {write_error}')
            deadline = time.monotonic() + timeout
            while True:
                if cancelled() or self._cancelled.is_set():
                    raise AhkBridgeError('AHK primitive cancelled.')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AhkBridgeError('AHK acknowledgement timed out.')
                try:
                    response = responses.get(timeout=min(CANCEL_POLL, remaining))
                except queue.Empty:
                    continue
                if response is None:
                    detail = '; '.join(self._stderr)
                    raise AhkBridgeError('AHK worker exited before acknowledgement. ' + detail)
                reply = parse_reply(response, request_id)
                if reply.status == 'error':
                    if reply.input_state == 'not_started':
                        with self._state_lock:
                            self._inflight_target = None
                    raise AhkBridgeError(reply.message, reply.input_state, reply.code)
                with self._state_lock:
                    self._inflight_target = None
                return reply
        except AhkBridgeError as error:
            if error.code in ('ipc','protocol') or error.input_state == 'unknown':
                self.cancel_input()
                if self._cleanup_error:
                    raise AhkBridgeError(f'{error} {self._cleanup_error}', error.input_state, error.code) from error
            raise

    def cancel_input(self) -> None:
        self._cancelled.set()
        with self._state_lock:
            process, self._process = self._process, None
            target, self._inflight_target = self._inflight_target, None
            if target is not None:
                self._cleanup_pending = True
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=.5)
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            if target is not None:
                try:
                    self._reset_target(target)
                finally:
                    with self._state_lock:
                        self._cleanup_pending = False

    def _reset_target(self, target):
        """No key events: clear target-local modifiers after interrupted keyboard work.

        This fresh process uses the same Discord ownership guard and never replays input.
        It has its own deadline because a killed worker cannot run its finally block.
        """
        line = f'1\t1\treset\t{target.window}\t{target.renderer}\t0\t0\t\n'
        try:
            result = subprocess.run(self._command(), input=line, capture_output=True, text=True,
                                    encoding='utf-8', timeout=.5, **hidden_process_options())
            reply = parse_reply(result.stdout, 1)
            if result.returncode or reply.status != 'ok' or reply.code != 'reset':
                raise ValueError('Target keyboard reset was not acknowledged')
            self._cleanup_error = ''
        except (OSError, ValueError, subprocess.TimeoutExpired, AhkBridgeError):
            self._cleanup_error = 'Could not reset Discord keyboard state; restart Discord before resuming.'

    def close(self) -> None:
        # EOF is an explicit shutdown signal; it needs no request lock or queue.
        self.cancel_input()
