"""Spatial layout analysis for screenshot OCR (v3/v4 ports).

Groups OCR text blocks by horizontal alignment and vertical proximity, and
detects chat bubbles via color thresholding. NumPy/OpenCV are imported lazily
so the rest of :mod:`sms_toolkit.ocr` works without them.
"""

from __future__ import annotations

from typing import Any

from sms_toolkit.ocr.models import TextBlock

_OCR_EXTRA = "pip install sms-toolkit[ocr]"


def _require_numpy() -> Any:
    try:
        import numpy as np

        return np
    except ImportError as exc:
        raise RuntimeError(f"NumPy is required for spatial analysis ({_OCR_EXTRA})") from exc


def _require_cv2() -> Any:
    try:
        import cv2  # type: ignore[import-not-found]

        return cv2
    except ImportError as exc:
        raise RuntimeError(f"OpenCV is required for bubble detection ({_OCR_EXTRA})") from exc


def cv2_available() -> bool:
    try:
        import cv2  # noqa: F401

        return True
    except ImportError:
        return False


def cluster_by_alignment(
    text_blocks: list[TextBlock], image_width: int
) -> dict[str, list[TextBlock]]:
    """Group text blocks by horizontal alignment (v3 rules).

    Blocks clearly left/right of the median center go left/right; ambiguous
    blocks default left.
    """
    np = _require_numpy()
    left_blocks: list[TextBlock] = []
    right_blocks: list[TextBlock] = []

    centers = [block.center_x for block in text_blocks]
    median_center = float(np.median(centers)) if centers else image_width / 2

    for block in text_blocks:
        if block.center_x < median_center - 50:  # Clearly left
            left_blocks.append(block)
        elif block.center_x > median_center + 50:  # Clearly right
            right_blocks.append(block)
        else:
            # Ambiguous - default left
            left_blocks.append(block)

    return {"left": left_blocks, "right": right_blocks}


def cluster_by_alignment_v4(
    text_blocks: list[TextBlock],
) -> dict[str, Any]:
    """Group text blocks by quartile-based alignment (v4 rules)."""
    np = _require_numpy()
    if not text_blocks:
        return {"left": [], "right": [], "center": []}

    centers_x = [block.center_x for block in text_blocks]
    q1, median, q3 = np.percentile(centers_x, [25, 50, 75])

    left_blocks: list[TextBlock] = []
    center_blocks: list[TextBlock] = []
    right_blocks: list[TextBlock] = []

    for block in text_blocks:
        if block.center_x < q1:
            left_blocks.append(block)
        elif block.center_x > q3:
            right_blocks.append(block)
        else:
            center_blocks.append(block)

    return {
        "left": left_blocks,
        "center": center_blocks,
        "right": right_blocks,
        "statistics": {
            "q1": float(q1),
            "median": float(median),
            "q3": float(q3),
            "all_centers": [float(x) for x in centers_x],
        },
    }


def group_blocks_into_messages(
    text_blocks: list[TextBlock], vertical_gap_threshold: float = 40
) -> list[list[TextBlock]]:
    """Group vertically-close text blocks into messages (v4 rules)."""
    if not text_blocks:
        return []

    sorted_blocks = sorted(text_blocks, key=lambda b: (b.top, b.x1))

    message_groups: list[list[TextBlock]] = []
    current_group = [sorted_blocks[0]]

    for i in range(1, len(sorted_blocks)):
        block = sorted_blocks[i]
        prev_block = sorted_blocks[i - 1]
        if block.vertical_gap_to(prev_block) <= vertical_gap_threshold:
            current_group.append(block)
        else:
            message_groups.append(current_group)
            current_group = [block]

    message_groups.append(current_group)
    return message_groups


def mask_ui_chrome(image_array: Any) -> Any:
    """Mask out static UI elements (header/footer) in white (v4 rules)."""
    height = image_array.shape[0]

    masked = image_array.copy()

    # Mask header (top 5% or top ~100 pixels)
    header_height = max(100, int(height * 0.05))
    masked[:header_height] = [255, 255, 255]

    # Mask footer (bottom 5% or bottom ~80 pixels)
    footer_height = max(80, int(height * 0.05))
    masked[-footer_height:] = [255, 255, 255]

    return masked


def segment_bubbles_v3(image_array: Any) -> list[tuple[int, int, int, int]]:
    """Detect chat bubbles via HSV color thresholding (v3 rules).

    Returns ``(x, y, w, h)`` rects for contours larger than the noise floor.
    """
    np = _require_numpy()
    cv2 = _require_cv2()

    hsv = cv2.cvtColor(image_array, cv2.COLOR_RGB2HSV)

    # Light backgrounds (sent messages) - typically blue/light colors
    lower_light = np.array([0, 0, 200])
    upper_light = np.array([180, 50, 255])
    mask_light = cv2.inRange(hsv, lower_light, upper_light)

    # Dark backgrounds (received messages) - typically dark/grey
    lower_dark = np.array([0, 0, 0])
    upper_dark = np.array([180, 180, 100])
    mask_dark = cv2.inRange(hsv, lower_dark, upper_dark)

    mask = cv2.bitwise_or(mask_light, mask_dark)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_area = 500
    bubble_rects: list[tuple[int, int, int, int]] = []
    for contour in contours:
        if cv2.contourArea(contour) > min_area:
            x, y, w, h = cv2.boundingRect(contour)
            bubble_rects.append((x, y, w, h))
    return bubble_rects


def detect_bubbles_v4(image_array: Any) -> dict[str, Any]:
    """Detect chat bubbles via LAB color-space thresholding (v4 rules)."""
    np = _require_numpy()
    cv2 = _require_cv2()

    image_masked = mask_ui_chrome(image_array)

    lab = cv2.cvtColor(image_masked, cv2.COLOR_RGB2LAB)

    # Light regions (sent messages) - high brightness
    lower_light = np.array([200, 0, 0])
    upper_light = np.array([255, 128, 128])
    mask_light = cv2.inRange(lab, lower_light, upper_light)

    # Dark regions (received messages) - low brightness
    lower_dark = np.array([0, 0, 0])
    upper_dark = np.array([150, 180, 180])
    mask_dark = cv2.inRange(lab, lower_dark, upper_dark)

    mask_combined = cv2.bitwise_or(mask_light, mask_dark)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (10, 5))
    mask_cleaned = cv2.morphologyEx(mask_combined, cv2.MORPH_CLOSE, kernel)
    mask_cleaned = cv2.morphologyEx(mask_cleaned, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(mask_cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_area = 300
    min_height = 20
    bubbles: list[dict[str, Any]] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        x, y, w, h = cv2.boundingRect(contour)

        if area > min_area and h > min_height:
            bubbles.append(
                {
                    "x": x,
                    "y": y,
                    "width": w,
                    "height": h,
                    "area": area,
                    "center_x": x + w // 2,
                    "is_light": float(np.mean(mask_light[y : y + h, x : x + w])) > 100,
                    "is_dark": float(np.mean(mask_dark[y : y + h, x : x + w])) > 100,
                }
            )

    return {"bubbles": bubbles, "method": "lab_color_space", "count": len(bubbles)}
