from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from semi_intel.domain.enums import NotificationDeliveryState
from semi_intel.notifications.digest import DigestService
from semi_intel.notifications.service import NotificationService
from tests.test_notifications_service import seed_topic_source_candidate


def test_manual_refresh_reuses_window_but_includes_new_material(db_session):
    now = dt.datetime(2026, 1, 2, 9, 0, tzinfo=dt.UTC)
    settings = NotificationService(db_session).settings(now=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    settings.timezone = "UTC"
    settings.digest_time = "08:00"
    first = DigestService(db_session).generate_manual(now=now)[0]
    assert "Why this digest is empty" in first.rendered_text

    _, _, candidate = seed_topic_source_candidate(
        db_session, latest=dt.datetime(2026, 1, 2, 7, 0, tzinfo=dt.UTC), score=0.9, groups=3
    )
    refreshed, generated = DigestService(db_session).generate_manual(now=now + dt.timedelta(minutes=1))
    assert refreshed.id == first.id
    assert refreshed.generated_at == now + dt.timedelta(minutes=1)
    assert json.loads(refreshed.structured_sections)["top_unseen_candidates"][0]["candidate_id"] == candidate.id
    assert generated["created"] >= 0


def test_refresh_never_clears_delivered_state(db_session):
    now = dt.datetime(2026, 1, 2, 9, 0, tzinfo=dt.UTC)
    settings = NotificationService(db_session).settings(now=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    settings.timezone = "UTC"
    settings.digest_time = "08:00"
    digest = DigestService(db_session).generate(now=now)
    digest.delivery_state = NotificationDeliveryState.DELIVERED
    db_session.commit()
    refreshed = DigestService(db_session).generate(now=now + dt.timedelta(minutes=2), refresh=True)
    assert refreshed.id == digest.id
    assert refreshed.delivery_state == NotificationDeliveryState.DELIVERED


@pytest.fixture()
def configured_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{tmp_path / 'delivery_repair.db'}")
    monkeypatch.setenv("SEMI_INTEL_WEBHOOK_URL", "https://hooks.example.test/semintel")
    from semi_intel.operations.webhook import WebhookAdapter
    from semi_intel.notifications.delivery import AdapterResult

    monkeypatch.setattr(
        WebhookAdapter, "deliver",
        lambda self, text, *, idempotency_key: AdapterResult(
            delivered=True, external_message_id="synthetic-delivery"
        ),
    )
    from semi_intel.web.app import create_app
    with TestClient(create_app(mutation_authorizer=lambda _value: True)) as client:
        yield client


def test_gui_delivery_requires_test_then_enable_and_can_deliver_digest(configured_client):
    status = configured_client.get("/api/notifications/delivery-status").json()
    assert status["configured"] is True
    assert status["enabled"] is False
    assert configured_client.post(
        "/api/notifications/delivery-enable", json={"enabled": True, "allow_untested": False}
    ).status_code == 409

    tested = configured_client.post("/api/notifications/delivery-test", json={})
    assert tested.json()["delivered"] is True
    enabled = configured_client.post(
        "/api/notifications/delivery-enable", json={"enabled": True, "allow_untested": False}
    )
    assert enabled.status_code == 200

    generated = configured_client.post(
        "/api/notifications/digest",
        json={"refresh": True, "generate_notifications": True, "deliver": True},
    )
    assert generated.status_code == 200, generated.text
    assert generated.json()["delivery"]["digests"] == 1
    repeated = configured_client.post("/api/notifications/delivery-retry", json={}).json()
    assert repeated["digests"] == 0


def test_digest_and_delivery_controls_and_no_obsolete_phase_message():
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    for expected in (
        'id="notification-delivery-enable"',
        'id="notification-delivery-retry"',
        'id="notification-digest-deliver"',
        "toggleWebhookDelivery()",
        "deliverPendingNotifications()",
        "SEMI_INTEL_WEBHOOK_URL",
        "SEMI_INTEL_WEBHOOK_TOKEN",
    ):
        assert expected in html
    assert "not configured in Phase 8" not in html
