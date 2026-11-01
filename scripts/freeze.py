"""Render the viewer as a static demo site from fictional seed data.

The viewer is a Flask app over a live SQLite database, which GitHub Pages
cannot run. This script seeds a throwaway demo database with invented
conversations (Alice Example / Bob Example — no real data, ever), runs the
viewer's own query functions against it, and writes a static site:

- ``index.html`` — the shipped template, plus a "Static demo" banner, the
  ``demo-shim.js`` fetch shim (injected before the viewer script), and
  ``/api/image/`` URLs relativized for the Pages subpath
- ``demo-shim.js`` — rewrites the viewer's ``/api/...`` fetches to static
  files (see that file for the full mapping)
- ``api/stats``, ``api/conversations``, ``api/messages`` — static JSON
- ``api/conversation/<address>`` — one static JSON file per thread
- ``api/message/<hash>`` — static JSON detail per message
- ``api/image/<hash>/<seq>`` — raw image bytes per image part

The output directory is wiped and rebuilt on every run, so stale threads
cannot survive a seed change. It defaults to ``freeze-site/`` at the repo
root, which is gitignored: the frozen site is a build artifact, deployed
by the Pages workflow, never committed.

Usage: ``python scripts/freeze.py [--out DIR]``. Only the base install is
needed (no Flask): the script reads the template as text and calls the
query functions directly. If the package is not installed, ``src/`` is
added to ``sys.path`` as a fallback so a plain checkout still works.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
try:
    from sms_toolkit.db import ensure_messages_table
    from sms_toolkit.viewer import queries
except ImportError:  # plain checkout without an install: use src/ directly
    sys.path.insert(0, str(ROOT / "src"))
    from sms_toolkit.db import ensure_messages_table
    from sms_toolkit.viewer import queries

TEMPLATE = ROOT / "src/sms_toolkit/viewer/templates/index.html"
SHIM = Path(__file__).resolve().parent / "demo-shim.js"

BANNER = (
    '<div id="demo-banner" style="position:fixed;top:0;left:0;right:0;z-index:50;'
    'text-align:center;padding:.3rem;background:#0a84ff;color:#fff;font-size:.8rem">'
    "Static demo · fictional data only · the live viewer runs locally "
    "against your own archive</div>"
)

# 1x1 transparent PNG: the demo MMS photo. A placeholder, not a screenshot.
PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)

# (hash, tag, address, normalized, date_ms, body, type, readable, contact)
MESSAGES: list[tuple[str, str, str, str, str, str, str, str, str]] = [
    (
        "demo-alice-1",
        "sms",
        "15550101001",
        "15550101001",
        "1717675200000",
        "Hey! Are we still on for the lake house this weekend?",
        "1",
        "June 6, 2024 12:00:00 PM",
        "Alice Example",
    ),
    (
        "demo-alice-2",
        "sms",
        "15550101001",
        "15550101001",
        "1717675500000",
        "Yes — leaving Saturday morning. Bringing the grill.",
        "2",
        "June 6, 2024 12:05:00 PM",
        "Alice Example",
    ),
    (
        "demo-alice-3",
        "mms",
        "15550101001",
        "15550101001",
        "1717675800000",
        "[MMS]",
        "132",
        "June 6, 2024 12:10:00 PM",
        "Alice Example",
    ),
    (
        "demo-alice-4",
        "sms",
        "15550101001",
        "15550101001",
        "1717698600000",
        "That sunset photo is perfect. See you Saturday!",
        "1",
        "June 6, 2024 6:30:00 PM",
        "Alice Example",
    ),
    (
        "demo-bob-1",
        "sms",
        "15550101002",
        "15550101002",
        "1717751700000",
        "Did you see the game last night?",
        "2",
        "June 7, 2024 9:15:00 AM",
        "Bob Example",
    ),
    (
        "demo-bob-2",
        "sms",
        "15550101002",
        "15550101002",
        "1717752000000",
        "Unbelievable finish. We need a rematch soon.",
        "1",
        "June 7, 2024 9:20:00 AM",
        "Bob Example",
    ),
    (
        "demo-bob-3",
        "sms",
        "15550101002",
        "15550101002",
        "1717752120000",
        "Next weekend, my place. I'll text the group.",
        "2",
        "June 7, 2024 9:22:00 AM",
        "Bob Example",
    ),
]


def _seed_demo_db(path: Path) -> None:
    """Create the demo database: the real messages DDL, invented rows."""
    conn = sqlite3.connect(path)
    try:
        ensure_messages_table(conn)
        # Mirrors sms/variant.py's mms_parts / mms_addrs schemas.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS mms_parts (
                msg_hash TEXT, seq INTEGER, ct TEXT, name TEXT, text TEXT, data TEXT,
                FOREIGN KEY(msg_hash) REFERENCES messages(msg_hash))"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS mms_addrs (
                msg_hash TEXT, address TEXT, type TEXT, charset TEXT,
                FOREIGN KEY(msg_hash) REFERENCES messages(msg_hash))"""
        )
        conn.executemany(
            "INSERT INTO messages (msg_hash, tag, address, normalized_address, date,"
            " normalized_date_ms, body, type, readable_date, contact_name)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    h,
                    tag,
                    addr,
                    norm,
                    date,
                    int(date),
                    body,
                    typ,
                    readable,
                    contact,
                )
                for h, tag, addr, norm, date, body, typ, readable, contact in MESSAGES
            ],
        )
        conn.execute(
            "INSERT INTO mms_parts (msg_hash, seq, ct, name, text, data) VALUES (?, ?, ?, ?, ?, ?)",
            ("demo-alice-3", 0, "text/plain", None, "Photo from the lake house", None),
        )
        conn.execute(
            "INSERT INTO mms_parts (msg_hash, seq, ct, name, text, data) VALUES (?, ?, ?, ?, ?, ?)",
            ("demo-alice-3", 1, "image/png", "sunset.png", None, PNG_1X1_B64),
        )
        conn.execute(
            "INSERT INTO mms_addrs (msg_hash, address, type, charset) VALUES (?, ?, ?, ?)",
            ("demo-alice-3", "15550101001", "137", "106"),
        )
        conn.commit()
    finally:
        conn.close()


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _freeze_index(out: Path) -> None:
    """Copy the template with banner, shim, and relativized image URLs."""
    html = TEMPLATE.read_text(encoding="utf-8")
    if html.count("<script>") != 1:
        raise SystemExit("freeze: template no longer has exactly one <script> block")
    if html.count("<body>") != 1:
        raise SystemExit("freeze: template no longer has exactly one <body> tag")
    html = html.replace("<body>", "<body>\n" + BANNER, 1)
    html = html.replace("<script>", '<script src="demo-shim.js"></script>\n<script>', 1)
    # <img> URLs are not fetches, so the shim cannot rewrite them; relativize
    # them here so they resolve under the Pages subpath (/sms-toolkit/).
    html = html.replace("/api/image/", "api/image/")
    (out / "index.html").write_text(html, encoding="utf-8")
    shutil.copyfile(SHIM, out / "demo-shim.js")


def freeze(out: Path) -> int:
    """Wipe *out* and rebuild the static demo site. Returns files written."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    _freeze_index(out)
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "demo.sqlite"
        _seed_demo_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            _write_json(out / "api" / "stats", queries.get_stats(conn))
            _write_json(out / "api" / "conversations", queries.list_conversations(conn, limit=200))
            _write_json(out / "api" / "messages", queries.search_messages(conn, page=1))
            addresses = [row[0] for row in conn.execute("SELECT DISTINCT address FROM messages")]
            hashes = [row[0] for row in conn.execute("SELECT msg_hash FROM messages")]
            for address in addresses:
                _write_json(
                    out / "api" / "conversation" / quote(address, safe=""),
                    queries.get_conversation(conn, address),
                )
            for msg_hash in hashes:
                detail = queries.get_message_detail(conn, msg_hash)
                if detail is not None:
                    _write_json(out / "api" / "message" / msg_hash, detail)
            image_files = 0
            for msg_hash, seq in conn.execute(
                "SELECT msg_hash, seq FROM mms_parts WHERE ct LIKE 'image/%'"
            ):
                result = queries.get_image_part(conn, msg_hash, seq)
                if result is not None:
                    payload, _ct = result
                    img_path = out / "api" / "image" / msg_hash / str(seq)
                    img_path.parent.mkdir(parents=True, exist_ok=True)
                    img_path.write_bytes(payload)
                    image_files += 1
        finally:
            conn.close()
    count = sum(1 for p in out.rglob("*") if p.is_file())
    print(f"froze {count} files into {out} ({image_files} images)")
    return count


def main(argv: list[str] | None = None) -> int:
    """Build the demo site into ``freeze-site/`` (or ``--out``)."""
    parser = argparse.ArgumentParser(description="Render the viewer as a static demo site.")
    parser.add_argument("--out", type=Path, default=ROOT / "freeze-site")
    args = parser.parse_args(argv)
    freeze(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
