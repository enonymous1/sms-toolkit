"""Tests for the unified ConversationExtractor (strategies, JSON shape, CLI runners)."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from sms_toolkit.ocr import backends
from sms_toolkit.ocr.extractor import STRATEGIES, ConversationExtractor, run_ocr_extract
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

V1 = import_original_module("orig_ocr_v1e", OCR_ORIG / "extract_messages.py")
V2 = import_original_module("orig_ocr_v2e", OCR_ORIG / "extract_messages_v2.py")

OCR_TEXT = """Carrie Lyons, Frances Lyon
Hello there

Carrie Lyons 555-010-1234 - 8:35 PM
I am doing well thanks

trailing content without metadata
"""


def test_unknown_strategy_raises() -> None:
    with pytest.raises(ValueError, match="Unknown OCR strategy"):
        ConversationExtractor("dummy.png", strategy="v9")


def test_strategies_cover_v1_through_v4() -> None:
    assert sorted(STRATEGIES) == ["v1", "v2", "v3", "v4"]


@pytest.mark.parametrize("strategy", ["v1", "v2"])
def test_text_strategies_match_original_parse(strategy: str) -> None:
    ours = ConversationExtractor("dummy.png", user_name="Justin", strategy=strategy)
    ours.raw_text = OCR_TEXT
    ours.extract_participants_from_header(OCR_TEXT)
    ours_messages = ours.parse_conversation(OCR_TEXT)

    if strategy == "v1":
        orig = object.__new__(V1.ConversationExtractor)
        orig.participants = ["Carrie Lyons", "Frances Lyon"]
        orig.user_name = "Justin"
        expected = orig.parse_conversation(OCR_TEXT)
    else:
        orig = V2.ConversationExtractor("dummy.png", user_name="Justin")
        orig.participants = ["Carrie Lyons", "Frances Lyon"]
        expected = orig.parse_conversation(OCR_TEXT)

    assert ours_messages == expected
    assert ours.participants == ["Carrie Lyons", "Frances Lyon"]
    assert ours.is_group_chat is True


def test_to_json_shape(tmp_path: Path) -> None:
    ours = ConversationExtractor("shot.png", user_name="Justin", strategy="v2")
    ours.raw_text = OCR_TEXT
    ours.extract_participants_from_header(OCR_TEXT)
    ours.parse_conversation(OCR_TEXT)

    out = tmp_path / "out.json"
    returned = ours.to_json(out)
    assert returned == str(out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["source_file"] == "shot.png"
    assert payload["extraction_method"] == "OCR (pytesseract)"
    assert payload["conversation_type"] == "Group Chat"
    assert payload["is_group_chat"] is True
    assert payload["total_messages"] == len(ours.messages)
    assert payload["messages"] == ours.messages
    assert payload["raw_ocr_text"] == ""

    # v1 keeps the raw OCR text in the JSON.
    v1 = ConversationExtractor("shot.png", user_name="Justin", strategy="v1")
    v1.raw_text = OCR_TEXT
    v1.extract_participants_from_header(OCR_TEXT)
    v1.parse_conversation(OCR_TEXT)
    payload_v1 = json.loads(v1.to_json())
    assert payload_v1["raw_ocr_text"] == OCR_TEXT


def test_run_ocr_extract_writes_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(backends, "ocr_text_v2", lambda path: OCR_TEXT)
    image = tmp_path / "shot.png"
    image.write_bytes(b"fake")
    out = tmp_path / "result.json"
    assert run_ocr_extract(image, out, strategy="v2", user_name="Justin") == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["total_messages"] == 2
    assert "Extracted 2 messages" in capsys.readouterr().out


def test_run_ocr_extract_default_output_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backends, "ocr_text_v2", lambda path: OCR_TEXT)
    image = tmp_path / "shot.png"
    image.write_bytes(b"fake")
    assert run_ocr_extract(image, None, strategy="v2") == 0
    assert (tmp_path / "shot_extracted.json").exists()


def test_run_ocr_extract_missing_image_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_ocr_extract(Path("/nonexistent/shot.png"), None, strategy="v2") == 1
    assert "failed" in capsys.readouterr().out
