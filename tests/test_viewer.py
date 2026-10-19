"""Tests for the SMS backup viewer (Flask test client)."""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from sms_toolkit import cli
from sms_toolkit.viewer import create_app, run_viewer


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
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
            ("h-sms-1", "sms", "5716435424", "Carrie Lyons", "hello there", 3000, "d1", "1"),
            ("h-sms-2", "sms", "15551234567", "Frances Lyon", "other body", 2000, "d2", "2"),
            ("h-mms-1", "mms", "5716435424", "Carrie Lyons", "[MMS]", 4000, "d3", "132"),
        ],
    )
    conn.executemany(
        "INSERT INTO mms_parts VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("h-mms-1", 0, "text/plain", "text_0.txt", "mms text part", None),
            ("h-mms-1", 1, "image/jpeg", "img.jpg", None, image_bytes),
        ],
    )
    conn.executemany(
        "INSERT INTO mms_addrs VALUES (?, ?, ?)",
        [("h-mms-1", "5716435424", "137"), ("h-mms-1", "15551234567", "151")],
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def client(db_path: Path):  # type: ignore[no-untyped-def]
    app = create_app(db_path)
    app.config["TESTING"] = True
    return app.test_client()


def test_index_renders_template(client: Any) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert b"SMS Backup Viewer" in response.data
    assert b"/api/messages" in response.data


def test_stats(client: Any) -> None:
    data = json.loads(client.get("/api/stats").data)
    assert data["total_messages"] == 3
    assert data["total_sms"] == 2
    assert data["total_mms"] == 1
    assert data["top_contacts"][0]["address"] == "5716435424"
    assert data["top_contacts"][0]["count"] == 2


def test_messages_pagination_and_order(client: Any) -> None:
    data = json.loads(client.get("/api/messages").data)
    assert [m["msg_hash"] for m in data] == ["h-mms-1", "h-sms-1", "h-sms-2"]  # date DESC


def test_messages_search(client: Any) -> None:
    data = json.loads(client.get("/api/messages?search=other").data)
    assert [m["msg_hash"] for m in data] == ["h-sms-2"]
    data = json.loads(client.get("/api/messages?search=Frances").data)
    assert [m["msg_hash"] for m in data] == ["h-sms-2"]
    data = json.loads(client.get("/api/messages?search=nomatchxyz").data)
    assert data == []


def test_messages_page_param(client: Any) -> None:
    assert client.get("/api/messages?page=2").status_code == 200
    assert json.loads(client.get("/api/messages?page=2").data) == []
    response = client.get("/api/messages?page=abc")
    assert response.status_code == 400


def test_mms_enrichment(client: Any) -> None:
    data = json.loads(client.get("/api/messages").data)
    mms = data[0]
    assert mms["msg_hash"] == "h-mms-1"
    assert mms["body"] == "mms text part"  # body replaced by text/plain part text
    assert mms["has_images"] is True


def test_message_detail(client: Any) -> None:
    data = json.loads(client.get("/api/message/h-mms-1").data)
    assert data["message"]["msg_hash"] == "h-mms-1"
    assert len(data["parts"]) == 2
    image_part = next(p for p in data["parts"] if p["seq"] == 1)
    assert image_part["has_data"] is True
    assert "data" not in image_part  # raw payload omitted
    text_part = next(p for p in data["parts"] if p["seq"] == 0)
    assert text_part["has_data"] is False
    assert len(data["addrs"]) == 2


def test_message_detail_unknown_hash_404(client: Any) -> None:
    assert client.get("/api/message/nope").status_code == 404


def test_get_image(client: Any) -> None:
    response = client.get("/api/image/h-mms-1/1")
    assert response.status_code == 200
    assert response.data == b"fake-image-bytes"
    assert response.headers["Content-Type"] == "image/jpeg"


def test_get_image_missing(client: Any) -> None:
    assert client.get("/api/image/h-mms-1/0").status_code == 404  # text part, no data
    assert client.get("/api/image/nope/0").status_code == 404


def test_run_viewer_missing_db(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_viewer(tmp_path / "nope.sqlite") == 1
    assert "not found" in capsys.readouterr().out


def test_serve_cli_missing_db(tmp_path: Path) -> None:
    assert cli.main(["--root", str(tmp_path), "serve", "--db", str(tmp_path / "nope.sqlite")]) == 1


def test_serve_cli_help() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["serve", "--help"])
    assert exc.value.code == 0


def test_message_direction_in_search(client: Any) -> None:
    data = client.get("/api/messages").get_json()
    by_hash = {m["msg_hash"]: m for m in data}
    assert by_hash["h-sms-1"]["direction"] == "received"  # type 1
    assert by_hash["h-sms-2"]["direction"] == "sent"  # type 2
    assert by_hash["h-mms-1"]["direction"] == "received"  # m_type 132
    assert by_hash["h-mms-1"]["image_seqs"] == [1]


def test_conversations_endpoint(client: Any) -> None:
    data = client.get("/api/conversations").get_json()
    assert [c["address"] for c in data] == ["5716435424", "15551234567"]  # recent first
    carrie = data[0]
    assert carrie["contact_name"] == "Carrie Lyons"
    assert carrie["count"] == 2
    assert carrie["mms_count"] == 1
    assert carrie["last_date_ms"] == 4000
    assert carrie["last_tag"] == "mms"


def test_conversation_endpoint(client: Any) -> None:
    data = client.get("/api/conversation?address=5716435424").get_json()
    msgs = data["messages"]
    assert [m["msg_hash"] for m in msgs] == ["h-sms-1", "h-mms-1"]  # oldest first
    assert msgs[0]["direction"] == "received"
    assert msgs[1]["body"] == "mms text part"  # MMS text enrichment
    assert data["has_more"] is False
    assert data["oldest_ms"] == 3000


def test_conversation_pagination_before(client: Any) -> None:
    data = client.get("/api/conversation?address=5716435424&before=4000").get_json()
    assert [m["msg_hash"] for m in data["messages"]] == ["h-sms-1"]
    assert data["has_more"] is False

    limited = client.get("/api/conversation?address=5716435424&limit=1").get_json()
    assert [m["msg_hash"] for m in limited["messages"]] == ["h-mms-1"]
    assert limited["has_more"] is True


def test_conversation_endpoint_errors(client: Any) -> None:
    assert client.get("/api/conversation").status_code == 400  # missing address
    assert client.get("/api/conversation?address=x&limit=abc").status_code == 400
    assert client.get("/api/conversation?address=x&before=abc").status_code == 400
    assert client.get("/api/conversation?address=nobody").get_json()["messages"] == []


def test_message_detail_has_direction(client: Any) -> None:
    data = client.get("/api/message/h-sms-2").get_json()
    assert data["message"]["direction"] == "sent"
