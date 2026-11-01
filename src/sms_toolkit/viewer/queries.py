"""SQLite query helpers for the SMS backup viewer.

Ports the queries from ``sms-backup-viewer/app.py`` verbatim; the Flask layer
in :mod:`sms_toolkit.viewer.app` only handles HTTP concerns.

Two deliberate hardenings versus the original: MMS parts are always read in
``seq`` order (the original relied on implicit rowid order, which is not a
guarantee), and search terms escape LIKE wildcards so a user typing ``%``
does not accidentally match everything.
"""

from __future__ import annotations

import base64
import binascii
import sqlite3
from typing import Any

PER_PAGE_DEFAULT = 50
CONVERSATION_PAGE_DEFAULT = 100
# Sidebar default: the conversation list is unbounded without this, and a
# real archive has tens of thousands of addresses. Server-side search (see
# list_conversations) keeps the sidebar usable past the cap.
CONVERSATION_LIST_DEFAULT_LIMIT = 200


def _escape_like(term: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` so a LIKE pattern matches literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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


def _fetch_mms_enrichment(
    conn: sqlite3.Connection, msg_hashes: list[str]
) -> dict[str, dict[str, Any]]:
    """Batch-fetch MMS text/image data for *msg_hashes* in two queries.

    The old code enriched each MMS row with two queries of its own, so one
    500-message all-MMS page cost ~1,000 queries. This fetches the page's
    parts once and groups them in Python; part order follows ``seq``.
    """
    enrichment: dict[str, dict[str, Any]] = {
        h: {"body": None, "has_images": False, "image_seqs": []} for h in msg_hashes
    }
    if not msg_hashes:
        return enrichment
    placeholders = ",".join("?" for _ in msg_hashes)
    text_parts: dict[str, list[str]] = {h: [] for h in msg_hashes}
    for msg_hash, text in conn.execute(
        f"SELECT msg_hash, text FROM mms_parts WHERE msg_hash IN ({placeholders})"
        ' AND ct = "text/plain" ORDER BY seq',
        msg_hashes,
    ).fetchall():
        if text:
            text_parts[msg_hash].append(text)
    for msg_hash, seq in conn.execute(
        f"SELECT msg_hash, seq FROM mms_parts WHERE msg_hash IN ({placeholders})"
        ' AND ct LIKE "image/%" ORDER BY seq',
        msg_hashes,
    ).fetchall():
        enrichment[msg_hash]["has_images"] = True
        enrichment[msg_hash]["image_seqs"].append(seq)
    for msg_hash, parts in text_parts.items():
        if parts:
            # " | " is a display convention, not data: the joined string is
            # only ever shown in the thread view.
            enrichment[msg_hash]["body"] = " | ".join(parts)
    return enrichment


def _apply_mms_enrichment(
    messages: list[dict[str, Any]], enrichment: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply batched enrichment to MMS rows; SMS rows get direction only."""
    for msg in messages:
        if msg["tag"] == "mms":
            data = enrichment[msg["msg_hash"]]
            if data["body"] is not None:
                msg["body"] = data["body"]
            msg["has_images"] = data["has_images"]
            msg["image_seqs"] = data["image_seqs"]
        else:
            msg["has_images"] = False
        msg["direction"] = _direction(msg)
    return messages


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
    image parts. (The return shape stays a bare list: the archive UI
    paginates by requesting pages until one comes back empty, and the
    shipped frontend expects an array.)
    """
    offset = (page - 1) * per_page
    search_param = f"%{_escape_like(search)}%"
    rows = conn.execute(
        "SELECT * FROM messages"
        " WHERE (body LIKE ? ESCAPE '\\' OR contact_name LIKE ? ESCAPE '\\'"
        " OR address LIKE ? ESCAPE '\\')"
        " ORDER BY date DESC LIMIT ? OFFSET ?",
        (search_param, search_param, search_param, per_page, offset),
    ).fetchall()

    messages = [dict(row) for row in rows]
    mms_hashes = [m["msg_hash"] for m in messages if m["tag"] == "mms"]
    return _apply_mms_enrichment(messages, _fetch_mms_enrichment(conn, mms_hashes))


def list_conversations(
    conn: sqlite3.Connection,
    *,
    search: str = "",
    limit: int = CONVERSATION_LIST_DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """One row per address: name, count, and latest-message timestamp.

    Ordered by most recent activity first. ``date`` is cast defensively
    because real databases may store it as TEXT. The list is capped
    (default 200) — without a cap this serializes every address in the
    archive as one JSON blob. Pass *search* to filter server-side by
    address or contact name instead of fetching everything and filtering
    in the browser, which is what the sidebar used to do.

    Why a window function instead of GROUP BY: one pass yields the latest
    row plus counts plus MMS counts. The correlated-subquery version this
    replaced took over 3 minutes on an 80k-message database.
    """
    search_param = f"%{_escape_like(search)}%"
    rows = conn.execute(
        """
        SELECT address, contact_name, body AS last_body, tag AS last_tag,
               count, last_date_ms, mms_count
          FROM (
                SELECT address, contact_name, body, tag,
                       COUNT(*) OVER (PARTITION BY address) AS count,
                       MAX(CAST(date AS INTEGER))
                           OVER (PARTITION BY address) AS last_date_ms,
                       SUM(CASE WHEN tag = 'mms' THEN 1 ELSE 0 END)
                           OVER (PARTITION BY address) AS mms_count,
                       ROW_NUMBER() OVER (
                           PARTITION BY address
                           ORDER BY CAST(date AS INTEGER) DESC
                       ) AS rn
                  FROM messages
               )
         WHERE rn = 1
           AND (address LIKE ? ESCAPE '\\' OR contact_name LIKE ? ESCAPE '\\')
         ORDER BY last_date_ms DESC
         LIMIT ?
        """,
        (search_param, search_param, limit),
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

    messages = [dict(row) for row in rows]
    mms_hashes = [m["msg_hash"] for m in messages if m["tag"] == "mms"]
    messages = _apply_mms_enrichment(messages, _fetch_mms_enrichment(conn, mms_hashes))
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
    for part_row in conn.execute(
        "SELECT * FROM mms_parts WHERE msg_hash = ? ORDER BY seq", (msg_hash,)
    ):
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

    Returns ``None`` when the part is missing, has no data, is not an
    image, or the stored base64 is corrupt (all are 404, instead of the
    original's 500 on corrupt data). The image-only whitelist matters:
    the stored ``ct`` used to be served verbatim as the response
    Content-Type, so a part with ``ct = text/html`` would render
    attacker-influenced HTML in the viewer's origin.
    """
    part = conn.execute(
        "SELECT data, ct FROM mms_parts WHERE msg_hash = ? AND seq = ?", (msg_hash, seq)
    ).fetchone()
    if part and part["data"]:
        content_type = part["ct"] or ""
        if not content_type.startswith("image/"):
            return None
        try:
            return base64.b64decode(part["data"]), content_type
        except (binascii.Error, ValueError):
            return None
    return None
