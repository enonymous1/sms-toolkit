"""Differential tests for OCR metadata/header detection vs the originals v1-v4."""

from __future__ import annotations

import sys
import types

import pytest

from sms_toolkit.ocr import detect as new_detect
from tests.diff_helpers import ORIGINAL_ROOT, import_original_module

OCR_ORIG = ORIGINAL_ROOT / "screenshot_analyzer"


def _raise_no_pil(path: object) -> object:
    raise RuntimeError("PIL stub")


def _stub_missing_third_party() -> None:
    """Stub PIL/pytesseract so v1's module imports without the ocr extra."""
    if "PIL" not in sys.modules:
        pil = types.ModuleType("PIL")
        pil.Image = types.SimpleNamespace(open=_raise_no_pil)  # type: ignore[attr-defined]
        sys.modules["PIL"] = pil
    sys.modules.setdefault("pytesseract", types.ModuleType("pytesseract"))


_stub_missing_third_party()

V1 = import_original_module("orig_ocr_v1", OCR_ORIG / "extract_messages.py")
V2 = import_original_module("orig_ocr_v2", OCR_ORIG / "extract_messages_v2.py")
V3 = import_original_module("orig_ocr_v3", OCR_ORIG / "extract_messages_v3.py")
V4 = import_original_module("orig_ocr_v4", OCR_ORIG / "extract_messages_v4.py")

CONTACTS = {"Carrie Lyons", "Frances Lyon", "Justin"}

METADATA_CASES = [
    # (line, expected kind, sender, phone, timestamp)
    (
        "Carrie Lyons 571-643-5424 - 8:35 PM",
        "name_phone_time",
        "Carrie Lyons",
        "571-643-5424",
        "8:35 PM",
    ),
    ("Carrie Lyons 571-643-5424", "name_phone", "Carrie Lyons", "571-643-5424", None),
    ("Frances Lyon + Mon, 4:12 PM", "name_time", "Frances Lyon", None, "Mon, 4:12 PM"),
    ("Carrie Lyons", "name_only", "Carrie Lyons", None, None),
    ("9:41 PM", "timestamp_only", None, None, "9:41 PM"),
    ("571-643-5424", "phone_only", None, "571-643-5424", None),
    ("just a normal message line", None, None, None, None),
    ("", None, None, None, None),
]


@pytest.mark.parametrize("line, kind, sender, phone, timestamp", METADATA_CASES)
def test_detect_metadata_patterns(
    line: str, kind: str | None, sender: str | None, phone: str | None, timestamp: str | None
) -> None:
    meta = new_detect.detect_metadata(line, CONTACTS)
    if kind is None:
        assert meta is None
    else:
        assert meta is not None
        assert (meta.kind, meta.sender, meta.phone, meta.timestamp) == (
            kind,
            sender,
            phone,
            timestamp,
        )


def test_detect_metadata_matches_v3_method() -> None:
    v3_ext = object.__new__(V3.SpatialConversationExtractor)
    for line, kind, _sender, _phone, _timestamp in METADATA_CASES:
        ours = new_detect.detect_metadata(line, CONTACTS, day_timestamp=True)
        v3_sender, v3_phone, v3_timestamp = V3.SpatialConversationExtractor.detect_metadata(
            v3_ext, line
        )
        if kind is None or kind == "name_only":
            # name_only depends on the contact set; check separately below
            continue
        ours_tuple = None if ours is None else (ours.sender, ours.phone, ours.timestamp)
        assert ours_tuple == (v3_sender, v3_phone, v3_timestamp), line


def test_detect_metadata_v3_day_timestamp() -> None:
    meta = new_detect.detect_metadata("Mon, 4:12 PM", CONTACTS, day_timestamp=True)
    assert meta is not None and meta.kind == "timestamp_only"
    assert new_detect.detect_metadata("Mon, 4:12 PM", CONTACTS) is None


def test_detect_metadata_v4_matches_original() -> None:
    v4_ext = object.__new__(V4.SpatialConversationExtractorV4)
    cases = [
        "Carrie Lyons 571-643-5424 - 8:35 PM",
        "Carrie Lyons 571-643-5424",
        "Frances Lyon + Mon, 4:12 PM",
        "Frances Lyon - hello there",  # not a timestamp -> no match
        "Carrie",
        "9:41 PM",
        "9:41",
        "Mon, 4:12 PM",
        "571-643-5424",
        "plain content line",
    ]
    for line in cases:
        ours = new_detect.detect_metadata_v4(line)
        v4_sender, v4_phone, v4_timestamp = V4.SpatialConversationExtractorV4.detect_metadata(
            v4_ext, line
        )
        # v4 returns a (None, None, None) sentinel instead of None; normalize.
        v4_tuple: tuple[object, object, object] | None = (v4_sender, v4_phone, v4_timestamp)
        if v4_tuple == (None, None, None):
            v4_tuple = None
        ours_tuple = None if ours is None else (ours.sender, ours.phone, ours.timestamp)
        assert ours_tuple == v4_tuple, line


@pytest.mark.parametrize(
    "header, expected_v2, expected_v34",
    [
        (
            "Carrie Lyons, Frances Lyon",
            ["Carrie Lyons", "Frances Lyon"],
            ["Carrie Lyons", "Frances Lyon"],
        ),
        ("Carrie Lyons", ["Carrie Lyons"], ["Carrie Lyons"]),
        ("©Carrie Lyons8}", ["Carrie Lyons"], ["Carrie Lyons"]),
        ("Carrie Lyons...", ["Carrie Lyons"], ["Carrie Lyons"]),
        (">Carrie Lyons, Frances Lyon", ["Frances Lyon"], ["Carrie Lyons", "Frances Lyon"]),
        ("", [], []),
    ],
)
def test_header_parsers(header: str, expected_v2: list[str], expected_v34: list[str]) -> None:
    text = header + "\nbody line"
    assert new_detect.extract_participants_v2(text) == expected_v2
    assert new_detect.extract_participants_v34(text) == expected_v34


def test_header_v1_matches_original() -> None:
    v1_ext = object.__new__(V1.ConversationExtractor)
    for header in [
        "Carrie Lyons, Frances Lyon",
        "Y Carrie Lyons, Frances Lyon ©",
        "Lyndsay M...",
        "X",
    ]:
        text = header + "\nbody"
        assert new_detect.extract_participants_v1(text) == v1_ext.extract_participants_from_header(
            text
        )


def test_header_v2_v34_match_originals() -> None:
    v2_ext = V2.ConversationExtractor("dummy.png")
    v3_ext = object.__new__(V3.SpatialConversationExtractor)
    v4_ext = object.__new__(V4.SpatialConversationExtractorV4)
    for header in ["Carrie Lyons, Frances Lyon", "©Carrie Lyons8}", "Carrie Lyons...", ""]:
        text = header + "\nbody"
        assert new_detect.extract_participants_v2(text) == v2_ext.extract_participants_from_header(
            text
        )
        assert new_detect.extract_participants_v34(text) == v3_ext.extract_participants_from_header(
            text
        )
        assert new_detect.extract_participants_v34(text) == v4_ext.extract_participants_from_header(
            text
        )


def test_header_arrow_case_follows_v3() -> None:
    # v2 has no arrow stripping; v3/v4 do. The v34 parser follows v3/v4.
    text = ">Carrie Lyons, Frances Lyon\nbody"
    v3_ext = object.__new__(V3.SpatialConversationExtractor)
    assert new_detect.extract_participants_v34(text) == v3_ext.extract_participants_from_header(
        text
    )
    assert new_detect.extract_participants_v34(text) == ["Carrie Lyons", "Frances Lyon"]
