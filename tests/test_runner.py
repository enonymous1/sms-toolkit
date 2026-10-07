"""End-to-end tests for pipeline.runner.run_pipeline."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sms_toolkit.calls.variant import CALLS_VARIANT
from sms_toolkit.config import load_config
from sms_toolkit.pipeline.runner import run_pipeline
from sms_toolkit.sms.variant import SMS_VARIANT
from tests.diff_helpers import CALLS_FIXTURE_XML, SMS_FIXTURE_XML


def _write_input(tmp_path: Path, name: str, text: str) -> Path:
    input_dir = tmp_path / "input"
    input_dir.mkdir(exist_ok=True)
    (input_dir / name).write_text(text, encoding="utf-8")
    return input_dir


def test_run_pipeline_sms_end_to_end_with_dedup(tmp_path: Path) -> None:
    input_dir = _write_input(tmp_path, "backup.xml", SMS_FIXTURE_XML)
    cfg = load_config(root=tmp_path)

    assert run_pipeline(SMS_VARIANT, input_dir=input_dir, config=cfg) == 0
    # Second run in append mode dedups against the existing rows.
    assert run_pipeline(SMS_VARIANT, input_dir=input_dir, config=cfg) == 0

    conn = sqlite3.connect(tmp_path / "sms_db.sqlite")
    try:
        # 3 unique elements: 2 sms + 1 mms (the duplicate sms is ignored twice).
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM mms_parts").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM mms_addrs").fetchone()[0] == 2
    finally:
        conn.close()

    assert (tmp_path / "Master_Backup.xml").exists()
    assert (tmp_path / "messages.csv").exists()
    assert (tmp_path / "processing_log.txt").exists()


def test_run_pipeline_calls_end_to_end(tmp_path: Path) -> None:
    input_dir = _write_input(tmp_path, "calls.xml", CALLS_FIXTURE_XML)
    cfg = load_config(root=tmp_path)

    assert run_pipeline(CALLS_VARIANT, input_dir=input_dir, config=cfg) == 0

    conn = sqlite3.connect(tmp_path / "calls_db.sqlite")
    try:
        # 2 unique calls (one duplicate); "abc" duration stored as 0.
        assert conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 2
        row = conn.execute("SELECT duration FROM calls WHERE number = '15551234567'").fetchone()
        assert row[0] == 0
    finally:
        conn.close()

    assert (tmp_path / "Master_Calls.xml").exists()
    assert (tmp_path / "calls.csv").exists()
    assert (tmp_path / "calls_processing_log.txt").exists()


def test_run_pipeline_fresh_mode(tmp_path: Path) -> None:
    input_dir = _write_input(tmp_path, "backup.xml", SMS_FIXTURE_XML)
    cfg = load_config(root=tmp_path)

    assert run_pipeline(SMS_VARIANT, input_dir=input_dir, config=cfg) == 0
    # Fresh run archives the old outputs and starts over.
    assert run_pipeline(SMS_VARIANT, input_dir=input_dir, config=cfg, fresh=True) == 0

    conn = sqlite3.connect(tmp_path / "sms_db.sqlite")
    try:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3
    finally:
        conn.close()
    assert list(tmp_path.glob("Master_Backup.xml.backup_*"))


def test_run_pipeline_invalid_dir(tmp_path: Path) -> None:
    cfg = load_config(root=tmp_path)
    assert run_pipeline(SMS_VARIANT, input_dir=tmp_path / "nope", config=cfg) == 1


def test_run_pipeline_no_xml_files(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    cfg = load_config(root=tmp_path)
    assert run_pipeline(SMS_VARIANT, input_dir=empty, config=cfg) == 1
