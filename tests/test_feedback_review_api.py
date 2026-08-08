"""Stage 2: review page, API, CSRF, Discord review links, list badges."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from oem_radar.core.models import ChangeEvent, ChangeType, Severity
from oem_radar.dashboard import _CSRF_TOKEN, serve
from oem_radar.dashboard.data import collect, collect_alert_detail
from oem_radar.dashboard.render import render, render_review_page
from oem_radar.providers.discord import DiscordNotifier, build_embed
from oem_radar.providers.sqlite import SqliteStore, connect_readonly
from test_models import make_product


@pytest.fixture()
def store(tmp_path):
    s = SqliteStore(str(tmp_path / "r.db"), str(tmp_path / "raw"))
    yield s
    s.close()


def _eid(store, key="src:k12"):
    return store.record_event(
        ChangeEvent(product_key=key, change_type=ChangeType.IMAGES_CHANGED,
                    field="images", severity=Severity.NOTABLE)
    )


# ---- data layer ------------------------------------------------------------

def test_collect_includes_review_status(store, tmp_path):
    eid = _eid(store)
    store.upsert_review(eid, outcome="NOISE", reason_codes=["CDN_URL_CHURN"])
    store.close()
    conn = connect_readonly(str(tmp_path / "r.db"))
    data = collect(conn)
    conn.close()
    ev = next(e for e in data["events"] if e["id"] == eid)
    assert ev["review_status"] == "NOISE"
    assert "unreviewed_events" in data["summary"]


def test_collect_alert_detail_and_related(store, tmp_path):
    e1 = store.record_event(
        ChangeEvent(product_key="src:k12", change_type=ChangeType.NEW_PRODUCT,
                    severity=Severity.BREAKING)
    )
    e2 = store.record_event(
        ChangeEvent(product_key="src:k12", change_type=ChangeType.PRICE_CHANGED,
                    field="prices", severity=Severity.NOTABLE)
    )
    store.close()
    conn = connect_readonly(str(tmp_path / "r.db"))
    detail = collect_alert_detail(conn, e2)
    conn.close()
    assert detail is not None
    assert detail["id"] == e2
    assert detail["review_status"] == "UNREVIEWED"
    assert any(r["id"] == e1 for r in detail["related_events"])


# ---- render ----------------------------------------------------------------

def test_review_page_escapes_and_has_shortcuts(store, tmp_path):
    eid = _eid(store)
    store.upsert_review(eid, outcome="HIT", reason_codes=["VALID_CONFIRMATION_SIGNAL"],
                        reviewer_note='<script>alert(1)</script>')
    store.close()
    conn = connect_readonly(str(tmp_path / "r.db"))
    detail = collect_alert_detail(conn, eid)
    conn.close()
    html = render_review_page(detail, csrf_token="test-csrf")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "isTypingTarget" in html
    assert "contentEditable" in html or "isContentEditable" in html
    assert 'value="HIT"' in html and "checked" in html
    assert "VALID_CONFIRMATION_SIGNAL" in html
    assert "Review history" in html
    assert 'name="csrf_token"' in html


def test_list_render_has_review_badge_and_link(store, tmp_path):
    eid = _eid(store)
    store.close()
    conn = connect_readonly(str(tmp_path / "r.db"))
    data = collect(conn)
    conn.close()
    html = render(data)
    # Links are built client-side from DATA; the template must include the path pattern.
    assert "/alerts/${e.id}" in html or "/alerts/" in html
    assert "rev-${esc(rev)}" in html or "rev-UNREVIEWED" in html or "review_status" in html
    assert "f-rev" in html
    assert "isTypingTarget" not in html  # guard lives on review page
    assert f'"id": {eid}' in html or f'"id":{eid}' in html


# ---- HTTP API via live server ---------------------------------------------

@pytest.fixture()
def server(tmp_path):
    db = str(tmp_path / "srv.db")
    raw = str(tmp_path / "raw")
    store = SqliteStore(db, raw)
    eid = store.record_event(
        ChangeEvent(product_key="gmktec:k12", change_type=ChangeType.NEW_PRODUCT,
                    severity=Severity.BREAKING)
    )
    store.close()

    # Bind ephemeral port
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    from oem_radar.dashboard import _Handler, _CSRF_TOKEN
    from http.server import ThreadingHTTPServer
    from functools import partial

    _Handler.db_path = db
    _Handler.raw_dir = raw
    _Handler.max_body = 16384
    _Handler.csrf_token = _CSRF_TOKEN
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield {"port": port, "eid": eid, "csrf": _CSRF_TOKEN, "db": db, "raw": raw}
    httpd.shutdown()
    httpd.server_close()


def _req(port, method, path, body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    hdrs = headers or {}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
        hdrs["Content-Length"] = str(len(payload))
    conn.request(method, path, body=payload, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        parsed = json.loads(data.decode("utf-8"))
    except Exception:
        parsed = data.decode("utf-8", errors="replace")
    return resp.status, parsed


def test_api_get_review_missing(server):
    status, data = _req(server["port"], "GET", "/api/alerts/999999/review")
    assert status == 404
    assert data["error"]["code"] == "not_found"


def test_api_get_review_unreviewed(server):
    status, data = _req(server["port"], "GET", f"/api/alerts/{server['eid']}/review")
    assert status == 200
    assert data["review"] is None
    assert data["outcomes"]
    assert data["reasons"]
    assert data["csrf_token"]


def test_api_post_review_and_history(server):
    body = {
        "outcome": "NOISE",
        "reason_codes": ["CDN_URL_CHURN"],
        "reviewer": "anil",
        "reviewer_note": "cdn churn",
        "csrf_token": server["csrf"],
    }
    status, data = _req(
        server["port"], "POST", f"/api/alerts/{server['eid']}/review",
        body=body, headers={"X-OEM-Radar-CSRF": server["csrf"]},
    )
    assert status == 200, data
    assert data["review"]["outcome"] == "NOISE"

    # update
    body2 = {
        "outcome": "BUG",
        "reason_codes": ["PARSER_ERROR"],
        "reviewer": "anil",
        "change_note": "actually a parser bug",
        "csrf_token": server["csrf"],
    }
    status, data = _req(
        server["port"], "POST", f"/api/alerts/{server['eid']}/review",
        body=body2, headers={"X-OEM-Radar-CSRF": server["csrf"]},
    )
    assert status == 200
    assert data["review"]["outcome"] == "BUG"
    assert len(data["history"]) == 2


def test_api_invalid_outcome(server):
    status, data = _req(
        server["port"], "POST", f"/api/alerts/{server['eid']}/review",
        body={"outcome": "MAYBE", "csrf_token": server["csrf"]},
        headers={"X-OEM-Radar-CSRF": server["csrf"]},
    )
    assert status == 400
    assert data["error"]["code"] == "invalid_outcome"


def test_api_invalid_reason(server):
    status, data = _req(
        server["port"], "POST", f"/api/alerts/{server['eid']}/review",
        body={"outcome": "NOISE", "reason_codes": ["NOPE"], "csrf_token": server["csrf"]},
        headers={"X-OEM-Radar-CSRF": server["csrf"]},
    )
    assert status == 400
    assert data["error"]["code"] == "invalid_reason_code"


def test_api_malformed_json(server):
    conn = HTTPConnection("127.0.0.1", server["port"], timeout=5)
    raw = b"{not json"
    conn.request("POST", f"/api/alerts/{server['eid']}/review", body=raw,
                 headers={"Content-Type": "application/json",
                          "Content-Length": str(len(raw)),
                          "X-OEM-Radar-CSRF": server["csrf"]})
    resp = conn.getresponse()
    body = json.loads(resp.read())
    conn.close()
    assert resp.status == 400
    assert body["error"]["code"] == "malformed_json"


def test_api_wrong_content_type(server):
    conn = HTTPConnection("127.0.0.1", server["port"], timeout=5)
    raw = b'{"outcome":"HIT"}'
    conn.request("POST", f"/api/alerts/{server['eid']}/review", body=raw,
                 headers={"Content-Type": "text/plain",
                          "Content-Length": str(len(raw))})
    resp = conn.getresponse()
    body = json.loads(resp.read())
    conn.close()
    assert resp.status == 400
    assert body["error"]["code"] == "invalid_content_type"


def test_api_oversized_body(server):
    big = {"outcome": "HIT", "reviewer_note": "x" * 20000, "csrf_token": server["csrf"]}
    status, data = _req(
        server["port"], "POST", f"/api/alerts/{server['eid']}/review",
        body=big, headers={"X-OEM-Radar-CSRF": server["csrf"]},
    )
    assert status == 413
    assert data["error"]["code"] == "body_too_large"


def test_api_unknown_fields(server):
    status, data = _req(
        server["port"], "POST", f"/api/alerts/{server['eid']}/review",
        body={"outcome": "HIT", "extra": 1, "csrf_token": server["csrf"]},
        headers={"X-OEM-Radar-CSRF": server["csrf"]},
    )
    assert status == 400
    assert data["error"]["code"] == "unknown_fields"


def test_api_csrf_missing(server):
    status, data = _req(
        server["port"], "POST", f"/api/alerts/{server['eid']}/review",
        body={"outcome": "HIT"},
    )
    assert status == 403
    assert data["error"]["code"] == "csrf_invalid"


def test_api_csrf_invalid(server):
    status, data = _req(
        server["port"], "POST", f"/api/alerts/{server['eid']}/review",
        body={"outcome": "HIT", "csrf_token": "wrong"},
        headers={"X-OEM-Radar-CSRF": "wrong"},
    )
    assert status == 403


def test_api_reasons_endpoint(server):
    status, data = _req(server["port"], "GET", "/api/feedback/reasons")
    assert status == 200
    assert "TEMPORARY_404" in {r["code"] for r in data["reasons"]}
    assert "HIT" in data["outcomes"]


def test_get_review_page_html(server):
    conn = HTTPConnection("127.0.0.1", server["port"], timeout=5)
    conn.request("GET", f"/alerts/{server['eid']}")
    resp = conn.getresponse()
    html = resp.read().decode("utf-8")
    conn.close()
    assert resp.status == 200
    assert f"#{server['eid']}" in html
    assert "isTypingTarget" in html


# ---- Discord ---------------------------------------------------------------

def test_discord_embed_includes_alert_id_and_review_url():
    event = ChangeEvent(product_key="gmktec-shopify:k12",
                        change_type=ChangeType.NEW_PRODUCT, severity=Severity.BREAKING)
    product = make_product()
    payload = build_embed(event, product, event_id=1842,
                          review_base_url="http://127.0.0.1:8787/")
    footer = payload["embeds"][0]["footer"]["text"]
    assert "Alert ID: 1842" in footer
    assert "http://127.0.0.1:8787/alerts/1842" in footer
    assert "//alerts" not in footer.replace("://", "")  # no double slash path
    fields = {f["name"]: f["value"] for f in payload["embeds"][0]["fields"]}
    assert fields.get("Collector") == "gmktec-shopify"
    assert fields.get("Alert type") == "new_product"
    assert "Confidence" in fields


def test_discord_embed_omits_review_url_when_disabled(store, tmp_path):
    sent = []
    n = DiscordNotifier(store, "https://hook.example", 3,
                        sender=lambda u, p: (sent.append(p) or True, None),
                        review_base_url="http://127.0.0.1:8787",
                        feedback_enabled=False)
    n.enqueue(ChangeEvent(product_key="s:k", change_type=ChangeType.NEW_PRODUCT,
                          severity=Severity.BREAKING), make_product())
    n.drain()
    assert sent
    footer = sent[0]["embeds"][0]["footer"]["text"]
    assert "Alert ID:" in footer
    assert "Review:" not in footer


def test_discord_review_base_url_normalized(store):
    event = ChangeEvent(product_key="s:k", change_type=ChangeType.NEW_PRODUCT,
                        severity=Severity.BREAKING)
    payload = build_embed(event, make_product(), event_id=7,
                          review_base_url="http://127.0.0.1:8787/")
    assert "http://127.0.0.1:8787/alerts/7" in payload["embeds"][0]["footer"]["text"]
