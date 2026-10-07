"""Pipeline variant contract: everything that differs between the sms and calls pipelines.

A variant is a frozen dataclass of constants plus small functions for keying,
row mapping, DDL, and reporting. The generic machinery in
:mod:`sms_toolkit.pipeline.parser`, :mod:`sms_toolkit.pipeline.processor`,
:mod:`sms_toolkit.pipeline.exporter` and :mod:`sms_toolkit.pipeline.runner`
only ever talks to this contract.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoid a runtime circular import with processor.py
    from sms_toolkit.pipeline.processor import DedupProcessor


@dataclass(frozen=True)
class MappedElement:
    """Variant mapping of one XML element to its sink rows.

    ``message_values``/``part_values``/``addr_values`` exclude the dedup hash;
    the exporter prepends ``dedup_hash(key)`` when building the DB rows.
    """

    key: str
    csv_row: tuple[Any, ...]
    message_values: tuple[Any, ...]
    part_values: tuple[tuple[Any, ...], ...] = ()
    addr_values: tuple[tuple[Any, ...], ...] = ()


@dataclass(frozen=True)
class PipelineVariant:
    """Complete description of one pipeline flavor (sms or calls)."""

    name: str
    tags: tuple[str, ...]  # XML element tags to stream
    parent_tag: str  # root element tag of the backup files
    xml_root_tag: str  # root tag written to the master XML
    csv_header: tuple[str, ...]
    log_filename: str
    summary_title: str
    starting_message: str
    total_label: str
    item_noun: str  # "messages" / "calls", for log/summary text
    default_xml_name: str
    default_csv_name: str
    default_db_name: str
    hash_table: str
    hash_column: str
    message_insert_sql: str
    key_fn: Callable[[Any], str]
    map_element: Callable[[Any], MappedElement]
    ensure_db: Callable[[sqlite3.Connection, bool], None]  # (conn, preserve)
    format_counts: Callable[[DedupProcessor], dict[str, str]]  # tqdm postfix
    summary_extra: Callable[[DedupProcessor], list[str]]  # extra summary lines
    part_insert_sql: str | None = None
    addr_insert_sql: str | None = None
