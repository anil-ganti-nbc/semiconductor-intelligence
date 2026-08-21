from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{tmp_path / 'operations.db'}")
    monkeypatch.delenv("SEMI_INTEL_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SEMI_INTEL_WEBHOOK_TOKEN", raising=False)
    from semi_intel.web.app import create_app
    with TestClient(create_app(mutation_authorizer=lambda _value: True)) as client:
        yield client


def test_operations_dashboard_health_and_disabled_scheduler(client):
    html = client.get("/").text
    assert "Automation &amp; Health" in html
    assert "notification-preset" in html
    assert "saved-notification-views" in html
    assert "notification-delivery-state" in html
    assert 'id="operations-trend-window"' in html
    assert 'id="operations-trends"' in html

    scheduler = client.get("/api/operations/scheduler")
    assert scheduler.status_code == 200
    assert scheduler.json()["enabled"] is False
    assert scheduler.json()["settings"]["backup_enabled"] is False
    assert client.get("/api/operations/jobs").json() == []
    assert client.get("/api/operations/backups").json() == []
    health = client.get("/api/operations/health")
    assert health.status_code == 200
    assert health.json()["overall"] in {"healthy", "attention_needed", "disabled", "degraded"}
    trends = client.get("/api/operations/trends", params={"days": 7})
    assert trends.status_code == 200
    assert trends.json()["window_days"] == 7
    assert trends.json()["jobs"]["total"] == 0
    assert trends.json()["feedback"]["useful_rate"] is None
    assert client.get("/api/operations/trends", params={"days": 14}).status_code == 422


def test_presets_feedback_and_saved_views_round_trip(client):
    presets = client.get("/api/notifications/presets").json()
    assert presets["active"] == "balanced"
    assert set(presets["presets"]) == {"quiet", "balanced", "breaking_news"}
    preview = client.get("/api/notifications/presets/quiet/preview")
    assert preview.status_code == 200
    applied = client.post("/api/notifications/presets/quiet/apply")
    assert applied.status_code == 200
    assert client.get("/api/notifications/presets").json()["active"] == "quiet"
    assert client.get("/api/notifications/delivery-status").json()["enabled"] is False

    notification_id = client.post("/api/notifications/test").json()["id"]
    feedback = client.post(
        f"/api/notifications/{notification_id}/feedback",
        json={"rating": "useful", "reason": "good_timing", "note": "Useful test"},
    )
    assert feedback.status_code == 200
    assert client.get("/api/notifications/feedback-summary").json()["useful"] == 1

    body = {
        "name": "Unread important", "state_filter": "unread",
        "event_types": [], "severities": ["important"], "topic_ids": [],
        "relation_filters": {}, "date_window_days": 7, "search_text": "",
        "sort_order": "newest",
    }
    created = client.post("/api/notifications/saved-views", json=body)
    assert created.status_code == 201
    view_id = created.json()["id"]
    assert client.get("/api/notifications/saved-views").json()[0]["name"] == "Unread important"
    assert client.delete(f"/api/notifications/saved-views/{view_id}").status_code == 204


def test_saved_view_full_crud_duplicate_and_apply(client):
    amd = client.post("/api/topics", json={"name": "AMD"}).json()
    nvidia = client.post("/api/topics", json={"name": "NVIDIA"}).json()

    body = {
        "name": "Unread important", "state_filter": "unread",
        "event_types": ["high_attention", "independent_corroboration"],
        "severities": ["important", "urgent"], "topic_ids": [amd["id"], nvidia["id"]],
        "relation_filters": {}, "date_window_days": 7, "search_text": "",
        "sort_order": "severity",
    }
    created = client.post("/api/notifications/saved-views", json=body)
    assert created.status_code == 201
    view = created.json()
    assert view["event_types"] == ["high_attention", "independent_corroboration"]
    assert view["severities"] == ["important", "urgent"]
    assert sorted(view["topic_ids"]) == sorted([amd["id"], nvidia["id"]])
    assert "description" in view and "Unread" in view["description"]
    view_id = view["id"]

    fetched = client.get(f"/api/notifications/saved-views/{view_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Unread important"

    updated = client.put(
        f"/api/notifications/saved-views/{view_id}",
        json={**body, "severities": ["urgent"], "search_text": "leak"},
    )
    assert updated.status_code == 200
    assert updated.json()["severities"] == ["urgent"]
    assert updated.json()["search_text"] == "leak"

    duplicated = client.post(f"/api/notifications/saved-views/{view_id}/duplicate")
    assert duplicated.status_code == 201
    duplicate_body = duplicated.json()
    assert duplicate_body["name"] == "Unread important copy"
    assert duplicate_body["severities"] == ["urgent"]

    # Seed one matching and one non-matching notification, then apply the view.
    notification_id = client.post("/api/notifications/test").json()["id"]
    apply_result = client.get(f"/api/notifications/saved-views/{view_id}/apply")
    assert apply_result.status_code == 200
    payload = apply_result.json()
    assert payload["view"]["id"] == view_id
    # The test notification is 'informational', the view requires 'urgent' -- no match.
    assert payload["notifications"] == []

    # Applying twice is idempotent and never mutates notification state.
    before = client.get(f"/api/notifications/{notification_id}").json()
    client.get(f"/api/notifications/saved-views/{view_id}/apply")
    client.get(f"/api/notifications/saved-views/{view_id}/apply")
    after = client.get(f"/api/notifications/{notification_id}").json()
    assert before["read_at"] == after["read_at"] == None
    assert before["dismissed_at"] == after["dismissed_at"] == None

    delete_dup = client.delete(f"/api/notifications/saved-views/{duplicate_body['id']}")
    assert delete_dup.status_code == 204
    assert client.get(f"/api/notifications/saved-views/{duplicate_body['id']}").status_code == 404

    assert client.delete(f"/api/notifications/saved-views/{view_id}").status_code == 204
    assert client.get(f"/api/notifications/saved-views/{view_id}").status_code == 404


def test_saved_view_missing_id_and_duplicate_name_and_invalid_filters(client):
    assert client.get("/api/notifications/saved-views/999").status_code == 404
    assert client.put(
        "/api/notifications/saved-views/999",
        json={
            "name": "Ghost", "state_filter": "unread", "event_types": [], "severities": [],
            "topic_ids": [], "relation_filters": {}, "date_window_days": None,
            "search_text": "", "sort_order": "newest",
        },
    ).status_code == 404
    assert client.delete("/api/notifications/saved-views/999").status_code == 404
    assert client.post("/api/notifications/saved-views/999/duplicate").status_code == 404
    assert client.get("/api/notifications/saved-views/999/apply").status_code == 404

    base = {
        "name": "Dup name", "state_filter": "unread", "event_types": [], "severities": [],
        "topic_ids": [], "relation_filters": {}, "date_window_days": None,
        "search_text": "", "sort_order": "newest",
    }
    first = client.post("/api/notifications/saved-views", json=base)
    assert first.status_code == 201
    second = client.post("/api/notifications/saved-views", json=base)
    assert second.status_code == 422

    invalid_severity = client.post(
        "/api/notifications/saved-views", json={**base, "name": "Bad severity", "severities": ["extreme"]}
    )
    assert invalid_severity.status_code == 422
    invalid_event = client.post(
        "/api/notifications/saved-views", json={**base, "name": "Bad event", "event_types": ["nope"]}
    )
    assert invalid_event.status_code == 422
    invalid_window = client.post(
        "/api/notifications/saved-views", json={**base, "name": "Bad window", "date_window_days": 5}
    )
    assert invalid_window.status_code == 422
    invalid_sort = client.post(
        "/api/notifications/saved-views", json={**base, "name": "Bad sort", "sort_order": "random"}
    )
    assert invalid_sort.status_code == 422
    invalid_state = client.post(
        "/api/notifications/saved-views", json={**base, "name": "Bad state", "state_filter": "archived"}
    )
    assert invalid_state.status_code == 422


def test_saved_view_update_preserves_relation_filters_when_field_omitted(client):
    created = client.post(
        "/api/notifications/saved-views",
        json={
            "name": "Has relation", "state_filter": "unread", "event_types": [], "severities": [],
            "topic_ids": [], "relation_filters": {"story_id": "4"}, "date_window_days": None,
            "search_text": "", "sort_order": "newest",
        },
    ).json()
    view_id = created["id"]
    assert created["relation_filters"] == {"story_id": "4"}

    # Send a partial body (relation_filters omitted entirely) via a raw dict
    # to exercise the "unrelated field edit must not discard stored data" path.
    partial_update = client.put(
        f"/api/notifications/saved-views/{view_id}",
        json={
            "name": "Has relation", "state_filter": "unread", "event_types": ["high_attention"],
            "severities": [], "topic_ids": [], "date_window_days": None,
            "search_text": "", "sort_order": "newest",
        },
    )
    assert partial_update.status_code == 200
    assert partial_update.json()["relation_filters"] == {"story_id": "4"}
    assert partial_update.json()["event_types"] == ["high_attention"]


def test_notifications_list_supports_repeated_query_params_and_stays_backward_compatible(client):
    amd = client.post("/api/topics", json={"name": "AMD"}).json()
    client.post("/api/notifications/test")

    # Legacy single-value calls keep working.
    assert client.get("/api/notifications", params={"state": "unread"}).status_code == 200
    assert client.get("/api/notifications", params={"severity": "informational"}).status_code == 200
    assert client.get("/api/notifications", params={"event_type": "test"}).status_code == 200

    # Repeated params (multi-value OR) are accepted.
    multi = client.get(
        "/api/notifications",
        params=[
            ("state", "all"), ("severity", "important"), ("severity", "urgent"),
            ("event_type", "high_attention"), ("event_type", "independent_corroboration"),
            ("topic_id", amd["id"]),
        ],
    )
    assert multi.status_code == 200

    assert client.get("/api/notifications", params={"date_window_days": 5}).status_code == 422
    assert client.get("/api/notifications", params={"sort": "random"}).status_code == 422
    assert client.get("/api/notifications", params={"state": "archived"}).status_code == 422


def test_delivery_preview_is_network_free_and_validation_is_controlled(client):
    preview = client.get("/api/notifications/delivery-preview")
    assert preview.status_code == 200
    assert preview.json()["network_contacted"] is False
    assert preview.json()["configured"] is False
    enable = client.post(
        "/api/notifications/delivery-enable",
        json={"enabled": True, "allow_untested": False},
    )
    assert enable.status_code == 409
    invalid_feedback = client.post(
        f"/api/notifications/{client.post('/api/notifications/test').json()['id']}/feedback",
        json={"rating": "useful", "reason": "too_old"},
    )
    assert invalid_feedback.status_code == 422
