"""Differential tests for the OCR parse strategies vs originals v1-v4."""

from __future__ import annotations

import sys
import types
from typing import Any

from sms_toolkit.ocr import parse as new_parse
from sms_toolkit.ocr.detect import detect_metadata_v4
from tests.diff_helpers import ORIGINAL_ROOT, import_original_module

OCR_ORIG = ORIGINAL_ROOT / "screenshot_analyzer"


def _raise_no_pil(path: object) -> object:
    raise RuntimeError("PIL stub")


def _stub_missing_third_party() -> None:
    if "PIL" not in sys.modules:
        pil = types.ModuleType("PIL")
        pil.Image = types.SimpleNamespace(open=_raise_no_pil)  # type: ignore[attr-defined]
        sys.modules["PIL"] = pil
    sys.modules.setdefault("pytesseract", types.ModuleType("pytesseract"))


_stub_missing_third_party()

V1 = import_original_module("orig_ocr_v1p", OCR_ORIG / "extract_messages.py")
V2 = import_original_module("orig_ocr_v2p", OCR_ORIG / "extract_messages_v2.py")
V3 = import_original_module("orig_ocr_v3p", OCR_ORIG / "extract_messages_v3.py")
V4 = import_original_module("orig_ocr_v4p", OCR_ORIG / "extract_messages_v4.py")

CONTACTS = ["Carrie Lyons", "Frances Lyon"]

# Exercises: plain content, name+phone+time, timestamp-only, name-only,
# blank-line block splits, the "0} type message" OCR artifact, and a
# no-metadata tail (sender inference).
OCR_TEXT = """Carrie Lyons, Frances Lyon
Hello there
How are you doing today?

Carrie Lyons 571-643-5424 - 8:35 PM
I am doing well thanks

9:41 PM
Sounds good, see you then

0} Type Message
Frances Lyon
On my way now
"""

# Exercises v1's long/short split heuristic: a long accumulated block followed
# by a short block with metadata from a different inferred sender.
OCR_TEXT_SPLIT = """Carrie Lyons, Frances Lyon
Carrie, can you please send me the details about the meeting tomorrow morning
I need to prepare the slides and the agenda before everyone arrives
Let me know if you need anything else from my side

Carrie Lyons 571-643-5424 - 8:35 PM
ok
"""


def _v1_parse(text: str) -> Any:
    ext = object.__new__(V1.ConversationExtractor)
    ext.participants = list(CONTACTS)
    ext.user_name = "Justin"
    return ext.parse_conversation(text)


def _v2_parse(text: str) -> Any:
    ext = V2.ConversationExtractor("dummy.png", user_name="Justin")
    ext.participants = list(CONTACTS)
    return ext.parse_conversation(text)


def _v3_parse(text: str) -> Any:
    ext = object.__new__(V3.SpatialConversationExtractor)
    ext.participants = list(CONTACTS)
    ext.user_name = "Justin"
    return ext.parse_conversation(text, [], 1080)


def _v4_parse(text: str) -> Any:
    ext = object.__new__(V4.SpatialConversationExtractorV4)
    ext.participants = list(CONTACTS)
    ext.user_name = "Justin"
    return ext.parse_conversation(text, [])


def test_parse_v1_matches_original() -> None:
    for text in (OCR_TEXT, OCR_TEXT_SPLIT, "Header\nonly content, no metadata\n"):
        assert new_parse.parse_v1(text, set(CONTACTS), "Justin") == _v1_parse(text)


def test_parse_v2_matches_original() -> None:
    for text in (OCR_TEXT, OCR_TEXT_SPLIT, "Header\nonly content, no metadata\n"):
        assert new_parse.parse_v2(text, set(CONTACTS), "Justin") == _v2_parse(text)


def test_parse_spatial_matches_v3() -> None:
    for text in (OCR_TEXT, "Header\nonly content, no metadata\n"):
        assert new_parse.parse_spatial(text, set(CONTACTS), "Justin") == _v3_parse(text)


def test_parse_spatial_v4_matches_original() -> None:
    for text in (OCR_TEXT, "Header\nonly content, no metadata\n"):
        ours = new_parse.parse_spatial(text, set(CONTACTS), "Justin", detect_metadata_v4)
        assert ours == _v4_parse(text)


def test_message_shape_and_direction() -> None:
    messages = new_parse.parse_v2(OCR_TEXT, set(CONTACTS), "Justin")
    assert messages, "expected at least one parsed message"
    for message in messages:
        assert set(message) == {
            "sender",
            "phone_number",
            "timestamp",
            "content",
            "line_count",
            "media_type",
            "direction",
        }
        assert message["media_type"] == "text"
        assert message["direction"] in ("sent", "received")
    # The unattributed tail is inferred as the user's -> sent.
    tail = messages[-1]
    assert tail["sender"] == "Justin"
    assert tail["direction"] == "sent"


def test_infer_sender_v1_addressing() -> None:
    assert new_parse.infer_sender_v1("Carrie, call me back", set(CONTACTS), "Justin") == "Justin"
    assert new_parse.infer_sender_v1("See you soon", set(CONTACTS), "Justin") == "Justin"
