"""Unified screenshot conversation extractor.

One :class:`ConversationExtractor` replaces the four copy-pasted
``extract_messages`` v1-v4 classes. The ``strategy`` argument selects the
original algorithm:

- ``"v1"`` — block-based parsing with content heuristics + aggressive header
  cleanup, pytesseract OCR with EasyOCR fallback.
- ``"v2"`` — simplified line-accumulator parsing (the default).
- ``"v3"`` — EasyOCR bounding boxes, median-based alignment clustering, HSV
  bubble detection.
- ``"v4"`` — quartile alignment clustering, LAB bubble detection, UI-chrome
  masking, refined metadata patterns.

Message dicts, JSON output keys, and parse behavior are kept identical to the
corresponding original so existing consumers see no change.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sms_toolkit.logging_config import get_logger
from sms_toolkit.ocr import backends, spatial
from sms_toolkit.ocr.detect import (
    detect_metadata_v4,
    extract_participants_v1,
    extract_participants_v2,
    extract_participants_v34,
)
from sms_toolkit.ocr.models import TextBlock
from sms_toolkit.ocr.parse import parse_spatial, parse_v1, parse_v2

logger = get_logger(__name__)


@dataclass(frozen=True)
class _Strategy:
    header_fn: Callable[[str], list[str]]
    parse_fn: Callable[..., list[dict[str, Any]]]
    ocr_mode: str  # text_v1 | text_v2 | blocks_v3 | blocks_v4
    method: str
    include_raw_text: bool
    spatial: bool


STRATEGIES: dict[str, _Strategy] = {
    "v1": _Strategy(
        header_fn=extract_participants_v1,
        parse_fn=parse_v1,
        ocr_mode="text_v1",
        method="OCR (pytesseract)",
        include_raw_text=True,
        spatial=False,
    ),
    "v2": _Strategy(
        header_fn=extract_participants_v2,
        parse_fn=parse_v2,
        ocr_mode="text_v2",
        method="OCR (pytesseract)",
        include_raw_text=False,
        spatial=False,
    ),
    "v3": _Strategy(
        header_fn=extract_participants_v34,
        parse_fn=parse_spatial,
        ocr_mode="blocks_v3",
        method="Spatial Layout Analysis (EasyOCR + OpenCV)",
        include_raw_text=False,
        spatial=True,
    ),
    "v4": _Strategy(
        header_fn=extract_participants_v34,
        parse_fn=lambda text, known_contacts=None, user_name="Justin": parse_spatial(
            text, known_contacts, user_name, detect_metadata_v4
        ),
        ocr_mode="blocks_v4",
        method="Advanced Spatial Layout Analysis (v4)",
        include_raw_text=False,
        spatial=True,
    ),
}


class ConversationExtractor:
    """Extract a structured conversation from a screenshot (strategy-based)."""

    def __init__(
        self,
        image_path: str | Path,
        user_name: str = "Justin",
        *,
        strategy: str = "v2",
        is_group_chat: bool | None = None,
        use_bubble_detection: bool = True,
    ) -> None:
        if strategy not in STRATEGIES:
            raise ValueError(f"Unknown OCR strategy {strategy!r}; choose from {sorted(STRATEGIES)}")
        self.image_path = Path(image_path)
        self.user_name = user_name
        self.strategy_name = strategy
        self._strategy = STRATEGIES[strategy]
        self.is_group_chat = is_group_chat
        self.use_bubble_detection = use_bubble_detection and spatial.cv2_available()
        self.participants: list[str] = []
        self.messages: list[dict[str, Any]] = []
        self.raw_text: str | None = None
        self.text_blocks: list[TextBlock] = []

    # -- OCR ------------------------------------------------------------
    def extract_text_from_image(self) -> str:
        """Extract raw OCR text (text strategies)."""
        mode = self._strategy.ocr_mode
        if mode == "text_v1":
            text = backends.ocr_text_v1(self.image_path)
        elif mode == "text_v2":
            text = backends.ocr_text_v2(self.image_path)
        else:
            raise RuntimeError(f"Strategy {self.strategy_name!r} needs bounding boxes")
        self.raw_text = text
        return text

    def extract_text_with_bboxes(self) -> tuple[str, list[TextBlock]]:
        """Extract OCR text with bounding boxes (spatial strategies)."""
        mode = self._strategy.ocr_mode
        if mode == "blocks_v3":
            text, blocks = backends.ocr_blocks_v3(self.image_path)
        elif mode == "blocks_v4":
            text, blocks = backends.ocr_blocks_v4(self.image_path)
        else:
            raise RuntimeError(f"Strategy {self.strategy_name!r} does not use bounding boxes")
        self.raw_text = text
        self.text_blocks = blocks
        return text, blocks

    # -- parsing ---------------------------------------------------------
    def extract_participants_from_header(self, text: str) -> list[str]:
        """Extract participant names from the first OCR line."""
        names = self._strategy.header_fn(text)
        self.participants = names
        self.is_group_chat = len(names) > 1
        return names

    def _known_contacts(self) -> set[str] | None:
        return set(self.participants) if self.participants else None

    def parse_conversation(self, text: str) -> list[dict[str, Any]]:
        """Parse OCR text into structured message dicts."""
        messages = self._strategy.parse_fn(text, self._known_contacts(), self.user_name)
        self.messages = messages
        return messages

    # -- pipeline --------------------------------------------------------
    def extract(self) -> dict[str, Any]:
        """Run the full extraction pipeline for the configured strategy."""
        print(f"\n{'=' * 60}")
        print(f"Extracting conversation from: {self.image_path.name}")
        if self._strategy.spatial:
            print(f"Using: {self._strategy.method}")
        print(f"{'=' * 60}\n")

        if not self._strategy.spatial:
            return self._extract_text_mode()
        if self.strategy_name == "v3":
            return self._extract_v3()
        return self._extract_v4()

    def _extract_text_mode(self) -> dict[str, Any]:
        text = self.extract_text_from_image()
        print(f"Text extraction complete ({len(text)} characters)\n")

        participants = self.extract_participants_from_header(text)
        chat_type = "Group Chat" if self.is_group_chat else "1-to-1 Conversation"
        print(f"Participants detected: {', '.join(participants)} ({chat_type})\n")

        messages = self.parse_conversation(text)
        print(f"Parsed {len(messages)} messages\n")

        return {
            "source_file": self.image_path.name,
            "extraction_method": self._strategy.method,
            "conversation_type": chat_type,
            "participants": participants,
            "is_group_chat": self.is_group_chat,
            "total_messages": len(messages),
            "messages": messages,
            "raw_ocr_text": text if self._strategy.include_raw_text else "",
        }

    def _extract_v3(self) -> dict[str, Any]:
        text, text_blocks = self.extract_text_with_bboxes()
        print(
            f"Text extraction complete ({len(text)} characters, {len(text_blocks)} text blocks)\n"
        )

        image_array = backends.load_image_array(self.image_path)
        image_width = image_array.shape[1]

        bubbles: list[tuple[int, int, int, int]] = []
        if self.use_bubble_detection:
            bubbles = spatial.segment_bubbles_v3(image_array)
            print(f"Bubble segmentation: {len(bubbles)} bubbles detected\n")

        alignment = spatial.cluster_by_alignment(text_blocks, image_width)
        print(
            f"Alignment clustering: {len(alignment['left'])} left, "
            f"{len(alignment['right'])} right\n"
        )

        participants = self.extract_participants_from_header(text)
        chat_type = "Group Chat" if self.is_group_chat else "1-to-1 Conversation"
        print(f"Participants detected: {', '.join(participants)} ({chat_type})\n")

        messages = self.parse_conversation(text)
        print(f"Parsed {len(messages)} messages\n")

        return {
            "source_file": self.image_path.name,
            "extraction_method": self._strategy.method,
            "conversation_type": chat_type,
            "participants": participants,
            "is_group_chat": self.is_group_chat,
            "total_messages": len(messages),
            "spatial_analysis": {
                "text_blocks": len(text_blocks),
                "left_aligned": len(alignment["left"]),
                "right_aligned": len(alignment["right"]),
                "bubbles_detected": len(bubbles) if self.use_bubble_detection else 0,
            },
            "messages": messages,
            "raw_ocr_text": "",
        }

    def _extract_v4(self) -> dict[str, Any]:
        image_array = backends.load_image_array(self.image_path)
        image_width = image_array.shape[1]
        image_height = image_array.shape[0]
        print(f"Image dimensions: {image_width}x{image_height}")

        text, text_blocks = self.extract_text_with_bboxes()
        print(f"Text extraction: {len(text)} chars, {len(text_blocks)} text blocks\n")

        try:
            bubble_results = spatial.detect_bubbles_v4(image_array)
        except RuntimeError:
            bubble_results = {"bubbles": [], "method": "unavailable", "count": 0}
        print(
            f"Bubble segmentation: {bubble_results['count']} bubbles detected "
            f"(method: {bubble_results['method']})\n"
        )

        alignment = spatial.cluster_by_alignment_v4(text_blocks)
        print("Alignment analysis:")
        print(f"    - Left: {len(alignment['left'])} blocks")
        print(f"    - Center: {len(alignment['center'])} blocks")
        print(f"    - Right: {len(alignment['right'])} blocks\n")

        message_groups = spatial.group_blocks_into_messages(text_blocks)
        print(f"Spatial grouping: {len(message_groups)} message groups\n")

        participants = self.extract_participants_from_header(text)
        is_group = len(participants) > 1
        chat_type = "Group Chat" if is_group else "1-to-1 Conversation"
        print(f"Participants: {', '.join(participants)} ({chat_type})\n")

        messages = self.parse_conversation(text)
        print(f"Parsed {len(messages)} messages\n")

        return {
            "source_file": self.image_path.name,
            "extraction_method": self._strategy.method,
            "conversation_type": chat_type,
            "participants": participants,
            "is_group_chat": is_group,
            "total_messages": len(messages),
            "spatial_analysis": {
                "image_dimensions": [image_width, image_height],
                "text_blocks_total": len(text_blocks),
                "left_aligned": len(alignment["left"]),
                "center_aligned": len(alignment["center"]),
                "right_aligned": len(alignment["right"]),
                "message_groups": len(message_groups),
                "bubbles_detected": bubble_results["count"],
                "alignment_stats": alignment["statistics"],
            },
            "messages": messages,
            "raw_ocr_text": "",
        }

    def to_json(self, output_path: str | Path | None = None) -> str:
        """Export the extracted conversation to JSON.

        Returns the output path when one is given, else the JSON string (the
        v2/v3/v4 convention; v1's original returned the JSON string in both
        cases).
        """
        output = {
            "source_file": self.image_path.name,
            "extraction_method": self._strategy.method,
            "conversation_type": "Group Chat" if self.is_group_chat else "1-to-1 Conversation",
            "participants": self.participants,
            "is_group_chat": self.is_group_chat,
            "total_messages": len(self.messages),
            "messages": self.messages,
            "raw_ocr_text": self.raw_text if self._strategy.include_raw_text else "",
        }

        json_str = json.dumps(output, indent=2, ensure_ascii=False)
        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json_str, encoding="utf-8")
            logger.info("JSON exported to: %s", path)
            return str(path)
        return json_str


def run_ocr_extract(
    image: Path,
    output: Path | None,
    *,
    strategy: str = "v2",
    user_name: str = "Justin",
) -> int:
    """Run ``ocr-extract``; returns the process exit code."""
    extractor = ConversationExtractor(image, user_name=user_name, strategy=strategy)
    try:
        extractor.extract()
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"sms-toolkit: ocr-extract failed: {exc}")
        return 1
    out_path = output or image.with_name(f"{image.stem}_extracted.json")
    extractor.to_json(out_path)
    print(f"Extracted {len(extractor.messages)} messages -> {out_path}")
    return 0


def run_ocr_match(json_path: Path, db_path: Path, output: Path | None) -> int:
    """Run ``ocr-match``; returns the process exit code."""
    from sms_toolkit.ocr.matcher import MessageMatcher

    if not json_path.exists():
        print(f"sms-toolkit: extracted JSON not found: {json_path}")
        return 1
    try:
        matcher = MessageMatcher(str(db_path))
    except Exception as exc:  # noqa: BLE001 - missing fuzzywuzzy etc.
        print(f"sms-toolkit: ocr-match failed: {exc}")
        return 1
    try:
        enriched = matcher.enrich_extracted_messages(str(json_path))
    finally:
        matcher.close()
    out_path = output or json_path.with_name(f"{json_path.stem}_matched.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = enriched.get("match_summary", {})
    print(f"Match rate: {summary.get('match_rate', '?')} -> {out_path}")
    return 0
