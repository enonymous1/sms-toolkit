"""Tests for sms_toolkit.db (incl. differential checks on backup behavior)."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

from sms_toolkit import db
from sms_toolkit.config import PipelineConfig

EXPECTED_COLUMNS = [
    "msg_hash",
    "tag",
    "address",
    "normalized_address",
    "date",
    "normalized_date_ms",
    "body",
    "type",
    "readable_date",
    "contact_name",
]


def _make_db(path: Path, rows: int = 3) -> Path:
    conn = sqlite3.connect(path)
    db.ensure_messages_table(conn)
    conn.executemany(
        "INSERT INTO messages (msg_hash, tag, address, body) VALUES (?, ?, ?, ?)",
        [(f"hash{i}", "sms", f"555000{i}", f"body {i}") for i in range(rows)],
    )
    conn.commit()
    conn.close()
    return path


def test_connect_db_applies_configured_pragmas(tmp_path: Path) -> None:
    conn = db.connect_db(tmp_path / "t.sqlite")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "off"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 0
        assert conn.execute("PRAGMA locking_mode").fetchone()[0] == "exclusive"
    finally:
        conn.close()


def test_connect_db_honors_pipeline_config(tmp_path: Path) -> None:
    cfg = PipelineConfig(sqlite_journal_mode="WAL", sqlite_synchronous="FULL")
    conn = db.connect_db(tmp_path / "t.sqlite", cfg)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    finally:
        conn.close()


def test_ensure_messages_table_idempotent(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "t.sqlite")
    try:
        db.ensure_messages_table(conn)
        conn.execute("INSERT INTO messages (msg_hash) VALUES ('abc')")
        conn.commit()
        db.ensure_messages_table(conn)  # second call must not wipe data
        conn.commit()
        assert db.table_columns(conn, "messages") == EXPECTED_COLUMNS
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    finally:
        conn.close()


def test_ensure_messages_table_migrates_old_schema(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "t.sqlite")
    try:
        # Pre-normalization schema: all columns except the two normalized ones.
        conn.execute(
            "CREATE TABLE messages (msg_hash TEXT PRIMARY KEY, tag TEXT, address TEXT,"
            " date TEXT, body TEXT, type TEXT, readable_date TEXT, contact_name TEXT)"
        )
        conn.execute(
            "INSERT INTO messages VALUES ('abc', 'sms', '5550001', 'd', 'b', '1', 'r', 'c')"
        )
        conn.commit()
        db.ensure_messages_table(conn)
        conn.commit()
        # ALTER TABLE appends the new columns (same as the originals); data preserved.
        assert db.table_columns(conn, "messages") == [
            "msg_hash",
            "tag",
            "address",
            "date",
            "body",
            "type",
            "readable_date",
            "contact_name",
            "normalized_address",
            "normalized_date_ms",
        ]
        assert conn.execute("SELECT address FROM messages").fetchone()[0] == "5550001"
    finally:
        conn.close()


def test_rebuild_messages_table_is_fresh(tmp_path: Path) -> None:
    path = _make_db(tmp_path / "t.sqlite", rows=2)
    conn = sqlite3.connect(path)
    try:
        db.rebuild_messages_table(conn)
        assert db.table_columns(conn, "messages") == EXPECTED_COLUMNS
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    finally:
        conn.close()


def test_create_backup_roundtrip(tmp_path: Path) -> None:
    src = _make_db(tmp_path / "sms_db.sqlite", rows=2)
    result = db.create_backup(src, tmp_path / "backups", label="pre-normalize")
    assert result.integrity_ok
    assert result.path.exists()
    assert result.size_bytes > 0
    assert "pre-normalize" in result.path.name

    dst = sqlite3.connect(result.path)
    try:
        assert dst.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
        assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        dst.close()


def test_create_backup_sanitizes_label(tmp_path: Path) -> None:
    src = _make_db(tmp_path / "sms_db.sqlite", rows=1)
    backup_dir = tmp_path / "backups"
    result = db.create_backup(src, backup_dir, label="../../evil label!")
    # The label must not escape the backup directory (no path separators survive).
    assert result.path.parent == backup_dir
    assert "/" not in result.path.name
    assert "\\" not in result.path.name
    assert result.path.exists()


def test_prune_old_backups_keeps_newest(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    db_path = tmp_path / "sms_db.sqlite"
    db_path.touch()
    now = time.time()
    for i in range(4):
        p = backup_dir / f"sms_db_pre-import_2020010{i}_000000.sqlite"
        p.write_bytes(b"x")
        os.utime(p, (now - i * 100, now - i * 100))

    removed = db.prune_old_backups(backup_dir, db_path, keep=2)
    assert removed == 2
    remaining = sorted(p.name for p in backup_dir.glob("*.sqlite"))
    assert remaining == [
        "sms_db_pre-import_20200100_000000.sqlite",
        "sms_db_pre-import_20200101_000000.sqlite",
    ]


def test_load_hashes(tmp_path: Path) -> None:
    path = _make_db(tmp_path / "t.sqlite", rows=2)
    conn = sqlite3.connect(path)
    try:
        conn.execute("INSERT INTO messages (msg_hash) VALUES (NULL)")
        conn.commit()
        assert db.load_hashes(conn) == {"hash0", "hash1"}
    finally:
        conn.close()


def test_load_hashes_missing_table_raises(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "t.sqlite")
    try:
        with pytest.raises(sqlite3.OperationalError):
            db.load_hashes(conn)
    finally:
        conn.close()


def test_load_hashes_rejects_bad_identifiers(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "t.sqlite")
    try:
        with pytest.raises(ValueError):
            db.load_hashes(conn, table="messages; DROP TABLE messages; --")
    finally:
        conn.close()
