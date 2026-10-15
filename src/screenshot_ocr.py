"""Read Discord screenshots with Windows' built-in OCR (optional: pip install -r requirements-ocr.txt)."""
import asyncio
from pathlib import Path
from .ocr_journal import trace_windows_read

# At 1x, Discord profiles and fishing results read cleanly; enlarging reordered and garbled some lines.
SCALE = 1


def ocr_available() -> bool:
    try:
        from winrt.windows.media.ocr import OcrEngine  # noqa: F401
    except ImportError:
        return False
    return True


@trace_windows_read
async def _read(path: Path, scale: float, region: tuple | None = None) -> list[dict]:
    from winrt.windows.graphics.imaging import (BitmapAlphaMode, BitmapBounds, BitmapDecoder,
                                                BitmapInterpolationMode, BitmapPixelFormat, BitmapTransform,
                                                ColorManagementMode, ExifOrientationMode)
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import FileAccessMode, StorageFile

    file = await StorageFile.get_file_from_path_async(str(Path(path).resolve()))
    stream = await file.open_async(FileAccessMode.READ)
    bitmap = None
    try:
        decoder = await BitmapDecoder.create_async(stream)
        transform = BitmapTransform()
        transform.scaled_width = int(decoder.pixel_width * scale)
        transform.scaled_height = int(decoder.pixel_height * scale)
        transform.interpolation_mode = BitmapInterpolationMode.FANT
        offset_x = offset_y = 0
        if region:
            left, top, right, bottom = (max(0, value) for value in region)
            right, bottom = min(right, decoder.pixel_width), min(bottom, decoder.pixel_height)
            if right <= left or bottom <= top:
                return []
            offset_x, offset_y = left, top
            transform.bounds = BitmapBounds(int(left * scale), int(top * scale), int((right - left) * scale),
                                            int((bottom - top) * scale))
        bitmap = await decoder.get_software_bitmap_transformed_async(
            BitmapPixelFormat.BGRA8, BitmapAlphaMode.PREMULTIPLIED, transform,
            ExifOrientationMode.IGNORE_EXIF_ORIENTATION, ColorManagementMode.DO_NOT_COLOR_MANAGE)
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError('Windows has no OCR language installed for your profile languages.')
        result = await engine.recognize_async(bitmap)
        lines = []
        for line in result.lines:
            rects = [word.bounding_rect for word in line.words]
            lines.append({'text': line.text,
                          'left': min(rect.x for rect in rects) / scale + offset_x,
                          'top': min(rect.y for rect in rects) / scale + offset_y,
                          'right': max(rect.x + rect.width for rect in rects) / scale + offset_x,
                          'bottom': max(rect.y + rect.height for rect in rects) / scale + offset_y})
        return lines
    finally:
        # Closed at once, not left to garbage collection: a x4 read of a 1294x1399 window is about 115 MB of
        # pixels, and the open stream holds the screenshot file that is deleted right after.
        if bitmap is not None:
            bitmap.close()
        stream.close()


def read_image_boxes(path: Path) -> list[dict]:
    """Return each recognized line as {'text', 'left', 'top', 'right', 'bottom'} in image pixels."""
    return asyncio.run(_read(path, SCALE))


def read_region_boxes(path: Path, region: tuple, scale: float = 2) -> list[dict]:
    return asyncio.run(_read(path, scale, region))


async def _save_region(path, output, region, scale, nearest=False):
    from winrt.windows.graphics.imaging import (BitmapAlphaMode, BitmapBounds, BitmapDecoder,
        BitmapEncoder, BitmapInterpolationMode, BitmapPixelFormat, BitmapTransform,
        ColorManagementMode, ExifOrientationMode)
    from winrt.windows.storage import FileAccessMode, StorageFile
    source = await StorageFile.get_file_from_path_async(str(Path(path).resolve()))
    source_stream = await source.open_async(FileAccessMode.READ)
    destination_stream = None
    try:
        decoder = await BitmapDecoder.create_async(source_stream)
        left, top, right, bottom = region
        left, top = max(0, int(left)), max(0, int(top))
        right, bottom = min(decoder.pixel_width, int(right)), min(decoder.pixel_height, int(bottom))
        if right <= left or bottom <= top:
            raise ValueError('Empty OCR region.')
        transform = BitmapTransform()
        transform.scaled_width, transform.scaled_height = int(decoder.pixel_width * scale), int(decoder.pixel_height * scale)
        transform.interpolation_mode = BitmapInterpolationMode.NEAREST_NEIGHBOR if nearest else BitmapInterpolationMode.FANT
        transform.bounds = BitmapBounds(int(left * scale), int(top * scale),
                                        int((right-left) * scale), int((bottom-top) * scale))
        bitmap = await decoder.get_software_bitmap_transformed_async(
            BitmapPixelFormat.BGRA8, BitmapAlphaMode.PREMULTIPLIED, transform,
            ExifOrientationMode.IGNORE_EXIF_ORIENTATION, ColorManagementMode.DO_NOT_COLOR_MANAGE)
        destination = await StorageFile.get_file_from_path_async(str(Path(output).resolve()))
        destination_stream = await destination.open_async(FileAccessMode.READ_WRITE)
        encoder = await BitmapEncoder.create_async(BitmapEncoder.png_encoder_id, destination_stream)
        encoder.set_software_bitmap(bitmap)
        await encoder.flush_async()
        bitmap.close()
    finally:
        if destination_stream is not None:
            destination_stream.close()
        source_stream.close()


def save_region(path, output, region, scale=3, nearest=False):
    """Write a cropped, enlarged image for an independent local OCR engine."""
    asyncio.run(_save_region(path, output, region, scale, nearest))


def read_region_lines(path: Path, left: float, top: float, right: float, bottom: float,
                      scale: float = 2) -> list[str]:
    """OCR one rectangle of an image, enlarged by `scale`, and return its text lines in reading order."""
    boxes = sorted(asyncio.run(_read(path, scale, (left, top, right, bottom))), key=lambda box: box['top'])
    rows = []
    for box in boxes:
        # Pieces of one visual row ("5" and "Salmon") overlap vertically; keep them left to right.
        if rows and box['top'] < rows[-1][0]['bottom'] - (rows[-1][0]['bottom'] - rows[-1][0]['top']) / 2:
            rows[-1].append(box)
        else:
            rows.append([box])
    return [box['text'] for row in rows for box in sorted(row, key=lambda box: box['left'])]


def read_image(path: Path) -> list[str]:
    """Return the recognized text lines of one image file."""
    return [line['text'] for line in read_image_boxes(path)]
