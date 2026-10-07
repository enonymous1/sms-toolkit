"""Call-log pipeline variant: tags, keying, row mapping, DDL, and reporting."""

from __future__ import annotations

import sqlite3
from typing import Any

from sms_toolkit import hashing
from sms_toolkit.pipeline.processor import DedupProcessor
from sms_toolkit.pipeline.variants import MappedElement, PipelineVariant

_CALLS_SCHEMA = """(
    call_hash TEXT PRIMARY KEY,
    number TEXT,
    duration INTEGER,
    date TEXT,
    type TEXT,
    presentation TEXT,
    readable_date TEXT,
    contact_name TEXT
)"""


def _calls_key(elem: Any) -> str:
    attrib = elem.attrib
    return hashing.call_key(
        attrib.get("number", ""),
        attrib.get("date", ""),
        attrib.get("duration", ""),
        attrib.get("type", ""),
    )


def _map_call_element(elem: Any) -> MappedElement:
    attrib = elem.attrib
    number = attrib.get("number", "")
    duration = attrib.get("duration", "0")
    date = attrib.get("date", "")
    call_type = attrib.get("type", "")
    presentation = attrib.get("presentation", "")
    readable_date = attrib.get("readable_date", "")
    contact_name = attrib.get("contact_name", "")

    try:
        duration_int = int(duration)
    except (ValueError, TypeError):
        duration_int = 0

    return MappedElement(
        key=_calls_key(elem),
        csv_row=(number, duration, date, call_type, presentation, readable_date, contact_name),
        message_values=(
            number,
            duration_int,
            date,
            call_type,
            presentation,
            readable_date,
            contact_name,
        ),
    )


def _ensure_calls_db(conn: sqlite3.Connection, preserve: bool) -> None:
    qualifier = "IF NOT EXISTS " if preserve else ""
    if not preserve:
        conn.execute("DROP TABLE IF EXISTS calls")
    conn.execute(f"CREATE TABLE {qualifier}calls {_CALLS_SCHEMA}")


def _calls_format_counts(processor: DedupProcessor) -> dict[str, str]:
    return {
        "calls": f"{processor.tag_counts.get('call', 0):,}",
        "dup": f"{processor.duplicate_rate * 100:.1f}%",
    }


def _calls_summary_extra(processor: DedupProcessor) -> list[str]:
    return []


CALLS_VARIANT = PipelineVariant(
    name="calls",
    tags=("call",),
    parent_tag="calls",
    xml_root_tag="calls",
    csv_header=(
        "number",
        "duration",
        "date",
        "type",
        "presentation",
        "readable_date",
        "contact_name",
    ),
    log_filename="calls_processing_log.txt",
    summary_title="CALLS PROCESSING SUMMARY",
    starting_message="Starting Calls Backup Analyzer",
    total_label="Total calls processed:",
    item_noun="calls",
    default_xml_name="Master_Calls.xml",
    default_csv_name="calls.csv",
    default_db_name="calls_db.sqlite",
    hash_table="calls",
    hash_column="call_hash",
    message_insert_sql="INSERT OR IGNORE INTO calls VALUES (?,?,?,?,?,?,?,?)",
    key_fn=_calls_key,
    map_element=_map_call_element,
    ensure_db=_ensure_calls_db,
    format_counts=_calls_format_counts,
    summary_extra=_calls_summary_extra,
)
