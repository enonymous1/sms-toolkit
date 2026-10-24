"""Differential tests: sms_toolkit.normalize vs the original implementations."""

from __future__ import annotations

from typing import Any

from sms_toolkit import normalize
from tests.diff_helpers import ORIGINAL_ROOT, load_original_function

SMS_SRC = ORIGINAL_ROOT / "sms-backup-analyzer" / "src"
MAINT = ORIGINAL_ROOT / "scripts" / "maintenance"

# The four address-normalizer originals.
_ADDR_ORIGINALS: list[Any] = [
    load_original_function(SMS_SRC / "analyzer.py", "normalize_address"),
    load_original_function(SMS_SRC / "build_contacts_table.py", "normalize_address_value"),
    load_original_function(SMS_SRC / "export_db_csv_attachments.py", "normalize_address_value"),
    load_original_function(SMS_SRC / "import_ifttt_sms.py", "normalize_phone_for_db"),
]

# The two E.164 originals (duplicated verbatim).
_PHONE_ORIGINALS: list[Any] = [
    load_original_function(MAINT / "extract_ifttt_samples.py", "normalize_phone"),
    load_original_function(MAINT / "ifttt_compatibility_summary.py", "normalize_phone"),
]

# The two date normalizers.
_DATE_ORIGINALS: list[Any] = [
    load_original_function(SMS_SRC / "analyzer.py", "normalize_date_ms"),
    load_original_function(SMS_SRC / "export_db_csv_attachments.py", "parse_date_ms"),
]

# String inputs: all four address originals must agree with the canonical one.
ADDRESS_VECTORS: list[Any] = [
    None,
    "",
    "   ",
    "+1 (555) 010-1234",
    "555-010-1234",
    "(555) 010-1234",
    "5550101234",
    "15550101234",
    "+15550101234",
    "011442079460018",
    "+44 20 7946 0018",
    "12345",
    "911",
    "(unknown)",
    "no digits here",
]

# Numeric inputs: only normalize_phone_for_db handled these correctly; the
# canonical implementation follows it (the older three mangled ".0").
NUMERIC_VECTORS: list[Any] = [5550101234, 5550101234.0, "5550101234.0"]

DATE_VECTORS: list[Any] = [
    None,
    "",
    "   ",
    "1685712000000",
    "0",
    1685712000000,
    "1685712000.5",
    "12ab",
    " 1685712000000 ",
    "+1685712000000",
    "-5",
]

PHONE_VECTORS: list[Any] = [
    None,
    "",
    0,
    0.0,
    "12",
    "1234",
    "12345",
    "123456789",
    "5550101234",
    "+1 (555) 010-1234",
    "+15550101234",
    5550101234,
    5550101234.0,
    "+44 20 7946 0018",
]


def test_normalize_address_matches_all_originals() -> None:
    for value in ADDRESS_VECTORS:
        expected = [fn(value) for fn in _ADDR_ORIGINALS]
        assert len(set(expected)) == 1, f"originals disagree on {value!r}: {expected}"
        assert normalize.normalize_address(value) == expected[0], f"mismatch on {value!r}"


def test_normalize_address_numeric_matches_phone_for_db() -> None:
    phone_for_db = _ADDR_ORIGINALS[3]
    for value in NUMERIC_VECTORS:
        assert normalize.normalize_address(value) == phone_for_db(value)


def test_normalize_address_pinned_values() -> None:
    assert normalize.normalize_address("+1 (555) 010-1234") == "5550101234"
    assert normalize.normalize_address("15550101234") == "5550101234"
    assert normalize.normalize_address(5550101234.0) == "5550101234"
    assert normalize.normalize_address(None) == ""
    assert normalize.normalize_address("12345") == "12345"


def test_normalize_phone_e164_matches_both_originals() -> None:
    for value in PHONE_VECTORS:
        expected = [fn(value) for fn in _PHONE_ORIGINALS]
        assert len(set(expected)) == 1, f"originals disagree on {value!r}: {expected}"
        assert normalize.normalize_phone_e164(value) == expected[0], f"mismatch on {value!r}"


def test_normalize_phone_e164_pinned_values() -> None:
    assert normalize.normalize_phone_e164(None) is None
    assert normalize.normalize_phone_e164("") is None
    assert normalize.normalize_phone_e164("1234") == "1234"
    assert normalize.normalize_phone_e164("5550101234") == "+15550101234"
    assert normalize.normalize_phone_e164("+44 20 7946 0018") == "+442079460018"


def test_normalize_date_ms_matches_both_originals() -> None:
    for value in DATE_VECTORS:
        expected = [fn(value) for fn in _DATE_ORIGINALS]
        assert len(set(expected)) == 1, f"originals disagree on {value!r}: {expected}"
        assert normalize.normalize_date_ms(value) == expected[0], f"mismatch on {value!r}"


def test_normalize_date_ms_pinned_values() -> None:
    assert normalize.normalize_date_ms("1685712000000") == 1685712000000
    assert normalize.normalize_date_ms(None) is None
    assert normalize.normalize_date_ms("12ab") is None
