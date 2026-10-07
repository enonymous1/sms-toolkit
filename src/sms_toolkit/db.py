"""SQLite helpers for sms_toolkit.

Unifies the database boilerplate scattered across the originals:

- ``connect_db`` (PRAGMA setup) <- ``sms``/``calls`` ``analyzer.__init__``
  (inline PRAGMA block, now driven by :class:`PipelineConfig`)
- ``ensure_messages_table`` <- ``sms analyzer._setup_db``,
  ``import_ifttt_sms.ensure_messages_table``,
  ``normalize_messages_stage.ensure_columns`` (identical DDL in all three)
- ``create_backup`` / ``prune_old_backups`` / ``BackupResult`` /
  ``_safe_name_token`` <- ``normalize_messages_stage`` and ``import_ifttt_sms``
  (duplicated verbatim)
- ``load_hashes`` <- ``sms``/``calls`` ``analyzer.get_existing_hashes``,
  ``import_ifttt_sms.load_existing_hashes``
- ``table_columns`` <- ``export_db_csv_attachments.table_columns``

Deliberate behavior change: the original ``get_existing_hashes`` swallowed
*all* exceptions and returned an empty set — which would silently re-import
every existing row as "new". ``load_hashes`` raises instead.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sms_toolkit.config import PipelineConfig

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class BackupResult:
    """Outcome of :func:`create_backup`."""

    path: Path
    size_bytes: int
    integrity_ok: bool


def connect_db(db_path: str | Path, pipeline: PipelineConfig | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with the toolkit's performance PRAGMAs applied."""
    cfg = pipeline or PipelineConfig()
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA journal_mode = {cfg.sqlite_journal_mode}")
    conn.execute(f"PRAGMA synchronous = {cfg.sqlite_synchronous}")
    conn.execute(f"PRAGMA cache_size = {cfg.sqlite_cache_size}")
    conn.execute(f"PRAGMA locking_mode = {cfg.sqlite_locking_mode}")
    conn.execute(f"PRAGMA temp_store = {cfg.sqlite_temp_store}")
    return conn


def _ensure_normalized_columns(conn: sqlite3.Connection) -> None:
    """Add normalization columns to databases created before that feature."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "normalized_address" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN normalized_address TEXT")
    if "normalized_date_ms" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN normalized_date_ms INTEGER")


def ensure_messages_table(conn: sqlite3.Connection) -> None:
    """Create the ``messages`` table if missing; migrate old schemas non-destructively.

    The caller owns the transaction (commit/rollback).
    """
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
    _ensure_normalized_columns(conn)


def rebuild_messages_table(conn: sqlite3.Connection) -> None:
    """Drop and recreate the ``messages`` table (fresh mode)."""
    conn.execute("DROP TABLE IF EXISTS messages")
    ensure_messages_table(conn)
    conn.commit()


def _safe_name_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return token.strip("-") or "backup"


def create_backup(
    db_path: str | Path, backup_dir: str | Path, label: str = "pre-import"
) -> BackupResult:
    """Create a verified SQLite backup using the SQLite backup API.

    Raises :class:`RuntimeError` if the integrity check fails (the partial file
    is removed first).
    """
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
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
    """Keep only the newest ``keep`` backups for this database prefix; return removed count."""
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


def load_hashes(
    conn: sqlite3.Connection, table: str = "messages", column: str = "msg_hash"
) -> set[str]:
    """Load all non-null dedup hashes from a table.

    Failures raise (deliberately not swallowed like the original: an empty set
    on error would silently re-import every existing row).
    """
    for identifier in (table, column):
        if not _IDENTIFIER_RE.match(identifier):
            raise ValueError(f"Invalid identifier: {identifier!r}")
    rows = conn.execute(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL").fetchall()
    return {row[0] for row in rows if row[0]}


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return the column names of a table."""
    if not _IDENTIFIER_RE.match(table):
        raise ValueError(f"Invalid table name: {table!r}")
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
