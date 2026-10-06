"""Accept uploaded screenshots, read them with OCR, and return cleaned text for the smart paste."""
from email import policy
from email.parser import BytesParser
import os
from pathlib import Path
import tempfile

from .ocr_cleanup import clean_ocr

IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg')
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
INSTALL_COMMAND = 'uv pip install --python .venv -r requirements-ocr.txt'


def uploaded_files(content_type: str, body: bytes) -> list[tuple[str, bytes]]:
    """Return (file name, bytes) for each file in a multipart/form-data body."""
    message = BytesParser(policy=policy.HTTP).parsebytes(
        b'Content-Type: ' + content_type.encode('latin-1') + b'\r\n\r\n' + body)
    if not message.is_multipart():
        return []
    return [(Path(part.get_filename()).name, part.get_payload(decode=True) or b'')
            for part in message.iter_parts() if part.get_filename()]


def _plural(count, word):
    return f'{count} {word}{"" if count == 1 else "s"}'


def read_screenshots(files, reader) -> tuple[str, str]:
    """OCR each image through a temporary file that is deleted afterwards. Return (text, summary)."""
    texts, skipped, failed = [], [], []
    for name, data in files:
        suffix = Path(name).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            skipped.append(name)
            continue
        handle, temporary = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(handle, 'wb') as output:
                output.write(data)
            lines = reader(temporary)
        except (OSError, RuntimeError, ValueError) as error:
            failed.append(f'{name} ({error})')
            continue
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        texts.append(clean_ocr(lines, name))
    summary = 'Read ' + _plural(len(texts), 'screenshot')
    if skipped:
        summary += '; skipped ' + ', '.join(skipped) + ' (not a PNG or JPEG image)'
    if failed:
        summary += '; could not read ' + ', '.join(failed)
    return '\n'.join(texts), summary + '.'
