"""Environment and database diagnostics (the ``diag`` subcommand).

Consolidates the reusable checks from the original one-off scripts
(``check_image.py``, ``db_browser.py``): database presence, integrity, table
row counts, message tag breakdown, and MMS image inventory, plus optional
dependency availability. Exits 0 when all checks pass, 1 on any failure.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sms_toolkit.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass
class DiagReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def render(self) -> str:
        lines = ["SMS Toolkit diagnostics", "=" * 60]
        for check in self.checks:
            status = "OK  " if check.ok else "FAIL"
            lines.append(f"[{status}] {check.name}: {check.detail}")
        lines.append("=" * 60)
        lines.append("All checks passed." if self.ok else "Some checks FAILED.")
        return "\n".join(lines)


def _single_value(conn: sqlite3.Connection, sql: str, args: tuple[object, ...] = ()) -> object:
    row = conn.execute(sql, args).fetchone()
    return row[0] if row else None


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return bool(row and row[0] > 0)


def check_database(path: Path, label: str) -> list[CheckResult]:
    """Run the database health checks for one SQLite file."""
    checks: list[CheckResult] = []
    if not path.exists():
        return [CheckResult(f"{label} exists", False, f"not found: {path}")]

    size = path.stat().st_size
    checks.append(CheckResult(f"{label} exists", True, f"{path} ({size:,} bytes)"))

    try:
        conn = sqlite3.connect(path)
    except sqlite3.Error as exc:
        return checks + [CheckResult(f"{label} open", False, str(exc))]

    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        conn.close()
        return checks + [CheckResult(f"{label} integrity", False, str(exc))]
    checks.append(CheckResult(f"{label} integrity", integrity == "ok", str(integrity).lower()))
    if integrity != "ok":
        conn.close()
        return checks

    if not _has_table(conn, "messages"):
        conn.close()
        return checks + [CheckResult(f"{label} messages table", False, "table 'messages' missing")]

    total = _single_value(conn, "SELECT COUNT(*) FROM messages")
    checks.append(CheckResult(f"{label} messages", True, f"{total:,} rows"))

    breakdown = conn.execute("SELECT tag, COUNT(*) FROM messages GROUP BY tag").fetchall()
    checks.append(
        CheckResult(
            f"{label} by tag", True, ", ".join(f"{tag}={count:,}" for tag, count in breakdown)
        )
    )

    date_min, date_max = conn.execute("SELECT MIN(date), MAX(date) FROM messages").fetchone()
    lo, hi = _coerce_ms(date_min), _coerce_ms(date_max)
    if lo is not None and hi is not None:
        lo_day = datetime.fromtimestamp(lo / 1000, tz=timezone.utc).date().isoformat()
        hi_day = datetime.fromtimestamp(hi / 1000, tz=timezone.utc).date().isoformat()
        checks.append(CheckResult(f"{label} date range", True, f"{lo_day} .. {hi_day} (UTC)"))

    # MMS image inventory (from the original check_image.py).
    if _has_table(conn, "mms_parts"):
        parts = _single_value(conn, "SELECT COUNT(*) FROM mms_parts")
        images = _single_value(
            conn, "SELECT COUNT(*) FROM mms_parts WHERE ct LIKE 'image/%' AND data IS NOT NULL"
        )
        checks.append(
            CheckResult(f"{label} mms_parts", True, f"{parts:,} parts, {images:,} with image data")
        )
    if _has_table(conn, "mms_addrs"):
        addrs = _single_value(conn, "SELECT COUNT(*) FROM mms_addrs")
        checks.append(CheckResult(f"{label} mms_addrs", True, f"{addrs:,} rows"))
    if _has_table(conn, "contacts"):
        contacts = _single_value(conn, "SELECT COUNT(*) FROM contacts")
        checks.append(CheckResult(f"{label} contacts", True, f"{contacts:,} rows"))

    conn.close()
    return checks


def _coerce_ms(value: object) -> int | None:
    """Best-effort epoch-ms coercion (the date column is TEXT in some DBs)."""
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _module_available(name: str) -> bool:
    """Whether *name* is importable (tolerates stubbed test modules)."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def check_optional_dependencies() -> list[CheckResult]:
    """Report optional extra availability (informational; never fails)."""
    extras: list[tuple[str, list[str]]] = [
        ("excel (pandas, openpyxl)", ["pandas", "openpyxl"]),
        ("viewer (flask)", ["flask"]),
        (
            "ocr (pillow, numpy, pytesseract, easyocr, opencv, fuzzywuzzy)",
            ["PIL", "numpy", "pytesseract", "easyocr", "cv2", "fuzzywuzzy"],
        ),
    ]
    checks: list[CheckResult] = []
    for label, modules in extras:
        missing = [m for m in modules if not _module_available(m)]
        if missing:
            checks.append(CheckResult(f"optional: {label}", True, f"missing: {', '.join(missing)}"))
        else:
            checks.append(CheckResult(f"optional: {label}", True, "installed"))
    return checks


def run_diag(sms_db: Path, calls_db: Path | None = None) -> int:
    """Print the diagnostics report; return 0 when all checks pass, else 1."""
    report = DiagReport()
    report.checks.extend(check_database(Path(sms_db), "sms_db"))
    if calls_db is not None:
        report.checks.extend(check_database(Path(calls_db), "calls_db"))
    report.checks.extend(check_optional_dependencies())

    print(report.render())
    return 0 if report.ok else 1
