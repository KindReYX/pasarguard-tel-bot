from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from aiogram.types import BufferedInputFile, Message


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKGROUND_FILENAME = 'back.jpg'
DEFAULT_QR_SIZE_PERCENT = 42


def _background_path() -> Path | None:
    """Return the optional QR delivery background placed beside bot files."""
    candidates = [
        PROJECT_ROOT / BACKGROUND_FILENAME,
        Path.cwd() / BACKGROUND_FILENAME,
    ]
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            pass
    return None


def _make_qr_image(data: Any):
    import qrcode
    from qrcode.exceptions import DataOverflowError

    text = str(data or '').strip()
    if not text:
        raise ValueError('QR data is empty')

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=3,
    )
    qr.add_data(text)
    try:
        qr.make(fit=True)
    except DataOverflowError:
        raise ValueError('QR data is too large')

    wrapped = qr.make_image(fill_color='black', back_color='white')
    return wrapped.get_image().convert('RGB')


def background_available() -> bool:
    return _background_path() is not None


def _normalize_qr_size_percent(value: Any) -> int:
    try:
        percent = int(value)
    except (TypeError, ValueError):
        percent = DEFAULT_QR_SIZE_PERCENT
    return max(1, min(100, percent))


def configured_qr_size_percent() -> int:
    try:
        import db
        return _normalize_qr_size_percent(db.get_setting('qr_size_percent', DEFAULT_QR_SIZE_PERCENT))
    except Exception:
        return DEFAULT_QR_SIZE_PERCENT


def _compose_on_background(qr_image, qr_size_percent: int | None = None):
    """Center QR on back.jpg using a configurable 1..100 percent size.

    The percentage is relative to the background's shorter side. 100 means the
    QR fills that side completely; 1 is the minimum selectable size. If
    back.jpg is absent/corrupt, the legacy standalone QR is returned.
    """
    background_path = _background_path()
    if background_path is None:
        return qr_image

    from PIL import Image, ImageOps

    try:
        with Image.open(background_path) as source:
            background = ImageOps.exif_transpose(source).convert('RGB')
    except Exception:
        # A missing/corrupt/unreadable background must never break delivery.
        return qr_image

    # Keep large source images reasonable for Telegram while preserving ratio.
    max_side = 2560
    if max(background.size) > max_side:
        background.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

    width, height = background.size
    short_side = min(width, height)

    percent = _normalize_qr_size_percent(
        configured_qr_size_percent() if qr_size_percent is None else qr_size_percent
    )
    qr_side = max(1, int(round(short_side * (percent / 100.0))))
    qr_side = min(qr_side, short_side)
    qr_resized = qr_image.resize((qr_side, qr_side), Image.Resampling.LANCZOS)

    left = (width - qr_side) // 2
    top = (height - qr_side) // 2
    background.paste(qr_resized, (left, top))
    return background


def make_qr_file(
    data: Any,
    filename: str = 'qrcode.png',
    qr_size_percent: int | None = None,
) -> BufferedInputFile:
    """Create a Telegram-ready QR image.

    If ``back.jpg`` exists in the project root, the QR is centered on that
    image. Without ``back.jpg`` the legacy standalone/full QR is returned.
    """
    qr_image = _make_qr_image(data)
    image = _compose_on_background(qr_image, qr_size_percent=qr_size_percent)

    buffer = BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    buffer.seek(0)
    return BufferedInputFile(buffer.getvalue(), filename=filename)


async def send_qr_photo(
    message: Message,
    data: Any,
    caption: str | None = None,
    filename: str = 'qrcode.png',
    parse_mode: str | None = None,
    reply_markup: Any = None,
    qr_size_percent: int | None = None,
) -> bool:
    """Send QR image and return True on success."""
    try:
        qr_file = make_qr_file(data, filename=filename, qr_size_percent=qr_size_percent)
        kwargs: dict[str, Any] = {'caption': caption}
        if reply_markup is not None:
            kwargs['reply_markup'] = reply_markup
        if parse_mode:
            kwargs['parse_mode'] = parse_mode
        await message.answer_photo(qr_file, **kwargs)
        return True
    except Exception:
        return False
