#!/usr/bin/env python3
"""Staged, non-destructive normalization workflow for messages table.

Stages:
1) report: assess current normalization gaps and address/date variants
2) apply: backfill normalized columns (with optional pre-backup)

This script never deletes rows and never rewrites raw source columns.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re


@dataclass
class BackupResult:
    path: Path
    size_bytes: int
    integrity_ok: bool


def ensure_columns(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            msg_hash TEXT PRIMARY KEY,
            tag TEXT,
            address TEXT,
            normalized_address TEXT,
            date TEXT,
            normalized_date_ms INTEGER,
            body TEXT,
            type TEXT,
            readable_date TEXT,
            contact_name TEXT
        )
        """
    )

    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "normalized_address" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN normalized_address TEXT")
    if "normalized_date_ms" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN normalized_date_ms INTEGER")


def _safe_name_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return token.strip("-") or "backup"


def create_sqlite_backup(db_path: Path, backup_dir: Path, label: str = "pre-normalize") -> BackupResult:
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = _safe_name_token(db_path.stem)
    suffix = _safe_name_token(label)
    backup_path = backup_dir / f"{stem}_{suffix}_{ts}.sqlite"

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
        row = dst.execute("PRAGMA integrity_check").fetchone()
        integrity_ok = bool(row and row[0] == "ok")
    finally:
        dst.close()
        src.close()

    if not integrity_ok:
        backup_path.unlink(missing_ok=True)
        raise RuntimeError("Backup integrity check failed")

    return BackupResult(path=backup_path, size_bytes=backup_path.stat().st_size, integrity_ok=True)


def print_report(conn: sqlite3.Connection) -> None:
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
    updated_addr = conn.execute(
        """
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
    ).rowcount

    updated_date = conn.execute(
        """
        UPDATE messages
        SET normalized_date_ms = CAST(date AS INTEGER)
        WHERE normalized_date_ms IS NULL
          AND date GLOB '[0-9]*'
        """
    ).rowcount

    conn.commit()
    return int(updated_addr or 0), int(updated_date or 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Staged normalization workflow for messages table")
    parser.add_argument("--db", default="sms_db.sqlite", help="SQLite DB path")
    parser.add_argument("--report", action="store_true", help="Print normalization report")
    parser.add_argument("--apply", action="store_true", help="Apply backfill to normalized columns")
    parser.add_argument("--backup", action="store_true", help="Create verified backup before --apply")
    parser.add_argument("--backup-dir", default="backups/sqlite", help="Backup directory")
    parser.add_argument("--backup-label", default="pre-normalize", help="Backup label")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.report and not args.apply:
        print("Choose at least one action: --report and/or --apply")
        return 1

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 1

    if args.apply and args.backup:
        result = create_sqlite_backup(db_path, Path(args.backup_dir), args.backup_label)
        print(f"Backup created: {result.path} ({result.size_bytes} bytes, integrity={result.integrity_ok})")

    conn = sqlite3.connect(db_path)
    try:
        ensure_columns(conn)

        if args.apply:
            updated_addr, updated_date = apply_backfill(conn)
            print(f"Backfill updated normalized_address rows: {updated_addr}")
            print(f"Backfill updated normalized_date_ms rows: {updated_date}")

        if args.report:
            print_report(conn)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
