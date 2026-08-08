from __future__ import annotations

import datetime as dt
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from semi_intel.domain.enums import (
    DeliveryAttemptStatus,
    NotificationDeliveryState,
    NotificationEventType,
    NotificationSeverity,
)
from semi_intel.domain.models import (
    EditorialStory,
    MonitoredTopic,
    Notification,
    NotificationDeliveryAttempt,
)
from semi_intel.notifications.delivery import AdapterResult, DeliveryService
from semi_intel.notifications.service import NotificationService
from semi_intel.notifications.windows_desktop import (
    DesktopSupport,
    WINDOWS_DESKTOP_CHANNEL,
    WindowsDesktopAdapter,
    WindowsDesktopDeliveryService,
)


BASE = dt.datetime(2026, 8, 1, 12, 0, tzinfo=dt.UTC)


class FakeDesktopAdapter:
    name = "fake_windows_desktop"
    channel = WINDOWS_DESKTOP_CHANNEL

    def __init__(self, *, supported=True, outcomes=None):
        self.is_supported = supported
        self.outcomes = list(outcomes or [AdapterResult(delivered=True, external_message_id="toast-1")])
        self.calls = []

    def support(self):
        return DesktopSupport(
            self.is_supported,
            "available" if self.is_supported else "unavailable",
            "Available." if self.is_supported else "Unavailable.",
        )

    def deliver(self, text, *, idempotency_key):
        self.calls.append((text, idempotency_key))
        return self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]


class FakeWebhookAdapter:
    name = "fake_webhook"
    channel = "webhook"

    def __init__(self):
        self.calls = []

    def deliver(self, text, *, idempotency_key):
        self.calls.append((text, idempotency_key))
        return AdapterResult(delivered=True, external_message_id="webhook-1")


def _notification(db_session, *, muted=False, story=None, topic=None):
    row = Notification(
        event_type=NotificationEventType.HIGH_ATTENTION,
        severity=NotificationSeverity.IMPORTANT,
        title="RTX 50 Super receives independent confirmation",
        body="Two independent publishers now corroborate the report.",
        reason="Independent source threshold reached.",
        dedup_key=f"desktop-test-{db_session.scalar(select(func.count()).select_from(Notification))}",
        story_id=story.id if story else None,
        topic_id=topic.id if topic else None,
        created_at=BASE,
        event_at=BASE,
        first_occurrence_at=BASE,
        latest_occurrence_at=BASE,
        muted=muted,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _settings(db_session):
    settings = NotificationService(db_session).settings(now=BASE - dt.timedelta(minutes=1))
    settings.timezone = "UTC"
    settings.quiet_hours_start = "00:00"
    settings.quiet_hours_end = "00:00"
    return settings


def test_desktop_delivery_is_disabled_by_default(db_session):
    settings = _settings(db_session)
    notification = _notification(db_session)
    adapter = FakeDesktopAdapter()

    result = WindowsDesktopDeliveryService(db_session, adapter=adapter).deliver_pending(now=BASE)

    assert settings.windows_desktop_notifications_enabled is False
    assert result == {"notifications": 0, "disabled": True, "supported": True}
    assert adapter.calls == []
    assert notification.delivery_state == NotificationDeliveryState.IN_APP


def test_enable_persists_and_unsupported_platform_cannot_enable(db_session):
    supported = WindowsDesktopDeliveryService(db_session, adapter=FakeDesktopAdapter())
    assert supported.set_enabled(True)["enabled"] is True
    db_session.expire_all()
    assert NotificationService(db_session).settings().windows_desktop_notifications_enabled is True

    supported.set_enabled(False)
    unavailable = WindowsDesktopDeliveryService(
        db_session, adapter=FakeDesktopAdapter(supported=False)
    )
    with pytest.raises(ValueError, match="Unavailable"):
        unavailable.set_enabled(True)


def test_success_is_exactly_once_and_preserves_story_and_notification_state(db_session):
    settings = _settings(db_session)
    settings.windows_desktop_notifications_enabled = True
    story = EditorialStory(
        canonical_key="desktop-story", headline="RTX 50 Super", latest_at=BASE.replace(tzinfo=None),
        seen_at=BASE.replace(tzinfo=None),
    )
    topic = MonitoredTopic(
        name="RTX 50 Super", normalized_name="rtx 50 super", keyword="RTX 50 Super",
    )
    db_session.add_all([story, topic])
    db_session.flush()
    notification = _notification(db_session, story=story, topic=topic)
    adapter = FakeDesktopAdapter()
    service = WindowsDesktopDeliveryService(db_session, adapter=adapter)

    first = service.deliver_pending(now=BASE)
    second = service.deliver_pending(now=BASE + dt.timedelta(minutes=1))

    assert first["notifications"] == 1
    assert second["notifications"] == 0
    assert len(adapter.calls) == 1
    assert "RTX 50 Super" in adapter.calls[0][0]
    assert notification.delivery_state == NotificationDeliveryState.IN_APP
    assert notification.read_at is None and notification.dismissed_at is None
    assert story.seen_at == BASE.replace(tzinfo=None)


def test_desktop_and_webhook_delivery_are_independent(db_session):
    settings = _settings(db_session)
    settings.windows_desktop_notifications_enabled = True
    settings.external_delivery_enabled = True
    notification = _notification(db_session)
    desktop = FakeDesktopAdapter()
    webhook = FakeWebhookAdapter()

    WindowsDesktopDeliveryService(db_session, adapter=desktop).deliver_pending(now=BASE)
    DeliveryService(db_session).deliver_notification(notification, webhook, now=BASE)

    assert len(desktop.calls) == len(webhook.calls) == 1
    assert notification.delivery_state == NotificationDeliveryState.DELIVERED
    assert set(db_session.scalars(select(NotificationDeliveryAttempt.channel))) == {
        WINDOWS_DESKTOP_CHANNEL, "webhook"
    }


def test_muted_and_quiet_hour_notifications_do_not_display(db_session):
    settings = _settings(db_session)
    settings.windows_desktop_notifications_enabled = True
    muted = _notification(db_session, muted=True)
    adapter = FakeDesktopAdapter()
    service = WindowsDesktopDeliveryService(db_session, adapter=adapter)

    assert service.deliver_pending(now=BASE)["notifications"] == 0
    assert adapter.calls == []

    muted.muted = False
    settings.quiet_hours_start = "11:00"
    settings.quiet_hours_end = "13:00"
    result = service.deliver_pending(now=BASE)
    attempt = db_session.scalar(select(NotificationDeliveryAttempt))
    assert result["notifications"] == 1
    assert attempt.status == DeliveryAttemptStatus.DEFERRED
    assert adapter.calls == []


def test_failure_retries_are_bounded_and_do_not_change_delivery_state(db_session):
    settings = _settings(db_session)
    settings.windows_desktop_notifications_enabled = True
    settings.maximum_delivery_attempts = 2
    notification = _notification(db_session)
    failure = AdapterResult(delivered=False, error="synthetic", retryable=True)
    adapter = FakeDesktopAdapter(outcomes=[failure, failure])
    service = WindowsDesktopDeliveryService(db_session, adapter=adapter)

    service.deliver_pending(now=BASE)
    service.deliver_pending(now=BASE + dt.timedelta(minutes=1))
    service.deliver_pending(now=BASE + dt.timedelta(minutes=6))
    service.deliver_pending(now=BASE + dt.timedelta(hours=1))

    assert len(adapter.calls) == 2
    assert notification.delivery_state == NotificationDeliveryState.IN_APP
    assert db_session.scalar(select(func.count()).select_from(NotificationDeliveryAttempt)) == 2


def test_permanent_failure_is_not_retried(db_session):
    settings = _settings(db_session)
    settings.windows_desktop_notifications_enabled = True
    notification = _notification(db_session)
    failure = AdapterResult(delivered=False, error="rejected", retryable=False)
    adapter = FakeDesktopAdapter(outcomes=[failure])
    service = WindowsDesktopDeliveryService(db_session, adapter=adapter)

    service.deliver_pending(now=BASE)
    service.deliver_pending(now=BASE + dt.timedelta(hours=1))

    assert len(adapter.calls) == 1
    assert notification.delivery_state == NotificationDeliveryState.IN_APP
    attempt = db_session.scalar(select(NotificationDeliveryAttempt))
    assert attempt.status == DeliveryAttemptStatus.FAILED
    assert attempt.retry_after is None


def test_desktop_adapter_failure_is_isolated_from_pipeline(db_session, monkeypatch):
    from semi_intel.notifications import windows_desktop
    from semi_intel.pipeline.service import PipelineService

    class RaisingService:
        def __init__(self, session):
            self.session = session

        def deliver_pending(self):
            raise RuntimeError("synthetic desktop failure")

    monkeypatch.setattr(windows_desktop, "WindowsDesktopDeliveryService", RaisingService)
    result = PipelineService(db_session).run_once(include_pci_ids=False)

    assert all(failure.source_name != "notifications" for failure in result.failures)
    assert NotificationService(db_session).settings() is not None


def test_native_adapter_reports_support_and_sanitizes_failures():
    assert WindowsDesktopAdapter(system_name="Linux", executable="powershell").support().supported is False
    assert WindowsDesktopAdapter(system_name="Windows", executable=None).support().state in {
        "available", "unavailable"
    }

    calls = []
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="C:\\secret\\token.txt failed")

    adapter = WindowsDesktopAdapter(system_name="Windows", executable="powershell.exe", runner=runner)
    result = adapter.deliver("Title\nBody", idempotency_key="desktop:1")
    assert result.delivered is False
    assert result.retryable is False
    assert "secret" not in result.error.lower()
    assert calls[0][0][-2] == "-EncodedCommand"
    assert "Title" not in calls[0][0][-1]


def test_status_and_test_endpoints_use_adapter_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{tmp_path / 'desktop-api.db'}")
    from semi_intel.web import app as web_app

    class FakeService:
        def __init__(self, session):
            self.session = session

        def status(self):
            return {"enabled": False, "supported": True, "state": "disabled", "message": "Ready."}

        def test(self):
            return AdapterResult(delivered=True, external_message_id="test-toast")

        def deliver_pending(self):
            return {"notifications": 0, "disabled": True, "supported": True}

    monkeypatch.setattr(web_app, "WindowsDesktopDeliveryService", FakeService)
    client = TestClient(web_app.create_app())
    status = client.get("/api/notifications/windows-desktop/status")
    test = client.post("/api/notifications/windows-desktop/test")
    assert status.json()["state"] == "disabled"
    assert test.json() == {"delivered": True, "external_message_id": "test-toast"}


def test_dashboard_contains_desktop_controls():
    from semi_intel.web.app import STATIC_DIR

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'name="windows_desktop_notifications_enabled"' in html
    assert 'id="windows-desktop-status"' in html
    assert 'onclick="testWindowsDesktopNotification()"' in html
    assert "/api/notifications/windows-desktop/status" in html
    assert "/api/notifications/windows-desktop/test" in html
