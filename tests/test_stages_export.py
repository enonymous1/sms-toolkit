"""Tests for stages.export_attachments and stages.export_year (with differentials)."""

from __future__ import annotations

import base64
import dataclasses
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from sms_toolkit.stages import export_attachments as ea
from sms_toolkit.stages import export_year as ey
from tests.diff_helpers import ORIGINAL_ROOT, import_original_module
from tests.stage_fixtures import (
    MMS_ADDR_ROWS,
    MMS_PART_ROWS,
    SMS_ROWS,
    dump_table,
    make_stage_db,
)

ORIG_SRC = ORIGINAL_ROOT / "sms-backup-analyzer" / "src"
orig_attach_mod = import_original_module(
    "orig_export_attachments", ORIG_SRC / "export_db_csv_attachments.py"
)
orig_year_mod = import_original_module("orig_export_year", ORIG_SRC / "export_year_subset.py")

MERGE_VARIANT_ROW = {
    "msg_hash": "hash-sms-4",
    "tag": "sms",
    "address": "555-010-1234",
    "normalized_address": None,
    "date": "1685712120000",
    "normalized_date_ms": None,
    "body": "again",
    "type": "1",
    "readable_date": "June 1, 2023 12:02:00 PM",
    "contact_name": "justin",
}


def _export_src_db(path: Path) -> Path:
    return make_stage_db(
        path,
        messages=[*SMS_ROWS, MERGE_VARIANT_ROW],
        parts=MMS_PART_ROWS,
        addrs=MMS_ADDR_ROWS,
    )


def _contacts_db(path: Path) -> Path:
    from sms_toolkit.stages.build_contacts import ensure_contacts_table

    conn = sqlite3.connect(path)
    try:
        ensure_contacts_table(conn)
        conn.execute(
            "INSERT INTO contacts (normalized_address, contact_name, message_count)"
            " VALUES (?, ?, ?)",
            ("5550101234", "Justin", 5),
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _tree_files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _run_both(tmp_path: Path, **kwargs: Any) -> tuple[Path, Path]:
    src = _export_src_db(tmp_path / "src.sqlite")
    contacts = _contacts_db(tmp_path / "contacts.sqlite")
    orig_out, new_out = tmp_path / "orig", tmp_path / "new"
    orig_stats = orig_attach_mod.export_db(src, orig_out, contacts_db_path=contacts, **kwargs)
    new_stats = ea.export_db(src, new_out, contacts_db_path=contacts, **kwargs)
    assert dataclasses.asdict(new_stats) == dataclasses.asdict(orig_stats)
    return orig_out, new_out


def test_export_db_matches_original(tmp_path: Path) -> None:
    orig_out, new_out = _run_both(
        tmp_path,
        include_text_parts=False,
        summary_group_by="normalized",
        apply_contact_merge="no",
        write_merge_review=True,
        dry_run=False,
    )
    assert _tree_files(new_out) == _tree_files(orig_out)


def test_export_db_matches_original_with_merges(tmp_path: Path) -> None:
    orig_out, new_out = _run_both(
        tmp_path,
        include_text_parts=True,
        summary_group_by="normalized",
        apply_contact_merge="yes",
        write_merge_review=True,
        dry_run=False,
    )
    assert _tree_files(new_out) == _tree_files(orig_out)


def test_export_db_dry_run_matches_original(tmp_path: Path) -> None:
    src = _export_src_db(tmp_path / "src.sqlite")
    orig_out, new_out = tmp_path / "orig", tmp_path / "new"
    orig_stats = orig_attach_mod.export_db(src, orig_out, False, "normalized", "no", True, True)
    new_stats = ea.export_db(
        src,
        new_out,
        include_text_parts=False,
        summary_group_by="normalized",
        apply_contact_merge="no",
        write_merge_review=True,
        dry_run=True,
    )
    assert dataclasses.asdict(new_stats) == dataclasses.asdict(orig_stats)
    assert not orig_out.exists() and not new_out.exists()


def test_decode_mms_data() -> None:
    assert ea.decode_mms_data(None) == (None, "no_data")
    assert ea.decode_mms_data("") == (None, "no_data")
    assert ea.decode_mms_data("null") == (None, "no_data")
    payload = b"hello world"
    encoded = base64.b64encode(payload).decode("ascii")
    assert ea.decode_mms_data(encoded) == (payload, "base64")
    # Whitespace breaks strict decoding; the relaxed pass handles it.
    assert ea.decode_mms_data(encoded[:8] + " \n" + encoded[8:]) == (payload, "base64-relaxed")
    # Undecodable input falls back to raw UTF-8 bytes.
    assert ea.decode_mms_data("!!!") == (b"!!!", "raw-utf8")


def test_compose_attachment_filename() -> None:
    assert ea.compose_attachment_filename(1, 2, "img.jpg", "image/jpeg") == "part_1_2_img.jpg"
    assert ea.compose_attachment_filename(1, 2, None, "image/jpeg") == "part_1_2_part_1.jpg"
    assert ea.compose_attachment_filename(1, 2, "doc", "application/pdf") == ("part_1_2_doc.pdf")


def test_safe_name() -> None:
    assert ea.safe_name("a b/c") == "a_b_c"
    assert ea.safe_name("...") == "part"
    assert ea.safe_name(" img.jpg ") == "img.jpg"


def test_contact_merge_helpers() -> None:
    assert ea.contacts_are_similar("Justin", "justin")
    assert ea.contacts_are_similar("Sara", "sarah")  # substring rule
    assert ea.contacts_are_similar("Sara", "Bob") is False
    assert ea.contacts_are_similar("", "x") is False
    assert ea.choose_canonical_contact([("justin", 1), ("Justin", 2)]) == "Justin"
    merge_map, review_rows, groups = ea.build_contact_merge_map(
        {"5550101234": {"Justin": 2, "justin": 1, "Sara": 1}}
    )
    assert groups == 1
    assert merge_map[("5550101234", "justin")] == "Justin"
    assert review_rows[0][1] == "Justin"


def test_export_year_matches_original(tmp_path: Path) -> None:
    rows = [
        {
            **SMS_ROWS[0],
            "msg_hash": "y2021",
            "date": "1625097600000",
            "readable_date": "July 1, 2021 12:00:00 AM",
        },
        {
            **SMS_ROWS[0],
            "msg_hash": "y2020",
            "date": "1593561600000",
            "readable_date": "July 1, 2020 12:00:00 AM",
        },
        {**SMS_ROWS[3], "msg_hash": "y2021b"},  # non-numeric date, 2021 readable_date
        {
            **SMS_ROWS[2],
            "msg_hash": "mms2021",
            "date": "1625097600000",
            "readable_date": "July 1, 2021 12:00:00 AM",
        },
    ]
    parts = [{**p, "msg_hash": "mms2021"} for p in MMS_PART_ROWS]
    addrs = [{**a, "msg_hash": "mms2021"} for a in MMS_ADDR_ROWS]
    src = make_stage_db(tmp_path / "src.sqlite", messages=rows, parts=parts, addrs=addrs)

    orig_out, new_out = tmp_path / "orig.sqlite", tmp_path / "new.sqlite"
    orig_stats = orig_year_mod.export_year(src, orig_out, 2021, False)
    new_stats = ey.export_year(src, new_out, 2021, False)
    assert dataclasses.asdict(new_stats) == dataclasses.asdict(orig_stats)
    assert new_stats.selected_hashes == 3  # y2021, y2021b, mms2021
    for table in ("messages", "mms_parts", "mms_addrs"):
        assert dump_table(new_out, table) == dump_table(orig_out, table)


def test_export_year_dry_run(tmp_path: Path) -> None:
    src = make_stage_db(tmp_path / "src.sqlite", messages=SMS_ROWS)
    out = tmp_path / "out.sqlite"
    orig_stats = orig_year_mod.export_year(src, tmp_path / "o.sqlite", 2023, True)
    new_stats = ey.export_year(src, out, 2023, True)
    assert dataclasses.asdict(new_stats) == dataclasses.asdict(orig_stats)
    assert not out.exists()


def test_run_export_year_validation(tmp_path: Path) -> None:
    src = make_stage_db(tmp_path / "src.sqlite", messages=SMS_ROWS)
    assert ey.run_export_year(src, tmp_path / "o.sqlite", 1999) == 1
    assert ey.run_export_year(tmp_path / "nope.sqlite", tmp_path / "o.sqlite", 2021) == 1


def test_run_export_attachments_validation(tmp_path: Path) -> None:
    assert ea.run_export_attachments(tmp_path / "nope.sqlite", tmp_path / "out") == 1


@pytest.mark.parametrize("value", ["text/plain", "application/smil+xml", "TEXT/PLAIN "])
def test_is_textlike_mime_true(value: str) -> None:
    assert ea.is_textlike_mime(value) is True


@pytest.mark.parametrize("value", [None, "", "image/jpeg"])
def test_is_textlike_mime_false(value: str | None) -> None:
    assert ea.is_textlike_mime(value) is False
