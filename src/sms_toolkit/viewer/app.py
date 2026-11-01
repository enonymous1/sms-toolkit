"""Flask app factory for the SMS backup viewer.

Ports ``sms-backup-viewer/app.py``: the same routes and queries, with the
database path injected through the app factory instead of a module-level
constant. Deliberate hardenings versus the original: unknown message
hashes return 404 (not 500), a non-integer ``page`` parameter returns 400
(not 500), the database is opened read-only (the viewer never writes, and
a plain ``sqlite3.connect`` would silently *create* a missing database
file), and image parts are served only for ``image/*`` content types with
a ``nosniff`` header.

The viewer has no authentication: it is a localhost development server for
a private archive. Never bind it to ``0.0.0.0`` without a reverse proxy
that authenticates; ``--debug`` on a non-loopback interface is refused
for the same reason (the Werkzeug debugger would allow remote code
execution).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask, Response, current_app, jsonify, render_template, request
from flask.typing import ResponseReturnValue

from sms_toolkit.logging_config import get_logger
from sms_toolkit.viewer import queries

logger = get_logger(__name__)


def _get_connection() -> sqlite3.Connection:
    """Open the viewer database read-only (never create, never write).

    ``mode=ro`` turns a missing file into an ``OperationalError`` instead
    of an empty database that 500s on every page, and guarantees no
    reader-side write (WAL ``-shm`` files aside, which SQLite manages).
    The path is resolved to an absolute URI so special characters in
    directory names cannot break URI parsing. A 30s timeout (up from
    SQLite's 5s default) keeps a concurrent import writer from turning
    reads into ``database is locked`` 500s.
    """
    db_uri = Path(current_app.config["SMS_DB_PATH"]).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def create_app(
    db_path: str | Path | None = None, *, per_page: int = queries.PER_PAGE_DEFAULT
) -> Flask:
    """Create the viewer Flask app bound to *db_path*.

    *db_path* defaults to ``sms_db.sqlite`` in the current directory (the
    original's behavior); :func:`run_viewer` checks existence first and
    exits 1 for a missing file, so the factory itself never has to. The
    ``PER_PAGE`` config key sets the archive page size; conversation
    pages use :data:`queries.CONVERSATION_PAGE_DEFAULT` (capped at 500
    per request).
    """
    app = Flask(__name__)
    app.config["SMS_DB_PATH"] = str(db_path) if db_path is not None else "sms_db.sqlite"
    app.config["PER_PAGE"] = per_page

    @app.after_request
    def _security_headers(response: Response) -> Response:
        # Stored content types are whitelisted to image/*, but defense in
        # depth: never let a served part be sniffed as HTML.
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.errorhandler(sqlite3.Error)
    def _database_unavailable(exc: sqlite3.Error) -> ResponseReturnValue:
        # A missing/unreadable database (mode=ro raises on connect) or a
        # corrupt table becomes a JSON 503, not a Flask 500 page leaking
        # internals. Logged server-side with the detail intact.
        logger.warning("Viewer database error: %s", exc)
        return jsonify({"error": "database unavailable"}), 503

    @app.get("/")
    def index() -> str:
        """Serve the single-page viewer frontend."""
        return render_template("index.html")

    @app.get("/api/stats")
    def stats() -> ResponseReturnValue:
        """Database-wide counts plus the top 5 contacts by volume."""
        conn = _get_connection()
        try:
            return jsonify(queries.get_stats(conn))
        finally:
            conn.close()

    @app.get("/api/messages")
    def messages() -> ResponseReturnValue:
        """One archive page (``?page=``, ``?search=``); 400 on bad page."""
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
        """Conversation list, most recent first.

        ``?search=`` filters server-side by address/contact name and
        ``?limit=`` caps the list (default 200); both are 400 on a bad
        limit value. Without the cap this endpoint serialized every
        address in the archive on every keystroke.
        """
        search = request.args.get("search", "")
        try:
            limit = int(request.args.get("limit", queries.CONVERSATION_LIST_DEFAULT_LIMIT))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid 'limit' parameter"}), 400
        limit = max(1, min(limit, 1000))

        conn = _get_connection()
        try:
            return jsonify(queries.list_conversations(conn, search=search, limit=limit))
        finally:
            conn.close()

    @app.get("/api/conversation")
    def conversation() -> ResponseReturnValue:
        """One thread, oldest first (``?address=`` required, ``?before=`` pages back)."""
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
        """Full detail for one message (parts, addresses); 404 when unknown."""
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
        """Raw bytes for one image MMS part; 404 for anything else."""
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
    """Run the viewer dev server; returns the process exit code.

    Exit 1 when the database file does not exist (never create an empty
    one), when ``--debug`` is combined with a non-loopback host (the
    Werkzeug debugger on a reachable interface is remote code execution),
    and 0 once the server stops normally.
    """
    path = Path(db_path)
    if not path.exists():
        print(f"sms-toolkit: database not found: {path}")
        return 1
    if debug and host not in ("127.0.0.1", "localhost", "::1"):
        print("sms-toolkit: refusing --debug on non-loopback host (debugger exposure)")
        return 1
    app = create_app(path, per_page=per_page)
    logger.info("Serving SMS viewer for %s on %s:%d", path, host, port)
    app.run(host=host, port=port, debug=debug)
    return 0
