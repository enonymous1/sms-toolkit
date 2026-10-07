"""Build/refresh the canonical address -> contact name lookup table.

Ports ``build_contacts_table.py``. For each normalized address the most
frequent non-empty contact name becomes canonical; the raw address/name
variants seen are kept as JSON. Multi-participant MMS rows store addresses
and names as ``~`` / ``,`` delimited lists in matching order, which lets a
name be attributed to every participant address, not just the row's primary
contact. Safe to rerun: rows are upserted.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sms_toolkit.logging_config import get_logger
from sms_toolkit.normalize import normalize_address

logger = get_logger(__name__)

UNKNOWN_NAMES = {"", "(unknown)", "unknown"}

CONTACTS_DDL = """
CREATE TABLE IF NOT EXISTS contacts (
    normalized_address TEXT PRIMARY KEY,
    contact_name TEXT,
    message_count INTEGER,
    raw_addresses_json TEXT,
    raw_contact_names_json TEXT,
    updated_at TEXT
)
"""


def ensure_contacts_table(conn: sqlite3.Connection) -> None:
    """Create the contacts table if it does not exist."""
    conn.execute(CONTACTS_DDL)


def collect_name_counts(
    conn: sqlite3.Connection,
) -> tuple[dict[str, dict[str, int]], dict[str, set[str]]]:
    """Count contact names per normalized address; also collect raw addresses."""
    name_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    raw_addresses: dict[str, set[str]] = defaultdict(set)

    rows = conn.execute(
        "SELECT address, contact_name FROM messages "
        "WHERE contact_name IS NOT NULL AND TRIM(contact_name) <> ''"
    ).fetchall()

    for raw_address, contact_name in rows:
        raw_address = str(raw_address or "")
        contact_name = str(contact_name or "").strip()
        if not contact_name or contact_name.lower() in UNKNOWN_NAMES:
            continue

        addresses = [a.strip() for a in raw_address.split("~") if a.strip()]
        names = [n.strip() for n in contact_name.split(",") if n.strip()]

        if len(addresses) > 1 and len(addresses) == len(names):
            pairs = list(zip(addresses, names, strict=True))
        elif raw_address.strip():
            pairs = [(raw_address.strip(), contact_name)]
        else:
            pairs = []

        for address, name in pairs:
            if name.lower() in UNKNOWN_NAMES:
                continue
            norm = normalize_address(address)
            if not norm:
                continue
            name_counts[norm][name] += 1
            raw_addresses[norm].add(address)

    return name_counts, raw_addresses


def choose_best_name(counts: dict[str, int]) -> str:
    """Pick the canonical name: highest count, then longest, then alphabetical."""
    return sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0].lower()))[0][0]


def build_contacts(db_path: Path, dry_run: bool) -> int:
    """Build/refresh the contacts table; returns the number of contacts resolved."""
    conn = sqlite3.connect(db_path)
    try:
        ensure_contacts_table(conn)
        name_counts, raw_addresses = collect_name_counts(conn)

        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for norm, counts in name_counts.items():
            best_name = choose_best_name(counts)
            total = sum(counts.values())
            rows.append(
                (
                    norm,
                    best_name,
                    total,
                    json.dumps(sorted(raw_addresses[norm]), ensure_ascii=False),
                    json.dumps(sorted(counts.keys()), ensure_ascii=False),
                    now,
                )
            )

        if not dry_run:
            conn.executemany(
                """
                INSERT INTO contacts (
                    normalized_address, contact_name, message_count,
                    raw_addresses_json, raw_contact_names_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(normalized_address) DO UPDATE SET
                    contact_name=excluded.contact_name,
                    message_count=excluded.message_count,
                    raw_addresses_json=excluded.raw_addresses_json,
                    raw_contact_names_json=excluded.raw_contact_names_json,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
            conn.commit()

        logger.info("Built contacts table: %d contacts (dry_run=%s)", len(rows), dry_run)
        return len(rows)
    finally:
        conn.close()


def run_build_contacts(db_path: Path, *, dry_run: bool) -> int:
    """Run the build-contacts stage; returns a process exit code."""
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 1
    count = build_contacts(db_path, dry_run)
    mode = "DRY-RUN" if dry_run else "WRITE"
    print(f"[{mode}] contacts resolved: {count}")
    return 0
