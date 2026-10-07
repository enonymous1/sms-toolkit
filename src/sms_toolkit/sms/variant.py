"""SMS/MMS pipeline variant: tags, keying, row mapping, DDL, and reporting."""

from __future__ import annotations

import sqlite3
from typing import Any

from sms_toolkit import hashing, normalize
from sms_toolkit.db import ensure_messages_table, rebuild_messages_table
from sms_toolkit.pipeline.processor import DedupProcessor
from sms_toolkit.pipeline.variants import MappedElement, PipelineVariant

_MMS_PARTS_SCHEMA = """(
    msg_hash TEXT,
    seq INTEGER,
    ct TEXT,
    name TEXT,
    text TEXT,
    data TEXT,
    FOREIGN KEY(msg_hash) REFERENCES messages(msg_hash)
)"""

_MMS_ADDRS_SCHEMA = """(
    msg_hash TEXT,
    address TEXT,
    type TEXT,
    charset TEXT,
    FOREIGN KEY(msg_hash) REFERENCES messages(msg_hash)
)"""


def _sms_key(elem: Any) -> str:
    attrib = elem.attrib
    if elem.tag == "sms":
        return hashing.sms_key(
            attrib.get("address", ""),
            attrib.get("date", ""),
            attrib.get("body", ""),
            attrib.get("type", ""),
        )
    return hashing.mms_key(
        attrib.get("address", ""), attrib.get("date", ""), attrib.get("m_type", "")
    )


def _map_sms_element(elem: Any) -> MappedElement:
    attrib = elem.attrib
    tag = elem.tag
    address = attrib.get("address", "")
    date = attrib.get("date", "")
    readable_date = attrib.get("readable_date", "")
    contact_name = attrib.get("contact_name", "")

    # SMS has body, MMS content is in parts.
    body = attrib.get("body", "[MMS]" if tag == "mms" else "")
    msg_type = attrib.get("type" if tag == "sms" else "m_type", "")

    part_values: list[tuple[Any, ...]] = []
    addr_values: list[tuple[Any, ...]] = []
    if tag == "mms":
        # Parts (attachments/text fragments).
        parts = elem.find("parts")
        if parts is not None:
            for part in parts:
                p_attr = part.attrib
                part_values.append(
                    (
                        p_attr.get("seq"),
                        p_attr.get("ct"),
                        p_attr.get("name"),
                        p_attr.get("text"),
                        p_attr.get("data"),  # Base64 data if present
                    )
                )
        # Addresses (recipients).
        addrs = elem.find("addrs")
        if addrs is not None:
            for addr in addrs:
                a_attr = addr.attrib
                addr_values.append(
                    (a_attr.get("address"), a_attr.get("type"), a_attr.get("charset"))
                )

    return MappedElement(
        key=_sms_key(elem),
        csv_row=(tag, address, date, body, msg_type, readable_date, contact_name),
        message_values=(
            tag,
            address,
            normalize.normalize_address(address),
            date,
            normalize.normalize_date_ms(date),
            body,
            msg_type,
            readable_date,
            contact_name,
        ),
        part_values=tuple(part_values),
        addr_values=tuple(addr_values),
    )


def _ensure_sms_db(conn: sqlite3.Connection, preserve: bool) -> None:
    qualifier = "IF NOT EXISTS " if preserve else ""
    if preserve:
        ensure_messages_table(conn)
    else:
        rebuild_messages_table(conn)
        conn.execute("DROP TABLE IF EXISTS mms_parts")
        conn.execute("DROP TABLE IF EXISTS mms_addrs")
    conn.execute(f"CREATE TABLE {qualifier}mms_parts {_MMS_PARTS_SCHEMA}")
    conn.execute(f"CREATE TABLE {qualifier}mms_addrs {_MMS_ADDRS_SCHEMA}")


def _sms_format_counts(processor: DedupProcessor) -> dict[str, str]:
    return {
        "sms": f"{processor.tag_counts.get('sms', 0):,}",
        "mms": f"{processor.tag_counts.get('mms', 0):,}",
        "dup": f"{processor.duplicate_rate * 100:.1f}%",
    }


def _sms_summary_extra(processor: DedupProcessor) -> list[str]:
    return [
        f"  - SMS: {processor.tag_counts.get('sms', 0):,}",
        f"  - MMS: {processor.tag_counts.get('mms', 0):,}",
    ]


SMS_VARIANT = PipelineVariant(
    name="sms",
    tags=("sms", "mms"),
    parent_tag="smses",
    xml_root_tag="smses",
    csv_header=("tag", "address", "date", "body", "type", "readable_date", "contact_name"),
    log_filename="processing_log.txt",
    summary_title="SMS PROCESSING SUMMARY",
    starting_message="Starting SMS Backup Analyzer",
    total_label="Total items processed:",
    item_noun="messages",
    default_xml_name="Master_Backup.xml",
    default_csv_name="messages.csv",
    default_db_name="sms_db.sqlite",
    hash_table="messages",
    hash_column="msg_hash",
    message_insert_sql=(
        "INSERT OR IGNORE INTO messages (msg_hash, tag, address, normalized_address, date,"
        " normalized_date_ms, body, type, readable_date, contact_name)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)"
    ),
    part_insert_sql="INSERT INTO mms_parts VALUES (?,?,?,?,?,?)",
    addr_insert_sql="INSERT INTO mms_addrs VALUES (?,?,?,?)",
    key_fn=_sms_key,
    map_element=_map_sms_element,
    ensure_db=_ensure_sms_db,
    format_counts=_sms_format_counts,
    summary_extra=_sms_summary_extra,
)
