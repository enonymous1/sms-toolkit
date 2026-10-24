"""Tests for the diag subcommand."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sms_toolkit import cli
from sms_toolkit.diag import check_database, check_optional_dependencies, run_diag


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "sms_db.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (msg_hash TEXT PRIMARY KEY, tag TEXT, date INTEGER)")
    conn.execute("CREATE TABLE mms_parts (msg_hash TEXT, seq INTEGER, ct TEXT, data TEXT)")
    conn.execute("CREATE TABLE mms_addrs (msg_hash TEXT, address TEXT, type TEXT)")
    conn.execute("CREATE TABLE contacts (normalized_address TEXT PRIMARY KEY)")
    conn.executemany(
        "INSERT INTO messages VALUES (?, ?, ?)",
        [("h1", "sms", 1685712000000), ("h2", "mms", 1685712060000)],
    )
    conn.execute("INSERT INTO mms_parts VALUES ('h2', 1, 'image/jpeg', 'aGVsbG8=')")
    conn.execute("INSERT INTO mms_addrs VALUES ('h2', '5550101234', '137')")
    conn.execute("INSERT INTO contacts VALUES ('5550101234')")
    conn.commit()
    conn.close()
    return path


def test_check_database_healthy(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_diag(db_path) == 0
    out = capsys.readouterr().out
    assert "All checks passed." in out
    assert "[OK  ] sms_db messages: 2 rows" in out
    assert "sms=1" in out and "mms=1" in out
    assert "1 with image data" in out
    assert "1 rows" in out  # contacts


def test_check_database_missing(tmp_path: Path) -> None:
    checks = check_database(tmp_path / "nope.sqlite", "sms_db")
    assert len(checks) == 1
    assert checks[0].ok is False
    assert run_diag(tmp_path / "nope.sqlite") == 1


def test_check_database_not_a_db(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.sqlite"
    bad.write_text("this is not a database", encoding="utf-8")
    assert run_diag(bad) == 1
    assert "FAIL" in capsys.readouterr().out


def test_check_database_no_messages_table(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite"
    sqlite3.connect(path).close()
    checks = check_database(path, "sms_db")
    assert any(c.name == "sms_db messages table" and not c.ok for c in checks)


def test_check_database_text_date_column(tmp_path: Path) -> None:
    """The date column is TEXT in some databases; diag must not crash."""
    path = tmp_path / "textdate.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (msg_hash TEXT PRIMARY KEY, tag TEXT, date TEXT)")
    conn.execute("INSERT INTO messages VALUES ('h1', 'sms', '1685712000000')")
    conn.commit()
    conn.close()
    checks = check_database(path, "sms_db")
    assert all(c.ok for c in checks)
    assert any(c.name == "sms_db date range" and "2023" in c.detail for c in checks)
    path = tmp_path / "empty.sqlite"
    sqlite3.connect(path).close()
    checks = check_database(path, "sms_db")
    assert any(c.name == "sms_db messages table" and not c.ok for c in checks)


def test_check_optional_dependencies_runs() -> None:
    checks = check_optional_dependencies()
    assert len(checks) == 3
    assert all(c.ok for c in checks)  # informational only
    assert any("flask" in c.name for c in checks)


def test_diag_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--root", str(tmp_path), "diag", "--db", str(tmp_path / "nope.sqlite")]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_diag_cli_help() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["diag", "--help"])
    assert exc.value.code == 0
