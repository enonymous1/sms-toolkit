"""Tests for stages.normalize_messages (with differentials vs the original)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sms_toolkit.stages import normalize_messages as nm
from tests.diff_helpers import ORIGINAL_ROOT, import_original_module
from tests.stage_fixtures import SMS_ROWS, dump_table, make_stage_db

ORIG_SRC = ORIGINAL_ROOT / "sms-backup-analyzer" / "src"
orig_mod = import_original_module("orig_normalize_stage", ORIG_SRC / "normalize_messages_stage.py")


def _normalize_db(path: Path) -> Path:
    rows = [r for r in SMS_ROWS if r["msg_hash"] != "hash-mms-1"]
    return make_stage_db(path, messages=rows)


def test_apply_backfill_matches_original(tmp_path: Path) -> None:
    orig_db = _normalize_db(tmp_path / "orig.sqlite")
    new_db = _normalize_db(tmp_path / "new.sqlite")

    orig_conn = sqlite3.connect(orig_db)
    new_conn = sqlite3.connect(new_db)
    try:
        orig_mod.ensure_columns(orig_conn)
        assert nm.apply_backfill(new_conn) == orig_mod.apply_backfill(orig_conn)
    finally:
        orig_conn.close()
        new_conn.close()

    assert dump_table(new_db, "messages") == dump_table(orig_db, "messages")


def test_print_report_matches_original(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = _normalize_db(tmp_path / "t.sqlite")
    conn = sqlite3.connect(db_path)
    try:
        nm.apply_backfill(conn)
        nm.print_report(conn)
        new_out = capsys.readouterr().out
        orig_mod.print_report(conn)
        orig_out = capsys.readouterr().out
    finally:
        conn.close()
    assert new_out == orig_out
    assert "Normalization Report" in new_out
    assert "messages total: 3" in new_out


def test_backfill_results(tmp_path: Path) -> None:
    db_path = _normalize_db(tmp_path / "t.sqlite")
    conn = sqlite3.connect(db_path)
    try:
        addr_rows, date_rows = nm.apply_backfill(conn)
        rows = {
            r[0]: r
            for r in conn.execute(
                "SELECT msg_hash, normalized_address, normalized_date_ms FROM messages"
            ).fetchall()
        }
    finally:
        conn.close()
    assert (addr_rows, date_rows) == (3, 2)
    # "(555) 010-1234" -> separators stripped.
    assert rows["hash-sms-1"][1] == "5550101234"
    # "15550101234" -> leading 1 stripped.
    assert rows["hash-sms-2"][1] == "5550101234"
    # 13-digit ms kept as-is; 10-digit seconds cast as-is (original semantics).
    assert rows["hash-sms-1"][2] == 1685712000000
    assert rows["hash-sms-2"][2] == 1685712060
    # Non-numeric date left alone.
    assert rows["hash-sms-3"][2] is None


def test_run_normalize_report_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = _normalize_db(tmp_path / "t.sqlite")
    before = dump_table(db_path, "messages")
    assert (
        nm.run_normalize(
            db_path,
            show_report=True,
            apply=False,
            backup=False,
            backup_dir=tmp_path / "b",
            backup_label="x",
        )
        == 0
    )
    assert dump_table(db_path, "messages") == before
    assert "Normalization Report" in capsys.readouterr().out


def test_run_normalize_apply_with_backup(tmp_path: Path) -> None:
    db_path = _normalize_db(tmp_path / "t.sqlite")
    backup_dir = tmp_path / "backups"
    assert (
        nm.run_normalize(
            db_path,
            show_report=False,
            apply=True,
            backup=True,
            backup_dir=backup_dir,
            backup_label="pre-normalize",
        )
        == 0
    )
    assert list(backup_dir.glob("*.sqlite"))
    conn = sqlite3.connect(db_path)
    try:
        missing = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE normalized_address IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert missing == 0


def test_run_normalize_apply_without_backup_flag(tmp_path: Path) -> None:
    # --apply alone does not back up (matches the original).
    db_path = _normalize_db(tmp_path / "t.sqlite")
    backup_dir = tmp_path / "backups"
    assert (
        nm.run_normalize(
            db_path,
            show_report=False,
            apply=True,
            backup=False,
            backup_dir=backup_dir,
            backup_label="pre-normalize",
        )
        == 0
    )
    assert not backup_dir.exists()


def test_run_normalize_validation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Neither --report nor --apply.
    assert (
        nm.run_normalize(
            tmp_path / "t.sqlite",
            show_report=False,
            apply=False,
            backup=False,
            backup_dir=tmp_path,
            backup_label="x",
        )
        == 1
    )
    assert "Choose at least one action" in capsys.readouterr().out
    # Missing DB.
    assert (
        nm.run_normalize(
            tmp_path / "nope.sqlite",
            show_report=True,
            apply=False,
            backup=False,
            backup_dir=tmp_path,
            backup_label="x",
        )
        == 1
    )
    assert "DB not found" in capsys.readouterr().out
