"""Tests for stages.import_ifttt (with differentials vs the original)."""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from sms_toolkit.stages import import_ifttt as ii
from tests.diff_helpers import ORIGINAL_ROOT, import_original_module
from tests.stage_fixtures import make_ifttt_xlsx, make_stage_db

ORIG_SRC = ORIGINAL_ROOT / "sms-backup-analyzer" / "src"
orig_mod = import_original_module("orig_import_ifttt", ORIG_SRC / "import_ifttt_sms.py")


def _ifttt_xlsx(path: Path) -> Path:
    return make_ifttt_xlsx(path)


def _sample_message(**overrides: Any) -> ii.ParsedMessage:
    dt = datetime(2021, 3, 28, 15, 4, 0)
    base: dict[str, Any] = {
        "source_file": "f.xlsx",
        "source_row": 1,
        "source_col": 1,
        "timestamp_raw": "March 28, 2021 at 3:04PM",
        "date_ms": int(dt.timestamp() * 1000),
        "readable_date": ii.format_readable_date(dt),
        "address": "5550101234",
        "body": "hello",
        "contact_name": "Justin",
    }
    base.update(overrides)
    return ii.ParsedMessage(**base)


def test_helpers_match_original() -> None:
    assert [ii.is_timestamp_cell(v) for v in ["March 28, 2021 at 3:04PM", "nope", 123, None]] == [
        orig_mod.is_timestamp_cell(v) for v in ["March 28, 2021 at 3:04PM", "nope", 123, None]
    ]
    for ts_text in ["March 28, 2021 at 3:04PM", "March 28, 2021 at 3:04 PM", "garbage"]:
        assert ii.parse_timestamp(ts_text) == orig_mod.parse_timestamp(ts_text)
    for body_text in ["  a\u00a0b  ", None, 123]:
        assert ii.normalize_body(body_text) == orig_mod.normalize_body(body_text)
    dt = datetime(2021, 3, 28, 15, 4, 5)
    assert ii.format_readable_date(dt) == orig_mod.format_readable_date(dt)
    for name_text in ["Justin", "Mary Jane", "http://x", "Bob3", "a b c d e", "O'Brien"]:
        assert ii.looks_like_name(name_text) == orig_mod.looks_like_name(name_text)


def test_extract_tuple_matches_original() -> None:
    windows = [
        (["5550101234", "Justin", "hello there"]),
        (["5550101234", "world"]),
        (["abc", "Bob", "no phone"]),
        ([]),
    ]
    for window in windows:
        new = ii.extract_tuple_from_window("f.xlsx", 1, 0, "March 28, 2021 at 3:04PM", window)
        old = orig_mod.extract_tuple_from_window("f.xlsx", 1, 0, "March 28, 2021 at 3:04PM", window)
        assert (dataclasses.asdict(new) if new else None) == (
            dataclasses.asdict(old) if old else None
        )


def test_msg_hash_and_day_key_match_original() -> None:
    msg = _sample_message()
    old = orig_mod.ParsedMessage(**dataclasses.asdict(msg))
    assert msg.msg_hash() == old.msg_hash()
    assert msg.day_key() == old.day_key()
    assert len(msg.msg_hash()) == 32


def test_parse_excel_matches_original(tmp_path: Path) -> None:
    xlsx = _ifttt_xlsx(tmp_path / "ifttt.xlsx")
    new_stats, old_stats = ii.ImportStats(), orig_mod.ImportStats()
    new_parsed = ii.parse_excel_messages(xlsx, new_stats)
    old_parsed = orig_mod.parse_excel_messages(xlsx, old_stats)
    assert [dataclasses.asdict(m) for m in new_parsed] == [
        dataclasses.asdict(m) for m in old_parsed
    ]
    assert dataclasses.asdict(new_stats) == dataclasses.asdict(old_stats)
    # 1 + 1 + 2 windows parsed; rows 3 and 4 skipped.
    assert new_stats.parsed_messages == 4
    assert new_stats.skipped_invalid == 1
    assert new_parsed[1].address == "5550101234"  # float phone normalized
    assert new_parsed[1].contact_name is None


def test_insert_messages_dedupes(tmp_path: Path) -> None:
    db_path = make_stage_db(tmp_path / "t.sqlite")
    msg = _sample_message()
    # Same address/body/day, different timestamp -> different hash.
    same_day = _sample_message(
        date_ms=int(datetime(2021, 3, 28, 18, 30).timestamp() * 1000),
        contact_name=None,
    )
    assert same_day.msg_hash() != msg.msg_hash()
    assert same_day.day_key() == msg.day_key()

    conn = sqlite3.connect(db_path)
    try:
        stats = ii.ImportStats()
        ii.insert_messages(conn, [msg, msg], True, stats)
        conn.commit()
        assert stats.inserted_messages == 1
        assert stats.dup_hash_batch == 1
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1

        # Same day + body already in the DB.
        stats2 = ii.ImportStats()
        ii.insert_messages(conn, [same_day], True, stats2)
        conn.commit()
        assert stats2.dup_same_day_body_db == 1
        assert stats2.inserted_messages == 0

        # Exact hash already in the DB.
        stats3 = ii.ImportStats()
        ii.insert_messages(conn, [msg], True, stats3)
        assert stats3.dup_hash_db == 1
        assert stats3.inserted_messages == 0
    finally:
        conn.close()


def test_insert_messages_dry_run_writes_nothing(tmp_path: Path) -> None:
    db_path = make_stage_db(tmp_path / "t.sqlite")
    conn = sqlite3.connect(db_path)
    try:
        stats = ii.ImportStats()
        ii.insert_messages(conn, [_sample_message()], False, stats)
        conn.rollback()
        assert stats.inserted_messages == 1
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    finally:
        conn.close()


def _message_count(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    finally:
        conn.close()


def test_run_import_dry_run_and_execute(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = make_stage_db(tmp_path / "t.sqlite")
    xlsx = _ifttt_xlsx(tmp_path / "ifttt.xlsx")

    assert (
        ii.run_import(db_path, [str(xlsx)], execute=False, preview=0, backup_dir=tmp_path / "b")
        == 0
    )
    assert "DRY-RUN" in capsys.readouterr().out
    assert _message_count(db_path) == 0

    assert (
        ii.run_import(
            db_path, [str(xlsx)], execute=True, no_backup=True, preview=0, backup_dir=tmp_path / "b"
        )
        == 0
    )
    assert _message_count(db_path) == 4

    # Re-running dedupes everything against the DB.
    assert (
        ii.run_import(
            db_path, [str(xlsx)], execute=True, no_backup=True, preview=0, backup_dir=tmp_path / "b"
        )
        == 0
    )
    assert _message_count(db_path) == 4


def test_run_import_backup_only(tmp_path: Path) -> None:
    db_path = make_stage_db(tmp_path / "t.sqlite")
    backup_dir = tmp_path / "backups"
    assert ii.run_import(db_path, [], execute=False, backup_only=True, backup_dir=backup_dir) == 0
    assert list(backup_dir.glob("*.sqlite"))


def test_run_import_validation(tmp_path: Path) -> None:
    db_path = make_stage_db(tmp_path / "t.sqlite")
    assert (
        ii.run_import(db_path, [str(tmp_path / "*.xlsx")], execute=False, backup_dir=tmp_path) == 1
    )  # no files match
    assert (
        ii.run_import(tmp_path / "nope.sqlite", [str(tmp_path)], execute=False, backup_dir=tmp_path)
        == 1
    )  # missing DB
    assert (
        ii.run_import(db_path, [], execute=True, backup_only=True, backup_dir=tmp_path) == 1
    )  # conflicting flags


def test_discover_files_dedupes(tmp_path: Path) -> None:
    a = tmp_path / "a.xlsx"
    a.touch()
    found = ii.discover_files([str(a), str(tmp_path / "*.xlsx")])
    assert found == [a]


@pytest.mark.parametrize("value", [None, "", "abc"])
def test_normalize_address_blank_cases(value: object) -> None:
    # The importer now shares the canonical normalizer (float ".0" bug fixed).
    from sms_toolkit.normalize import normalize_address

    assert normalize_address(value) == ""


def test_normalize_address_zero() -> None:
    from sms_toolkit.normalize import normalize_address

    assert normalize_address(0) == "0"
