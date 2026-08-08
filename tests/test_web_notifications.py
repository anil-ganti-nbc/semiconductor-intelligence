from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{tmp_path / 'alerts.db'}")
    from semi_intel.web.app import create_app
    with TestClient(create_app()) as client:
        yield client


def test_clean_status_and_dashboard(client):
    status = client.get("/api/notifications/status")
    assert status.status_code == 200
    body = status.json()
    assert body["counts"]["unread"] == 0
    assert body["settings"]["in_app_enabled"] is True
    assert body["delivery"]["external_adapter_available"] is False
    assert "Alerts &amp; Digest" in client.get("/").text


def test_notification_lifecycle(client):
    created = client.post("/api/notifications/test").json()
    notification_id = created["id"]
    assert client.get("/api/notifications").json()[0]["id"] == notification_id
    assert client.get(f"/api/notifications/{notification_id}").status_code == 200

    read = client.post(
        "/api/notifications/read",
        json={"notification_ids": [notification_id], "read": True},
    )
    assert read.status_code == 200
    assert read.json()[0]["read_at"]
    assert client.post(f"/api/notifications/{notification_id}/dismiss").json()["dismissed_at"]
    assert client.post(f"/api/notifications/{notification_id}/restore").json()["dismissed_at"] is None
    assert client.get("/api/notifications/delivery-status").json()["external_adapter_available"] is False
    muted = client.post("/api/notifications/mute-event/test?muted=true")
    assert "test" in muted.json()["muted_event_types"]
    unmuted = client.post("/api/notifications/mute-event/test?muted=false")
    assert "test" not in unmuted.json()["muted_event_types"]


def test_settings_round_trip_and_stable_digest(client):
    current = client.get("/api/notifications/settings").json()
    current.pop("activation_at")
    current.pop("external_adapter_available")
    current["daily_digest_enabled"] = True
    current["minimum_attention_score"] = 0.81
    current["timezone"] = "Asia/Kolkata"
    saved = client.put("/api/notifications/settings", json=current)
    assert saved.status_code == 200, saved.text
    assert saved.json()["minimum_attention_score"] == 0.81

    first = client.post("/api/notifications/digest")
    second = client.post("/api/notifications/digest")
    assert first.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert client.get("/api/notifications/digests/current").json()["id"] == first.json()["id"]
    assert "Nothing material" in first.json()["rendered_text"]
    assert client.get("/api/notifications/incidents").json() == []


def test_generate_is_idempotent_on_clean_database(client):
    assert client.post("/api/notifications/generate").json()["created_count"] == 0


def test_notification_filters_and_unknown_topic_mute(client):
    row = client.post("/api/notifications/test").json()
    assert client.get(
        "/api/notifications",
        params={"severity": "informational", "date_from": row["event_at"][:10]},
    ).json()[0]["id"] == row["id"]
    assert client.get("/api/notifications", params={"severity": "urgent"}).json() == []
    assert client.post("/api/notifications/mute-topic/999?muted=true").status_code == 404
    assert client.post("/api/notifications/generate").json()["created_count"] == 0
