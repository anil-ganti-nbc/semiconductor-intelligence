from __future__ import annotations

import re
from pathlib import Path
import os

import feedparser
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "source_management.db"
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{db_file}")
    from semi_intel.web.app import create_app
    with TestClient(create_app()) as client:
        yield client


def test_source_update_and_untested_health(client):
    created = client.post("/api/radar/sources", json={"handle_or_url": "@old_handle"}).json()
    before = client.get("/api/radar/sources").json()[0]
    assert before["health"]["state"] == "untested"
    assert before["last_success_at"] is None

    response = client.put(
        f"/api/radar/sources/{created['id']}",
        json={
            "display_name": "Useful Leaker",
            "handle_or_url": "@new_handle",
            "priority": 5,
            "trust_weight": 0.75,
            "enabled": True,
            "polling_enabled": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Useful Leaker"
    assert body["provider_key"] == "new_handle"
    assert body["priority"] == 5
    assert body["polling_enabled"] is True
    assert body["health"]["state"] == "untested"


def test_disabling_source_forces_polling_off_and_blocks_manual_collection(client):
    created = client.post("/api/radar/sources", json={"handle_or_url": "@handle"}).json()
    response = client.put(
        f"/api/radar/sources/{created['id']}",
        json={
            "display_name": "Disabled source",
            "handle_or_url": "@handle",
            "priority": 3,
            "trust_weight": 0.5,
            "enabled": False,
            "polling_enabled": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["polling_enabled"] is False
    assert response.json()["health"]["state"] == "disabled"
    collect = client.post(f"/api/radar/sources/{created['id']}/collect", json={})
    assert collect.status_code == 409


def test_rss_manual_collection_works_with_polling_off(client, monkeypatch):
    import semi_intel.signals.providers.rss as rss_module

    content = Path("tests/fixtures/sample_feed.xml").read_bytes()
    monkeypatch.setattr(rss_module, "_default_fetch", lambda _url: feedparser.parse(content))
    created = client.post(
        "/api/radar/sources",
        json={"handle_or_url": "https://example.com/feed", "polling_enabled": False},
    ).json()
    result = client.post(f"/api/radar/sources/{created['id']}/collect", json={})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "ok"
    row = client.get("/api/radar/sources").json()[0]
    assert row["polling_enabled"] is False
    assert row["health"]["state"] == "healthy"
    assert row["health"]["last_attempt_at"] is not None


@pytest.mark.parametrize(
    ("error", "state"),
    [
        ("The read operation timed out", "timed_out"),
        ("Client error '404 Not Found' for url", "http_error"),
        ("429 Too Many Requests", "rate_limited"),
        ("X session not authenticated", "authentication_required"),
        ("challenge page detected", "challenged"),
        ("not a valid RSS/Atom feed", "invalid_feed"),
    ],
)
def test_error_classification(error, state):
    from semi_intel.signals.source_management import classify_error
    assert classify_error(error)[0] == state


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "BrowserType.launch_persistent_context: spawn EPERM Call log: enormous command",
            "Chromium could not start because Windows denied the browser process.",
        ),
        (
            "BrowserType.launch_persistent_context: Executable doesn't exist at C:/secret/path",
            "Playwright Chromium is not installed or could not be found.",
        ),
        (
            "No X session imported at 'C:/private/x_session.json'. Run setup first.",
            "No X session is imported. Import or refresh the local X session.",
        ),
    ],
)
def test_playwright_errors_are_short_and_path_free(raw, expected):
    from semi_intel.notifications.service import safe_error

    assert safe_error(raw) == expected


def test_radar_status_never_returns_raw_provider_error(client):
    from semi_intel.db import get_engine, get_sessionmaker
    from semi_intel.domain.enums import ProviderRunStatus
    from semi_intel.domain.models import ProviderRun

    engine = get_engine(os.environ["SEMI_INTEL_DB_URL"])
    session = get_sessionmaker(engine)()
    session.add(ProviderRun(
        provider="x",
        status=ProviderRunStatus.FAILED,
        error="BrowserType.launch_persistent_context: spawn EPERM Call log: --private-path",
    ))
    session.commit()
    session.close()

    row = client.get("/api/radar/status").json()["recent_provider_runs"][0]
    assert row["error"] == "Chromium could not start because Windows denied the browser process."
    assert "private-path" not in row["error"]


def test_source_edit_rejects_duplicate_identity(client):
    first = client.post("/api/radar/sources", json={"handle_or_url": "@first"}).json()
    client.post("/api/radar/sources", json={"handle_or_url": "@second"})
    response = client.put(
        f"/api/radar/sources/{first['id']}",
        json={
            "display_name": "First",
            "handle_or_url": "@second",
            "priority": 3,
            "trust_weight": 0.5,
            "enabled": True,
            "polling_enabled": False,
        },
    )
    assert response.status_code == 422
    assert "already registered" in response.json()["detail"]


def test_radar_source_management_controls_and_no_duplicate_suggestion_queue():
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    radar = html[html.index('<section id="radar">'):html.index('<section id="notifications">')]
    for expected in (
        'id="radar-source-select-all"',
        "collectSelectedRadarSources()",
        "collectAllRadarSources()",
        "cancelRadarCollection()",
        'id="radar-source-dialog"',
        "Review suggested",
    ):
        assert expected in radar
    assert "Domains and platform accounts repeatedly credited" not in radar
    assert 'value="source_suggestions"' in radar
    assert "openRadarSourceEditor(" in html
    assert "saveRadarSource(" in html


def test_bulk_collection_is_sequential_confirms_x_and_postprocesses_once():
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    start = html.index("async function collectRadarSourceBatch")
    end = html.index("async function submitAddRadarSource", start)
    body = html[start:end]
    assert "for (const source of queue)" in body
    assert "X account(s)" in body
    assert "confirm(" in body
    assert "cancelRadarCollectionRequested" in body
    assert body.count('postJSON("/api/radar/cluster"') == 1
    assert "Promise.all(queue" not in body
