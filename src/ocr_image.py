"""Deterministic pixel preprocessing of native captures; never changes recognized text."""
from math import floor, ceil
from pathlib import Path
import struct
import zlib


def contrast_region(source, output, region, scale=3, threshold=None, border=12):
    """Invert a native 32-bit BMP crop to dark text on white, then enlarge by integer replication."""
    data = Path(source).read_bytes()
    if data[:2] != b'BM' or len(data) < 54:
        raise ValueError('Expected a native BMP capture.')
    offset = struct.unpack_from('<I', data, 10)[0]
    width, signed_height = struct.unpack_from('<ii', data, 18)
    bits, compression = struct.unpack_from('<HI', data, 28)
    if bits != 32 or compression != 0 or width <= 0 or signed_height == 0:
        raise ValueError('Unsupported BMP layout.')
    height = abs(signed_height)
    if len(data) < offset + width * height * 4:
        raise ValueError('Truncated BMP pixels.')
    left, top, right, bottom = region
    left, top = max(0, floor(left)), max(0, floor(top))
    right, bottom = min(width, ceil(right)), min(height, ceil(bottom))
    if right <= left or bottom <= top or scale not in (2, 3, 4) or border < 0:
        raise ValueError('Invalid OCR crop or scale.')
    out_width = (right-left)*scale + 2*border
    out_height = (bottom-top)*scale + 2*border
    white_row = b'\x00' + b'\xff' * out_width
    rows = [white_row] * border
    for y in range(top, bottom):
        source_y = y if signed_height < 0 else height-1-y
        start = offset + (source_y * width + left) * 4
        row = bytearray(b'\x00' + b'\xff' * border)
        for x in range(right-left):
            pixel = start + x*4
            brightness = max(data[pixel:pixel+3])
            value = 255-brightness if threshold is None else (0 if brightness >= threshold else 255)
            row.extend(bytes([value]) * scale)
        row.extend(b'\xff' * border)
        rows.extend([bytes(row)] * scale)
    rows.extend([white_row] * border)

    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))
    Path(output).write_bytes(b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', out_width, out_height, 8, 0, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(b''.join(rows), 6)) + chunk(b'IEND', b''))
