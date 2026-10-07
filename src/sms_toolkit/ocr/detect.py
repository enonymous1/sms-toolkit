"""Metadata and participant-header detection for screenshot OCR text.

Consolidates the regex detectors that were copy-pasted across
``extract_messages`` v1-v4 (with per-version notes where they drifted).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass

#: Fallback contact list used when no participants were extracted from the
#: header (identical in v1/v2/v3; v4's parser adds ``'Crystal'``).
DEFAULT_KNOWN_CONTACTS = frozenset(
    {"Carrie Lyons", "Frances Lyon", "Crystal Lyon", "Lyndsay", "Justin"}
)

#: v4's parser default (adds the single-name ``'Crystal'``).
V4_KNOWN_CONTACTS = DEFAULT_KNOWN_CONTACTS | {"Crystal"}

#: v4's detector default (adds single first names).
V4_DETECTOR_CONTACTS = V4_KNOWN_CONTACTS | {"Carrie", "Frances"}

_NAME = r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)"
_PHONE = r"(\+?[\d\s\-\(\)]{10,}|[\d\-]{10,})"


@dataclass(frozen=True)
class Metadata:
    """One detected metadata line: sender/phone/timestamp (any may be None)."""

    kind: str  # name_phone_time | name_phone | name_time | name_only | timestamp_only | phone_only
    sender: str | None
    phone: str | None
    timestamp: str | None


def detect_metadata(
    line: str,
    known_contacts: Collection[str] | None = None,
    *,
    day_timestamp: bool = False,
) -> Metadata | None:
    """Detect sender/phone/timestamp metadata in an OCR line (v1/v2/v3 rules).

    Returns ``None`` when the line looks like message content. ``known_contacts``
    defaults to the hardcoded v3/v4 set; v1/v2 callers pass the extracted
    participants (or their own default). ``day_timestamp`` enables v3's extra
    ``"Mon, 4:12 PM"``-style pattern.

    Note: captured groups are stripped, matching v3 (v1/v2 left them raw, which
    is a no-op for these patterns since trailing whitespace is consumed by the
    separators that follow).
    """
    line = line.strip()
    if not line:
        return None
    contacts = known_contacts if known_contacts is not None else DEFAULT_KNOWN_CONTACTS

    match = re.match(rf"^{_NAME}\s+{_PHONE}\s+[-:+]\s*([\d:]+\s*(?:AM|PM|am|pm)?.*?)$", line)
    if match:
        return Metadata(
            "name_phone_time",
            match.group(1).strip(),
            match.group(2).strip(),
            match.group(3).strip(),
        )

    match = re.match(rf"^{_NAME}\s+{_PHONE}$", line)
    if match:
        return Metadata("name_phone", match.group(1).strip(), match.group(2).strip(), None)

    match = re.match(
        rf"^{_NAME}\s+[+\-]\s*(.*?(?:AM|PM|am|pm|Mon|Tue|Wed|Thu|Fri|Sat|Sun).*?)$", line
    )
    if match:
        return Metadata("name_time", match.group(1).strip(), None, match.group(2).strip())

    if line in contacts:
        return Metadata("name_only", line, None, None)

    if re.match(r"^\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?$", line):
        return Metadata("timestamp_only", None, None, line)

    if day_timestamp and re.match(r"^[A-Za-z]{3,9},?\s+\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?$", line):
        return Metadata("timestamp_only", None, None, line)

    if re.match(r"^\+?[\d\s\-\(\)]{10,}$", line) or re.match(
        r"^\d{3}[-.\s]?\d{3}[-.\s]?\d{4}$", line
    ):
        return Metadata("phone_only", None, line.strip(), None)

    return None


def detect_metadata_v4(line: str, known_contacts: Collection[str] | None = None) -> Metadata | None:
    """Detect metadata with the v4 rule set.

    Differences from :func:`detect_metadata`: the name+phone+timestamp pattern
    accepts any trailing text as the timestamp; the name+timestamp pattern
    verifies the remainder looks like a timestamp; the bare timestamp pattern
    requires AM/PM (or a weekday prefix); the phone-only pattern drops the
    ``XXX-XXX-XXXX`` alternative.

    Note: the original v4 returned a ``(None, None, None)`` tuple on no match;
    this returns ``None``, which :func:`sms_toolkit.ocr.parse.parse_spatial`
    treats identically.
    """
    line = line.strip()
    if not line:
        return None
    contacts = known_contacts if known_contacts is not None else V4_DETECTOR_CONTACTS

    match = re.match(rf"^{_NAME}\s+{_PHONE}\s+[-:+]\s*(.*?)$", line)
    if match:
        return Metadata(
            "name_phone_time",
            match.group(1).strip(),
            match.group(2).strip(),
            match.group(3).strip(),
        )

    match = re.match(rf"^{_NAME}\s+{_PHONE}$", line)
    if match:
        return Metadata("name_phone", match.group(1).strip(), match.group(2).strip(), None)

    match = re.match(rf"^{_NAME}\s+[+\-]\s*(.*?)$", line)
    if match:
        potential_timestamp = match.group(2).strip()
        if re.search(
            r"(?:AM|PM|am|pm|\d{1,2}:\d{2}|Mon|Tue|Wed|Thu|Fri|Sat|Sun)", potential_timestamp
        ):
            return Metadata("name_time", match.group(1).strip(), None, potential_timestamp)

    if line in contacts:
        return Metadata("name_only", line, None, None)

    if re.match(r"^\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)$", line):
        return Metadata("timestamp_only", None, None, line)

    if re.match(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?", line):
        return Metadata("timestamp_only", None, None, line)

    if re.match(r"^\+?[\d\s\-\(\)]{10,}$", line):
        return Metadata("phone_only", None, line.strip(), None)

    return None


def extract_participants_v1(text: str) -> list[str]:
    """Extract participant names from the header (v1's aggressive cleanup)."""
    first_line = text.split("\n")[0].strip() if text else ""

    # Remove leading arrow/caret (often parsed as Y, >, v, etc.)
    header = re.sub(r"^[Y>v\^↓]|\s+$", "", first_line)
    # Remove trailing emoji/copyright/menu symbols (© 8 0} etc.)
    header = re.sub(r"©|8|0\}|☰|⋮|\s+\.\.\.\s+$", "", header)

    names = []
    for part in header.split(","):
        part = part.strip()
        # Remove ellipsis and emoji remnants
        part = re.sub(r"\s*\.{3}\s*|©|8|0\}", "", part)
        part = re.sub(r"\s+", " ", part).strip()
        if part and len(part) > 1:  # Must be longer than 1 char
            names.append(part)
    return names


def extract_participants_v2(text: str) -> list[str]:
    """Extract participant names from the header (v2 rules)."""
    lines = text.split("\n")
    if not lines:
        return []

    header = lines[0]
    # Remove common OCR artifacts
    header = re.sub(r"[©8O\}]", "", header)  # Remove emoji-like chars
    header = re.sub(r"\.\.\.$", "", header)  # Remove trailing ...

    # Split by comma
    names = [n.strip() for n in header.split(",") if n.strip()]

    # Filter valid names (at least one capital letter followed by lowercase)
    return [n for n in names if re.match(r"^[A-Z][a-z]+", n)]


def extract_participants_v34(text: str) -> list[str]:
    """Extract participant names from the header (v3/v4 rules: v2 + arrow strip)."""
    lines = text.split("\n")
    if not lines:
        return []

    header = lines[0]
    header = re.sub(r"[©8O\}]", "", header)
    header = re.sub(r"\.\.\.$", "", header)
    header = re.sub(r"^[<>]+\s*", "", header)  # Remove arrow artifacts

    names = [n.strip() for n in header.split(",") if n.strip()]
    return [n for n in names if re.match(r"^[A-Z][a-z]+", n)]
