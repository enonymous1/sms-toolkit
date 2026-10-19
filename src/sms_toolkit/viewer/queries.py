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
CONVERSATION_PAGE_DEFAULT = 100


def _direction(msg: dict[str, Any]) -> str:
    """Derive bubble direction from the stored ``type`` column.

    SMS Backup & Restore semantics: sms ``type`` 1 = received, 2 = sent;
    mms ``m_type`` 128 = sent (m-send-req), 132 = received. Unknown or
    missing values default to ``"received"``.
    """
    tag = msg.get("tag")
    msg_type = str(msg.get("type") or "").strip()
    if tag == "sms":
        return "sent" if msg_type == "2" else "received"
    return "sent" if msg_type == "128" else "received"


def _enrich_mms(conn: sqlite3.Connection, msg: dict[str, Any]) -> dict[str, Any]:
    """Replace an MMS placeholder body with joined text parts; flag images."""
    parts = conn.execute(
        'SELECT text, ct FROM mms_parts WHERE msg_hash = ? AND ct = "text/plain"',
        (msg["msg_hash"],),
    ).fetchall()
    if parts:
        msg["body"] = " | ".join([p["text"] for p in parts if p["text"]])

    images = conn.execute(
        'SELECT seq FROM mms_parts WHERE msg_hash = ? AND ct LIKE "image/%"',
        (msg["msg_hash"],),
    ).fetchall()
    msg["has_images"] = len(images) > 0
    msg["image_seqs"] = [img["seq"] for img in images]
    msg["direction"] = _direction(msg)
    return msg


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
            msg = _enrich_mms(conn, msg)
        else:
            msg["direction"] = _direction(msg)
            msg["has_images"] = False
        messages.append(msg)

    return messages


def list_conversations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """One row per address: name, count, and latest-message timestamp.

    Ordered by most recent activity first. ``date`` is cast defensively
    because real databases may store it as TEXT.
    """
    rows = conn.execute(
        """
        SELECT address,
               (SELECT m2.contact_name FROM messages m2
                 WHERE m2.address = m.address
                 ORDER BY CAST(m2.date AS INTEGER) DESC LIMIT 1) AS contact_name,
               (SELECT m3.body FROM messages m3
                 WHERE m3.address = m.address
                 ORDER BY CAST(m3.date AS INTEGER) DESC LIMIT 1) AS last_body,
               (SELECT m4.tag FROM messages m4
                 WHERE m4.address = m.address
                 ORDER BY CAST(m4.date AS INTEGER) DESC LIMIT 1) AS last_tag,
               COUNT(*) AS count,
               MAX(CAST(date AS INTEGER)) AS last_date_ms,
               SUM(CASE WHEN tag = 'mms' THEN 1 ELSE 0 END) AS mms_count
          FROM messages m
         GROUP BY address
         ORDER BY last_date_ms DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_conversation(
    conn: sqlite3.Connection,
    address: str,
    *,
    limit: int = CONVERSATION_PAGE_DEFAULT,
    before: int | None = None,
) -> dict[str, Any]:
    """Messages with *address* in chronological order (oldest first).

    When *before* is given, only messages older than that date-ms are
    returned, for "load earlier" pagination. ``has_more`` reports whether
    older messages remain.
    """
    query = "SELECT * FROM messages WHERE address = ?"
    args: list[Any] = [address]
    if before is not None:
        query += " AND CAST(date AS INTEGER) < ?"
        args.append(before)
    query += " ORDER BY CAST(date AS INTEGER) DESC LIMIT ?"
    args.append(limit + 1)
    rows = conn.execute(query, args).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]

    messages: list[dict[str, Any]] = []
    for row in rows:
        msg = dict(row)
        if msg["tag"] == "mms":
            msg = _enrich_mms(conn, msg)
        else:
            msg["direction"] = _direction(msg)
            msg["has_images"] = False
        messages.append(msg)
    messages.reverse()  # oldest first for chat display

    oldest_ms = None
    if messages:
        try:
            oldest_ms = int(messages[0]["date"])
        except (TypeError, ValueError):
            oldest_ms = None
    return {"messages": messages, "has_more": has_more, "oldest_ms": oldest_ms}


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
    msg["direction"] = _direction(msg)

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
