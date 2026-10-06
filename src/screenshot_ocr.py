"""Read Discord screenshots with Windows' built-in OCR (optional: pip install -r requirements-ocr.txt)."""
import asyncio
from pathlib import Path

# At 1x, Discord profiles and fishing results read cleanly; enlarging reordered and garbled some lines.
SCALE = 1


def ocr_available() -> bool:
    try:
        from winrt.windows.media.ocr import OcrEngine  # noqa: F401
    except ImportError:
        return False
    return True


async def _read(path: Path) -> list[str]:
    from winrt.windows.graphics.imaging import (BitmapAlphaMode, BitmapDecoder, BitmapInterpolationMode,
                                                BitmapPixelFormat, BitmapTransform, ColorManagementMode,
                                                ExifOrientationMode)
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import FileAccessMode, StorageFile

    file = await StorageFile.get_file_from_path_async(str(Path(path).resolve()))
    stream = await file.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    transform = BitmapTransform()
    transform.scaled_width = decoder.pixel_width * SCALE
    transform.scaled_height = decoder.pixel_height * SCALE
    transform.interpolation_mode = BitmapInterpolationMode.FANT
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
        left = min(rect.x for rect in rects) / SCALE
        top = min(rect.y for rect in rects) / SCALE
        right = max(rect.x + rect.width for rect in rects) / SCALE
        bottom = max(rect.y + rect.height for rect in rects) / SCALE
        lines.append({'text': line.text, 'left': left, 'top': top, 'right': right, 'bottom': bottom})
    return lines


def read_image_boxes(path: Path) -> list[dict]:
    """Return each recognized line as {'text', 'left', 'top', 'right', 'bottom'} in image pixels."""
    return asyncio.run(_read(path))


def read_image(path: Path) -> list[str]:
    """Return the recognized text lines of one image file."""
    return [line['text'] for line in read_image_boxes(path)]
