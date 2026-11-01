"""Staged address/date normalization for the messages table.

Ports ``normalize_messages_stage.py``: report what is missing, then apply the
backfill in place.

Deliberate deviation from the original: the original's backfill was a
verbatim SQL expression (nested ``REPLACE`` calls) that did NOT match the
canonical Python normalizers used at import time — SQL stripped only
``-() +. /`` and space where :func:`sms_toolkit.normalize.normalize_address`
strips all non-digits (``"555.010.1234 x89"`` became ``5550101234x89`` by
SQL but ``555010123489`` at import), and SQL's ``CAST(date AS INTEGER)``
parsed leading digits where :func:`normalize_date_ms` requires all-digit
input. The same raw address could therefore normalize two different ways
depending on which path filled it. The backfill now applies the same Python
functions the pipeline uses, so there is exactly one normalization truth.
Row selection is unchanged: only rows with a missing normalized value are
filled (``normalized_date_ms`` is only set when the date parses, so
non-numeric dates stay NULL exactly as before).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sms_toolkit import db
from sms_toolkit.logging_config import get_logger
from sms_toolkit.normalize import normalize_address, normalize_date_ms

logger = get_logger(__name__)


def print_report(conn: sqlite3.Connection) -> None:
    """Print the human-readable normalization report to stdout."""
    total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    missing_norm_addr = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE normalized_address IS NULL "
        "OR TRIM(normalized_address) = ''"
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
    """Fill missing normalized values with the canonical Python normalizers.

    Returns ``(address_rows, date_rows)``. Address rows are every row with a
    missing/blank ``normalized_address``; date rows are every row with a NULL
    ``normalized_date_ms`` whose ``date`` parses as epoch milliseconds —
    non-numeric dates stay NULL (the old SQL backfill's ``CAST`` silently
    turned ``"123abc"`` into ``123``; the canonical normalizer refuses it).
    Rows already backfilled by the old SQL are NOT repaired: they are
    non-NULL, so this pass skips them. A future ``--force`` recompute would
    be needed to unify those; it is intentionally not the default, since
    recomputing every row would also overwrite any hand-corrected values.
    """
    rows = conn.execute(
        "SELECT rowid, address, date, normalized_address, normalized_date_ms FROM messages"
    ).fetchall()
    addr_updates: list[tuple[str, int]] = []
    date_updates: list[tuple[int, int]] = []
    for rowid, address, date, norm_addr, norm_date in rows:
        if norm_addr is None or not str(norm_addr).strip():
            addr_updates.append((normalize_address(address), rowid))
        if norm_date is None:
            ms = normalize_date_ms(date)
            if ms is not None:
                date_updates.append((ms, rowid))
    conn.executemany("UPDATE messages SET normalized_address = ? WHERE rowid = ?", addr_updates)
    conn.executemany("UPDATE messages SET normalized_date_ms = ? WHERE rowid = ?", date_updates)
    conn.commit()
    logger.info(
        "Backfill updated %d address rows, %d date rows", len(addr_updates), len(date_updates)
    )
    return len(addr_updates), len(date_updates)


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

    if apply and not backup:
        # --apply rewrites up to every row in place. Unlike import-ifttt it
        # does not auto-back-up (that would change long-standing behavior
        # the tests lock in), so at least say so loudly.
        print("Warning: --apply without --backup mutates the database in place.")

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
