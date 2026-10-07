"""SQLite query helpers for the SMS backup viewer.

Ports the queries from ``sms-backup-viewer/app.py`` verbatim; the Flask layer
in :mod:`sms_toolkit.viewer.app` only handles HTTP concerns.
"""

from __future__ import annotations

import base64
import binascii
import sqlite3
from typing import Any

PER_PAGE_DEFAULT = 50


def get_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Database-wide counts plus the top 5 contacts by message volume."""
    stats: dict[str, Any] = {}
    stats["total_messages"] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    stats["total_sms"] = conn.execute('SELECT COUNT(*) FROM messages WHERE tag="sms"').fetchone()[0]
    stats["total_mms"] = conn.execute('SELECT COUNT(*) FROM messages WHERE tag="mms"').fetchone()[0]
    stats["top_contacts"] = [
        dict(row)
        for row in conn.execute(
            "SELECT contact_name, address, COUNT(*) as count FROM messages"
            " GROUP BY address ORDER BY count DESC LIMIT 5"
        ).fetchall()
    ]
    return stats


def search_messages(
    conn: sqlite3.Connection, *, page: int = 1, per_page: int = PER_PAGE_DEFAULT, search: str = ""
) -> list[dict[str, Any]]:
    """One page of messages matching *search*, newest first.

    MMS rows are enriched like the original: ``body`` is replaced with the
    joined ``text/plain`` part texts when present, and ``has_images`` flags
    image parts.
    """
    offset = (page - 1) * per_page
    search_param = f"%{search}%"
    rows = conn.execute(
        "SELECT * FROM messages"
        " WHERE (body LIKE ? OR contact_name LIKE ? OR address LIKE ?)"
        " ORDER BY date DESC LIMIT ? OFFSET ?",
        (search_param, search_param, search_param, per_page, offset),
    ).fetchall()

    messages: list[dict[str, Any]] = []
    for row in rows:
        msg = dict(row)
        if msg["tag"] == "mms":
            # Check for text in parts if body is generic [MMS]
            parts = conn.execute(
                'SELECT text, ct FROM mms_parts WHERE msg_hash = ? AND ct = "text/plain"',
                (msg["msg_hash"],),
            ).fetchall()
            if parts:
                msg["body"] = " | ".join([p["text"] for p in parts if p["text"]])

            # Check for image parts
            images = conn.execute(
                'SELECT seq FROM mms_parts WHERE msg_hash = ? AND ct LIKE "image/%"',
                (msg["msg_hash"],),
            ).fetchall()
            msg["has_images"] = len(images) > 0
        messages.append(msg)

    return messages


def get_message_detail(conn: sqlite3.Connection, msg_hash: str) -> dict[str, Any] | None:
    """Full detail for one message: row, parts, and addresses.

    Part payloads are replaced with a ``has_data`` flag to keep the JSON
    small. Returns ``None`` when the hash is unknown (the original raised a
    ``TypeError`` here and returned HTTP 500).
    """
    row = conn.execute("SELECT * FROM messages WHERE msg_hash = ?", (msg_hash,)).fetchone()
    if row is None:
        return None
    msg = dict(row)

    detail_parts: list[dict[str, Any]] = []
    for part_row in conn.execute("SELECT * FROM mms_parts WHERE msg_hash = ?", (msg_hash,)):
        part = dict(part_row)
        # Include a has_data flag instead of raw data to keep the JSON small.
        part["has_data"] = part["data"] is not None
        del part["data"]
        detail_parts.append(part)

    addrs = [
        dict(addr_row)
        for addr_row in conn.execute("SELECT * FROM mms_addrs WHERE msg_hash = ?", (msg_hash,))
    ]
    return {"message": msg, "parts": detail_parts, "addrs": addrs}


def get_image_part(conn: sqlite3.Connection, msg_hash: str, seq: int) -> tuple[bytes, str] | None:
    """Decoded image bytes and content type for one MMS part.

    Returns ``None`` when the part is missing, has no data, or the stored
    base64 is corrupt (all are 404, instead of the original's 500 on corrupt
    data).
    """
    part = conn.execute(
        "SELECT data, ct FROM mms_parts WHERE msg_hash = ? AND seq = ?", (msg_hash, seq)
    ).fetchone()
    if part and part["data"]:
        try:
            return base64.b64decode(part["data"]), part["ct"]
        except (binascii.Error, ValueError):
            return None
    return None
