#!/usr/bin/env python3
"""Import IFTTT SMS Excel exports into sms_db.sqlite with deduplication.

Supports both observed IFTTT layouts:
1) Standard rows: timestamp, phone, optional contact, body
2) Diagonal/shifted rows where the message tuple starts at varying columns

Default mode is dry-run. Use --execute to insert.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import openpyxl

TIMESTAMP_RE = re.compile(
    r"^[A-Za-z]+\s+\d{1,2},\s+\d{4}\s+at\s+\d{1,2}:\d{2}\s*(?:AM|PM)$",
    re.IGNORECASE,
)

TIMESTAMP_FORMATS = [
    "%B %d, %Y at %I:%M%p",
    "%B %d, %Y at %I:%M %p",
]


@dataclass
class ParsedMessage:
    source_file: str
    source_row: int
    source_col: int
    timestamp_raw: str
    date_ms: int
    readable_date: str
    address: str
    body: str
    contact_name: Optional[str]
    type_value: str = "1"  # IFTTT file is "SMS received"
    tag: str = "sms"

    @property
    def normalized_address(self) -> str:
        # Address is already normalized by normalize_phone_for_db; keep one canonical field.
        return self.address

    def msg_hash(self) -> str:
        key = f"sms|{self.address}|{self.date_ms}|{self.body}|{self.type_value}"
        return hashlib.md5(key.encode("utf-8")).hexdigest()

    def day_key(self) -> str:
        # e.g. "March 28, 2021 %"
        dt = datetime.fromtimestamp(self.date_ms / 1000.0)
        return f"{dt.strftime('%B')} {dt.day}, {dt.year} %"


@dataclass
class ImportStats:
    files_processed: int = 0
    rows_scanned: int = 0
    parsed_messages: int = 0
    inserted_messages: int = 0
    dup_hash_db: int = 0
    dup_hash_batch: int = 0
    dup_same_day_body_db: int = 0
    dup_same_day_body_batch: int = 0
    skipped_invalid: int = 0


@dataclass
class BackupResult:
    path: Path
    size_bytes: int
    integrity_ok: bool


def is_timestamp_cell(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(TIMESTAMP_RE.match(text))


def parse_timestamp(text: str) -> Optional[datetime]:
    raw = text.strip()
    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def normalize_phone_for_db(value: object) -> str:
    if value is None:
        return ""

    if isinstance(value, (int, float)):
        # Excel often stores phone-like numeric values as float with .0
        text = str(int(value))
    else:
        text = str(value).strip()
        # Remove accidental ".0" from numeric-looking strings
        if re.fullmatch(r"\d+\.0", text):
            text = text[:-2]

    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""

    # Align with existing db examples: US numbers often stored as 10 digits.
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def normalize_body(text: object) -> str:
    if text is None:
        return ""
    body = str(text)
    body = body.replace("\u00a0", " ")
    body = re.sub(r"\s+", " ", body).strip()
    return body


def format_readable_date(dt: datetime) -> str:
    hour12 = dt.strftime("%I").lstrip("0") or "12"
    return f"{dt.strftime('%B')} {dt.day}, {dt.year} {hour12}:{dt.strftime('%M:%S %p')}"


def looks_like_name(text: str) -> bool:
    candidate = text.strip()
    if not candidate:
        return False
    if len(candidate) > 50:
        return False
    if "http" in candidate.lower():
        return False
    if any(ch.isdigit() for ch in candidate):
        return False

    words = re.split(r"\s+", candidate)
    if len(words) > 4:
        return False

    # Accept title-case-ish names with punctuation like apostrophes/hyphens.
    for w in words:
        if not re.fullmatch(r"[A-Za-z][A-Za-z'\-]*", w):
            return False
    return True


def extract_tuple_from_window(
    source_file: str,
    row_idx: int,
    start_col_idx: int,
    timestamp_text: str,
    window_values: Sequence[object],
) -> Optional[ParsedMessage]:
    dt = parse_timestamp(timestamp_text)
    if dt is None:
        return None

    if not window_values:
        return None

    phone_raw = window_values[0]
    address = normalize_phone_for_db(phone_raw)
    if not address:
        return None

    trailing = [normalize_body(v) for v in window_values[1:] if normalize_body(v)]
    if not trailing:
        return None

    contact_name: Optional[str] = None
    body: str

    if len(trailing) >= 2 and looks_like_name(trailing[0]):
        contact_name = trailing[0]
        body = trailing[-1]
    else:
        body = trailing[-1]

    if not body:
        return None

    date_ms = int(dt.timestamp() * 1000)
    return ParsedMessage(
        source_file=source_file,
        source_row=row_idx,
        source_col=start_col_idx + 1,
        timestamp_raw=timestamp_text.strip(),
        date_ms=date_ms,
        readable_date=format_readable_date(dt),
        address=address,
        body=body,
        contact_name=contact_name,
    )


def parse_excel_messages(path: Path, stats: ImportStats) -> List[ParsedMessage]:
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    parsed: List[ParsedMessage] = []

    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        stats.rows_scanned += 1

        ts_positions = [
            col_idx for col_idx, val in enumerate(row) if is_timestamp_cell(val)
        ]
        if not ts_positions:
            continue

        for i, ts_col in enumerate(ts_positions):
            ts_text = str(row[ts_col]).strip()
            next_boundary = ts_positions[i + 1] if i + 1 < len(ts_positions) else len(row)

            # Collect non-empty cells to the right of timestamp until next timestamp in row.
            right_values = [
                row[c]
                for c in range(ts_col + 1, next_boundary)
                if row[c] not in (None, "")
            ]

            msg = extract_tuple_from_window(
                source_file=path.name,
                row_idx=row_idx,
                start_col_idx=ts_col,
                timestamp_text=ts_text,
                window_values=right_values,
            )

            if msg is None:
                stats.skipped_invalid += 1
                continue

            parsed.append(msg)

    stats.files_processed += 1
    stats.parsed_messages += len(parsed)
    return parsed


def ensure_messages_table(conn: sqlite3.Connection) -> None:
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

    # Non-destructive schema evolution for pre-existing databases.
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()
    }
    if "normalized_address" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN normalized_address TEXT")
    if "normalized_date_ms" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN normalized_date_ms INTEGER")


def load_existing_hashes(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT msg_hash FROM messages WHERE msg_hash IS NOT NULL").fetchall()
    return {r[0] for r in rows if r[0]}


def exists_same_day_same_body(
    conn: sqlite3.Connection,
    address: str,
    body: str,
    day_like: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM messages
        WHERE tag='sms'
          AND type='1'
          AND address=?
          AND body=?
          AND readable_date LIKE ?
        LIMIT 1
        """,
        (address, body, day_like),
    ).fetchone()
    return row is not None


def insert_messages(
    conn: sqlite3.Connection,
    messages: Iterable[ParsedMessage],
    execute: bool,
    stats: ImportStats,
) -> None:
    existing_hashes = load_existing_hashes(conn)
    batch_hashes: set[str] = set()
    batch_day_keys: set[Tuple[str, str, str]] = set()  # (address, body, day_key)

    to_insert: List[ParsedMessage] = []

    for msg in messages:
        h = msg.msg_hash()
        day_key = msg.day_key()
        batch_day_key = (msg.address, msg.body, day_key)

        if h in existing_hashes:
            stats.dup_hash_db += 1
            continue

        if h in batch_hashes:
            stats.dup_hash_batch += 1
            continue

        if batch_day_key in batch_day_keys:
            stats.dup_same_day_body_batch += 1
            continue

        if exists_same_day_same_body(conn, msg.address, msg.body, day_key):
            stats.dup_same_day_body_db += 1
            continue

        batch_hashes.add(h)
        batch_day_keys.add(batch_day_key)
        to_insert.append(msg)

    if execute and to_insert:
        conn.executemany(
            """
            INSERT INTO messages (
                msg_hash, tag, address, normalized_address, date, normalized_date_ms,
                body, type, readable_date, contact_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    m.msg_hash(),
                    m.tag,
                    m.address,
                    m.normalized_address,
                    str(m.date_ms),
                    m.date_ms,
                    m.body,
                    m.type_value,
                    m.readable_date,
                    m.contact_name,
                )
                for m in to_insert
            ],
        )

    stats.inserted_messages = len(to_insert)


def discover_files(patterns: Sequence[str]) -> List[Path]:
    files: List[Path] = []
    for p in patterns:
        path = Path(p)
        if path.is_file():
            files.append(path)
            continue

        files.extend(sorted(Path().glob(p)))

    # Preserve order but remove duplicates
    seen = set()
    ordered: List[Path] = []
    for fp in files:
        key = str(fp.resolve())
        if key in seen:
            continue
        seen.add(key)
        ordered.append(fp)
    return ordered


def _safe_name_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return token.strip("-") or "backup"


def create_sqlite_backup(db_path: Path, backup_dir: Path, label: str = "pre-import") -> BackupResult:
    """Create a verified SQLite backup using the SQLite backup API."""
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
        raise RuntimeError("Backup integrity check failed.")

    return BackupResult(
        path=backup_path,
        size_bytes=backup_path.stat().st_size,
        integrity_ok=True,
    )


def prune_old_backups(backup_dir: Path, db_path: Path, keep: int) -> int:
    """Keep only the newest N backups for this database prefix."""
    if keep <= 0 or not backup_dir.exists():
        return 0

    prefix = f"{_safe_name_token(db_path.stem)}_"
    backups = sorted(
        [p for p in backup_dir.glob(f"{prefix}*.sqlite") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    removed = 0
    for stale in backups[keep:]:
        stale.unlink(missing_ok=True)
        removed += 1
    return removed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import IFTTT SMS Excel files into sms_db.sqlite with dedupe checks.",
    )
    parser.add_argument(
        "--db",
        default="sms_db.sqlite",
        help="Path to target SQLite database (default: sms_db.sqlite)",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        default=["temp/IFTTT/SMS/SMS received*.xlsx"],
        help="Excel file(s) or glob pattern(s).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually write to DB. Without this flag, runs as dry-run.",
    )
    parser.add_argument(
        "--backup-only",
        action="store_true",
        help="Create a verified DB backup and exit without import.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Disable automatic backup before --execute imports.",
    )
    parser.add_argument(
        "--backup-dir",
        default="backups/sqlite",
        help="Backup directory (default: backups/sqlite).",
    )
    parser.add_argument(
        "--backup-label",
        default="pre-import",
        help="Label segment in backup filename (default: pre-import).",
    )
    parser.add_argument(
        "--keep-backups",
        type=int,
        default=20,
        help="Keep newest N backups per DB name (default: 20).",
    )
    parser.add_argument(
        "--preview",
        type=int,
        default=5,
        help="How many parsed sample messages to print (default: 5).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.backup_only and args.execute:
        print("Choose either --backup-only or --execute, not both.")
        return 1

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    backup_dir = Path(args.backup_dir)

    if args.backup_only:
        try:
            result = create_sqlite_backup(db_path, backup_dir, args.backup_label)
            removed = prune_old_backups(backup_dir, db_path, args.keep_backups)
        except Exception as exc:
            print(f"Backup failed: {exc}")
            return 1

        print("=" * 88)
        print("Backup created")
        print("Database:", db_path)
        print("Backup:", result.path)
        print("Size (bytes):", result.size_bytes)
        print("Integrity check:", "ok" if result.integrity_ok else "failed")
        if removed:
            print("Old backups pruned:", removed)
        print("=" * 88)
        return 0

    files = discover_files(args.files)
    if not files:
        print("No matching files found.")
        return 1

    stats = ImportStats()
    all_messages: List[ParsedMessage] = []

    print("=" * 88)
    print("IFTTT SMS Import")
    print("Mode:", "EXECUTE (write)" if args.execute else "DRY-RUN (no writes)")
    print("Database:", args.db)
    print("Auto backup:", "disabled" if args.no_backup else "enabled")
    print("Backup dir:", backup_dir)
    print("Files:")
    for f in files:
        print("  -", f)
    print("=" * 88)

    for fp in files:
        parsed = parse_excel_messages(fp, stats)
        all_messages.extend(parsed)
        print(f"Parsed {len(parsed):5d} messages from {fp.name}")

    print("-" * 88)

    if args.execute and not args.no_backup:
        try:
            backup_result = create_sqlite_backup(db_path, backup_dir, args.backup_label)
            removed = prune_old_backups(backup_dir, db_path, args.keep_backups)
        except Exception as exc:
            print(f"Pre-import backup failed: {exc}")
            print("Import aborted. No DB changes were made.")
            return 1

        print("Backup created:", backup_result.path)
        print("Backup size (bytes):", backup_result.size_bytes)
        if removed:
            print("Old backups pruned:", removed)
        print("-" * 88)

    conn = sqlite3.connect(db_path)
    try:
        ensure_messages_table(conn)
        insert_messages(conn, all_messages, args.execute, stats)

        if args.execute:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()

    print("Rows scanned:", stats.rows_scanned)
    print("Parsed messages:", stats.parsed_messages)
    print("Candidates after dedupe:", stats.inserted_messages)
    print("Duplicate: hash in DB:", stats.dup_hash_db)
    print("Duplicate: hash in batch:", stats.dup_hash_batch)
    print("Duplicate: same day/body in DB:", stats.dup_same_day_body_db)
    print("Duplicate: same day/body in batch:", stats.dup_same_day_body_batch)
    print("Skipped invalid rows/windows:", stats.skipped_invalid)

    if all_messages and args.preview > 0:
        print("-" * 88)
        print("Sample parsed messages:")
        for m in all_messages[: args.preview]:
            print(
                f"[{m.source_file} R{m.source_row} C{m.source_col}] "
                f"{m.readable_date} | {m.address} | "
                f"{(m.contact_name + ' | ') if m.contact_name else ''}{m.body[:80]}"
            )

    if not args.execute:
        print("-" * 88)
        print("Dry-run complete. Re-run with --execute to write records.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
