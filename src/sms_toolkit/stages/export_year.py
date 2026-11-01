"""Export a single calendar year of messages into a separate SQLite database.

Ports ``export_year_subset.py``. Non-destructive by design: the source database
is never modified, and the output is refused when it resolves to the source
path (a typo'd ``--output`` pointing at the primary database used to unlink
it and rebuild it as a year subset).

Known limitation, kept deliberately: the output schema is the original's
subset schema — it drops ``normalized_address``/``normalized_date_ms``
and has no ``contacts`` table, so downstream tools expecting those
columns will not find them. Widening the schema would break the
differential test against the original; do it as a versioned change, not
a silent one.

Selection matches a message when its numeric ``date`` falls inside the
year or its ``readable_date`` mentions the year. The ``readable_date``
fallback (``LIKE "%, {year} %"``) assumes the full-month format
(``"June 1, 2023 ..."``); exports with abbreviated months will miss rows
on that branch (the numeric-date branch still catches them).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sms_toolkit.logging_config import get_logger

logger = get_logger(__name__)

OUTPUT_SCHEMA_SCRIPT = """
CREATE TABLE IF NOT EXISTS messages (
    msg_hash TEXT PRIMARY KEY,
    tag TEXT,
    address TEXT,
    date TEXT,
    body TEXT,
    type TEXT,
    readable_date TEXT,
    contact_name TEXT
);

CREATE TABLE IF NOT EXISTS mms_parts (
    msg_hash TEXT,
    seq INTEGER,
    ct TEXT,
    name TEXT,
    text TEXT,
    data TEXT
);

CREATE TABLE IF NOT EXISTS mms_addrs (
    msg_hash TEXT,
    address TEXT,
    type TEXT,
    charset TEXT
);
"""

# NOTE: ported verbatim from the original; duplicated in two places there too.
SELECTION_WHERE = """
WHERE msg_hash IS NOT NULL
  AND (
    (date GLOB '[0-9]*' AND CAST(date AS INTEGER) >= ? AND CAST(date AS INTEGER) < ?)
    OR readable_date LIKE ?
  )
"""


@dataclass
class ExportStats:
    """Counts for one year export (selection + rows copied per table)."""

    selected_hashes: int = 0
    messages_rows: int = 0
    mms_parts_rows: int = 0
    mms_addrs_rows: int = 0


def ms_range_for_year(year: int) -> tuple[int, int]:
    """Epoch-ms bounds for *year* in local time (matching the original)."""
    # Local-timezone boundaries, matching the original.
    start = datetime(year, 1, 1)
    end = datetime(year + 1, 1, 1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _selection_params(year: int) -> tuple[int, int, str]:
    """Parameters for :data:`SELECTION_WHERE`: ms bounds + year LIKE pattern."""
    start_ms, end_ms = ms_range_for_year(year)
    return start_ms, end_ms, f"%, {year} %"


def build_year_hash_temp_table(conn: sqlite3.Connection, year: int) -> int:
    """Fill a temp table with the selected year's message hashes; return its size."""
    conn.execute("DROP TABLE IF EXISTS temp.selected_hashes")
    conn.execute("CREATE TEMP TABLE selected_hashes (msg_hash TEXT PRIMARY KEY)")
    conn.execute(
        f"""
        INSERT OR IGNORE INTO selected_hashes (msg_hash)
        SELECT msg_hash
        FROM messages
        {SELECTION_WHERE}
        """,
        _selection_params(year),
    )
    row = conn.execute("SELECT COUNT(*) FROM selected_hashes").fetchone()
    return int(row[0])


def create_output_schema(dst: sqlite3.Connection) -> None:
    """Create the (subset) output schema in *dst*; see module docstring."""
    dst.executescript(OUTPUT_SCHEMA_SCRIPT)


def export_year(source_db: Path, output_db: Path, year: int, dry_run: bool) -> ExportStats:
    """Export *year* from *source_db* to *output_db*; return the stats.

    Raises :class:`ValueError` when *output_db* resolves to *source_db* —
    without this, the unlink below would destroy the primary database.
    With *dry_run*, only the selection counts are computed and no output
    file is created.
    """
    if source_db.resolve() == output_db.resolve():
        raise ValueError(f"Refusing to export onto the source database itself: {output_db}")
    stats = ExportStats()

    src = sqlite3.connect(source_db)
    src.row_factory = sqlite3.Row

    try:
        stats.selected_hashes = build_year_hash_temp_table(src, year)

        stats.messages_rows = src.execute(
            """
            SELECT COUNT(*)
            FROM messages m
            JOIN selected_hashes s ON s.msg_hash = m.msg_hash
            """
        ).fetchone()[0]

        stats.mms_parts_rows = src.execute(
            """
            SELECT COUNT(*)
            FROM mms_parts p
            JOIN selected_hashes s ON s.msg_hash = p.msg_hash
            """
        ).fetchone()[0]

        stats.mms_addrs_rows = src.execute(
            """
            SELECT COUNT(*)
            FROM mms_addrs a
            JOIN selected_hashes s ON s.msg_hash = a.msg_hash
            """
        ).fetchone()[0]

        if dry_run:
            return stats

        output_db.parent.mkdir(parents=True, exist_ok=True)
        if output_db.exists():
            output_db.unlink()

        dst = sqlite3.connect(output_db)
        try:
            create_output_schema(dst)

            dst.execute("ATTACH DATABASE ? AS srcdb", (str(source_db),))
            dst.execute("CREATE TEMP TABLE selected_hashes (msg_hash TEXT PRIMARY KEY)")
            dst.execute(
                f"""
                INSERT OR IGNORE INTO selected_hashes (msg_hash)
                SELECT msg_hash
                FROM srcdb.messages
                {SELECTION_WHERE}
                """,
                _selection_params(year),
            )

            dst.execute(
                """
                INSERT INTO messages (msg_hash, tag, address, date, body, type, readable_date, contact_name)
                SELECT m.msg_hash, m.tag, m.address, m.date, m.body, m.type, m.readable_date, m.contact_name
                FROM srcdb.messages m
                JOIN selected_hashes s ON s.msg_hash = m.msg_hash
                """
            )

            dst.execute(
                """
                INSERT INTO mms_parts (msg_hash, seq, ct, name, text, data)
                SELECT p.msg_hash, p.seq, p.ct, p.name, p.text, p.data
                FROM srcdb.mms_parts p
                JOIN selected_hashes s ON s.msg_hash = p.msg_hash
                """
            )

            dst.execute(
                """
                INSERT INTO mms_addrs (msg_hash, address, type, charset)
                SELECT a.msg_hash, a.address, a.type, a.charset
                FROM srcdb.mms_addrs a
                JOIN selected_hashes s ON s.msg_hash = a.msg_hash
                """
            )

            dst.commit()
            dst.execute("VACUUM")
            logger.info("Exported year %d to %s", year, output_db)
        finally:
            dst.close()

        return stats
    finally:
        src.close()


def print_summary(
    stats: ExportStats, year: int, source_db: Path, output_db: Path, *, dry_run: bool
) -> None:
    """Print the year-export summary block to stdout."""
    print("=" * 80)
    print(f"Year export summary: {year}")
    print(f"Source DB: {source_db}")
    print(f"Mode: {'DRY-RUN' if dry_run else 'WRITE'}")
    print("-")
    print(f"Selected message hashes: {stats.selected_hashes}")
    print(f"messages rows: {stats.messages_rows}")
    print(f"mms_parts rows: {stats.mms_parts_rows}")
    print(f"mms_addrs rows: {stats.mms_addrs_rows}")
    if not dry_run:
        print(f"Output DB: {output_db}")
        print(f"Output size (bytes): {output_db.stat().st_size if output_db.exists() else 0}")
    print("=" * 80)


def run_export_year(
    source_db: Path,
    output_db: Path,
    year: int,
    *,
    dry_run: bool = False,
) -> int:
    """Run the year-export stage; returns a process exit code.

    Exit 1 for an out-of-range year, a missing source, or an output path
    that resolves to the source itself.
    """
    if year < 2000 or year > 2100:
        print("Year out of expected range (2000-2100).")
        return 1
    if not source_db.exists():
        print(f"Source DB not found: {source_db}")
        return 1
    if source_db.resolve() == output_db.resolve():
        print(f"Refusing to export onto the source database itself: {output_db}")
        return 1

    stats = export_year(source_db, output_db, year, dry_run)
    print_summary(stats, year, source_db, output_db, dry_run=dry_run)
    return 0
