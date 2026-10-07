"""Tests for the pipeline core: variants, processor, parser (with differentials)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sms_toolkit.calls.variant import CALLS_VARIANT
from sms_toolkit.pipeline.parser import stream_elements
from sms_toolkit.pipeline.processor import DedupProcessor
from sms_toolkit.sms.variant import SMS_VARIANT
from tests.diff_helpers import (
    CALLS_FIXTURE_XML,
    ORIGINAL_ROOT,
    SMS_FIXTURE_XML,
    import_original_module,
)

SMS_SRC = ORIGINAL_ROOT / "sms-backup-analyzer" / "src"
CALLS_SRC = ORIGINAL_ROOT / "calls-backup-analyzer" / "src"

sms_processor_mod = import_original_module("orig_sms_processor", SMS_SRC / "processor.py")
calls_processor_mod = import_original_module("orig_calls_processor", CALLS_SRC / "processor.py")
sms_parser_mod = import_original_module("orig_sms_parser", SMS_SRC / "parser.py")
calls_parser_mod = import_original_module("orig_calls_parser", CALLS_SRC / "parser.py")


def _lxml_elements(xml_text: str, tags: tuple[str, ...]) -> list[Any]:
    from lxml import etree

    root = etree.fromstring(xml_text.encode("utf-8"))
    return [el for el in root if el.tag in tags]


def test_variant_contracts() -> None:
    assert SMS_VARIANT.tags == ("sms", "mms")
    assert SMS_VARIANT.parent_tag == "smses"
    assert SMS_VARIANT.xml_root_tag == "smses"
    assert len(SMS_VARIANT.csv_header) == 7
    assert CALLS_VARIANT.tags == ("call",)
    assert CALLS_VARIANT.parent_tag == "calls"
    assert CALLS_VARIANT.xml_root_tag == "calls"
    assert len(CALLS_VARIANT.csv_header) == 7
    # INSERT placeholder counts match row widths (hash + values).
    assert SMS_VARIANT.message_insert_sql.count("?") == 10
    assert SMS_VARIANT.part_insert_sql is not None
    assert SMS_VARIANT.part_insert_sql.count("?") == 6
    assert SMS_VARIANT.addr_insert_sql is not None
    assert SMS_VARIANT.addr_insert_sql.count("?") == 4
    assert CALLS_VARIANT.message_insert_sql.count("?") == 8
    assert CALLS_VARIANT.part_insert_sql is None
    assert CALLS_VARIANT.addr_insert_sql is None


def test_processor_matches_sms_original() -> None:
    elems = _lxml_elements(SMS_FIXTURE_XML, ("sms", "mms"))
    orig = sms_processor_mod.MessageProcessor()
    new = DedupProcessor(SMS_VARIANT.key_fn)
    for el in elems:
        assert new.process_element(el) == orig.process_element(el)
    assert (new.total_processed, new.unique_count, new.duplicate_count) == (
        orig.total_processed,
        orig.unique_count,
        orig.duplicate_count,
    )
    assert new.tag_counts == {"sms": orig.sms_count, "mms": orig.mms_count}


def test_processor_matches_calls_original() -> None:
    elems = _lxml_elements(CALLS_FIXTURE_XML, ("call",))
    orig = calls_processor_mod.CallProcessor()
    new = DedupProcessor(CALLS_VARIANT.key_fn)
    for el in elems:
        assert new.process_element(el) == orig.process_element(el)
    assert (new.total_processed, new.unique_count, new.duplicate_count) == (
        orig.total_processed,
        orig.unique_count,
        orig.duplicate_count,
    )
    assert new.tag_counts == {"call": orig.call_count}


def test_processor_copies_existing_hashes() -> None:
    existing = {"abc"}
    proc = DedupProcessor(SMS_VARIANT.key_fn, existing_hashes=existing)
    assert proc.seen_hashes == {"abc"}
    assert proc.seen_hashes is not existing


def test_parser_matches_sms_original(tmp_path: Path) -> None:
    xml_file = tmp_path / "in.xml"
    xml_file.write_text(SMS_FIXTURE_XML, encoding="utf-8")
    orig_seq = [(e.tag, dict(e.attrib)) for e in sms_parser_mod.stream_sms_elements(str(xml_file))]
    new_seq = [
        (e.tag, dict(e.attrib))
        for e in stream_elements(str(xml_file), tags=("sms", "mms"), parent_tag="smses")
    ]
    assert new_seq == orig_seq
    # The junk <note> element is skipped by both.
    assert [tag for tag, _ in new_seq] == ["sms", "sms", "sms", "mms"]


def test_parser_matches_calls_original(tmp_path: Path) -> None:
    xml_file = tmp_path / "in.xml"
    xml_file.write_text(CALLS_FIXTURE_XML, encoding="utf-8")
    orig_seq = [
        (e.tag, dict(e.attrib)) for e in calls_parser_mod.stream_call_elements(str(xml_file))
    ]
    new_seq = [
        (e.tag, dict(e.attrib))
        for e in stream_elements(str(xml_file), tags=("call",), parent_tag="calls")
    ]
    assert new_seq == orig_seq
    assert [tag for tag, _ in new_seq] == ["call", "call", "call"]
