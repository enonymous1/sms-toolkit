#!/usr/bin/env python3
"""Build/refresh a canonical address -> contact_name lookup table.

Non-destructive: only creates/refreshes the `contacts` table in the target DB
by scanning `messages.address` / `messages.contact_name`. Multi-participant
MMS rows store both fields as `~` / `, ` delimited lists in matching order,
which lets us attribute a name to every participant address, not just the
row's primary contact.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Set, Tuple

UNKNOWN_NAMES = {"", "(unknown)", "unknown"}


def normalize_address_value(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def ensure_contacts_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contacts (
            normalized_address TEXT PRIMARY KEY,
            contact_name TEXT,
            message_count INTEGER,
            raw_addresses_json TEXT,
            raw_contact_names_json TEXT,
            updated_at TEXT
        )
        """
    )


def collect_name_counts(
    conn: sqlite3.Connection,
) -> Tuple[Dict[str, Dict[str, int]], Dict[str, Set[str]]]:
    name_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    raw_addresses: Dict[str, Set[str]] = defaultdict(set)

    rows = conn.execute(
        "SELECT address, contact_name FROM messages WHERE contact_name IS NOT NULL AND TRIM(contact_name) <> ''"
    ).fetchall()

    for raw_address, contact_name in rows:
        raw_address = str(raw_address or "")
        contact_name = str(contact_name or "").strip()
        if not contact_name or contact_name.lower() in UNKNOWN_NAMES:
            continue

        addresses = [a.strip() for a in raw_address.split("~") if a.strip()]
        names = [n.strip() for n in contact_name.split(",") if n.strip()]

        if len(addresses) > 1 and len(addresses) == len(names):
            pairs = list(zip(addresses, names))
        elif raw_address.strip():
            pairs = [(raw_address.strip(), contact_name)]
        else:
            pairs = []

        for address, name in pairs:
            if name.lower() in UNKNOWN_NAMES:
                continue
            norm = normalize_address_value(address)
            if not norm:
                continue
            name_counts[norm][name] += 1
            raw_addresses[norm].add(address)

    return name_counts, raw_addresses


def choose_best_name(counts: Dict[str, int]) -> str:
    return sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0].lower()))[0][0]


def build_contacts(db_path: Path, dry_run: bool) -> int:
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

        return len(rows)
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build/refresh the contacts lookup table from messages.",
    )
    parser.add_argument("--db", default="sms_db.sqlite", help="Canonical DB path (default: sms_db.sqlite)")
    parser.add_argument("--dry-run", action="store_true", help="Report only, do not write")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 1

    count = build_contacts(db_path, args.dry_run)
    mode = "DRY-RUN" if args.dry_run else "WRITE"
    print(f"[{mode}] contacts resolved: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
