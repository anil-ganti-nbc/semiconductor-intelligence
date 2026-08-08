"""Stabilization Pass 1 -- direct loopback HTTP verification of every core
endpoint called out by the assignment. Checks status AND minimal response
validity: a response containing an error object behind HTTP 200 is not
considered successful.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{tmp_path / 'core_endpoints.db'}")
    from semi_intel.web.app import create_app
    with TestClient(create_app()) as client:
        yield client


def _assert_ok_list(response, *, label):
    assert response.status_code == 200, f"{label}: {response.status_code} {response.text}"
    body = response.json()
    assert isinstance(body, list), f"{label}: expected a list, got {body!r}"


def _assert_ok_dict_without_error(response, *, label):
    assert response.status_code == 200, f"{label}: {response.status_code} {response.text}"
    body = response.json()
    assert isinstance(body, dict), f"{label}: expected an object, got {body!r}"
    assert "error" not in body and "detail" not in body, f"{label}: error object behind 200: {body!r}"


def test_dashboard_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Semiconductor Intelligence Platform" in response.text


def test_editorial_stories(client):
    _assert_ok_list(client.get("/api/editorial/stories"), label="editorial stories")


def test_topics(client):
    _assert_ok_list(client.get("/api/topics"), label="topics")


def test_radar_status(client):
    _assert_ok_dict_without_error(client.get("/api/radar/status"), label="radar status")


def test_radar_sources(client):
    _assert_ok_list(client.get("/api/radar/sources"), label="radar sources")


def test_radar_candidates(client):
    _assert_ok_list(client.get("/api/radar/candidates"), label="radar candidates")


def test_radar_source_suggestions(client):
    _assert_ok_list(client.get("/api/radar/source-suggestions"), label="radar source suggestions")


def test_notification_status(client):
    body = client.get("/api/notifications/status")
    _assert_ok_dict_without_error(body, label="notification status")
    assert "counts" in body.json()


def test_notification_list(client):
    _assert_ok_list(client.get("/api/notifications", params={"state": "all"}), label="notification list")


def test_notification_saved_views(client):
    _assert_ok_list(client.get("/api/notifications/saved-views"), label="notification saved views")


def test_operational_jobs(client):
    _assert_ok_list(client.get("/api/operations/jobs"), label="operational jobs")


def test_operational_health(client):
    response = client.get("/api/operations/health")
    _assert_ok_dict_without_error(response, label="operational health")
    assert response.json()["overall"] in {"healthy", "attention_needed", "degraded", "disabled"}


def test_operational_trends(client):
    response = client.get("/api/operations/trends", params={"days": 30})
    _assert_ok_dict_without_error(response, label="operational trends")
    assert response.json()["window_days"] == 30


def test_scheduler_status(client):
    response = client.get("/api/operations/scheduler")
    _assert_ok_dict_without_error(response, label="scheduler status")
    assert response.json()["enabled"] is False  # disabled by default


def test_backup_listing(client):
    _assert_ok_list(client.get("/api/operations/backups"), label="backup listing")


def test_no_startup_traceback_and_missing_favicon_is_harmless(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 404  # a controlled 404, not a 500/traceback
    # Every core endpoint above must have already succeeded with no server
    # error surfacing as part of this fixture's own app startup.
