"""Tests for stages.build_contacts (with differentials vs the original)."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from sms_toolkit.stages import build_contacts as bc
from tests.diff_helpers import ORIGINAL_ROOT, import_original_module
from tests.stage_fixtures import SMS_ROWS, make_stage_db

ORIG_SRC = ORIGINAL_ROOT / "sms-backup-analyzer" / "src"
orig_mod = import_original_module("orig_build_contacts", ORIG_SRC / "build_contacts_table.py")

MULTI_ROW: dict[str, Any] = {
    "msg_hash": "hash-multi",
    "tag": "mms",
    "address": "5550101234~15551234567",
    "normalized_address": None,
    "date": "1685712000000",
    "normalized_date_ms": None,
    "body": "group",
    "type": "1",
    "readable_date": "June 1, 2023 12:00:00 PM",
    "contact_name": "Justin, Sara",
}

UNKNOWN_ROW: dict[str, Any] = {
    "msg_hash": "hash-unknown",
    "tag": "sms",
    "address": "19998887777",
    "normalized_address": None,
    "date": "1685712000000",
    "normalized_date_ms": None,
    "body": "spam",
    "type": "1",
    "readable_date": "June 1, 2023 12:00:00 PM",
    "contact_name": "(Unknown)",
}


def _contacts_db(path: Path, rows: Sequence[Mapping[str, Any]] | None = None) -> Path:
    return make_stage_db(path, messages=[*(rows or SMS_ROWS), MULTI_ROW, UNKNOWN_ROW])


def _plain_counts(
    result: tuple[dict[str, dict[str, int]], dict[str, set[str]]],
) -> tuple[dict[str, dict[str, int]], dict[str, list[str]]]:
    name_counts, raw_addresses = result
    return (
        {k: dict(v) for k, v in name_counts.items()},
        {k: sorted(v) for k, v in raw_addresses.items()},
    )


def test_collect_name_counts_matches_original(tmp_path: Path) -> None:
    db_path = _contacts_db(tmp_path / "t.sqlite")
    conn = sqlite3.connect(db_path)
    try:
        assert _plain_counts(bc.collect_name_counts(conn)) == _plain_counts(
            orig_mod.collect_name_counts(conn)
        )
    finally:
        conn.close()


def test_collect_name_counts_multi_address(tmp_path: Path) -> None:
    db_path = _contacts_db(tmp_path / "t.sqlite")
    conn = sqlite3.connect(db_path)
    try:
        name_counts, raw_addresses = bc.collect_name_counts(conn)
    finally:
        conn.close()
    # "~"-delimited addresses pair up with ","-delimited names.
    # ("15551234567" is 11 digits starting with 1 -> "5551234567".)
    assert name_counts["5550101234"]["Justin"] == 3  # 2 sms + 1 mms pairing
    assert name_counts["5551234567"]["Sara"] == 2  # 1 sms + 1 mms pairing
    assert "19998887777" not in name_counts  # "(Unknown)" is skipped
    assert raw_addresses["5550101234"] == {"(555) 010-1234", "15550101234", "5550101234"}


def test_choose_best_name_matches_original() -> None:
    cases = [
        {"Justin": 3, "justin": 1},
        {"Sara": 1, "Sarah": 1},
        {"Al": 2, "Alexander": 2},
    ]
    for counts in cases:
        assert bc.choose_best_name(counts) == orig_mod.choose_best_name(counts)


def _contacts_rows(db_path: Path) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT normalized_address, contact_name, message_count,"
            " raw_addresses_json, raw_contact_names_json"
            " FROM contacts ORDER BY normalized_address"
        ).fetchall()
    finally:
        conn.close()


def test_build_contacts_matches_original(tmp_path: Path) -> None:
    orig_db = _contacts_db(tmp_path / "orig.sqlite")
    new_db = _contacts_db(tmp_path / "new.sqlite")

    assert bc.build_contacts(new_db, dry_run=False) == orig_mod.build_contacts(
        orig_db, dry_run=False
    )
    # updated_at is wall-clock; excluded from the comparison.
    assert _contacts_rows(new_db) == _contacts_rows(orig_db)


def test_build_contacts_dry_run_writes_nothing(tmp_path: Path) -> None:
    db_path = _contacts_db(tmp_path / "t.sqlite")
    count = bc.build_contacts(db_path, dry_run=True)
    assert count == 2
    assert _contacts_rows(db_path) == []


def test_build_contacts_upsert(tmp_path: Path) -> None:
    db_path = _contacts_db(tmp_path / "t.sqlite")
    assert bc.build_contacts(db_path, dry_run=False) == 2
    # Change a name and rebuild: the UPSERT updates the row.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE messages SET contact_name = 'Justin!' WHERE msg_hash = 'hash-sms-1'")
        conn.commit()
    finally:
        conn.close()
    assert bc.build_contacts(db_path, dry_run=False) == 2
    rows = {r[0]: r[1] for r in _contacts_rows(db_path)}
    assert rows["5550101234"] == "Justin"  # still most frequent


def test_run_build_contacts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = _contacts_db(tmp_path / "t.sqlite")
    assert bc.run_build_contacts(db_path, dry_run=True) == 0
    assert "[DRY-RUN] contacts resolved: 2" in capsys.readouterr().out
    assert _contacts_rows(db_path) == []

    assert bc.run_build_contacts(db_path, dry_run=False) == 0
    assert "[WRITE] contacts resolved: 2" in capsys.readouterr().out
    assert len(_contacts_rows(db_path)) == 2

    assert bc.run_build_contacts(tmp_path / "nope.sqlite", dry_run=False) == 1
