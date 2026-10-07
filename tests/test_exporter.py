"""Differential tests: pipeline Exporter vs the original DataExporter/CallDataExporter.

Feeds the same fixture elements through both and compares the outputs
byte-for-byte (XML, CSV) and row-for-row (SQLite).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from lxml import etree

from sms_toolkit.calls.variant import CALLS_VARIANT
from sms_toolkit.pipeline.exporter import Exporter
from sms_toolkit.sms.variant import SMS_VARIANT
from tests.diff_helpers import (
    CALLS_FIXTURE_XML,
    ORIGINAL_ROOT,
    SMS_FIXTURE_XML,
    import_original_module,
)

SMS_SRC = ORIGINAL_ROOT / "sms-backup-analyzer" / "src"
CALLS_SRC = ORIGINAL_ROOT / "calls-backup-analyzer" / "src"

sms_analyzer_mod = import_original_module("orig_sms_analyzer", SMS_SRC / "analyzer.py")
calls_analyzer_mod = import_original_module("orig_calls_analyzer", CALLS_SRC / "analyzer.py")


def _elements(xml_text: str, tags: tuple[str, ...]) -> list[Any]:
    root = etree.fromstring(xml_text.encode("utf-8"))
    return [el for el in root if el.tag in tags]


def _db_dump(db_path: Path, tables: list[str]) -> dict[str, list[tuple[Any, ...]]]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in tables
        }
    finally:
        conn.close()


def _run_orig_sms(workdir: Path, preserve: bool) -> None:
    exporter = sms_analyzer_mod.DataExporter(
        xml_file=str(workdir / "Master_Backup.xml"),
        db_file=str(workdir / "sms_db.sqlite"),
        csv_file=str(workdir / "messages.csv"),
        preserve_data=preserve,
    )
    for el in _elements(SMS_FIXTURE_XML, ("sms", "mms")):
        exporter.write_message(el)
    exporter.close()


def _run_new_sms(workdir: Path, preserve: bool) -> None:
    with Exporter(
        SMS_VARIANT,
        xml_path=workdir / "Master_Backup.xml",
        csv_path=workdir / "messages.csv",
        db_path=workdir / "sms_db.sqlite",
        preserve_data=preserve,
    ) as exporter:
        for el in _elements(SMS_FIXTURE_XML, ("sms", "mms")):
            exporter.write(el)


def _run_orig_calls(workdir: Path, preserve: bool) -> None:
    exporter = calls_analyzer_mod.CallDataExporter(
        xml_file=str(workdir / "Master_Calls.xml"),
        db_file=str(workdir / "calls_db.sqlite"),
        csv_file=str(workdir / "calls.csv"),
        preserve_data=preserve,
    )
    for el in _elements(CALLS_FIXTURE_XML, ("call",)):
        exporter.write_call(el)
    exporter.close()


def _run_new_calls(workdir: Path, preserve: bool) -> None:
    with Exporter(
        CALLS_VARIANT,
        xml_path=workdir / "Master_Calls.xml",
        csv_path=workdir / "calls.csv",
        db_path=workdir / "calls_db.sqlite",
        preserve_data=preserve,
    ) as exporter:
        for el in _elements(CALLS_FIXTURE_XML, ("call",)):
            exporter.write(el)


def _assert_dirs_equal(orig_dir: Path, new_dir: Path, files: list[str]) -> None:
    for name in files:
        assert (new_dir / name).read_bytes() == (orig_dir / name).read_bytes(), name


def test_sms_exporter_matches_original_fresh_and_append(tmp_path: Path) -> None:
    orig_dir = tmp_path / "orig"
    new_dir = tmp_path / "new"
    orig_dir.mkdir()
    new_dir.mkdir()

    # Fresh mode.
    _run_orig_sms(orig_dir, preserve=False)
    _run_new_sms(new_dir, preserve=False)
    _assert_dirs_equal(orig_dir, new_dir, ["Master_Backup.xml", "messages.csv"])
    tables = ["messages", "mms_parts", "mms_addrs"]
    assert _db_dump(new_dir / "sms_db.sqlite", tables) == _db_dump(
        orig_dir / "sms_db.sqlite", tables
    )

    # Append mode on top of the fresh outputs.
    _run_orig_sms(orig_dir, preserve=True)
    _run_new_sms(new_dir, preserve=True)
    _assert_dirs_equal(orig_dir, new_dir, ["Master_Backup.xml", "messages.csv"])
    assert _db_dump(new_dir / "sms_db.sqlite", tables) == _db_dump(
        orig_dir / "sms_db.sqlite", tables
    )


def test_calls_exporter_matches_original_fresh_and_append(tmp_path: Path) -> None:
    orig_dir = tmp_path / "orig"
    new_dir = tmp_path / "new"
    orig_dir.mkdir()
    new_dir.mkdir()

    _run_orig_calls(orig_dir, preserve=False)
    _run_new_calls(new_dir, preserve=False)
    _assert_dirs_equal(orig_dir, new_dir, ["Master_Calls.xml", "calls.csv"])
    tables = ["calls"]
    assert _db_dump(new_dir / "calls_db.sqlite", tables) == _db_dump(
        orig_dir / "calls_db.sqlite", tables
    )

    _run_orig_calls(orig_dir, preserve=True)
    _run_new_calls(new_dir, preserve=True)
    _assert_dirs_equal(orig_dir, new_dir, ["Master_Calls.xml", "calls.csv"])
    assert _db_dump(new_dir / "calls_db.sqlite", tables) == _db_dump(
        orig_dir / "calls_db.sqlite", tables
    )


def test_exporter_load_hashes_roundtrip(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    with Exporter(
        SMS_VARIANT,
        xml_path=workdir / "out.xml",
        csv_path=workdir / "out.csv",
        db_path=workdir / "out.sqlite",
        preserve_data=False,
    ) as exporter:
        for el in _elements(SMS_FIXTURE_XML, ("sms", "mms")):
            exporter.write(el)
        hashes = exporter.load_hashes()
    # 3 unique elements in the fixture (one sms is a duplicate).
    assert len(hashes) == 3
    assert all(len(h) == 32 for h in hashes)
