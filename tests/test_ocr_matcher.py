"""Differential tests for MessageMatcher vs the original match_messages."""

from __future__ import annotations

import json
import sqlite3
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from sms_toolkit.ocr.matcher import MessageMatcher
from tests.diff_helpers import ORIGINAL_ROOT, import_original_module

OCR_ORIG = ORIGINAL_ROOT / "screenshot_analyzer"


class _StubFuzz:
    """Deterministic token-overlap stand-in for fuzzywuzzy."""

    @staticmethod
    def token_set_ratio(a: str, b: str) -> int:
        ta, tb = set(a.split()), set(b.split())
        if not ta and not tb:
            return 100
        if not ta or not tb:
            return 0
        return int(100 * len(ta & tb) / len(ta | tb))


def _stub_fuzzywuzzy() -> None:
    if "fuzzywuzzy" not in sys.modules:
        module = types.ModuleType("fuzzywuzzy")
        module.fuzz = _StubFuzz  # type: ignore[attr-defined]
        sys.modules["fuzzywuzzy"] = module


_stub_fuzzywuzzy()

ORIG_MATCH = import_original_module(
    "orig_match_messages", ORIGINAL_ROOT / "screenshot_analyzer/match_messages.py"
)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "sms_db.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE messages (
            msg_hash TEXT PRIMARY KEY,
            address TEXT,
            contact_name TEXT,
            body TEXT,
            readable_date TEXT
        )"""
    )
    conn.executemany(
        "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
        [
            ("h1", "5550101234", "Carrie Lyons", "hello there how are you", "May 23, 2014"),
            ("h2", "5550101234", "Carrie Lyons", "completely unrelated text here", "May 24, 2014"),
            ("h3", "15551234567", "Frances Lyon", "hello there how are you", "May 25, 2014"),
        ],
    )
    conn.commit()
    conn.close()
    return path


def _matchers(db_path: Path) -> tuple[MessageMatcher, Any]:
    ours = MessageMatcher(db_path)
    theirs = ORIG_MATCH.MessageMatcher(str(db_path))
    return ours, theirs


def test_find_matches_by_phone(db_path: Path) -> None:
    ours, theirs = _matchers(db_path)
    try:
        extracted = {
            "sender": "Carrie Lyons",
            "phone_number": "555-010-1234",
            "content": "hello there",
        }
        our_matches = ours.find_matches_for_message(extracted)
        their_matches = theirs.find_matches_for_message(extracted)
        assert [m["msg_hash"] for m in our_matches] == [m["msg_hash"] for m in their_matches]
        assert [m["msg_hash"] for m in our_matches] == ["h1"]  # h2 is below the 25% threshold
        assert our_matches[0]["confidence"] == pytest.approx(their_matches[0]["confidence"])
        assert our_matches[0]["contact_name"] == "Carrie Lyons"
    finally:
        ours.close()
        theirs.close()


def test_find_matches_by_sender_name(db_path: Path) -> None:
    ours, theirs = _matchers(db_path)
    try:
        extracted = {
            "sender": "Frances Lyon",
            "phone_number": None,
            "content": "hello there how are you",
        }
        our_matches = ours.find_matches_for_message(extracted)
        their_matches = theirs.find_matches_for_message(extracted)
        assert (
            [m["msg_hash"] for m in our_matches] == [m["msg_hash"] for m in their_matches] == ["h3"]
        )
    finally:
        ours.close()
        theirs.close()


def test_no_sender_info_returns_empty(db_path: Path) -> None:
    ours, theirs = _matchers(db_path)
    try:
        extracted = {"sender": None, "phone_number": None, "content": "hello"}
        assert ours.find_matches_for_message(extracted) == []
        assert theirs.find_matches_for_message(extracted) == []
    finally:
        ours.close()
        theirs.close()


def test_fuzzy_similarity_edges() -> None:
    matcher = MessageMatcher(":memory:")
    try:
        assert matcher._compute_fuzzy_similarity("", "x") == 0.0
        assert matcher._compute_fuzzy_similarity("hello world", "hello world") == 1.0
    finally:
        matcher.close()


def test_fuzzy_similarity_requires_fuzzywuzzy(monkeypatch: pytest.MonkeyPatch) -> None:
    # None in sys.modules makes the import raise ImportError, which _fuzz wraps.
    monkeypatch.setitem(sys.modules, "fuzzywuzzy", None)
    matcher = MessageMatcher(":memory:")
    try:
        with pytest.raises(RuntimeError, match="fuzzywuzzy"):
            matcher._compute_fuzzy_similarity("a", "b")
    finally:
        matcher.close()


def test_enrich_extracted_messages(db_path: Path, tmp_path: Path) -> None:
    extracted = {
        "messages": [
            {
                "sender": "Carrie Lyons",
                "phone_number": "5550101234",
                "content": "hello there how are you",
            },
            {"sender": None, "phone_number": None, "content": "orphan"},
        ]
    }
    json_path = tmp_path / "extracted.json"
    json_path.write_text(json.dumps(extracted), encoding="utf-8")

    ours, theirs = _matchers(db_path)
    try:
        our_enriched = ours.enrich_extracted_messages(json_path)
        their_enriched = theirs.enrich_extracted_messages(str(json_path))
        assert our_enriched["match_summary"] == their_enriched["match_summary"]
        assert our_enriched["match_summary"]["messages_with_matches"] == 1
        assert our_enriched["match_summary"]["match_rate"] == "50.0%"
        assert len(our_enriched["messages"][0]["database_matches"]) == 1
        assert our_enriched["messages"][1]["database_matches"] == []
    finally:
        ours.close()
        theirs.close()


def test_context_manager(db_path: Path) -> None:
    with MessageMatcher(db_path) as matcher:
        assert matcher.find_matches_for_message({"sender": "X", "content": "y"}) == []


def test_run_ocr_match_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from sms_toolkit import cli

    db = tmp_path / "sms_db.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE messages ("
        "msg_hash TEXT PRIMARY KEY, address TEXT, contact_name TEXT, "
        "body TEXT, readable_date TEXT)"
    )
    conn.execute(
        "INSERT INTO messages VALUES "
        "('h1', '5550101234', 'Carrie Lyons', 'hello there', 'May 23, 2014')"
    )
    conn.commit()
    conn.close()

    extracted = {
        "messages": [
            {"sender": "Carrie Lyons", "phone_number": "5550101234", "content": "hello there"}
        ]
    }
    json_path = tmp_path / "extracted.json"
    json_path.write_text(json.dumps(extracted), encoding="utf-8")

    out = tmp_path / "matched.json"
    assert (
        cli.main(
            [
                "--root",
                str(tmp_path),
                "ocr-match",
                "--json",
                str(json_path),
                "--db",
                str(db),
                "--output",
                str(out),
            ]
        )
        == 0
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["match_summary"]["messages_with_matches"] == 1
    assert "Match rate" in capsys.readouterr().out


def test_ocr_extract_cli_wires_strategy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from sms_toolkit import cli
    from sms_toolkit.ocr import backends

    monkeypatch.setattr(backends, "ocr_text_v2", lambda path: "Carrie Lyons\nhello\n")
    image = tmp_path / "shot.png"
    image.write_bytes(b"fake")
    out = tmp_path / "out.json"
    assert (
        cli.main(
            [
                "--root",
                str(tmp_path),
                "ocr-extract",
                "--image",
                str(image),
                "--strategy",
                "v2",
                "--user-name",
                "Justin",
                "--output",
                str(out),
            ]
        )
        == 0
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["participants"] == ["Carrie Lyons"]
