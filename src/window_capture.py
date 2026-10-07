"""Copy the Discord window's pixels on Windows (read-only; nothing is sent to the window)."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import struct
import sys
import zlib


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
                ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD)]


# PrintWindow flag that makes Chromium-based apps such as Discord render their real content.
PW_RENDERFULLCONTENT = 2
_APIS = None


def _apis():
    """user32 and gdi32 with argument and result types declared. Without them ctypes passes and returns plain
    C ints, which truncates 64-bit window, device-context, and bitmap handles."""
    global _APIS
    if _APIS is None:
        user32, gdi32 = ctypes.WinDLL('user32', use_last_error=True), ctypes.WinDLL('gdi32', use_last_error=True)
        signatures = (
            (user32.IsIconic, [wintypes.HWND], wintypes.BOOL),
            (user32.GetWindowRect, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            (user32.GetWindowDC, [wintypes.HWND], wintypes.HDC),
            (user32.ReleaseDC, [wintypes.HWND, wintypes.HDC], ctypes.c_int),
            (user32.PrintWindow, [wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL),
            (user32.GetDpiForWindow, [wintypes.HWND], wintypes.UINT),
            (user32.SetProcessDPIAware, [], wintypes.BOOL),
            (gdi32.CreateCompatibleDC, [wintypes.HDC], wintypes.HDC),
            (gdi32.CreateCompatibleBitmap, [wintypes.HDC, ctypes.c_int, ctypes.c_int], wintypes.HBITMAP),
            (gdi32.SelectObject, [wintypes.HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ),
            (gdi32.DeleteObject, [wintypes.HGDIOBJ], wintypes.BOOL),
            (gdi32.DeleteDC, [wintypes.HDC], wintypes.BOOL),
            (gdi32.GetDIBits, [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                               ctypes.c_void_p, wintypes.UINT], ctypes.c_int),
        )
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result
        _APIS = user32, gdi32
    return _APIS


def _check(value, what):
    if not value:
        raise OSError(f'{what} failed (Windows error {ctypes.get_last_error()})')
    return value


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
    """Write the window as a BMP file. Returns {'width', 'height', 'scale', 'minimized', 'blank'}.

    Raises OSError when a Windows call fails (for example, the window closed); every handle acquired so far is
    released either way, with the memory DC's original bitmap selected back before anything is deleted.
    """
    user32, gdi32 = _apis()
    user32.SetProcessDPIAware()
    if user32.IsIconic(hwnd):
        return {'minimized': True, 'blank': True, 'width': 0, 'height': 0, 'scale': 1.0}
    rect = wintypes.RECT()
    _check(user32.GetWindowRect(hwnd, ctypes.byref(rect)), 'GetWindowRect')
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise OSError(f'The window has no area ({width}x{height}).')
    window_dc = memory_dc = bitmap = previous = None
    try:
        window_dc = _check(user32.GetWindowDC(hwnd), 'GetWindowDC')
        memory_dc = _check(gdi32.CreateCompatibleDC(window_dc), 'CreateCompatibleDC')
        bitmap = _check(gdi32.CreateCompatibleBitmap(window_dc, width, height), 'CreateCompatibleBitmap')
        previous = _check(gdi32.SelectObject(memory_dc, bitmap), 'SelectObject')
        _check(user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT), 'PrintWindow')
        header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        _check(gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(header), 0), 'GetDIBits')
    finally:
        if previous:
            gdi32.SelectObject(memory_dc, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if window_dc:
            user32.ReleaseDC(hwnd, window_dc)
    raw = pixels.raw
    Path(path).write_bytes(struct.pack('<2sIHHI', b'BM', 54 + len(raw), 0, 0, 54) + bytes(header) + raw)
    dpi = user32.GetDpiForWindow(hwnd) or 96
    return {'width': width, 'height': height, 'scale': dpi / 96, 'minimized': False,
            'blank': len(set(raw[::4096])) <= 2}


def bmp_to_png(bmp_path: Path, png_path: Path) -> None:
    """Convert a 32-bit top-down BMP written by capture_window to PNG (standard library only)."""
    data = Path(bmp_path).read_bytes()
    width, height = struct.unpack('<ii', data[18:26])
    height = abs(height)
    raw = data[54:]
    rgba = bytearray(raw)
    rgba[0::4], rgba[2::4] = raw[2::4], raw[0::4]
    rgba[3::4] = b'\xff' * (width * height)
    stride = width * 4
    rows = b''.join(b'\x00' + bytes(rgba[y * stride:(y + 1) * stride]) for y in range(height))

    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))

    Path(png_path).write_bytes(b'\x89PNG\r\n\x1a\n'
                               + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
                               + chunk(b'IDAT', zlib.compress(rows, 6)) + chunk(b'IEND', b''))
