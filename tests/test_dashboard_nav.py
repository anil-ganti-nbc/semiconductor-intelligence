"""Dashboard navigation discoverability for Stage 2–3 feedback surfaces."""

from __future__ import annotations

from pathlib import Path

import pytest

from oem_radar.core.models import ChangeEvent, ChangeType, Severity
from oem_radar.dashboard.data import collect, collect_alert_detail
from oem_radar.dashboard.render import render, render_feedback_page, render_review_page
from oem_radar.providers.sqlite import SqliteStore


@pytest.fixture()
def store(tmp_path):
    s = SqliteStore(str(tmp_path / "n.db"), str(tmp_path / "raw"))
    yield s
    s.close()


def _event(store, key="gmktec-shopify:x"):
    return store.record_event(ChangeEvent(
        product_key=key, change_type=ChangeType.NEW_PRODUCT, severity=Severity.NOTABLE,
    ))


def test_main_dashboard_has_feedback_nav(store):
    eid = _event(store)
    data = collect(store.db)
    html = render(data)
    assert 'href="/feedback"' in html
    assert 'class="topnav"' in html or "topnav" in html
    assert "Feedback" in html
    assert "Overview" in html


def test_event_rows_link_to_review_pages(store):
    eid = _event(store)
    data = collect(store.db)
    html = render(data)
    assert 'href="/alerts/${e.id}"' in html or f"/alerts/" in html
    assert "card-link" in html


def test_review_badges_render(store):
    eid = _event(store)
    store.upsert_review(eid, outcome="HIT", reason_codes=["VALID_CONFIRMATION_SIGNAL"])
    data = collect(store.db)
    assert data["events"][0]["review_status"] == "HIT"
    html = render(data)
    assert "rev-${esc(rev)}" in html or "rev-HIT" in html
    assert "f-rev" in html  # review state filter


def test_unreviewed_summary_count(store):
    _event(store)
    eid2 = _event(store, "gmktec-shopify:y")
    store.upsert_review(eid2, outcome="NOISE", reason_codes=["CDN_URL_CHURN"])
    data = collect(store.db)
    assert data["summary"]["unreviewed_events"] >= 1
    assert data["summary"]["reviewed_events"] >= 1
    html = render(data)
    assert "Unreviewed" in html
    assert "fb-summary" in html
    assert "Review unreviewed alerts" in html
    assert "signal_rate" in html or "Signal rate" in html


def test_feedback_page_links_back(store):
    html = render_feedback_page({"summary": {}, "rankings": {}}, [])
    assert 'href="/"' in html
    assert "Dashboard" in html or "Overview" in html
    assert "Feedback" in html


def test_review_page_links_back_and_neighbors(store):
    a = _event(store, "gmktec-shopify:a")
    b = _event(store, "gmktec-shopify:b")
    c = _event(store, "gmktec-shopify:c")
    detail = collect_alert_detail(store.db, b)
    assert detail["prev_id"] == a
    assert detail["next_id"] == c
    html = render_review_page(detail, csrf_token="tok")
    assert 'href="/"' in html
    assert "/feedback" in html
    assert f"/alerts/{a}" in html
    assert f"/alerts/{c}" in html
    assert "topnav" in html


def test_nav_present_on_all_pages(store):
    eid = _event(store)
    main = render(collect(store.db))
    feedback = render_feedback_page({"summary": {}, "rankings": {}}, [])
    review = render_review_page(collect_alert_detail(store.db, eid), csrf_token="t")
    for html in (main, feedback, review):
        assert 'href="/"' in html
        assert "Feedback" in html or "/feedback" in html
