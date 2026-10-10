import struct
from pathlib import Path
import tempfile
import unittest
import zlib

from src.ocr_image import contrast_region


class ContrastTests(unittest.TestCase):
    def test_crop_inversion_and_integer_scaling_preserve_pixel_positions(self):
        pixels = bytes([0, 0, 0, 0, 255, 255, 255, 0] * 2)
        header = struct.pack('<IiiHHIIiiII', 40, 2, -2, 1, 32, 0, 16, 0, 0, 0, 0)
        bmp = struct.pack('<2sIHHI', b'BM', 70, 0, 0, 54) + header + pixels
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/'in.bmp', Path(folder)/'out.png'
            source.write_bytes(bmp)
            contrast_region(source, output, (1, 0, 2, 2), scale=2, border=0)
            data = output.read_bytes()
            self.assertEqual(struct.unpack('>II', data[16:24]), (2, 4))
            index, compressed = 8, b''
            while index < len(data):
                length = struct.unpack('>I', data[index:index+4])[0]
                if data[index+4:index+8] == b'IDAT':
                    compressed += data[index+8:index+8+length]
                index += length + 12
            self.assertEqual(zlib.decompress(compressed), b'\x00\x00\x00' * 4)



def write_bmp(path, width, height, pixel):
    """A bottom-up 32-bit BMP whose colour at (x, y) is pixel(x, y) as (r, g, b)."""
    rows = []
    for y in range(height - 1, -1, -1):
        rows.append(b''.join(bytes((b, g, r, 255)) for r, g, b in (pixel(x, y) for x in range(width))))
    data = b''.join(rows)
    header = struct.pack('<2sIHHI', b'BM', 54 + len(data), 0, 0, 54)
    info = struct.pack('<IiiHHIIiiII', 40, width, height, 1, 32, 0, len(data), 0, 0, 0, 0)
    Path(path).write_bytes(header + info + data)


class ButtonRowTests(unittest.TestCase):
    def test_finds_the_arrows_right_of_back_and_which_is_enabled(self):
        from src.ocr_image import button_rows
        grey, background = (60, 62, 68), (43, 45, 49)
        dim, bright, white = (61, 69, 138), (88, 101, 242), (255, 255, 255)

        def pixel(x, y):
            if not 10 <= y < 40:
                return background
            if 10 <= x < 80:
                return grey                       # the Back button
            if 90 <= x < 160:
                return white if 122 <= x < 128 else dim     # disabled ←, with its arrow glyph
            if 170 <= x < 240:
                return white if 202 <= x < 208 else bright  # enabled →
            return background
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'capture.bmp'
            write_bmp(path, 260, 50, pixel)
            rows = button_rows(path, [{'text': 'Back', 'left': 30, 'right': 60, 'top': 18, 'bottom': 32},
                                      {'text': 'Page 1/2', 'left': 10, 'right': 70, 'top': 0, 'bottom': 8}])
        self.assertEqual(len(rows), 1)
        self.assertEqual([(run['left'], run['right'], run['enabled']) for run in rows[0]['runs']],
                         [(90, 159, False), (170, 239, True)])
        self.assertEqual(rows[0]['y'], 25)
