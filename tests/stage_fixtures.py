"""Shared fixtures for the stages tests: small messages databases."""

from __future__ import annotations

import base64
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import openpyxl

from sms_toolkit import db as db_mod

MESSAGE_COLUMNS = (
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
)

SMS_ROWS: list[dict[str, Any]] = [
    {
        "msg_hash": "hash-sms-1",
        "tag": "sms",
        "address": "(571) 643-5424",
        "normalized_address": None,
        "date": "1685712000000",
        "normalized_date_ms": None,
        "body": "hello",
        "type": "1",
        "readable_date": "June 1, 2023 12:00:00 PM",
        "contact_name": "Justin",
    },
    {
        "msg_hash": "hash-sms-2",
        "tag": "sms",
        "address": "15716435424",
        "normalized_address": None,
        "date": "1685712060",  # 10-digit seconds
        "normalized_date_ms": None,
        "body": "world",
        "type": "2",
        "readable_date": "June 1, 2023 12:01:00 PM",
        "contact_name": "Justin",
    },
    {
        "msg_hash": "hash-mms-1",
        "tag": "mms",
        "address": "5716435424",
        "normalized_address": "5716435424",
        "date": "1672531200000",  # 2023-01-01
        "normalized_date_ms": 1672531200000,
        "body": "[MMS]",
        "type": "1",
        "readable_date": "January 1, 2023 12:00:00 AM",
        "contact_name": "",
    },
    {
        "msg_hash": "hash-sms-3",
        "tag": "sms",
        "address": "15551234567",
        "normalized_address": None,
        "date": "not-a-date",
        "normalized_date_ms": None,
        "body": "old one",
        "type": "1",
        "readable_date": "March 5, 2021 3:04:05 PM",
        "contact_name": "Sara",
    },
]

MMS_PART_ROWS: list[dict[str, Any]] = [
    {
        "msg_hash": "hash-mms-1",
        "seq": 0,
        "ct": "text/plain",
        "name": "text_0.txt",
        "text": "mms text",
        "data": None,
    },
    {
        "msg_hash": "hash-mms-1",
        "seq": 1,
        "ct": "image/jpeg",
        "name": "img.jpg",
        "text": None,
        "data": base64.b64encode(b"fake-jpeg-bytes").decode("ascii"),
    },
]

MMS_ADDR_ROWS: list[dict[str, Any]] = [
    {"msg_hash": "hash-mms-1", "address": "5716435424", "type": "137", "charset": "106"},
    {"msg_hash": "hash-mms-1", "address": "15551234567", "type": "151", "charset": "106"},
]


MMS_PARTS_DDL = """
CREATE TABLE IF NOT EXISTS mms_parts (
    msg_hash TEXT,
    seq INTEGER,
    ct TEXT,
    name TEXT,
    text TEXT,
    data TEXT
)
"""

MMS_ADDRS_DDL = """
CREATE TABLE IF NOT EXISTS mms_addrs (
    msg_hash TEXT,
    address TEXT,
    type TEXT,
    charset TEXT
)
"""


def make_stage_db(
    path: Path,
    messages: Sequence[Mapping[str, Any]] = (),
    parts: Sequence[Mapping[str, Any]] = (),
    addrs: Sequence[Mapping[str, Any]] = (),
) -> Path:
    """Create a messages database with the 10-column schema and sample rows."""
    conn = sqlite3.connect(path)
    try:
        db_mod.ensure_messages_table(conn)
        conn.execute(MMS_PARTS_DDL)
        conn.execute(MMS_ADDRS_DDL)
        for row in messages:
            conn.execute(
                f"INSERT INTO messages ({', '.join(MESSAGE_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in MESSAGE_COLUMNS)})",
                [row[col] for col in MESSAGE_COLUMNS],
            )
        for row in parts:
            conn.execute(
                "INSERT INTO mms_parts (msg_hash, seq, ct, name, text, data)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [row["msg_hash"], row["seq"], row["ct"], row["name"], row["text"], row["data"]],
            )
        for row in addrs:
            conn.execute(
                "INSERT INTO mms_addrs (msg_hash, address, type, charset) VALUES (?, ?, ?, ?)",
                [row["msg_hash"], row["address"], row["type"], row["charset"]],
            )
        conn.commit()
    finally:
        conn.close()
    return path


def dump_table(path: Path, table: str) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(path)
    try:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        order = ", ".join(cols) if cols else "rowid"
        return [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]
    finally:
        conn.close()


def make_ifttt_xlsx(path: Path) -> Path:
    """Create a small IFTTT-style Excel export for importer tests."""
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["March 28, 2021 at 3:04PM", "5716435424", "Justin", "hello there"])
    ws.append(["March 28, 2021 at 3:05 PM", 5716435424.0, "world"])
    ws.append(["not a timestamp", "5716435424", "x"])
    ws.append(["March 28, 2021 at 4:00PM", "abc", "Bob", "no phone"])
    ws.append(
        [
            "March 29, 2021 at 9:00AM",
            "15551234567",
            "Sara",
            "first",
            "March 29, 2021 at 9:05AM",
            "15551234567",
            "second",
        ]
    )
    wb.save(path)
    return path
