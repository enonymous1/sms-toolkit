"""Shared data models for screenshot OCR extraction.

Consolidates the message-dict shape and the ``TextBlock`` geometry helper that
were copy-pasted across ``extract_messages`` v1-v4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def message_direction(sender: str | None, user_name: str) -> str:
    """Return ``'sent'`` when *sender* looks like *user_name*, else ``'received'``.

    This is the exact direction heuristic used by all four original extractors.
    """
    if sender and user_name and sender.lower().find(user_name.lower()) >= 0:
        return "sent"
    return "received"


def make_message(
    sender: str | None,
    phone_number: str | None,
    timestamp: str | None,
    content: list[str],
    user_name: str,
) -> dict[str, Any]:
    """Build the structured message dict every extractor version produced.

    Keys (``sender``, ``phone_number``, ``timestamp``, ``content``,
    ``line_count``, ``media_type``, ``direction``) are kept byte-identical to
    the originals so downstream JSON consumers see no change.
    """
    return {
        "sender": sender,
        "phone_number": phone_number,
        "timestamp": timestamp,
        "content": "\n".join(content) if content else "",
        "line_count": len(content),
        "media_type": "text",
        "direction": message_direction(sender, user_name),
    }


@dataclass
class TextBlock:
    """A run of OCR text with spatial coordinates.

    Superset of the v3 and v4 ``TextBlock`` classes: v3 contributed
    ``is_metadata`` and the ``*_distance_to`` helpers, v4 contributed
    ``top``/``bottom``, ``vertical_gap_to`` and ``is_vertically_close``.
    (v3's ``vertical_distance_to`` and v4's ``vertical_gap_to`` were the same
    computation; both names are kept.)
    """

    text: str
    bbox: tuple[int, int, int, int]
    confidence: float = 0.0
    is_metadata: bool = False

    x1: int = field(init=False)
    y1: int = field(init=False)
    x2: int = field(init=False)
    y2: int = field(init=False)
    center_x: float = field(init=False)
    center_y: float = field(init=False)
    width: int = field(init=False)
    height: int = field(init=False)
    top: int = field(init=False)
    bottom: int = field(init=False)

    def __post_init__(self) -> None:
        self.x1, self.y1, self.x2, self.y2 = self.bbox
        self.center_x = (self.x1 + self.x2) / 2
        self.center_y = (self.y1 + self.y2) / 2
        self.width = self.x2 - self.x1
        self.height = self.y2 - self.y1
        self.top = self.y1
        self.bottom = self.y2

    def vertical_gap_to(self, other: TextBlock) -> float:
        """Vertical gap between this block and *other* (0 when overlapping)."""
        if self.bottom < other.top:
            return other.top - self.bottom
        if other.bottom < self.top:
            return self.top - other.bottom
        return 0

    def vertical_distance_to(self, other: TextBlock) -> float:
        """Alias of :meth:`vertical_gap_to` (the v3 name)."""
        return self.vertical_gap_to(other)

    def horizontal_distance_to(self, other: TextBlock) -> float:
        """Horizontal gap between this block and *other* (0 when overlapping)."""
        if self.x2 < other.x1:
            return other.x1 - self.x2
        if other.x2 < self.x1:
            return self.x1 - other.x2
        return 0

    def is_vertically_close(self, other: TextBlock, threshold: float = 30) -> bool:
        """Whether the vertical gap to *other* is within *threshold* pixels."""
        return self.vertical_gap_to(other) <= threshold

    def __repr__(self) -> str:
        return f"TextBlock({self.text[:30]!r}..., x={self.center_x:.0f}, y={self.center_y:.0f})"
