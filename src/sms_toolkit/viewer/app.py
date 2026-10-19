"""Flask app factory for the SMS backup viewer.

Ports ``sms-backup-viewer/app.py``: the same routes and queries, with the
database path injected through the app factory instead of a module-level
constant. Two deliberate hardenings versus the original: unknown message
hashes return 404 (not 500), and a non-integer ``page`` parameter returns 400
(not 500).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask, current_app, jsonify, render_template, request
from flask.typing import ResponseReturnValue

from sms_toolkit.logging_config import get_logger
from sms_toolkit.viewer import queries

logger = get_logger(__name__)


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(current_app.config["SMS_DB_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def create_app(
    db_path: str | Path | None = None, *, per_page: int = queries.PER_PAGE_DEFAULT
) -> Flask:
    """Create the viewer Flask app bound to *db_path*."""
    app = Flask(__name__)
    app.config["SMS_DB_PATH"] = str(db_path) if db_path is not None else "sms_db.sqlite"
    app.config["PER_PAGE"] = per_page

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/api/stats")
    def stats() -> ResponseReturnValue:
        conn = _get_connection()
        try:
            return jsonify(queries.get_stats(conn))
        finally:
            conn.close()

    @app.get("/api/messages")
    def messages() -> ResponseReturnValue:
        try:
            page = int(request.args.get("page", 1))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid 'page' parameter"}), 400
        page = max(page, 1)
        search = request.args.get("search", "")

        conn = _get_connection()
        try:
            result = queries.search_messages(
                conn, page=page, per_page=current_app.config["PER_PAGE"], search=search
            )
        finally:
            conn.close()
        return jsonify(result)

    @app.get("/api/conversations")
    def conversations() -> ResponseReturnValue:
        conn = _get_connection()
        try:
            return jsonify(queries.list_conversations(conn))
        finally:
            conn.close()

    @app.get("/api/conversation")
    def conversation() -> ResponseReturnValue:
        address = request.args.get("address", "")
        if not address:
            return jsonify({"error": "missing 'address' parameter"}), 400
        try:
            limit = int(request.args.get("limit", queries.CONVERSATION_PAGE_DEFAULT))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid 'limit' parameter"}), 400
        limit = max(1, min(limit, 500))
        before_raw = request.args.get("before")
        before: int | None = None
        if before_raw is not None:
            try:
                before = int(before_raw)
            except (TypeError, ValueError):
                return jsonify({"error": "invalid 'before' parameter"}), 400

        conn = _get_connection()
        try:
            result = queries.get_conversation(conn, address, limit=limit, before=before)
        finally:
            conn.close()
        return jsonify(result)

    @app.get("/api/message/<msg_hash>")
    def message_detail(msg_hash: str) -> ResponseReturnValue:
        conn = _get_connection()
        try:
            detail = queries.get_message_detail(conn, msg_hash)
        finally:
            conn.close()
        if detail is None:
            return jsonify({"error": "message not found"}), 404
        return jsonify(detail)

    @app.get("/api/image/<msg_hash>/<int:seq>")
    def get_image(msg_hash: str, seq: int) -> ResponseReturnValue:
        conn = _get_connection()
        try:
            result = queries.get_image_part(conn, msg_hash, seq)
        finally:
            conn.close()
        if result is None:
            return "Not found", 404
        payload, content_type = result
        return payload, 200, {"Content-Type": content_type}

    return app


def run_viewer(
    db_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    debug: bool = False,
    per_page: int = queries.PER_PAGE_DEFAULT,
) -> int:
    """Run the viewer dev server; returns the process exit code."""
    path = Path(db_path)
    if not path.exists():
        print(f"sms-toolkit: database not found: {path}")
        return 1
    app = create_app(path, per_page=per_page)
    logger.info("Serving SMS viewer for %s on %s:%d", path, host, port)
    app.run(host=host, port=port, debug=debug)
    return 0
