"""Staged address/date normalization for the messages table.

Ports ``normalize_messages_stage.py``: report what is missing, then apply the
backfill in place. The backfill SQL is kept verbatim so existing databases are
updated exactly as before: separators (``-() +. /`` and space) are stripped,
a single leading ``1`` is dropped from 11-digit NANP numbers, and numeric
``date`` values are cast to integers.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sms_toolkit import db
from sms_toolkit.logging_config import get_logger

logger = get_logger(__name__)

# Ported verbatim from the original stage.
BACKFILL_ADDRESS_SQL = """
UPDATE messages
SET normalized_address = (
    CASE
        WHEN address IS NULL THEN ''
        ELSE (
            CASE
                WHEN LENGTH(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(address,'-',''),'(',''),')',''),' ',''),'+',''),'.',''),'/','')) = 11
                     AND SUBSTR(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(address,'-',''),'(',''),')',''),' ',''),'+',''),'.',''),'/',''),1,1) = '1'
                THEN SUBSTR(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(address,'-',''),'(',''),')',''),' ',''),'+',''),'.',''),'/',''),2)
                ELSE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(address,'-',''),'(',''),')',''),' ',''),'+',''),'.',''),'/','')
            END
        )
    END
)
WHERE normalized_address IS NULL OR TRIM(normalized_address) = ''
"""

# Ported verbatim from the original stage.
BACKFILL_DATE_SQL = """
UPDATE messages
SET normalized_date_ms = CAST(date AS INTEGER)
WHERE normalized_date_ms IS NULL
  AND date GLOB '[0-9]*'
"""


def print_report(conn: sqlite3.Connection) -> None:
    """Print the human-readable normalization report to stdout."""
    total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    missing_norm_addr = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE normalized_address IS NULL OR TRIM(normalized_address) = ''"
    ).fetchone()[0]
    missing_norm_date = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE normalized_date_ms IS NULL"
    ).fetchone()[0]

    print("=" * 88)
    print("Normalization Report")
    print("-")
    print(f"messages total: {total}")
    print(f"missing normalized_address: {missing_norm_addr}")
    print(f"missing normalized_date_ms: {missing_norm_date}")

    print("-")
    print("Top raw address variants that collapse to one normalized_address:")
    rows = conn.execute(
        """
        SELECT
            normalized_address,
            COUNT(*) AS cnt,
            COUNT(DISTINCT address) AS raw_variants
        FROM messages
        WHERE normalized_address IS NOT NULL AND TRIM(normalized_address) <> ''
        GROUP BY normalized_address
        HAVING COUNT(DISTINCT address) > 1
        ORDER BY raw_variants DESC, cnt DESC
        LIMIT 20
        """
    ).fetchall()

    if not rows:
        print("  (none)")
    else:
        for norm_addr, cnt, raw_variants in rows:
            print(f"  {norm_addr}: {cnt} messages across {raw_variants} raw formats")

    print("-")
    print("Top unresolved rows (no normalized_address):")
    unresolved = conn.execute(
        """
        SELECT address, COUNT(*)
        FROM messages
        WHERE normalized_address IS NULL OR TRIM(normalized_address) = ''
        GROUP BY address
        ORDER BY COUNT(*) DESC
        LIMIT 10
        """
    ).fetchall()
    if not unresolved:
        print("  (none)")
    else:
        for addr, cnt in unresolved:
            print(f"  {addr!r}: {cnt}")

    print("=" * 88)


def apply_backfill(conn: sqlite3.Connection) -> tuple[int, int]:
    """Run the address/date backfill UPDATEs; returns (address_rows, date_rows)."""
    updated_addr = conn.execute(BACKFILL_ADDRESS_SQL).rowcount
    updated_date = conn.execute(BACKFILL_DATE_SQL).rowcount
    conn.commit()
    logger.info("Backfill updated %d address rows, %d date rows", updated_addr, updated_date)
    return int(updated_addr or 0), int(updated_date or 0)


def run_normalize(
    db_path: Path,
    *,
    show_report: bool,
    apply: bool,
    backup: bool,
    backup_dir: Path,
    backup_label: str,
) -> int:
    """Run the normalization stage; returns a process exit code."""
    if not (show_report or apply):
        print("Choose at least one action: --report and/or --apply")
        return 1
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 1

    if apply and backup:
        logger.info("Creating backup before normalization")
        try:
            result = db.create_backup(db_path, backup_dir, backup_label)
        except Exception as exc:
            print(f"Backup failed: {exc}")
            return 1
        print(
            f"Backup created: {result.path} ({result.size_bytes} bytes,"
            f" integrity={result.integrity_ok})"
        )

    conn = sqlite3.connect(db_path)
    try:
        db.ensure_messages_table(conn)

        if apply:
            updated_addr, updated_date = apply_backfill(conn)
            print(f"Backfill updated normalized_address rows: {updated_addr}")
            print(f"Backfill updated normalized_date_ms rows: {updated_date}")

        if show_report:
            print_report(conn)
    finally:
        conn.close()

    return 0
