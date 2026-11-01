"""Tests for scripts/freeze.py (static viewer demo for GitHub Pages)."""

from __future__ import annotations

import functools
import http.client
import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

FREEZE = Path(__file__).resolve().parent.parent / "scripts" / "freeze.py"

PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def _run_freeze(out: Path) -> Path:
    result = subprocess.run(
        [sys.executable, str(FREEZE), "--out", str(out)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "froze 15 files" in result.stdout
    return out


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_freeze_builds_static_site(tmp_path: Path) -> None:
    out = _run_freeze(tmp_path / "freeze-site")
    assert (out / "index.html").is_file()
    assert (out / "demo-shim.js").is_file()

    stats = _load_json(out / "api" / "stats")
    assert stats["total_messages"] == 7
    assert stats["total_sms"] == 6
    assert stats["total_mms"] == 1

    conversations = _load_json(out / "api" / "conversations")
    assert [c["address"] for c in conversations] == ["15550101002", "15550101001"]

    alice = _load_json(out / "api" / "conversation" / "15550101001")
    assert len(alice["messages"]) == 4
    assert alice["has_more"] is False
    dates = [int(m["date"]) for m in alice["messages"]]
    assert dates == sorted(dates)  # oldest first
    assert alice["messages"][0]["body"].startswith("Hey!")
    mms = [m for m in alice["messages"] if m["tag"] == "mms"][0]
    assert mms["has_images"] is True
    assert mms["image_seqs"] == [1]

    archive = _load_json(out / "api" / "messages")
    assert len(archive) == 7

    detail = _load_json(out / "api" / "message" / "demo-alice-3")
    assert len(detail["parts"]) == 2
    assert all("data" not in part for part in detail["parts"])  # has_data flag only
    assert len(detail["addrs"]) == 1

    image = (out / "api" / "image" / "demo-alice-3" / "1").read_bytes()
    assert image[:8] == PNG_MAGIC


def test_frozen_html_has_no_live_hooks(tmp_path: Path) -> None:
    out = _run_freeze(tmp_path / "freeze-site")
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "Static demo" in html
    assert 'src="demo-shim.js"' in html
    assert "localhost" not in html
    assert "127.0.0.1" not in html
    # <img> URLs are relativized (the shim cannot rewrite img tags, which
    # are not fetches); the app script's fetch("/api/...") calls remain and
    # are intercepted by the shim at runtime.
    assert 'src="/api/' not in html
    assert 'data-full="/api/' not in html


def test_frozen_site_serves_over_http(tmp_path: Path) -> None:
    out = _run_freeze(tmp_path / "freeze-site")
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out)),
    )
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:

        def get(path: str) -> tuple[int, bytes]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", path)
            response = conn.getresponse()
            body = response.read()
            conn.close()
            return response.status, body

        status, body = get("/")
        assert status == 200 and b"demo-banner" in body
        status, body = get("/api/conversations")
        assert status == 200
        assert len(json.loads(body)) == 2
        status, body = get("/api/conversation/15550101002")
        assert status == 200
        assert len(json.loads(body)["messages"]) == 3
    finally:
        server.shutdown()
