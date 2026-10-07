"""Match extracted screenshot messages against SMS database records.

Port of ``screenshot_analyzer/match_messages.py``: sender identification
(name/phone) is primary, text content similarity (fuzzy) scores the candidates,
and timestamp matching is de-emphasized because OCR timestamps are unreliable.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from sms_toolkit.logging_config import get_logger

logger = get_logger(__name__)

_OCR_EXTRA = "pip install sms-toolkit[ocr]"
_SIMILARITY_THRESHOLD = 0.25
_MAX_MATCHES = 5
_CANDIDATE_LIMIT = 50


def _fuzz() -> Any:
    """Import fuzzywuzzy lazily (it is an optional ``ocr`` extra)."""
    try:
        from fuzzywuzzy import fuzz  # type: ignore[import-not-found]

        return fuzz
    except ImportError as exc:
        raise RuntimeError(f"fuzzywuzzy is required for message matching ({_OCR_EXTRA})") from exc


class MessageMatcher:
    """Match OCR-extracted messages against the ``messages`` table."""

    def __init__(self, db_path: str | Path = "sms_db.sqlite") -> None:
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()

    def __enter__(self) -> MessageMatcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_db_schema(self) -> list[str]:
        """Inspect the messages table schema (prints it, returns columns)."""
        self.cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'")
        result = self.cursor.fetchone()
        if result:
            print("Messages table schema:")
            print(result[0])
            print()

        self.cursor.execute("SELECT * FROM messages LIMIT 1")
        columns = [description[0] for description in self.cursor.description or []]
        print(f"Columns: {columns}\n")
        return columns

    def find_matches_for_message(self, extracted_msg: dict[str, Any]) -> list[dict[str, Any]]:
        """Find candidate DB matches for one extracted message.

        Strategy: match by phone (most reliable) or sender name, then score by
        fuzzy content similarity. Returns the top matches with confidence
        scores, highest first.
        """
        matches: list[dict[str, Any]] = []

        sender = extracted_msg.get("sender")
        phone = extracted_msg.get("phone_number")
        content = extracted_msg.get("content", "").strip()

        # Build query focused on sender identification
        query = "SELECT * FROM messages WHERE 1=1"
        params: list[str] = []

        # PRIMARY: match by phone number if available (most reliable)
        if phone:
            clean_phone = "".join(c for c in phone if c.isdigit())
            query += " AND address LIKE ?"
            params.append(f"%{clean_phone}%")
        # FALLBACK: match by sender name if phone not available
        elif sender:
            query += " AND contact_name LIKE ?"
            params.append(f"%{sender}%")
        else:
            # No sender info - cannot reliably match, return empty
            return []

        # Limit results for performance
        query += f" LIMIT {_CANDIDATE_LIMIT}"

        try:
            self.cursor.execute(query, params)
            potential_matches = self.cursor.fetchall()

            # Score each match based on content similarity
            for db_record in potential_matches:
                db_msg = db_record["body"] if "body" in db_record.keys() else ""  # noqa: SIM118 - verbatim port

                # Use fuzzy string matching for better OCR tolerance
                similarity = self._compute_fuzzy_similarity(content, db_msg)

                # Lower threshold since OCR text may have errors
                if similarity > _SIMILARITY_THRESHOLD:  # 25% similarity minimum
                    matches.append(
                        {
                            "confidence": similarity,
                            "db_record": dict(db_record),
                            "msg_hash": db_record["msg_hash"],
                            "contact_name": db_record["contact_name"],
                            "phone": db_record["address"],
                            "timestamp": db_record["readable_date"],
                            "body_preview": db_msg[:100],
                            "body_full": db_msg,
                        }
                    )

            # Sort by confidence (highest first)
            matches.sort(key=lambda x: x["confidence"], reverse=True)
            return matches[:_MAX_MATCHES]

        except sqlite3.OperationalError as exc:
            print(f"Query error: {exc}")
            return []

    def _compute_fuzzy_similarity(self, text1: str, text2: str) -> float:
        """Fuzzy similarity between two strings (0-1), tolerant of OCR errors.

        Uses ``token_set_ratio``, which handles partial/subset matches well.
        """
        if not text1 or not text2:
            return 0.0

        # Normalize text
        t1 = text1.lower().strip()
        t2 = text2.lower().strip()

        # Use fuzzy matching - token_set_ratio is good for OCR text with errors
        # It breaks text into tokens and compares (more flexible than ratio)
        fuzzy_score = float(_fuzz().token_set_ratio(t1, t2) / 100.0)

        return fuzzy_score

    def enrich_extracted_messages(self, extracted_json_path: str | Path) -> dict[str, Any]:
        """Match every message in an extraction JSON against the database.

        Adds ``database_matches`` to each message and a ``match_summary``;
        returns the enriched payload (the caller decides where to save it).
        """
        with open(extracted_json_path, encoding="utf-8") as handle:
            data: dict[str, Any] = json.load(handle)

        total = len(data["messages"])
        print(f"Processing {total} extracted messages...\n")

        for i, msg in enumerate(data["messages"], 1):
            sender = msg.get("sender") or "[Unknown]"
            phone = msg.get("phone_number") or "[No phone]"
            content_preview = msg.get("content", "")[:60].replace("\n", " ")

            print(f"[{i}/{total}] {sender} {phone}")
            print(f"           Content: {content_preview}...")

            matches = self.find_matches_for_message(msg)

            if matches:
                print(f"           Found {len(matches)} match(es):")
                for j, match in enumerate(matches, 1):
                    conf = match["confidence"]
                    contact = match["contact_name"]
                    preview = match["body_preview"].replace("\n", " ")
                    print(f"             [{j}] {contact} ({conf * 100:.0f}%) - {preview}...")
            else:
                print("           No matches")

            # Add matches to message
            msg["database_matches"] = matches
            print()

        total_with_matches = sum(1 for msg in data["messages"] if msg.get("database_matches"))
        data["match_summary"] = {
            "total_messages": total,
            "messages_with_matches": total_with_matches,
            "match_rate": f"{(total_with_matches / total * 100):.1f}%" if total else "0.0%",
            "matching_strategy": "Sender name/phone (primary) + content similarity (fuzzy)",
            "note": "Timestamp matching excluded due to OCR unreliability",
        }

        return data

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
