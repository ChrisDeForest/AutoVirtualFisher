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

