"""Regression tests for the 2026-10-31 staff-review fix tranche.

Each test pins one fixed behavior: silent-success runs, destructive
defaults, normalization divergence, cross-source dedup misses, viewer
hardening, and the smaller correctness fixes.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from sms_toolkit import db
from sms_toolkit.config import load_config
from sms_toolkit.diag import check_database
from sms_toolkit.pipeline import runner as runner_module
from sms_toolkit.pipeline.processor import DedupProcessor
from sms_toolkit.pipeline.runner import run_pipeline
from sms_toolkit.sms.variant import SMS_VARIANT
from sms_toolkit.stages import import_ifttt as ii
from sms_toolkit.stages import normalize_messages as nm
from sms_toolkit.stages.export_year import run_export_year
from sms_toolkit.viewer import create_app, run_viewer
from tests.diff_helpers import SMS_FIXTURE_XML


def _write_input(tmp_path: Path, name: str, text: str) -> Path:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / name).write_text(text, encoding="utf-8")
    return input_dir


def _summary_total(stdout: str) -> int:
    for line in stdout.splitlines():
        if line.startswith("Total items processed:"):
            return int(line.split(":", 1)[1].strip().replace(",", ""))
    raise AssertionError("summary total line not found")


def _make_messages_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    db.ensure_messages_table(conn)
    conn.commit()
    return conn


# -- runner: failure semantics -------------------------------------------


def test_failed_file_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    input_dir = _write_input(tmp_path, "good.xml", SMS_FIXTURE_XML)
    _write_input(tmp_path, "bad.xml", SMS_FIXTURE_XML)
    orig = runner_module.stream_elements

    def flaky(*args: Any, **kwargs: Any) -> Any:
        if "bad.xml" in str(args[0]):
            raise RuntimeError("boom")
        yield from orig(*args, **kwargs)

    monkeypatch.setattr(runner_module, "stream_elements", flaky)
    cfg = load_config(root=tmp_path)
    assert run_pipeline(SMS_VARIANT, input_dir=input_dir, config=cfg, retries=0) == 1
    out = capsys.readouterr().out
    assert "bad.xml" in out  # failed-file list, not just a log line
    conn = sqlite3.connect(tmp_path / "sms_db.sqlite")
    try:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3
    finally:
        conn.close()


def test_negative_retries_clamped(tmp_path: Path) -> None:
    input_dir = _write_input(tmp_path, "backup.xml", SMS_FIXTURE_XML)
    cfg = load_config(root=tmp_path)
    assert run_pipeline(SMS_VARIANT, input_dir=input_dir, config=cfg, retries=-1) == 0
    conn = sqlite3.connect(tmp_path / "sms_db.sqlite")
    try:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3
    finally:
        conn.close()


def test_processor_snapshot_restore() -> None:
    proc = DedupProcessor(lambda e: str(e))

    class Elem:
        def __init__(self, tag: str) -> None:
            self.tag = tag

    # Keep references alive: str() of a freed temporary can reuse an
    # address, which would make distinct elements hash identically.
    e1, e2, e3 = Elem("sms"), Elem("sms"), Elem("sms")
    proc.process_element(e1)
    proc.process_element(e2)
    snap = proc.snapshot_counters()
    proc.process_element(e3)
    proc.restore_counters(snap)
    assert proc.total_processed == 2
    assert proc.unique_count == 2
    assert proc.duplicate_count == 0


def test_retry_does_not_double_count_totals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    orig = runner_module.stream_elements
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        gen = orig(*args, **kwargs)
        if calls["n"] == 1:
            yield next(gen)  # one element lands, then the attempt fails
            raise RuntimeError("transient")
        yield from gen

    monkeypatch.setattr(runner_module, "stream_elements", flaky)

    input_dir = _write_input(tmp_path, "backup.xml", SMS_FIXTURE_XML)
    cfg = load_config(root=tmp_path)
    cfg = dataclasses.replace(
        cfg, pipeline=dataclasses.replace(cfg.pipeline, retry_backoff_base_s=0)
    )
    assert run_pipeline(SMS_VARIANT, input_dir=input_dir, config=cfg, retries=1) == 0
    retried_total = _summary_total(capsys.readouterr().out)

    clean_dir = _write_input(tmp_path / "clean", "backup.xml", SMS_FIXTURE_XML)
    clean_cfg = load_config(root=tmp_path / "clean")
    monkeypatch.undo()  # restore the real stream_elements for the clean run
    assert run_pipeline(SMS_VARIANT, input_dir=clean_dir, config=clean_cfg) == 0
    clean_total = _summary_total(capsys.readouterr().out)
    assert retried_total == clean_total


# -- export-year: destructive default -------------------------------------


def test_export_year_refuses_source_as_output(tmp_path: Path) -> None:
    db_path = tmp_path / "sms_db.sqlite"
    conn = _make_messages_db(db_path)
    conn.execute(
        "INSERT INTO messages (msg_hash, tag, address, date, body, type)"
        " VALUES ('h1', 'sms', '5550101234', '1685712000000', 'hi', '1')"
    )
    conn.commit()
    conn.close()
    before = db_path.read_bytes()
    assert run_export_year(db_path, db_path, 2023) == 1
    assert db_path.read_bytes() == before


# -- normalize: one normalization truth -----------------------------------


def test_backfill_uses_python_normalizers(tmp_path: Path) -> None:
    db_path = tmp_path / "t.sqlite"
    conn = _make_messages_db(db_path)
    conn.execute(
        "INSERT INTO messages (msg_hash, tag, address, normalized_address, date,"
        " normalized_date_ms, body, type) VALUES ('h-div', 'sms', '555.010.1234 x89',"
        " NULL, '123abc', NULL, 'b', '1')"
    )
    conn.execute(
        "INSERT INTO messages (msg_hash, tag, address, normalized_address, date,"
        " normalized_date_ms, body, type) VALUES ('h-ok', 'sms', '(555) 010-1234',"
        " NULL, '1685712000000', NULL, 'b', '1')"
    )
    conn.commit()
    addr_rows, date_rows = nm.apply_backfill(conn)
    rows = {
        r[0]: r
        for r in conn.execute(
            "SELECT msg_hash, normalized_address, normalized_date_ms FROM messages"
        ).fetchall()
    }
    conn.close()
    assert (addr_rows, date_rows) == (2, 1)
    # SQL backfill gave "5550101234x89" here; the Python normalizer (the
    # same one import uses) gives all digits.
    assert rows["h-div"][1] == "555010123489"
    # SQL CAST gave 123 for "123abc"; the Python normalizer refuses it.
    assert rows["h-div"][2] is None
    assert rows["h-ok"][1] == "5550101234"
    assert rows["h-ok"][2] == 1685712000000


# -- IFTTT: cross-source dedup --------------------------------------------


def _ifttt_sample(**overrides: Any) -> ii.ParsedMessage:
    dt = datetime(2021, 3, 28, 15, 4, 0)
    base: dict[str, Any] = {
        "source_file": "f.xlsx",
        "source_row": 1,
        "source_col": 1,
        "timestamp_raw": "March 28, 2021 at 3:04PM",
        "date_ms": int(dt.timestamp() * 1000),
        "readable_date": ii.format_readable_date(dt),
        "address": "5550101234",
        "body": "hello",
        "contact_name": None,
    }
    base.update(overrides)
    return ii.ParsedMessage(**base)


def _seed_pipeline_row(conn: sqlite3.Connection, *, address: str, readable_date: str) -> None:
    conn.execute(
        "INSERT INTO messages (msg_hash, tag, address, normalized_address, date,"
        " normalized_date_ms, body, type, readable_date, contact_name)"
        " VALUES ('seed', 'sms', ?, '5550101234', '1616941440000', 1616941440000,"
        " 'hello', '1', ?, NULL)",
        (address, readable_date),
    )
    conn.commit()


def test_ifttt_same_day_matches_formatted_pipeline_address(tmp_path: Path) -> None:
    conn = _make_messages_db(tmp_path / "t.sqlite")
    # Pipeline row: raw formatted address, normalized column filled at import.
    _seed_pipeline_row(conn, address="+1 (555) 010-1234", readable_date="March 28, 2021 3:04:00 PM")
    stats = ii.ImportStats()
    same_day = _ifttt_sample(date_ms=int(datetime(2021, 3, 28, 18, 30).timestamp() * 1000))
    ii.insert_messages(conn, [same_day], True, stats)
    conn.commit()
    assert stats.dup_same_day_body_db == 1
    assert stats.inserted_messages == 0
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    conn.close()


def test_ifttt_same_day_matches_abbreviated_month(tmp_path: Path) -> None:
    conn = _make_messages_db(tmp_path / "t.sqlite")
    _seed_pipeline_row(conn, address="5550101234", readable_date="Mar 28, 2021 3:04:00 PM")
    stats = ii.ImportStats()
    same_day = _ifttt_sample(date_ms=int(datetime(2021, 3, 28, 18, 30).timestamp() * 1000))
    ii.insert_messages(conn, [same_day], True, stats)
    conn.commit()
    assert stats.dup_same_day_body_db == 1
    assert stats.inserted_messages == 0
    conn.close()


# -- viewer: hardening ------------------------------------------------------


@pytest.fixture()
def viewer_db(tmp_path: Path) -> Path:
    import base64

    path = tmp_path / "sms_db.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE messages (
            msg_hash TEXT PRIMARY KEY, tag TEXT, address TEXT, contact_name TEXT,
            body TEXT, date INTEGER, readable_date TEXT, type TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE mms_parts (
            msg_hash TEXT, seq INTEGER, ct TEXT, name TEXT, text TEXT, data TEXT
        )"""
    )
    conn.execute("CREATE TABLE mms_addrs (msg_hash TEXT, address TEXT, type TEXT)")
    image_bytes = base64.b64encode(b"fake-image-bytes").decode("ascii")
    conn.executemany(
        "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("h-sms-1", "sms", "5550101234", "Carrie Lyons", "hello", 3000, "d1", "1"),
            ("h-mms-1", "mms", "5550101234", "Carrie Lyons", "[MMS]", 4000, "d3", "132"),
            ("h-mms-2", "mms", "15551234567", "Frances Lyon", "[MMS]", 5000, "d4", "128"),
        ],
    )
    # Parts deliberately inserted out of seq order: display must follow seq.
    conn.executemany(
        "INSERT INTO mms_parts VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("h-mms-1", 2, "text/plain", "text_2.txt", "second", None),
            ("h-mms-1", 1, "text/plain", "text_1.txt", "first", None),
            ("h-mms-1", 3, "image/jpeg", "img.jpg", None, image_bytes),
            ("h-mms-1", 4, "text/html", "evil.html", None, image_bytes),
            ("h-mms-2", 0, "text/plain", "text_0.txt", "solo", None),
        ],
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def viewer_client(viewer_db: Path):  # type: ignore[no-untyped-def]
    app = create_app(viewer_db)
    app.config["TESTING"] = True
    return app.test_client()


def test_image_route_rejects_non_image_content_type(viewer_client: Any) -> None:
    assert viewer_client.get("/api/image/h-mms-1/4").status_code == 404  # text/html part
    ok = viewer_client.get("/api/image/h-mms-1/3")
    assert ok.status_code == 200
    assert ok.headers["X-Content-Type-Options"] == "nosniff"


def test_conversation_part_order_follows_seq(viewer_client: Any) -> None:
    data = viewer_client.get("/api/conversation?address=5550101234").get_json()
    mms = [m for m in data["messages"] if m["msg_hash"] == "h-mms-1"][0]
    assert mms["body"] == "first | second"


def test_conversations_search_and_limit(viewer_client: Any) -> None:
    data = viewer_client.get("/api/conversations?search=Frances").get_json()
    assert [c["address"] for c in data] == ["15551234567"]
    limited = viewer_client.get("/api/conversations?limit=1").get_json()
    assert len(limited) == 1
    assert limited[0]["address"] == "15551234567"  # most recent first
    assert viewer_client.get("/api/conversations?limit=abc").status_code == 400


def test_conversation_mms_enrichment_is_batched(viewer_db: Path) -> None:
    from sms_toolkit.viewer import queries

    conn = sqlite3.connect(viewer_db)
    conn.row_factory = sqlite3.Row
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        result = queries.get_conversation(conn, "5550101234")
    finally:
        conn.close()
    assert len(result["messages"]) == 2
    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    # 1 thread query + 2 batched part queries; the old code needed 2 per MMS row.
    assert len(selects) <= 4


def test_viewer_opens_missing_db_read_only(tmp_path: Path) -> None:
    missing = tmp_path / "nope.sqlite"
    app = create_app(missing)
    app.config["TESTING"] = True
    client = app.test_client()
    # The sqlite3.Error handler converts the connect failure to JSON 503.
    response = client.get("/api/stats")
    assert response.status_code == 503
    assert response.get_json() == {"error": "database unavailable"}
    assert not missing.exists()  # mode=ro must not create the file


def test_run_viewer_refuses_debug_on_public_host(tmp_path: Path) -> None:
    db_path = tmp_path / "x.sqlite"
    db_path.write_bytes(b"")
    assert run_viewer(db_path, host="0.0.0.0", debug=True) == 1


# -- diag / db / matcher / spatial / extractor -------------------------------


def test_diag_date_range_uses_numeric_order(tmp_path: Path) -> None:
    db_path = tmp_path / "t.sqlite"
    conn = _make_messages_db(db_path)
    conn.execute(
        "INSERT INTO messages (msg_hash, tag, address, date, body, type)"
        " VALUES ('a', 'sms', 'x', '999999999', 'b', '1')"
    )
    conn.execute(
        "INSERT INTO messages (msg_hash, tag, address, date, body, type)"
        " VALUES ('b', 'sms', 'x', '1700000000000', 'b', '1')"
    )
    conn.commit()
    conn.close()
    checks = {c.name: c for c in check_database(db_path, "sms_db")}
    assert checks["sms_db date range"].detail == "1970-01-12 .. 2023-11-14 (UTC)"


def test_diag_huge_date_does_not_crash(tmp_path: Path) -> None:
    db_path = tmp_path / "t.sqlite"
    conn = _make_messages_db(db_path)
    conn.execute(
        "INSERT INTO messages (msg_hash, tag, address, date, body, type)"
        " VALUES ('a', 'sms', 'x', '99999999999999999999', 'b', '1')"
    )
    conn.commit()
    conn.close()
    checks = check_database(db_path, "sms_db")  # must not raise
    assert not any(c.name.endswith("date range") for c in checks)


def test_create_backup_missing_source(tmp_path: Path) -> None:
    missing = tmp_path / "nope.sqlite"
    with pytest.raises(FileNotFoundError):
        db.create_backup(missing, tmp_path / "backups", "pre-test")
    assert not missing.exists()
    assert not (tmp_path / "backups").exists() or not list((tmp_path / "backups").glob("*"))


def test_matcher_digitless_phone_matches_nothing(tmp_path: Path) -> None:
    from sms_toolkit.ocr.matcher import MessageMatcher

    db_path = tmp_path / "t.sqlite"
    conn = _make_messages_db(db_path)
    conn.execute(
        "INSERT INTO messages (msg_hash, tag, address, body, type)"
        " VALUES ('h', 'sms', '5550101234', 'hello world', '1')"
    )
    conn.commit()
    conn.close()
    with MessageMatcher(db_path) as matcher:
        assert (
            matcher.find_matches_for_message(
                {"phone_number": "+++", "sender": None, "content": "hello"}
            )
            == []
        )


def test_spatial_v4_empty_blocks_have_statistics() -> None:
    from sms_toolkit.ocr.spatial import cluster_by_alignment_v4

    result = cluster_by_alignment_v4([])
    assert result["statistics"] == {"q1": None, "median": None, "q3": None, "all_centers": []}


def test_explicit_group_flag_survives_header() -> None:
    from sms_toolkit.ocr.extractor import ConversationExtractor

    ext = ConversationExtractor("dummy.png", user_name="X", is_group_chat=False)
    names = ext.extract_participants_from_header("Carrie Lyons, Frances Lyon")
    assert len(names) == 2
    assert ext.is_group_chat is False


def test_malformed_env_int_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sms_toolkit.config import load_config

    monkeypatch.setenv("SMS_TOOLKIT_BATCH_SIZE", "abc")
    with pytest.warns(UserWarning, match="SMS_TOOLKIT_BATCH_SIZE"):
        cfg = load_config(root=".")
    assert cfg.pipeline.batch_size == 5000
