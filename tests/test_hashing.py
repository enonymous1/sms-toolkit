"""Differential tests: sms_toolkit.hashing vs the original implementations."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from typing import Any

from sms_toolkit import hashing
from tests.diff_helpers import ORIGINAL_ROOT, import_original_module, load_original_function

SMS_SRC = ORIGINAL_ROOT / "sms-backup-analyzer" / "src"
CALLS_SRC = ORIGINAL_ROOT / "calls-backup-analyzer" / "src"

sms_processor = import_original_module("orig_sms_processor", SMS_SRC / "processor.py")
calls_processor = import_original_module("orig_calls_processor", CALLS_SRC / "processor.py")

# Analyzer-side hash methods exec'd in isolation (constructing the exporter
# classes would do file/DB I/O in __init__).
sms_msg_hash = load_original_function(SMS_SRC / "analyzer.py", "_get_msg_hash")
calls_msg_hash = load_original_function(CALLS_SRC / "analyzer.py", "_get_call_hash")
ifttt_msg_hash = load_original_function(SMS_SRC / "import_ifttt_sms.py", "msg_hash")

SMS_VECTORS: list[dict[str, str]] = [
    {"address": "5550101234", "date": "1685712000000", "body": "hello", "type": "1"},
    {"address": "", "date": "", "body": "", "type": ""},
    {"address": "+1 (555) 010-1234", "date": "1685712000000", "body": "multi\nline", "type": "2"},
]

MMS_VECTORS: list[dict[str, str]] = [
    {"address": "5550101234", "date": "1685712000000", "m_type": "132"},
    {"address": "", "date": "", "m_type": ""},
]

CALL_VECTORS: list[dict[str, str]] = [
    {"number": "5550101234", "date": "1685712000000", "duration": "61", "type": "1"},
    {"number": "", "date": "", "duration": "", "type": ""},
]


def _sms_elem(attrs: dict[str, str]) -> Any:
    return ET.Element("sms", attrs)


def _mms_elem(attrs: dict[str, str]) -> Any:
    return ET.Element("mms", attrs)


def _call_elem(attrs: dict[str, str]) -> Any:
    return ET.Element("call", attrs)


def test_dedup_hash_is_md5_hex() -> None:
    # Pinned: the algorithm must stay MD5 — existing DBs store these as PRIMARY KEYs.
    assert hashing.dedup_hash("sms|a|b|c|d") == hashlib.md5(b"sms|a|b|c|d").hexdigest()


def test_sms_key_matches_processor_and_exporter() -> None:
    proc = sms_processor.MessageProcessor()
    for attrs in SMS_VECTORS:
        elem = _sms_elem(attrs)
        key = hashing.sms_key(
            attrs.get("address", ""),
            attrs.get("date", ""),
            attrs.get("body", ""),
            attrs.get("type", ""),
        )
        assert proc.get_hash(elem) == hashing.dedup_hash(key)
        assert sms_msg_hash(None, elem) == hashing.dedup_hash(key)


def test_sms_key_missing_attrs_use_empty_defaults() -> None:
    proc = sms_processor.MessageProcessor()
    elem = _sms_elem({"address": "5550101234"})
    key = hashing.sms_key("5550101234", "", "", "")
    assert proc.get_hash(elem) == hashing.dedup_hash(key)
    assert sms_msg_hash(None, elem) == hashing.dedup_hash(key)


def test_mms_key_matches_exporter() -> None:
    for attrs in MMS_VECTORS:
        elem = _mms_elem(attrs)
        key = hashing.mms_key(
            attrs.get("address", ""), attrs.get("date", ""), attrs.get("m_type", "")
        )
        assert sms_msg_hash(None, elem) == hashing.dedup_hash(key)


def test_call_key_matches_processor_and_exporter() -> None:
    proc = calls_processor.CallProcessor()
    for attrs in CALL_VECTORS:
        elem = _call_elem(attrs)
        key = hashing.call_key(
            attrs.get("number", ""),
            attrs.get("date", ""),
            attrs.get("duration", ""),
            attrs.get("type", ""),
        )
        assert proc.get_hash(elem) == hashing.dedup_hash(key)
        assert calls_msg_hash(None, elem) == hashing.dedup_hash(key)


def test_ifttt_msg_hash_matches() -> None:
    msg = SimpleNamespace(address="5550101234", date_ms=1685712000000, body="hi", type_value="1")
    key = hashing.sms_key("5550101234", str(1685712000000), "hi", "1")
    assert ifttt_msg_hash(msg) == hashing.dedup_hash(key)
