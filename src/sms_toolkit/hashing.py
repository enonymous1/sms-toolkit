"""Dedup hashing for sms_toolkit.

Every pipeline (sms, mms, calls, IFTTT import) identifies duplicates by the
MD5 hex digest of a ``kind|field|field|...`` key string. MD5 is kept
deliberately: these hashes are stored as PRIMARY KEYs in existing databases,
so the algorithm must stay byte-identical to the originals or dedup against
existing data silently breaks. (This is not a security use.)

Unifies:
- ``sms-backup-analyzer`` ``processor.get_hash`` / ``analyzer._get_msg_hash``
- ``calls-backup-analyzer`` ``processor.get_hash`` / ``analyzer._get_call_hash``
- ``import_ifttt_sms.ParsedMessage.msg_hash``
"""

from __future__ import annotations

import hashlib


def dedup_hash(key: str) -> str:
    """MD5 hex digest of a dedup key string (kept byte-identical to the originals)."""
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def sms_key(address: str, date: str, body: str, msg_type: str) -> str:
    """Build the dedup key for an ``<sms>`` element."""
    return f"sms|{address}|{date}|{body}|{msg_type}"


def mms_key(address: str, date: str, m_type: str) -> str:
    """Build the dedup key for an ``<mms>`` element."""
    return f"mms|{address}|{date}|{m_type}"


def call_key(number: str, date: str, duration: str, call_type: str) -> str:
    """Build the dedup key for a ``<call>`` element."""
    return f"call|{number}|{date}|{duration}|{call_type}"
