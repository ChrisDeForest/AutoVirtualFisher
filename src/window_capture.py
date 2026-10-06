"""Copy the Discord window's pixels on Windows (read-only; nothing is sent to the window)."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import struct
import sys


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
                ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD)]


# PrintWindow flag that makes Chromium-based apps such as Discord render their real content.
PW_RENDERFULLCONTENT = 2


def capture_supported() -> bool:
    return sys.platform == 'win32'


def find_discord_window() -> tuple[int, str] | None:
    """Return (window handle, title) of the first visible window whose title ends with 'Discord'."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length and user32.IsWindowVisible(hwnd):
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value.endswith('Discord'):
                found.append((hwnd, buffer.value))
                return False
        return True

    user32.EnumWindows(visit, 0)
    return found[0] if found else None


def capture_window(hwnd: int, path: Path) -> dict:
    """Write the window as a BMP file. Returns {'width', 'height', 'scale', 'minimized', 'blank'}."""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.SetProcessDPIAware()
    if user32.IsIconic(hwnd):
        return {'minimized': True, 'blank': True, 'width': 0, 'height': 0, 'scale': 1.0}
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    try:
        gdi32.SelectObject(memory_dc, bitmap)
        user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT)
        header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(header), 0)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)
    raw = pixels.raw
    Path(path).write_bytes(struct.pack('<2sIHHI', b'BM', 54 + len(raw), 0, 0, 54) + bytes(header) + raw)
    dpi = user32.GetDpiForWindow(hwnd) or 96
    return {'width': width, 'height': height, 'scale': dpi / 96, 'minimized': False,
            'blank': len(set(raw[::4096])) <= 2}
