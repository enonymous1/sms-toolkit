"""Canonical normalization helpers for sms_toolkit.

Unifies the address/phone/date normalizers that were copy-pasted (and slowly
diverging) across the original scripts:

- ``normalize_address`` <- ``sms-backup-analyzer/src/analyzer.py::normalize_address``,
  ``build_contacts_table.py::normalize_address_value``,
  ``export_db_csv_attachments.py::normalize_address_value``,
  ``import_ifttt_sms.py::normalize_phone_for_db``
- ``normalize_phone_e164`` <- ``scripts/maintenance/extract_ifttt_samples.py`` and
  ``ifttt_compatibility_summary.py::normalize_phone`` (duplicated verbatim)
- ``normalize_date_ms`` <- ``sms-backup-analyzer/src/analyzer.py::normalize_date_ms``,
  ``export_db_csv_attachments.py::parse_date_ms`` (behaviorally identical)
"""

from __future__ import annotations

import re


def normalize_address(value: object) -> str:
    """Return the canonical digit-only address key for DB grouping and dedup.

    Ported from ``import_ifttt_sms.normalize_phone_for_db`` — the most complete
    of the four originals, behavior-identical to the other three on string input:
    - ``None`` -> ``""``; all non-digit characters are dropped.
    - 11-digit NANP numbers lose their leading ``1`` (``1571...`` -> ``571...``).
    - ``int``/``float`` inputs (Excel numeric cells, e.g. ``5716435424.0``) are
      converted without the ``.0`` artifact. The three older copies turned
      ``5716435424.0`` into ``"57164354240"``; that was a bug, now fixed.
    """
    if value is None:
        return ""

    if isinstance(value, (int, float)):
        # Excel often stores phone-like numeric values as float with .0
        text = str(int(value))
    else:
        text = str(value).strip()
        # Remove accidental ".0" from numeric-looking strings
        if re.fullmatch(r"\d+\.0", text):
            text = text[:-2]

    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def normalize_phone_e164(value: object) -> str | None:
    """Return an E.164-ish display form of a phone number.

    Ports ``scripts/maintenance/.../normalize_phone`` (was duplicated verbatim
    in two scripts):
    - falsy input -> ``None``
    - fewer than 5 characters (short codes) -> returned unchanged
    - 10+ characters -> ``+``-prefixed digits (``+1`` for 10-digit NANP numbers)
    """
    if not value:
        return None

    phone = str(int(float(value))) if isinstance(value, (int, float)) else str(value)

    if len(phone) < 5:
        return phone
    if len(phone) >= 10:
        digits = re.sub(r"\D", "", phone)
        if len(digits) == 10:
            return f"+1{digits}"
        if len(digits) == 11 and digits[0] == "1":
            return f"+{digits}"
        return f"+{digits}"
    return phone


def normalize_date_ms(value: object) -> int | None:
    """Return epoch milliseconds as int, or ``None`` when unavailable.

    Unifies ``analyzer.normalize_date_ms`` and
    ``export_db_csv_attachments.parse_date_ms`` (behaviorally identical:
    digit-only strings are accepted, everything else yields ``None``).
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    try:
        return int(text)
    except ValueError:
        return None
