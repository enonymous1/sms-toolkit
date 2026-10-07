"""Import IFTTT "SMS received" Excel exports into the messages database.

Ports ``import_ifttt_sms.py``. Each row of an IFTTT export holds one or more
``(timestamp, phone, [contact name], body)`` windows; parsed messages are
deduped against the database by hash and by same-day/same-body before insert.

Runs as a dry-run unless ``execute=True``.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import openpyxl

from sms_toolkit import db, hashing
from sms_toolkit.logging_config import get_logger
from sms_toolkit.normalize import normalize_address

logger = get_logger(__name__)

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
    contact_name: str | None
    type_value: str = "1"  # IFTTT file is "SMS received"
    tag: str = "sms"

    @property
    def normalized_address(self) -> str:
        # Address is already normalized at parse time; keep one canonical field.
        return self.address

    def msg_hash(self) -> str:
        # Identical to the pipeline's key builder for an sms row: the address
        # is the normalized one and date_ms formats as str(int).
        return hashing.dedup_hash(
            hashing.sms_key(self.address, str(self.date_ms), self.body, self.type_value)
        )

    def day_key(self) -> str:
        # e.g. "March 28, 2021 %". Uses the local timezone, matching the original.
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


def is_timestamp_cell(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(TIMESTAMP_RE.match(text))


def parse_timestamp(text: str) -> datetime | None:
    raw = text.strip()
    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


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
    return all(re.fullmatch(r"[A-Za-z][A-Za-z'\-]*", w) for w in words)


def extract_tuple_from_window(
    source_file: str,
    row_idx: int,
    start_col_idx: int,
    timestamp_text: str,
    window_values: Sequence[object],
) -> ParsedMessage | None:
    dt = parse_timestamp(timestamp_text)
    if dt is None:
        return None

    if not window_values:
        return None

    phone_raw = window_values[0]
    address = normalize_address(phone_raw)
    if not address:
        return None

    trailing = [normalize_body(v) for v in window_values[1:] if normalize_body(v)]
    if not trailing:
        return None

    contact_name: str | None = None
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


def parse_excel_messages(path: Path, stats: ImportStats) -> list[ParsedMessage]:
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    assert ws is not None
    parsed: list[ParsedMessage] = []

    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        stats.rows_scanned += 1

        ts_positions = [col_idx for col_idx, val in enumerate(row) if is_timestamp_cell(val)]
        if not ts_positions:
            continue

        for i, ts_col in enumerate(ts_positions):
            ts_text = str(row[ts_col]).strip()
            next_boundary = ts_positions[i + 1] if i + 1 < len(ts_positions) else len(row)

            # Collect non-empty cells to the right of the timestamp until the
            # next timestamp in the row.
            right_values = [
                row[c] for c in range(ts_col + 1, next_boundary) if row[c] not in (None, "")
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
    existing_hashes = db.load_hashes(conn)
    batch_hashes: set[str] = set()
    batch_day_keys: set[tuple[str, str, str]] = set()  # (address, body, day_key)

    to_insert: list[ParsedMessage] = []

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


def discover_files(patterns: Sequence[str]) -> list[Path]:
    """Expand file paths and glob patterns, de-duplicated, in order.

    Unlike the original (which crashed with ``NotImplementedError`` on
    absolute glob patterns), absolute patterns are globbed from their
    parent directory.
    """
    files: list[Path] = []
    for p in patterns:
        path = Path(p)
        if path.is_file():
            files.append(path)
            continue

        if path.is_absolute():
            files.extend(sorted(path.parent.glob(path.name)))
        else:
            files.extend(sorted(Path().glob(p)))

    # Preserve order but remove duplicates
    seen = set()
    ordered: list[Path] = []
    for fp in files:
        key = str(fp.resolve())
        if key in seen:
            continue
        seen.add(key)
        ordered.append(fp)
    return ordered


def print_summary(stats: ImportStats, preview: int, messages: Sequence[ParsedMessage]) -> None:
    print("Rows scanned:", stats.rows_scanned)
    print("Parsed messages:", stats.parsed_messages)
    print("Candidates after dedupe:", stats.inserted_messages)
    print("Duplicate: hash in DB:", stats.dup_hash_db)
    print("Duplicate: hash in batch:", stats.dup_hash_batch)
    print("Duplicate: same day/body in DB:", stats.dup_same_day_body_db)
    print("Duplicate: same day/body in batch:", stats.dup_same_day_body_batch)
    print("Skipped invalid rows/windows:", stats.skipped_invalid)

    if messages and preview > 0:
        print("-" * 88)
        print("Sample parsed messages:")
        for m in messages[:preview]:
            print(
                f"[{m.source_file} R{m.source_row} C{m.source_col}] "
                f"{m.readable_date} | {m.address} | "
                f"{(m.contact_name + ' | ') if m.contact_name else ''}{m.body[:80]}"
            )


def run_import(
    db_path: Path,
    file_patterns: Sequence[str],
    *,
    execute: bool,
    backup_only: bool = False,
    no_backup: bool = False,
    backup_dir: Path = Path("backups/sqlite"),
    backup_label: str = "pre-import",
    keep_backups: int = 20,
    preview: int = 5,
) -> int:
    """Run the IFTTT import stage; returns a process exit code."""
    if backup_only and execute:
        print("Choose either --backup-only or --execute, not both.")
        return 1
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    if backup_only:
        try:
            result = db.create_backup(db_path, backup_dir, backup_label)
            removed = db.prune_old_backups(backup_dir, db_path, keep_backups)
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

    files = discover_files(file_patterns)
    if not files:
        print("No matching files found.")
        return 1

    stats = ImportStats()
    all_messages: list[ParsedMessage] = []

    print("=" * 88)
    print("IFTTT SMS Import")
    print("Mode:", "EXECUTE (write)" if execute else "DRY-RUN (no writes)")
    print("Database:", db_path)
    print("Auto backup:", "disabled" if no_backup else "enabled")
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

    if execute and not no_backup:
        try:
            backup_result = db.create_backup(db_path, backup_dir, backup_label)
            removed = db.prune_old_backups(backup_dir, db_path, keep_backups)
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
        db.ensure_messages_table(conn)
        insert_messages(conn, all_messages, execute, stats)

        if execute:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()

    print_summary(stats, preview, all_messages)

    if not execute:
        print("-" * 88)
        print("Dry-run complete. Re-run with --execute to write records.")

    return 0
