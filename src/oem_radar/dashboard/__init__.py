"""Local read-only dashboard (M12) + alert review workflow (feedback v4).

Stdlib-only HTTP server over the SQLite DB. Opens the DB read-only for reads;
opens read-write only for mark-seen and review POST. Bound to localhost by
default. CSRF token is per-process and required for browser review writes.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ..providers.sqlite import SqliteStore, connect_readonly
from .data import collect, collect_alert_detail
from .render import render, render_review_page

log = logging.getLogger("oem_radar.dashboard")

_CSRF_TOKEN = secrets.token_urlsafe(32)

_ALERT_PATH_RE = re.compile(r"^/alerts/(\d+)/?$")
_API_REVIEW_RE = re.compile(r"^/api/alerts/(\d+)/review/?$")


def _json_error(handler: BaseHTTPRequestHandler, status: int, code: str, message: str) -> None:
    body = json.dumps({"error": {"code": code, "message": message}}).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json_body(handler: BaseHTTPRequestHandler, max_bytes: int) -> dict | None:
    ctype = (handler.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype not in ("application/json", "text/json"):
        _json_error(handler, 400, "invalid_content_type",
                    "Content-Type must be application/json")
        return None
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        _json_error(handler, 400, "invalid_content_length", "Invalid Content-Length")
        return None
    if length < 0:
        _json_error(handler, 400, "invalid_content_length", "Invalid Content-Length")
        return None
    if length > max_bytes:
        _json_error(handler, 413, "body_too_large",
                    f"Request body exceeds {max_bytes} bytes")
        return None
    raw = handler.rfile.read(length) if length else b""
    if not raw:
        _json_error(handler, 400, "empty_body", "Request body is empty")
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _json_error(handler, 400, "malformed_json", f"Malformed JSON: {exc}")
        return None
    if not isinstance(data, dict):
        _json_error(handler, 400, "invalid_body", "JSON body must be an object")
        return None
    return data


class _Handler(BaseHTTPRequestHandler):
    db_path: str = ""
    raw_dir: str = ""
    max_body: int = 16384
    csrf_token: str = ""

    def log_message(self, *a):
        pass

    def _readonly(self):
        return connect_readonly(self.db_path)

    def do_GET(self):
        try:
            path = urlparse(self.path).path
            if path.startswith("/api/data"):
                conn = self._readonly()
                try:
                    body = json.dumps(collect(conn)).encode("utf-8")
                finally:
                    conn.close()
                self._send(200, body, "application/json")
                return

            if path == "/api/feedback/metrics":
                from urllib.parse import parse_qs
                qs = parse_qs(urlparse(self.path).query)
                def q1(k):
                    v = qs.get(k, [None])[0]
                    return v
                try:
                    limit = int(q1("limit") or 50)
                except ValueError:
                    _json_error(self, 400, "invalid_limit", "limit must be an integer")
                    return
                group_by = q1("group_by")
                conn = self._readonly()
                try:
                    from ..core.feedback_analytics import build_metrics_payload
                    from ..core.feedback import FeedbackError
                    from ..core.config import load_radar_config
                    try:
                        min_sample = 5
                        payload = build_metrics_payload(
                            conn, start=q1("start"), end=q1("end"),
                            group_by=group_by, limit=limit, min_sample=min_sample,
                        )
                    except FeedbackError as fe:
                        _json_error(self, 400, "invalid_query", str(fe))
                        return
                finally:
                    conn.close()
                self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
                return

            if path == "/api/feedback/suggestions":
                raw_dir = self.raw_dir or str(Path(self.db_path).parent / "raw")
                store = SqliteStore(self.db_path, raw_dir)
                try:
                    rows = store.list_rule_suggestions(limit=100)
                finally:
                    store.close()
                self._send(200, json.dumps({"suggestions": rows}).encode("utf-8"), "application/json")
                return

            if path == "/feedback" or path.startswith("/feedback/"):
                # Minimal analytics page: JSON summary embedded
                conn = self._readonly()
                try:
                    from ..core.feedback_analytics import build_metrics_payload
                    metrics = build_metrics_payload(conn)
                except Exception as exc:
                    metrics = {"error": str(exc)}
                finally:
                    conn.close()
                raw_dir = self.raw_dir or str(Path(self.db_path).parent / "raw")
                store = SqliteStore(self.db_path, raw_dir)
                try:
                    suggestions = store.list_rule_suggestions(limit=50)
                finally:
                    store.close()
                from .render import render_feedback_page
                html = render_feedback_page(metrics, suggestions, csrf_token=self.csrf_token)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                return

            if path == "/api/feedback/reasons":
                from ..core.feedback import reason_taxonomy, OUTCOMES
                payload = {
                    "outcomes": list(OUTCOMES),
                    "reasons": reason_taxonomy(),
                }
                self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
                return

            m = _API_REVIEW_RE.match(path)
            if m:
                alert_id = int(m.group(1))
                conn = self._readonly()
                try:
                    detail = collect_alert_detail(conn, alert_id)
                finally:
                    conn.close()
                if detail is None:
                    _json_error(self, 404, "not_found", f"No alert with id={alert_id}")
                    return
                from ..core.feedback import reason_taxonomy, OUTCOMES
                payload = {
                    "alert": detail,
                    "review": detail.get("review"),
                    "history": detail.get("history") or [],
                    "outcomes": list(OUTCOMES),
                    "reasons": reason_taxonomy(),
                    "csrf_token": self.csrf_token,
                }
                self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
                return

            m = _ALERT_PATH_RE.match(path)
            if m:
                alert_id = int(m.group(1))
                conn = self._readonly()
                try:
                    detail = collect_alert_detail(conn, alert_id)
                finally:
                    conn.close()
                if detail is None:
                    self.send_error(404, f"Alert {alert_id} not found")
                    return
                html = render_review_page(detail, csrf_token=self.csrf_token)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                return

            if path in ("/", "/index.html"):
                conn = self._readonly()
                try:
                    data = collect(conn)
                finally:
                    conn.close()
                body = render(data).encode("utf-8")
                self._send(200, body, "text/html; charset=utf-8")
                return

            self.send_error(404)
        except Exception as exc:
            log.exception("dashboard GET failed")
            self.send_error(500, str(exc)[:200])

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/mark-seen":
                self._handle_mark_seen()
                return
            m = _API_REVIEW_RE.match(path)
            if m:
                self._handle_review_post(int(m.group(1)))
                return
            self.send_error(404)
        except Exception as exc:
            log.exception("dashboard POST failed")
            _json_error(self, 500, "internal_error", "Internal server error")

    def do_PUT(self):
        self.send_error(405, "Method Not Allowed")

    def do_DELETE(self):
        self.send_error(405, "Method Not Allowed")

    def _handle_mark_seen(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self.send_error(400)
            return
        if length > self.max_body:
            _json_error(self, 413, "body_too_large", f"Body exceeds {self.max_body} bytes")
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            _json_error(self, 400, "malformed_json", "Malformed JSON")
            return
        raw_dir = self.raw_dir or str(Path(self.db_path).parent / "raw")
        store = SqliteStore(self.db_path, raw_dir)
        try:
            names = None if payload.get("all") else payload.get("names")
            changed = store.mark_component_seen(names)
        finally:
            store.close()
        body = json.dumps({"ok": True, "changed": changed}).encode("utf-8")
        self._send(200, body, "application/json")

    def _handle_review_post(self, alert_id: int):
        data = _read_json_body(self, self.max_body)
        if data is None:
            return

        token = (
            self.headers.get("X-OEM-Radar-CSRF")
            or data.get("csrf_token")
            or data.get("_csrf")
        )
        if not token or not secrets.compare_digest(str(token), self.csrf_token):
            _json_error(self, 403, "csrf_invalid",
                        "Missing or invalid CSRF token (X-OEM-Radar-CSRF header or csrf_token field)")
            return

        allowed = {"outcome", "reason_codes", "reviewer_note", "reviewer", "change_note",
                   "csrf_token", "_csrf"}
        unknown = set(data.keys()) - allowed
        if unknown:
            _json_error(self, 400, "unknown_fields",
                        f"Unknown fields: {', '.join(sorted(unknown))}")
            return

        outcome = data.get("outcome")
        if not outcome or not isinstance(outcome, str):
            _json_error(self, 400, "missing_outcome", "outcome is required")
            return

        reason_codes = data.get("reason_codes", [])
        if reason_codes is not None and not isinstance(reason_codes, list):
            _json_error(self, 400, "invalid_reason_codes",
                        "reason_codes must be an array of strings")
            return

        raw_dir = self.raw_dir or str(Path(self.db_path).parent / "raw")
        store = SqliteStore(self.db_path, raw_dir)
        try:
            from ..core.feedback import FeedbackError
            try:
                rev = store.upsert_review(
                    alert_id,
                    outcome=outcome,
                    reason_codes=reason_codes,
                    reviewer_note=data.get("reviewer_note"),
                    reviewer=data.get("reviewer"),
                    change_note=data.get("change_note"),
                )
            except FeedbackError as fe:
                msg = str(fe)
                code = "validation_error"
                if "no change_event" in msg:
                    _json_error(self, 404, "not_found", msg)
                    return
                if "invalid outcome" in msg:
                    code = "invalid_outcome"
                elif "invalid reason code" in msg:
                    code = "invalid_reason_code"
                elif "exceeds" in msg:
                    code = "field_too_long"
                _json_error(self, 400, code, msg)
                return
        finally:
            store.close()

        hist_store = SqliteStore(self.db_path, raw_dir)
        try:
            history = hist_store.list_review_history(alert_id)
        finally:
            hist_store.close()

        body = json.dumps({"ok": True, "review": rev, "history": history}).encode("utf-8")
        self._send(200, body, "application/json")

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(
    db_path: str,
    host: str = "127.0.0.1",
    port: int = 8787,
    open_browser: bool = True,
    *,
    max_body: int = 16384,
    raw_dir: str | None = None,
) -> None:
    handler = partial(_Handler)
    _Handler.db_path = db_path
    _Handler.raw_dir = raw_dir or str(Path(db_path).parent / "raw")
    _Handler.max_body = max_body
    _Handler.csrf_token = _CSRF_TOKEN
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"OEM Radar dashboard: {url}  (Ctrl+C to stop)")
    print("  CSRF token active for review writes (localhost model)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ndashboard stopped")
    finally:
        httpd.server_close()
