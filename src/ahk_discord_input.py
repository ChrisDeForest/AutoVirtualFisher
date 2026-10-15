"""AHK primitives behind the existing Python command workflow and OCR guards."""
import ctypes
import time
from ctypes import wintypes
from .ahk_bridge import AhkBridge, AhkBridgeError, AhkTarget
from .ahk_runtime import AhkRuntime
from .ocr_journal import ocr_scope
from .automation_models import InputUnavailable, TransportError
from .discord_input import WindowsDiscordDriver
from .automation_discord import composer_click

MODIFIER_KEYS = (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C)
MODIFIER_POLL = .05


class AhkDiscordDriver(WindowsDiscordDriver):
    requires_foreground = False

    def __init__(self, window: int, channel: str, runtime: AhkRuntime, *, bridge: AhkBridge | None = None):
        super().__init__(window, channel)
        self.bridge = bridge if bridge is not None else AhkBridge(runtime)
        self._frame_geometry = None
        self._composer_anchor = None
        self.ocr_journal = None
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self._callback_type = callback_type
        for name, args, result in (
            ('GetAsyncKeyState',[ctypes.c_int],ctypes.c_short),
            ('IsIconic',[wintypes.HWND],wintypes.BOOL),
            ('IsWindowVisible',[wintypes.HWND],wintypes.BOOL),
            ('GetClientRect',[wintypes.HWND,ctypes.POINTER(wintypes.RECT)],wintypes.BOOL),
            ('ScreenToClient',[wintypes.HWND,ctypes.POINTER(wintypes.POINT)],wintypes.BOOL),
            ('GetClassNameW',[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int],ctypes.c_int),
            ('GetWindowThreadProcessId',[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)],wintypes.DWORD),
            ('EnumChildWindows',[wintypes.HWND,callback_type,wintypes.LPARAM],wintypes.BOOL),
        ):
            function = getattr(self.api,name)
            function.argtypes, function.restype = args, result
        capture = self.capture
        def checked_capture(**kwargs):
            before = self._geometry()
            with ocr_scope(self.ocr_journal, window=self.window, **kwargs):
                frame = capture(**kwargs)
            after = self._geometry()
            if before != after:
                return {**frame, 'status':'target_changed'}
            return {**frame, '_ahk_geometry':after}
        self.capture = checked_capture

    def _window_rect(self):
        rect = wintypes.RECT()
        if not self.api.GetWindowRect(self.window, ctypes.byref(rect)):
            raise TransportError('Discord window geometry unavailable.')
        return rect.left, rect.top, rect.right, rect.bottom

    def _client_bounds(self):
        rect = wintypes.RECT()
        if not self.api.GetClientRect(self.window, ctypes.byref(rect)):
            raise TransportError('Discord client geometry unavailable.')
        return rect.right, rect.bottom

    def _screen_to_client(self,x,y):
        point = wintypes.POINT(round(x),round(y))
        if not self.api.ScreenToClient(self.window,ctypes.byref(point)):
            raise TransportError('Discord coordinate conversion failed.')
        return point.x, point.y

    def _pid(self,hwnd):
        pid = wintypes.DWORD()
        self.api.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        return pid.value

    def _renderers(self):
        result = []
        pid = self._pid(self.window)
        def visit(hwnd,unused):
            name = ctypes.create_unicode_buffer(256)
            self.api.GetClassNameW(hwnd,name,len(name))
            if name.value == 'Chrome_RenderWidgetHostHWND' and self.api.IsWindowVisible(hwnd) and self._pid(hwnd) == pid:
                result.append(int(hwnd))
            return True
        self.api.EnumChildWindows(self.window,self._callback_type(visit),0)
        return result

    def _resolve_target(self) -> AhkTarget:
        renderers = self._renderers()
        if len(renderers) != 1:
            raise TransportError('Discord renderer missing or ambiguous; keep Discord open and not minimized.')
        return AhkTarget(self.window,renderers[0])

    def _geometry(self):
        return self._window_rect(), self._client_bounds(), tuple(self._renderers())

    def _guard(self,cancelled):
        if cancelled():
            raise TransportError('Stopped.')
        current = self._target_window()
        if current is None or self.api.IsIconic(self.window):
            raise TransportError('Discord target changed or is minimized.')
        if self.title is not None and current[1] != self.title:
            raise TransportError('Discord title changed during input.')
        self._resolve_target()

    def _frame(self,cancelled,frame=None,deep=False):
        result = super()._frame(cancelled,frame,deep)
        geometry = result.get('_ahk_geometry')
        if geometry is None or geometry != self._geometry():
            raise TransportError('Discord geometry changed; obtain a fresh capture before input.')
        self._frame_geometry = geometry
        point = composer_click(result.get('original_boxes', result.get('boxes', [])), self.channel)
        if point is not None:
            self._composer_anchor = (geometry, point)
        return result

    def _recover_composer(self, frame, cancelled):
        # The user authorizes clearing leftovers, including drafts no longer tracked as ours.
        # Use only a location previously verified in this same channel/window/renderer geometry.
        anchor = getattr(self, '_composer_anchor', None)
        if anchor is not None and anchor[0] == self._geometry():
            self.draft_composer = anchor[1]
            return self._clear_draft(cancelled)
        if getattr(self, 'draft_composer', None) is not None:
            self.draft_composer = None
        return super()._recover_composer(frame, cancelled)

    def _client_point(self,point: tuple[float,float]) -> tuple[int,int]:
        current = self._geometry()
        if self._frame_geometry is not None and self._frame_geometry != current:
            raise TransportError('Discord moved, resized or changed renderer after capture.')
        left,top,_,_ = current[0]
        x,y = self._screen_to_client(left+point[0],top+point[1])
        width,height = current[1]
        if not (0 <= x < width and 0 <= y < height):
            raise TransportError('Input point outside Discord client area.')
        return x,y

    def _wait_modifiers(self, cancelled):
        waited = False
        while True:
            self._guard(cancelled)
            if not any(self.api.GetAsyncKeyState(vk) & 0x8000 for vk in MODIFIER_KEYS):
                return waited
            waited = True
            time.sleep(MODIFIER_POLL)

    def _primitive(self,operation,cancelled,point=None,payload=''):
        refresh = False
        geometry = self._geometry()
        while True:
            waited = self._wait_modifiers(cancelled)
            if waited or refresh:
                if geometry != self._geometry():
                    raise TransportError('Discord geometry changed while waiting for held keys.')
                # A held key can outlast the OCR frame: recheck channel and verification prompts.
                self._frame(cancelled)
                if geometry != self._geometry():
                    raise TransportError('Discord geometry changed while refreshing after held keys.')
                refresh = False
            self._guard(cancelled)
            target = self._resolve_target()
            if self._frame_geometry is not None and self._frame_geometry != self._geometry():
                raise TransportError('Discord geometry or renderer changed during input.')
            try:
                return self.bridge.request(operation,target,point,payload,cancelled)
            except AhkBridgeError as error:
                if error.code == 'modifiers' and error.input_state == 'not_started':
                    # Worker caught a modifier pressed after Python checked. No event was posted.
                    refresh = True
                    time.sleep(MODIFIER_POLL)
                    continue
                if error.code in ('ipc', 'protocol') or error.input_state == 'unknown':
                    raise InputUnavailable(f'AHK worker unavailable: {error}. Stop and restart the run.') from error
                raise TransportError(f'AHK input unavailable: {error}') from error
            except ValueError as error:
                raise TransportError(f'AHK input unavailable: {error}') from error

    def _click(self,point,cancelled):
        self._primitive('target',cancelled,point=self._client_point(point))

    def click(self,point,cancelled):
        self._guard(cancelled)
        # Button coordinates come from the transport capture, not a cached composer.
        self._primitive('click',cancelled,point=self._client_point(point))

    def _key(self,vk,cancelled):
        try:
            key = {13:'Enter',9:'Tab',27:'Escape',8:'Backspace'}[vk]
        except KeyError as error:
            raise TransportError('Unsupported targeted input key.') from error
        self._primitive('key',cancelled,payload=key)

    def _text(self,text,cancelled):
        self._primitive('text',cancelled,payload=text)

    def _clear(self,cancelled):
        self._key(27,cancelled)
        self._primitive('select_all',cancelled)
        self._key(8,cancelled)

    def prepare_input(self, frame, cancelled) -> None:
        self.challenges = None
        self._frame(cancelled, frame)

    def enable_input(self) -> None:
        if getattr(self, 'ocr_journal', None) is not None:
            self.ocr_journal.resume()
        self.bridge.start()

    def cancel_input(self) -> None:
        if getattr(self, 'ocr_journal', None) is not None:
            self.ocr_journal.close()
        self.bridge.cancel_input()
        self._frame_geometry = None

    def close(self) -> None:
        if getattr(self, 'ocr_journal', None) is not None:
            self.ocr_journal.close()
        self.bridge.close()
        self._frame_geometry = None
