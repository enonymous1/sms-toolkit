"""Multi-sink exporter: master XML + CSV + SQLite, driven by a PipelineVariant.

Unifies ``sms-backup-analyzer`` ``DataExporter`` and ``calls-backup-analyzer``
``CallDataExporter``. Compared to the originals:

- the three sinks are managed through a context manager (``with Exporter(...)``)
  instead of relying on a manual ``close()`` call;
- handles are typed and the DB connection is created via
  :func:`sms_toolkit.db.connect_db` (config-driven PRAGMAs);
- ``load_hashes`` raises on DB errors instead of swallowing them (see
  :mod:`sms_toolkit.db`).
"""

from __future__ import annotations

import csv
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, TextIO

from sms_toolkit import db, hashing
from sms_toolkit.config import PipelineConfig
from sms_toolkit.logging_config import get_logger
from sms_toolkit.pipeline.variants import PipelineVariant

logger = get_logger("pipeline.exporter")


class Exporter:
    """Writes pipeline elements to the master XML, CSV and SQLite outputs.

    Lifecycle: ``with Exporter(...) as exporter:`` opens the files, connects
    the DB and prepares the schema; ``write(elem)`` appends one element to all
    three sinks (SQLite is batched); on exit everything is flushed, the XML
    closing tag is written, and all handles are closed.
    """

    def __init__(
        self,
        variant: PipelineVariant,
        *,
        xml_path: str | Path,
        csv_path: str | Path,
        db_path: str | Path,
        preserve_data: bool = True,
        archive_old: bool = False,
        pipeline: PipelineConfig | None = None,
    ) -> None:
        self._variant = variant
        self.xml_path = Path(xml_path)
        self.csv_path = Path(csv_path)
        self.db_path = Path(db_path)
        self._preserve_data = preserve_data
        self._archive_old = archive_old
        self._pipeline = pipeline or PipelineConfig()
        self._batch_size = self._pipeline.batch_size
        self._msg_batch: list[tuple[Any, ...]] = []
        self._part_batch: list[tuple[Any, ...]] = []
        self._addr_batch: list[tuple[Any, ...]] = []
        # Opened in __enter__.
        self._xml_handle: BinaryIO | None = None
        self._csv_handle: TextIO | None = None
        self._csv_writer: Any = None
        self._conn: sqlite3.Connection | None = None

    # -- lifecycle ------------------------------------------------------

    def __enter__(self) -> Exporter:
        # Archive old output files only if not preserving data. Archiving
        # must succeed before anything is opened for writing: a failed
        # archive followed by truncation would destroy the only copy.
        try:
            if self._archive_old and not self._preserve_data:
                self._archive_old_files()

            self._init_xml()
            self._init_csv()

            self._conn = db.connect_db(self.db_path, self._pipeline)
            self._variant.ensure_db(self._conn, self._preserve_data)
            self._conn.commit()
        except BaseException:
            # __exit__ never runs when __enter__ raises, so close anything
            # already opened here; otherwise the XML file is left without
            # its closing tag and handles leak.
            self._close_handles()
            raise
        return self

    def _close_handles(self) -> None:
        """Close any opened output handles (idempotent, best-effort)."""
        if self._xml_handle is not None:
            try:
                self._xml_handle.close()
            finally:
                self._xml_handle = None
        if self._csv_handle is not None:
            try:
                self._csv_handle.close()
            finally:
                self._csv_handle = None
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.flush()
        finally:
            root = self._variant.xml_root_tag
            assert self._xml_handle is not None
            self._xml_handle.write(f"</{root}>\n".encode())
            self._xml_handle.close()
            assert self._csv_handle is not None
            self._csv_handle.close()
            assert self._conn is not None
            self._conn.commit()
            self._conn.close()

    # -- file setup -----------------------------------------------------

    def _archive_old_files(self) -> None:
        """Archive old output files with a timestamp instead of truncating.

        Raises :class:`OSError` on failure: the caller must abort before any
        output is opened for writing, or the un-archived original would be
        truncated. SQLite sidecar files (``-wal``/``-shm``/``-journal``)
        move with their database — a stale sidecar left beside a fresh
        database would be mistaken for that database's own journal.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for filepath in (self.xml_path, self.db_path, self.csv_path):
            if filepath.exists():
                archive_name = filepath.with_name(f"{filepath.name}.backup_{timestamp}")
                filepath.rename(archive_name)
                logger.info("Archived old file: %s -> %s", filepath, archive_name)
                for suffix in ("-wal", "-shm", "-journal"):
                    sidecar = filepath.with_name(filepath.name + suffix)
                    if sidecar.exists():
                        sidecar.rename(
                            filepath.with_name(f"{filepath.name}.backup_{timestamp}{suffix}")
                        )

    def _init_xml(self) -> None:
        """Initialize the master XML file in append or fresh mode.

        Append mode reuses the existing file without reading it whole: it
        seeks to the tail, drops a trailing closing tag if present, and
        appends. Reading the whole multi-year master file on every run
        was O(file size) RAM and I/O for no benefit.
        """
        root = self._variant.xml_root_tag
        if self._preserve_data and self.xml_path.exists():
            closing_nl = f"</{root}>\n".encode()
            closing = f"</{root}>".encode()
            handle = self.xml_path.open("r+b")
            try:
                handle.seek(0, 2)
                size = handle.tell()
                tail = b""
                if size:
                    handle.seek(max(0, size - 128))
                    tail = handle.read()
                for tag in (closing_nl, closing):
                    if tail.endswith(tag):
                        handle.truncate(size - len(tag))
                        break
                handle.seek(0, 2)
            except BaseException:
                handle.close()
                raise
            self._xml_handle = handle
            logger.info("Appending to existing XML file: %s", self.xml_path)
        else:
            # Fresh mode: create new file.
            self._xml_handle = self.xml_path.open("wb")
            self._xml_handle.write(b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n')
            self._xml_handle.write(f"<{root}>\n".encode())
            logger.info("Creating fresh XML file: %s", self.xml_path)

    def _init_csv(self) -> None:
        """Initialize the CSV file in append or fresh mode."""
        if self._preserve_data and self.csv_path.exists():
            # Append mode: open in append, don't rewrite header.
            self._csv_handle = self.csv_path.open("a", newline="", encoding="utf-8")
            self._csv_writer = csv.writer(self._csv_handle)
            logger.info("Appending to existing CSV file: %s", self.csv_path)
        else:
            # Fresh mode: create new file with header.
            self._csv_handle = self.csv_path.open("w", newline="", encoding="utf-8")
            self._csv_writer = csv.writer(self._csv_handle)
            self._csv_writer.writerow(self._variant.csv_header)
            logger.info("Creating fresh CSV file: %s", self.csv_path)

    # -- writing --------------------------------------------------------

    def write(self, elem: Any) -> None:
        """Write one element to all three sinks (SQLite writes are batched)."""
        # Explicit errors, not asserts: asserts vanish under `python -O`
        # and would turn a misuse into a confusing AttributeError.
        xml_handle = self._xml_handle
        csv_writer = self._csv_writer
        if xml_handle is None or csv_writer is None:
            raise RuntimeError("Exporter.write called outside its context manager")

        mapped = self._variant.map_element(elem)

        # 1. XML output (direct streaming; stdlib tostring, as in the originals).
        xml_handle.write(
            b"  " + ET.tostring(elem, encoding="utf-8", short_empty_elements=True).strip() + b"\n"
        )

        msg_hash = hashing.dedup_hash(mapped.key)

        # 2. CSV output.
        csv_writer.writerow(mapped.csv_row)

        # 3. SQLite batching.
        self._msg_batch.append((msg_hash, *mapped.message_values))
        self._part_batch.extend((msg_hash, *part) for part in mapped.part_values)
        self._addr_batch.extend((msg_hash, *addr) for addr in mapped.addr_values)

        if len(self._msg_batch) >= self._batch_size:
            self.flush()

    def flush(self) -> None:
        """Write pending SQLite batches and commit (safe to call any time)."""
        if self._conn is None:
            return
        if self._msg_batch:
            self._conn.executemany(self._variant.message_insert_sql, self._msg_batch)
            self._msg_batch.clear()
        if self._part_batch and self._variant.part_insert_sql:
            self._conn.executemany(self._variant.part_insert_sql, self._part_batch)
            self._part_batch.clear()
        if self._addr_batch and self._variant.addr_insert_sql:
            self._conn.executemany(self._variant.addr_insert_sql, self._addr_batch)
            self._addr_batch.clear()
        self._conn.commit()

    def load_hashes(self) -> set[str]:
        """Load existing dedup hashes from the database (call inside the context).

        Flushes pending batches first so the result reflects everything
        written so far.
        """
        self.flush()
        assert self._conn is not None
        return db.load_hashes(
            self._conn, table=self._variant.hash_table, column=self._variant.hash_column
        )
