"""Conversation-parsing strategies for screenshot OCR text.

Each function ports one original ``parse_conversation`` implementation
verbatim in behavior:

- :func:`parse_v1` — v1's block-based parser with the long/short split
  heuristic and content-based sender inference.
- :func:`parse_v2` — v2's simplified line accumulator.
- :func:`parse_spatial` — the v3/v4 line parser (the two were behaviorally
  identical: v3's alignment fallback always returned the user name for the
  empty block list it was given). Takes the metadata detector as a parameter
  so v4's rule set can be plugged in.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

from sms_toolkit.ocr.detect import (
    DEFAULT_KNOWN_CONTACTS,
    Metadata,
    detect_metadata,
)
from sms_toolkit.ocr.models import make_message

DetectFn = Callable[[str], Metadata | None]


def infer_sender_v1(content: str, known_contacts: Collection[str], user_name: str) -> str | None:
    """Infer the sender of content without metadata (v1 heuristics).

    If the content starts by addressing a known contact it is attributed to the
    user; otherwise content without metadata is assumed to be the user's
    (right-aligned in the UI).
    """
    content_start = content.split("\n")[0].lower() if content else ""

    # Check if addressing a known contact (user responding to them)
    for contact in known_contacts:
        if content_start.startswith(contact.lower()):
            return user_name

    # Messages without sender metadata are typically the user's (right-aligned)
    return user_name


def _skip_header_lines(text: str) -> list[str]:
    """Split OCR text into lines, dropping the participant header line."""
    lines = text.strip().split("\n")
    return lines[1:] if lines else []


def parse_v1(
    text: str,
    known_contacts: Collection[str] | None = None,
    user_name: str = "Justin",
) -> list[dict[str, Any]]:
    """Parse OCR text with v1's block-based algorithm."""
    contacts = set(known_contacts) if known_contacts is not None else set(DEFAULT_KNOWN_CONTACTS)
    lines = _skip_header_lines(text)

    # Group lines into content blocks separated by blank lines; a trailing
    # metadata line marks the end of a message.
    content_blocks: list[list[str]] = []
    current_block: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.lower() == "0} type message":
            if current_block:
                content_blocks.append(current_block)
                current_block = []
            continue
        current_block.append(line)
    if current_block:
        content_blocks.append(current_block)

    messages: list[dict[str, Any]] = []
    accumulated_blocks: list[list[str]] = []

    for block in content_blocks:
        meta = detect_metadata(block[-1], contacts)

        if meta is not None:
            # This block ends with metadata.
            content_lines_in_block = len(block) - 1  # -1 for the metadata line itself

            # If long earlier content meets very short current content, they are
            # likely separate messages; split when the inferred senders differ.
            if len(accumulated_blocks) > 0 and content_lines_in_block <= 2:
                accumulated_lines = sum(len(b) for b in accumulated_blocks)
                if accumulated_lines >= 3 and accumulated_lines > content_lines_in_block * 3:
                    all_earlier = [line for earlier in accumulated_blocks for line in earlier]
                    earlier_text = "\n".join(all_earlier)
                    earlier_inferred = infer_sender_v1(earlier_text, contacts, user_name)

                    if earlier_inferred and earlier_inferred != meta.sender:
                        messages.append(
                            make_message(earlier_inferred, None, None, all_earlier, user_name)
                        )

                        current_content = block[:-1]
                        if current_content:
                            if meta.kind in ("timestamp_only", "phone_only"):
                                inferred = infer_sender_v1(
                                    "\n".join(current_content), contacts, user_name
                                )
                                messages.append(
                                    make_message(
                                        inferred,
                                        meta.phone,
                                        meta.timestamp,
                                        current_content,
                                        user_name,
                                    )
                                )
                            else:
                                messages.append(
                                    make_message(
                                        meta.sender,
                                        meta.phone,
                                        meta.timestamp,
                                        current_content,
                                        user_name,
                                    )
                                )

                        accumulated_blocks = []
                        continue

            # Standard case: accumulated blocks + current block belong together.
            all_content = [line for b in accumulated_blocks for line in b] + block[:-1]

            if all_content:  # Only create a message if there is content
                if meta.kind in ("timestamp_only", "phone_only"):
                    inferred = infer_sender_v1("\n".join(all_content), contacts, user_name)
                    messages.append(
                        make_message(inferred, meta.phone, meta.timestamp, all_content, user_name)
                    )
                else:
                    messages.append(
                        make_message(
                            meta.sender, meta.phone, meta.timestamp, all_content, user_name
                        )
                    )

            accumulated_blocks = []
        else:
            # No metadata; accumulate this block.
            accumulated_blocks.append(block)

    # Handle remaining blocks (messages without metadata).
    if accumulated_blocks:
        all_remaining = [line for b in accumulated_blocks for line in b]
        if all_remaining:
            inferred = infer_sender_v1("\n".join(all_remaining), contacts, user_name)
            messages.append(make_message(inferred, None, None, all_remaining, user_name))

    return messages


def parse_v2(
    text: str,
    known_contacts: Collection[str] | None = None,
    user_name: str = "Justin",
) -> list[dict[str, Any]]:
    """Parse OCR text with v2's line-accumulator algorithm."""
    contacts = set(known_contacts) if known_contacts is not None else set(DEFAULT_KNOWN_CONTACTS)
    lines = _skip_header_lines(text)

    messages: list[dict[str, Any]] = []
    accumulated: list[str] = []

    for line in lines:
        line_stripped = line.strip()

        if not line_stripped or line_stripped.lower() == "0} type message":
            continue

        meta = detect_metadata(line_stripped, contacts)

        if meta is not None and (meta.sender or meta.phone or meta.timestamp):
            # Metadata line: finalize accumulated content.
            if accumulated:
                sender = meta.sender or user_name
                messages.append(
                    make_message(sender, meta.phone, meta.timestamp, accumulated, user_name)
                )
                accumulated = []
        else:
            accumulated.append(line_stripped)

    # Finalize any remaining content.
    if accumulated:
        messages.append(make_message(user_name, None, None, accumulated, user_name))

    return messages


def parse_spatial(
    text: str,
    known_contacts: Collection[str] | None = None,
    user_name: str = "Justin",
    detect_fn: DetectFn | None = None,
) -> list[dict[str, Any]]:
    """Parse OCR text with the v3/v4 line algorithm.

    *detect_fn* selects the metadata rule set (v3's by default, v4's via
    :func:`sms_toolkit.ocr.detect.detect_metadata_v4`). Unattributed content
    falls back to *user_name*. (``known_contacts`` is accepted for API symmetry
    with the other parsers; v3/v4 never used it.)
    """
    detect = detect_fn or (lambda line: detect_metadata(line, None, day_timestamp=True))
    lines = _skip_header_lines(text)

    messages: list[dict[str, Any]] = []
    accumulated: list[str] = []

    for line in lines:
        line_stripped = line.strip()

        if not line_stripped or line_stripped.lower() == "0} type message":
            continue

        meta = detect(line_stripped)

        if meta is not None and (meta.sender or meta.phone or meta.timestamp):
            if accumulated:
                sender = meta.sender or user_name
                messages.append(
                    make_message(sender, meta.phone, meta.timestamp, accumulated, user_name)
                )
                accumulated = []
        else:
            accumulated.append(line_stripped)

    if accumulated:
        messages.append(make_message(user_name, None, None, accumulated, user_name))

    return messages
