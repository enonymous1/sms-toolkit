"""Export the messages database to CSV plus extracted attachment files.

Ports ``export_db_csv_attachments.py``. Produces, in the output directory:

- ``messages.csv`` — all message columns plus attachment/MMS context metadata
- ``mms_parts.csv`` — part inventory with extraction metadata
- ``mms_addrs.csv`` — all MMS address columns
- ``thread_summary.csv`` — per-thread aggregates
- ``contact_merge_review.csv`` — proposed contact-name merges
- ``attachments/<msg_hash>/...`` — decoded binary MMS payloads

The source database is only read, never modified.
"""

from __future__ import annotations

import base64
import csv
import difflib
import hashlib
import json
import mimetypes
import re
import shutil
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sms_toolkit import db
from sms_toolkit.logging_config import get_logger
from sms_toolkit.normalize import normalize_address, normalize_date_ms

logger = get_logger(__name__)

TEXTLIKE_MIME_PREFIXES = (
    "text/",
    "application/smil",
    "application/smil+xml",
)

# MMS PDU header address types (Android Telephony.Mms.Addr).
MMS_ADDR_ROLES = {
    "137": "from",
    "151": "to",
    "130": "cc",
    "129": "bcc",
}


@dataclass
class ExportStats:
    messages_rows: int = 0
    mms_parts_rows: int = 0
    mms_addrs_rows: int = 0
    files_written: int = 0
    bytes_written: int = 0
    parts_extracted: int = 0
    parts_skipped_textlike: int = 0
    parts_skipped_no_data: int = 0
    parts_decode_failed: int = 0
    summary_rows: int = 0
    contact_merge_candidates: int = 0
    contact_merges_applied: int = 0
    mms_text_only_rows: int = 0
    mms_group_rows: int = 0
    mms_attachment_rows: int = 0


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "part"


def is_textlike_mime(ct: str | None) -> bool:
    if not ct:
        return False
    mime = ct.lower().strip()
    return any(mime.startswith(prefix) for prefix in TEXTLIKE_MIME_PREFIXES)


def guess_extension(ct: str | None, name: str | None) -> str:
    if name:
        suffix = Path(name).suffix
        if suffix:
            return suffix

    if ct:
        mime = ct.split(",", 1)[0].strip().lower()
        ext = mimetypes.guess_extension(mime)
        if ext:
            return ext

        special = {
            "image/jpg": ".jpg",
            "image/jpeg": ".jpg",
            "text/x-vcard": ".vcf",
            "video/3gpp": ".3gp",
        }
        return special.get(mime, ".bin")

    return ".bin"


def compose_attachment_filename(
    seq: object, rowid: object, name: str | None, ct: str | None
) -> str:
    base_name = safe_name(name or f"part_{seq}")
    ext = guess_extension(ct, name)
    if base_name.lower().endswith(ext.lower()):
        return f"part_{seq}_{rowid}_{base_name}"
    return f"part_{seq}_{rowid}_{base_name}{ext}"


def decode_mms_data(raw_data: str | None) -> tuple[bytes | None, str]:
    if raw_data is None:
        return None, "no_data"

    text = str(raw_data).strip()
    if not text or text.lower() == "null":
        return None, "no_data"

    # Try strict base64 first.
    try:
        decoded = base64.b64decode(text, validate=True)
        if decoded:
            return decoded, "base64"
    except Exception:
        pass

    # Try relaxed base64 with whitespace stripped.
    compact = re.sub(r"\s+", "", text)
    try:
        decoded = base64.b64decode(compact, validate=False)
        if decoded:
            return decoded, "base64-relaxed"
    except Exception:
        pass

    # Last resort: raw text bytes.
    try:
        return text.encode("utf-8"), "raw-utf8"
    except Exception:
        return None, "decode_failed"


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
            row_count += 1
    return row_count


def extract_text_value(part: sqlite3.Row) -> str:
    text_value = part["text"]
    if text_value is None:
        return ""
    text = str(text_value).strip()
    if text.lower() == "null":
        return ""
    return re.sub(r"\s+", " ", text).strip()


def load_contacts_lookup(contacts_db_path: Path | None) -> dict[str, str]:
    """Load the normalized_address -> contact_name lookup from a contacts DB."""
    if contacts_db_path is None or not contacts_db_path.exists():
        return {}

    lookup: dict[str, str] = {}
    conn = sqlite3.connect(contacts_db_path)
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "contacts" not in tables:
            return {}
        for normalized_address, contact_name in conn.execute(
            "SELECT normalized_address, contact_name FROM contacts"
        ):
            if normalized_address:
                lookup[str(normalized_address)] = str(contact_name or "")
    finally:
        conn.close()
    return lookup


def build_mms_derived_data(
    mms_parts: list[sqlite3.Row],
    mms_addrs: list[sqlite3.Row],
    contacts_lookup: dict[str, str],
) -> tuple[dict[str, dict[str, object]], dict[str, list[str]]]:
    parts_by_hash: dict[str, list[sqlite3.Row]] = defaultdict(list)
    addrs_by_hash: dict[str, list[sqlite3.Row]] = defaultdict(list)

    for part in mms_parts:
        parts_by_hash[part["msg_hash"]].append(part)

    for addr in mms_addrs:
        addrs_by_hash[addr["msg_hash"]].append(addr)

    def resolve_name(address: str) -> str:
        norm = normalize_address(address)
        return contacts_lookup.get(norm, "") or "(Unknown)"

    derived: dict[str, dict[str, object]] = {}
    participants: dict[str, list[str]] = {}

    for msg_hash, parts in parts_by_hash.items():
        ordered_parts = sorted(
            parts, key=lambda p: (p["seq"] if p["seq"] is not None else 0, p["_rowid"])
        )
        text_parts: list[str] = []
        part_types: list[str] = []
        attachment_count = 0
        binary_part_count = 0

        for part in ordered_parts:
            ct = str(part["ct"] or "")
            part_types.append(ct)
            text_value = extract_text_value(part)
            if text_value:
                text_parts.append(text_value)
            if not is_textlike_mime(ct):
                binary_part_count += 1
                if part["data"] not in (None, "", "null"):
                    attachment_count += 1

        addrs = addrs_by_hash.get(msg_hash, [])
        by_role: dict[str, list[str]] = defaultdict(list)
        ordered_participants: list[tuple[str, str]] = []
        seen_role_addr = set()
        for addr in addrs:
            address = str(addr["address"] or "").strip()
            role = MMS_ADDR_ROLES.get(str(addr["type"] or "").strip(), "unknown")
            if not address:
                continue
            key = (address, role)
            if key in seen_role_addr:
                continue
            seen_role_addr.add(key)
            by_role[role].append(address)
            ordered_participants.append((address, role))

        sender_address = by_role["from"][0] if by_role.get("from") else ""

        recipient_addresses: list[str] = []
        seen_recipients = set()
        for role in ("to", "cc", "bcc"):
            for address in by_role.get(role, []):
                if address != sender_address and address not in seen_recipients:
                    seen_recipients.add(address)
                    recipient_addresses.append(address)

        all_participant_addresses: list[str] = []
        seen_all = set()
        for address, _role in ordered_participants:
            if address not in seen_all:
                seen_all.add(address)
                all_participant_addresses.append(address)

        sender_contact_name = resolve_name(sender_address) if sender_address else ""
        recipient_contact_names = [resolve_name(a) for a in recipient_addresses]
        all_participant_names = [resolve_name(a) for a in all_participant_addresses]

        participants_detail = [
            {
                "address": address,
                "normalized_address": normalize_address(address),
                "contact_name": resolve_name(address),
                "role": role,
            }
            for address, role in ordered_participants
        ]

        text_only = binary_part_count == 0
        is_group = len(recipient_addresses) > 1

        derived[msg_hash] = {
            "mms_content_text": "\n".join(text_parts).strip(),
            "mms_part_count": len(ordered_parts),
            "mms_text_part_count": len(text_parts),
            "mms_binary_part_count": binary_part_count,
            "mms_attachment_count": attachment_count,
            "mms_has_binary_attachments": 1 if attachment_count > 0 else 0,
            "mms_is_text_only": 1 if text_only else 0,
            "mms_is_group": 1 if is_group else 0,
            "mms_recipient_count": len(recipient_addresses),
            "mms_sender_address": sender_address,
            "mms_sender_contact_name": sender_contact_name,
            "mms_recipient_addresses": ";".join(recipient_addresses),
            "mms_recipient_contact_names": ";".join(recipient_contact_names),
            "mms_participant_addresses": ";".join(all_participant_addresses),
            "mms_participant_contact_names": ";".join(all_participant_names),
            "mms_part_types_json": json.dumps(part_types, ensure_ascii=False),
            "mms_participants_json": json.dumps(participants_detail, ensure_ascii=False),
        }
        participants[msg_hash] = all_participant_addresses

    return derived, participants


def normalize_contact_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9\s]", "", value.lower())
    token = re.sub(r"\s+", " ", token).strip()
    if not token:
        return ""
    words = []
    for w in token.split(" "):
        if len(w) > 3 and w.endswith("s"):
            words.append(w[:-1])
        else:
            words.append(w)
    return " ".join(words)


def contacts_are_similar(a: str, b: str) -> bool:
    na = normalize_contact_token(a)
    nb = normalize_contact_token(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    if ratio >= 0.90:
        return True
    return (na in nb or nb in na) and abs(len(na) - len(nb)) <= 2


def choose_canonical_contact(variants: list[tuple[str, int]]) -> str:
    # Prefer highest-frequency variant, then longest, then alphabetical.
    best = sorted(
        variants,
        key=lambda x: (-x[1], -len(x[0]), x[0].lower()),
    )[0][0]
    return best


def _find(parent: dict[str, str], x: str) -> str:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _union(parent: dict[str, str], a: str, b: str) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[rb] = ra


def build_contact_merge_map(
    contact_counts_by_address: dict[str, dict[str, int]],
) -> tuple[dict[tuple[str, str], str], list[list[Any]], int]:
    merge_map: dict[tuple[str, str], str] = {}
    review_rows: list[list[Any]] = []
    candidate_group_count = 0

    for address_key, counts in contact_counts_by_address.items():
        contacts = [c for c in counts if c.strip()]
        if len(contacts) < 2:
            continue

        parent = {c: c for c in contacts}

        for i in range(len(contacts)):
            for j in range(i + 1, len(contacts)):
                if contacts_are_similar(contacts[i], contacts[j]):
                    _union(parent, contacts[i], contacts[j])

        groups: dict[str, list[str]] = defaultdict(list)
        for c in contacts:
            groups[_find(parent, c)].append(c)

        for variants in groups.values():
            if len(variants) < 2:
                continue

            candidate_group_count += 1
            variant_pairs = [(v, counts[v]) for v in variants]
            canonical = choose_canonical_contact(variant_pairs)

            total_msgs = sum(counts[v] for v in variants)
            for v in variants:
                merge_map[(address_key, v)] = canonical

            review_rows.append(
                [
                    address_key,
                    canonical,
                    len(variants),
                    total_msgs,
                    json.dumps(sorted(variants), ensure_ascii=False),
                ]
            )

    review_rows.sort(key=lambda r: (-int(r[3]), r[0], r[1]))
    return merge_map, review_rows, candidate_group_count


def export_db(
    db_path: Path,
    output_dir: Path,
    include_text_parts: bool,
    summary_group_by: str,
    apply_contact_merge: str,
    write_merge_review: bool,
    dry_run: bool,
    contacts_db_path: Path | None = None,
) -> ExportStats:
    stats = ExportStats()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        messages_cols = db.table_columns(conn, "messages")
        mms_addrs_cols = db.table_columns(conn, "mms_addrs")

        messages = conn.execute("SELECT * FROM messages ORDER BY date, rowid").fetchall()
        stats.messages_rows = len(messages)

        mms_parts = conn.execute(
            """
            SELECT rowid as _rowid, msg_hash, seq, ct, name, text, data
            FROM mms_parts
            ORDER BY msg_hash, seq, rowid
            """
        ).fetchall()
        stats.mms_parts_rows = len(mms_parts)

        mms_addrs = conn.execute("SELECT * FROM mms_addrs ORDER BY msg_hash, rowid").fetchall()
        stats.mms_addrs_rows = len(mms_addrs)

        contacts_lookup = load_contacts_lookup(contacts_db_path)
        mms_derived, mms_participants = build_mms_derived_data(
            mms_parts, mms_addrs, contacts_lookup
        )
        logger.debug("MMS participants derived for %d hashes", len(mms_participants))

        attachments_root = output_dir / "attachments"
        messages_csv = output_dir / "messages.csv"
        mms_parts_csv = output_dir / "mms_parts.csv"
        mms_addrs_csv = output_dir / "mms_addrs.csv"
        summary_csv = output_dir / "thread_summary.csv"
        merge_review_csv = output_dir / "contact_merge_review.csv"

        if not dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)
            if attachments_root.exists():
                shutil.rmtree(attachments_root)
            for csv_file in (
                messages_csv,
                mms_parts_csv,
                mms_addrs_csv,
                summary_csv,
                merge_review_csv,
            ):
                if csv_file.exists():
                    csv_file.unlink()
            attachments_root.mkdir(parents=True, exist_ok=True)

        paths_by_hash: dict[str, list[str]] = defaultdict(list)
        parts_rows_for_csv: list[list[object]] = []

        for part in mms_parts:
            msg_hash = part["msg_hash"]
            seq = part["seq"]
            ct = part["ct"]
            name = part["name"]
            text_value = part["text"]
            raw_data = part["data"]
            rowid = part["_rowid"]

            text_len = len(text_value) if isinstance(text_value, str) else 0
            data_len = len(raw_data) if isinstance(raw_data, str) else 0

            extracted_rel = ""
            extract_status = "skipped"
            data_source = ""
            sha256_hex = ""
            file_size = 0

            should_extract = not is_textlike_mime(ct) or include_text_parts

            if should_extract:
                payload, source = decode_mms_data(raw_data)
                data_source = source

                if payload is None:
                    if data_source == "decode_failed":
                        stats.parts_decode_failed += 1
                        extract_status = "decode_failed"
                    else:
                        stats.parts_skipped_no_data += 1
                        extract_status = "no_data"
                else:
                    filename = compose_attachment_filename(seq, rowid, name, ct)
                    rel_path = Path("attachments") / safe_name(msg_hash) / filename
                    out_path = output_dir / rel_path

                    sha256_hex = hashlib.sha256(payload).hexdigest()
                    file_size = len(payload)

                    if not dry_run:
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        out_path.write_bytes(payload)

                    extracted_rel = rel_path.as_posix()
                    paths_by_hash[msg_hash].append(extracted_rel)
                    stats.parts_extracted += 1
                    stats.files_written += 1
                    stats.bytes_written += file_size
                    extract_status = "extracted"
            else:
                stats.parts_skipped_textlike += 1
                extract_status = "textlike_skipped"

            parts_rows_for_csv.append(
                [
                    msg_hash,
                    seq,
                    ct,
                    name,
                    text_value,
                    text_len,
                    data_len,
                    data_source,
                    extract_status,
                    extracted_rel,
                    file_size,
                    sha256_hex,
                ]
            )

        # messages.csv includes all original columns + attachment + MMS context metadata
        msg_header = list(messages_cols) + [
            "attachment_count",
            "attachments",
            "derived_content_text",
            "mms_part_count",
            "mms_text_part_count",
            "mms_binary_part_count",
            "mms_attachment_count",
            "mms_has_binary_attachments",
            "mms_is_text_only",
            "mms_is_group",
            "mms_recipient_count",
            "mms_sender_address",
            "mms_sender_contact_name",
            "mms_recipient_addresses",
            "mms_recipient_contact_names",
            "mms_participant_addresses",
            "mms_participant_contact_names",
            "mms_part_types_json",
            "mms_participants_json",
        ]
        msg_rows: list[list[object]] = []

        summary_by_thread: dict[tuple[str, str], dict[str, Any]] = {}
        has_normalized_col = "normalized_address" in messages_cols
        contact_counts_by_address: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        for row in messages:
            raw_address = str(row["address"] or "")
            if summary_group_by == "normalized":
                if has_normalized_col and str(row["normalized_address"] or "").strip():
                    address_key = str(row["normalized_address"] or "").strip()
                else:
                    address_key = normalize_address(raw_address)
            else:
                address_key = raw_address

            contact = str(row["contact_name"] or "")
            if contact.strip():
                contact_counts_by_address[address_key][contact] += 1

        merge_map, merge_review_rows, candidate_groups = build_contact_merge_map(
            contact_counts_by_address
        )
        stats.contact_merge_candidates = candidate_groups
        merged_contact_mode = "yes" if apply_contact_merge == "yes" else "no"

        for row in messages:
            msg_hash = row["msg_hash"]
            attachment_paths = paths_by_hash.get(msg_hash, [])
            derived = mms_derived.get(msg_hash, {}) if str(row["tag"] or "") == "mms" else {}
            body_text = str(row["body"] or "")
            derived_content_text = derived.get("mms_content_text") if derived else ""
            if not derived_content_text and body_text and body_text != "[MMS]":
                derived_content_text = body_text
            if not derived_content_text and body_text == "[MMS]" and str(row["tag"] or "") == "sms":
                derived_content_text = body_text

            msg_rows.append(
                [row[col] for col in messages_cols]
                + [
                    len(attachment_paths),
                    json.dumps(attachment_paths, ensure_ascii=False),
                    derived_content_text,
                    derived.get("mms_part_count", ""),
                    derived.get("mms_text_part_count", ""),
                    derived.get("mms_binary_part_count", ""),
                    derived.get("mms_attachment_count", ""),
                    derived.get("mms_has_binary_attachments", ""),
                    derived.get("mms_is_text_only", ""),
                    derived.get("mms_is_group", ""),
                    derived.get("mms_recipient_count", ""),
                    derived.get("mms_sender_address", ""),
                    derived.get("mms_sender_contact_name", ""),
                    derived.get("mms_recipient_addresses", ""),
                    derived.get("mms_recipient_contact_names", ""),
                    derived.get("mms_participant_addresses", ""),
                    derived.get("mms_participant_contact_names", ""),
                    derived.get("mms_part_types_json", ""),
                    derived.get("mms_participants_json", ""),
                ]
            )

            raw_address = str(row["address"] or "")
            if summary_group_by == "normalized":
                if has_normalized_col and str(row["normalized_address"] or "").strip():
                    address_key = str(row["normalized_address"] or "").strip()
                else:
                    address_key = normalize_address(raw_address)
            else:
                address_key = raw_address

            contact = str(row["contact_name"] or "")
            original_contact = contact
            if apply_contact_merge == "yes":
                contact = merge_map.get((address_key, contact), contact)
                if contact != original_contact:
                    stats.contact_merges_applied += 1
            thread_key = (address_key, contact)

            if thread_key not in summary_by_thread:
                summary_by_thread[thread_key] = {
                    "address": address_key,
                    "contact_name": contact,
                    "summary_mode": summary_group_by,
                    "contact_merge_mode": merged_contact_mode,
                    "raw_addresses": set(),
                    "raw_contacts": set(),
                    "message_count": 0,
                    "received_count": 0,
                    "sent_count": 0,
                    "messages_with_attachments": 0,
                    "attachment_file_count": 0,
                    "body_char_total": 0,
                    "first_ms": None,
                    "last_ms": None,
                    "first_readable_date": "",
                    "last_readable_date": "",
                }

            bucket = summary_by_thread[thread_key]
            bucket["raw_addresses"].add(raw_address)
            if original_contact.strip():
                bucket["raw_contacts"].add(original_contact)
            bucket["message_count"] = int(bucket["message_count"]) + 1

            msg_type = str(row["type"] or "")
            if msg_type == "1":
                bucket["received_count"] = int(bucket["received_count"]) + 1
            elif msg_type == "2":
                bucket["sent_count"] = int(bucket["sent_count"]) + 1

            body_text = str(row["body"] or "")
            bucket["body_char_total"] = int(bucket["body_char_total"]) + len(body_text)

            if attachment_paths:
                bucket["messages_with_attachments"] = int(bucket["messages_with_attachments"]) + 1
                bucket["attachment_file_count"] = int(bucket["attachment_file_count"]) + len(
                    attachment_paths
                )

            if str(row["tag"] or "") == "mms":
                if derived.get("mms_is_text_only") == 1:
                    stats.mms_text_only_rows += 1
                if derived.get("mms_is_group") == 1:
                    stats.mms_group_rows += 1
                if derived.get("mms_has_binary_attachments") == 1:
                    stats.mms_attachment_rows += 1

            date_ms = normalize_date_ms(row["date"])
            readable = str(row["readable_date"] or "")
            if date_ms is not None:
                first_ms = bucket["first_ms"]
                last_ms = bucket["last_ms"]
                if first_ms is None or date_ms < int(first_ms):
                    bucket["first_ms"] = date_ms
                    bucket["first_readable_date"] = readable
                if last_ms is None or date_ms > int(last_ms):
                    bucket["last_ms"] = date_ms
                    bucket["last_readable_date"] = readable

        summary_header = [
            "address",
            "contact_name",
            "summary_mode",
            "contact_merge_mode",
            "raw_address_variants",
            "raw_addresses",
            "raw_contact_variants",
            "raw_contacts",
            "message_count",
            "received_count",
            "sent_count",
            "messages_with_attachments",
            "attachment_file_count",
            "body_char_total",
            "first_date_ms",
            "last_date_ms",
            "first_readable_date",
            "last_readable_date",
        ]

        summary_rows = sorted(
            [
                [
                    data["address"],
                    data["contact_name"],
                    data["summary_mode"],
                    data["contact_merge_mode"],
                    len(data["raw_addresses"]),
                    json.dumps(sorted(data["raw_addresses"]), ensure_ascii=False),
                    len(data["raw_contacts"]),
                    json.dumps(sorted(data["raw_contacts"]), ensure_ascii=False),
                    data["message_count"],
                    data["received_count"],
                    data["sent_count"],
                    data["messages_with_attachments"],
                    data["attachment_file_count"],
                    data["body_char_total"],
                    data["first_ms"] if data["first_ms"] is not None else "",
                    data["last_ms"] if data["last_ms"] is not None else "",
                    data["first_readable_date"],
                    data["last_readable_date"],
                ]
                for data in summary_by_thread.values()
            ],
            key=lambda r: int(r[8]),
            reverse=True,
        )
        stats.summary_rows = len(summary_rows)

        parts_header = [
            "msg_hash",
            "seq",
            "ct",
            "name",
            "text",
            "text_length",
            "data_length",
            "data_source",
            "extract_status",
            "attachment_path",
            "attachment_size",
            "attachment_sha256",
        ]

        addrs_header = list(mms_addrs_cols)
        addrs_rows = [[row[col] for col in mms_addrs_cols] for row in mms_addrs]
        merge_review_header = [
            "address_key",
            "canonical_contact",
            "variant_count",
            "total_messages_in_group",
            "variants",
            "would_apply_merge",
        ]
        merge_review_rows_with_flag = [
            row + ["yes" if apply_contact_merge == "yes" else "no"] for row in merge_review_rows
        ]

        if not dry_run:
            write_csv(messages_csv, msg_header, msg_rows)
            write_csv(mms_parts_csv, parts_header, parts_rows_for_csv)
            write_csv(mms_addrs_csv, addrs_header, addrs_rows)
            write_csv(summary_csv, summary_header, summary_rows)
            if write_merge_review:
                write_csv(merge_review_csv, merge_review_header, merge_review_rows_with_flag)

        return stats
    finally:
        conn.close()


def print_summary(
    stats: ExportStats,
    db_path: Path,
    output_dir: Path,
    *,
    dry_run: bool,
    summary_group_by: str,
    apply_contact_merge: str,
    contacts_db_path: Path | None,
    write_merge_review: bool,
) -> None:
    """Print the human-readable export summary to stdout."""
    print("=" * 90)
    print("DB -> CSV + Attachments Export")
    print(f"Source DB: {db_path}")
    print(f"Mode: {'DRY-RUN' if dry_run else 'WRITE'}")
    print(f"Summary group by: {summary_group_by}")
    print(f"Apply contact merge: {apply_contact_merge}")
    print(f"Contacts DB: {contacts_db_path if contacts_db_path else '(disabled)'}")
    print(f"Output dir: {output_dir}")
    print("-")
    print(f"messages rows: {stats.messages_rows}")
    print(f"mms_parts rows: {stats.mms_parts_rows}")
    print(f"mms_addrs rows: {stats.mms_addrs_rows}")
    print("-")
    print(f"parts extracted: {stats.parts_extracted}")
    print(f"parts skipped (textlike): {stats.parts_skipped_textlike}")
    print(f"parts skipped (no data): {stats.parts_skipped_no_data}")
    print(f"parts decode failed: {stats.parts_decode_failed}")
    print("-")
    print(f"mms text-only rows: {stats.mms_text_only_rows}")
    print(f"mms group rows: {stats.mms_group_rows}")
    print(f"mms rows with binary attachments: {stats.mms_attachment_rows}")
    print(f"files written: {stats.files_written}")
    print(f"bytes written: {stats.bytes_written}")
    print(f"summary rows: {stats.summary_rows}")
    print(f"contact merge candidate groups: {stats.contact_merge_candidates}")
    print(f"messages affected by contact merges: {stats.contact_merges_applied}")
    if not dry_run:
        print(f"messages csv: {(output_dir / 'messages.csv')}")
        print(f"mms parts csv: {(output_dir / 'mms_parts.csv')}")
        print(f"mms addrs csv: {(output_dir / 'mms_addrs.csv')}")
        print(f"thread summary csv: {(output_dir / 'thread_summary.csv')}")
        if write_merge_review:
            print(f"contact merge review csv: {(output_dir / 'contact_merge_review.csv')}")
    print("=" * 90)


def run_export_attachments(
    db_path: Path,
    output_dir: Path,
    *,
    include_text_parts: bool = False,
    summary_group_by: str = "normalized",
    apply_contact_merge: str = "no",
    write_merge_review: bool = True,
    dry_run: bool = False,
    contacts_db_path: Path | None = None,
) -> int:
    """Run the CSV+attachments export stage; returns a process exit code."""
    if not db_path.exists():
        print(f"Source DB not found: {db_path}")
        return 1

    stats = export_db(
        db_path=db_path,
        output_dir=output_dir,
        include_text_parts=include_text_parts,
        summary_group_by=summary_group_by,
        apply_contact_merge=apply_contact_merge,
        write_merge_review=write_merge_review,
        dry_run=dry_run,
        contacts_db_path=contacts_db_path,
    )
    print_summary(
        stats,
        db_path,
        output_dir,
        dry_run=dry_run,
        summary_group_by=summary_group_by,
        apply_contact_merge=apply_contact_merge,
        contacts_db_path=contacts_db_path,
        write_merge_review=write_merge_review,
    )
    return 0
