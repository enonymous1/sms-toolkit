"""OCR backends for screenshot extraction.

Heavy vision dependencies (Pillow, pytesseract, easyocr) are imported lazily
inside these functions so ``sms_toolkit.ocr`` imports cleanly without them;
callers get a clear install hint instead of an ``ImportError``. The pure-text
parsing logic in :mod:`sms_toolkit.ocr.parse` needs none of these.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sms_toolkit.logging_config import get_logger
from sms_toolkit.ocr.models import TextBlock

logger = get_logger(__name__)

_OCR_EXTRA = "pip install sms-toolkit[ocr]"


def _require_pillow() -> Any:
    try:
        from PIL import Image  # type: ignore[import-not-found]

        return Image
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for OCR image loading ({_OCR_EXTRA})") from exc


def load_image_array(image_path: Path) -> Any:
    """Load an image as a NumPy array (for spatial analysis)."""
    Image = _require_pillow()
    np_module = _require_numpy()
    return np_module.array(Image.open(image_path))


def _require_numpy() -> Any:
    try:
        import numpy as np

        return np
    except ImportError as exc:
        raise RuntimeError(f"NumPy is required for spatial analysis ({_OCR_EXTRA})") from exc


def ocr_text_v1(image_path: Path) -> str:
    """Extract text via pytesseract, falling back to EasyOCR (v1 rules)."""
    Image = _require_pillow()
    image = Image.open(image_path)
    try:
        from pytesseract import image_to_string  # type: ignore[import-not-found]

        text = str(image_to_string(image))
        logger.info("OCR extraction via pytesseract")
        return text
    except Exception as exc:  # noqa: BLE001 - fall back to EasyOCR on any failure
        logger.warning("pytesseract failed (%s); trying EasyOCR", exc)

    try:
        from easyocr import Reader  # type: ignore[import-not-found]

        reader = Reader(["en"])
        result = reader.readtext(str(image_path))
        return str("\n".join(line[1] for line in result))
    except Exception as exc2:  # noqa: BLE001 - report clearly when both fail
        raise RuntimeError(f"Both OCR methods failed for {image_path}: {exc2}") from exc2


def ocr_text_v2(image_path: Path) -> str:
    """Extract text via pytesseract, falling back to EasyOCR (v2 rules).

    Raises ``FileNotFoundError`` for a missing image and ``RuntimeError`` when
    no OCR library is available.
    """
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    try:
        from pytesseract import image_to_string

        Image = _require_pillow()
        image = Image.open(image_path)
        try:
            text = str(image_to_string(image))
        except Exception as exc:  # noqa: BLE001 - fall through to EasyOCR
            logger.warning("pytesseract failed: %s", exc)
        else:
            if text.strip():
                return text
    except ImportError:
        logger.debug("pytesseract not installed; trying EasyOCR")

    try:
        from easyocr import Reader

        reader = Reader(["en"])
        result = reader.readtext(str(image_path))
        return str("\n".join(line[1] for line in result))
    except ImportError as exc:
        raise RuntimeError("No OCR library available (pytesseract or easyocr)") from exc
    except Exception as exc:  # noqa: BLE001 - both engines failed
        raise RuntimeError(f"Both OCR methods failed: {exc}") from exc


def _easyocr_blocks(image_path: Path) -> tuple[str, list[TextBlock]]:
    """Run EasyOCR and return ``(full_text, text_blocks)``."""
    try:
        from easyocr import Reader
    except ImportError as exc:
        raise RuntimeError(f"EasyOCR is required for spatial extraction ({_OCR_EXTRA})") from exc

    reader = Reader(["en"], gpu=False)
    result = reader.readtext(str(image_path))

    text_blocks: list[TextBlock] = []
    full_text_lines: list[str] = []
    for bbox, text, confidence in result:
        # bbox is 4 corners [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]; reduce to a rect.
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        bbox_rect = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
        text_blocks.append(TextBlock(text, bbox_rect, confidence))
        full_text_lines.append(text)
    return "\n".join(full_text_lines), text_blocks


def ocr_blocks_v3(image_path: Path) -> tuple[str, list[TextBlock]]:
    """Extract text with bounding boxes, preferring EasyOCR (v3 rules).

    Falls back to pytesseract with dummy bounding boxes when EasyOCR is
    unavailable, exactly like v3.
    """
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    try:
        return _easyocr_blocks(image_path)
    except RuntimeError:
        # EasyOCR not installed: fall through to the pytesseract fallback below.
        logger.warning("EasyOCR unavailable; falling back to pytesseract")
    except Exception as exc:  # noqa: BLE001 - runtime OCR failure also falls back (v3 behavior)
        logger.warning("EasyOCR failed: %s", exc)

    try:
        from pytesseract import image_to_string
    except ImportError as exc:
        raise RuntimeError("No OCR library available") from exc

    Image = _require_pillow()
    image = Image.open(image_path)
    try:
        text = str(image_to_string(image))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"OCR extraction failed: {exc}") from exc

    # Dummy blocks without real coordinates (v3 behavior).
    text_blocks = [
        TextBlock(line, (0, i * 20, 100, i * 20 + 20), 0.9)
        for i, line in enumerate(text.split("\n"))
        if line.strip()
    ]
    return text, text_blocks


def ocr_blocks_v4(image_path: Path) -> tuple[str, list[TextBlock]]:
    """Extract text with bounding boxes via EasyOCR (v4 rules).

    Unlike v3 there is no pytesseract fallback; blocks are sorted top to
    bottom, left to right.
    """
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    text, text_blocks = _easyocr_blocks(image_path)
    text_blocks.sort(key=lambda b: (b.top, b.x1))
    return text, text_blocks
