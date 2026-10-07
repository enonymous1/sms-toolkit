#!/usr/bin/env python3
"""Export a single year of messages from sms_db.sqlite into a new SQLite DB.

Non-destructive by design: source DB is never modified.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class ExportStats:
    selected_hashes: int = 0
    messages_rows: int = 0
    mms_parts_rows: int = 0
    mms_addrs_rows: int = 0


def ms_range_for_year(year: int) -> tuple[int, int]:
    start = datetime(year, 1, 1)
    end = datetime(year + 1, 1, 1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def build_year_hash_temp_table(conn: sqlite3.Connection, year: int) -> int:
    start_ms, end_ms = ms_range_for_year(year)
    readable_like = f"%, {year} %"

    conn.execute("DROP TABLE IF EXISTS temp.selected_hashes")
    conn.execute("CREATE TEMP TABLE selected_hashes (msg_hash TEXT PRIMARY KEY)")

    conn.execute(
        """
        INSERT OR IGNORE INTO selected_hashes (msg_hash)
        SELECT msg_hash
        FROM messages
        WHERE msg_hash IS NOT NULL
          AND (
            (date GLOB '[0-9]*' AND CAST(date AS INTEGER) >= ? AND CAST(date AS INTEGER) < ?)
            OR readable_date LIKE ?
          )
        """,
        (start_ms, end_ms, readable_like),
    )

    row = conn.execute("SELECT COUNT(*) FROM selected_hashes").fetchone()
    return int(row[0])


def create_output_schema(dst: sqlite3.Connection) -> None:
    dst.executescript(
        """
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
    )


def export_year(source_db: Path, output_db: Path, year: int, dry_run: bool) -> ExportStats:
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

            dst.execute(
                """
                ATTACH DATABASE ? AS srcdb
                """,
                (str(source_db),),
            )

            start_ms, end_ms = ms_range_for_year(year)
            readable_like = f"%, {year} %"

            dst.execute(
                """
                CREATE TEMP TABLE selected_hashes (msg_hash TEXT PRIMARY KEY)
                """
            )
            dst.execute(
                """
                INSERT OR IGNORE INTO selected_hashes (msg_hash)
                SELECT msg_hash
                FROM srcdb.messages
                WHERE msg_hash IS NOT NULL
                  AND (
                    (date GLOB '[0-9]*' AND CAST(date AS INTEGER) >= ? AND CAST(date AS INTEGER) < ?)
                    OR readable_date LIKE ?
                  )
                """,
                (start_ms, end_ms, readable_like),
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
        finally:
            dst.close()

        return stats
    finally:
        src.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export one calendar year from sms_db.sqlite into a separate SQLite file.",
    )
    parser.add_argument("--year", type=int, required=True, help="Year to export (e.g. 2021)")
    parser.add_argument("--db", default="sms_db.sqlite", help="Source DB path")
    parser.add_argument(
        "--output",
        default=None,
        help="Output DB path (default: exports/sms_db_<year>.sqlite)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show export counts only; do not write output DB.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.year < 2000 or args.year > 2100:
        print("Year out of expected range (2000-2100).")
        return 1

    source_db = Path(args.db)
    if not source_db.exists():
        print(f"Source DB not found: {source_db}")
        return 1

    output_db = Path(args.output) if args.output else Path("exports") / f"sms_db_{args.year}.sqlite"

    stats = export_year(source_db, output_db, args.year, args.dry_run)

    print("=" * 80)
    print(f"Year export summary: {args.year}")
    print(f"Source DB: {source_db}")
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'WRITE'}")
    print("-")
    print(f"Selected message hashes: {stats.selected_hashes}")
    print(f"messages rows: {stats.messages_rows}")
    print(f"mms_parts rows: {stats.mms_parts_rows}")
    print(f"mms_addrs rows: {stats.mms_addrs_rows}")
    if not args.dry_run:
        print(f"Output DB: {output_db}")
        print(f"Output size (bytes): {output_db.stat().st_size if output_db.exists() else 0}")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
