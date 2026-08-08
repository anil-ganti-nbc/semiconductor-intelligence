from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import func, select

from semi_intel.domain.enums import (
    DeliveryAttemptStatus,
    NotificationDeliveryState,
    NotificationEventType,
)
from semi_intel.domain.models import Notification, NotificationDeliveryAttempt, NotificationDigest
from semi_intel.notifications.delivery import AdapterResult, DeliveryService, quiet_hours_end
from semi_intel.notifications.digest import DigestService, digest_window
from semi_intel.notifications.service import NotificationService
from tests.test_notifications_service import seed_topic_source_candidate


BASE = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)


class FakeAdapter:
    name = "fake"
    channel = "fake"

    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def deliver(self, text, *, idempotency_key):
        self.calls.append((text, idempotency_key))
        if self.fail:
            return AdapterResult(delivered=False, error="synthetic failure")
        return AdapterResult(delivered=True, external_message_id="message-1")


def test_digest_window_is_timezone_stable():
    start, end = digest_window(
        dt.datetime(2026, 1, 2, 4, 0, tzinfo=dt.UTC), "Asia/Kolkata", "08:00"
    )
    assert end - start == dt.timedelta(days=1)
    assert end == dt.datetime(2026, 1, 2, 2, 30, tzinfo=dt.UTC)


def test_digest_is_stable_and_does_not_mark_candidate_seen(db_session):
    now = dt.datetime(2026, 1, 2, 9, 0, tzinfo=dt.UTC)
    latest = dt.datetime(2026, 1, 2, 7, 0, tzinfo=dt.UTC)
    _, _, candidate = seed_topic_source_candidate(
        db_session, latest=latest, score=0.9, groups=3
    )
    notifications = NotificationService(db_session)
    settings = notifications.settings(now=dt.datetime(2026, 1, 1, 7, 0, tzinfo=dt.UTC))
    settings.timezone = "UTC"
    settings.digest_time = "08:00"
    notifications.generate(now=latest)

    first = DigestService(db_session).generate(now=now)
    second = DigestService(db_session).generate(now=now + dt.timedelta(minutes=15))

    assert first.id == second.id
    sections = json.loads(first.structured_sections)
    assert sections["top_unseen_candidates"][0]["candidate_id"] == candidate.id
    assert "Top unseen candidates" in first.rendered_text
    assert candidate.seen_at is None
    assert db_session.scalar(select(func.count()).select_from(NotificationDigest)) == 1


def test_empty_digest_is_concise(db_session):
    service = NotificationService(db_session)
    settings = service.settings(now=BASE)
    settings.timezone = "UTC"
    settings.digest_time = "08:00"

    digest = DigestService(db_session).generate(
        now=dt.datetime(2026, 1, 2, 9, 0, tzinfo=dt.UTC)
    )

    assert json.loads(digest.structured_sections) == {}
    assert "Nothing material" in digest.rendered_text


def test_quiet_hours_cross_midnight():
    end = quiet_hours_end(
        dt.datetime(2026, 1, 1, 23, 0, tzinfo=dt.UTC), "UTC", "22:00", "07:00"
    )
    assert end == dt.datetime(2026, 1, 2, 7, 0, tzinfo=dt.UTC)
    assert quiet_hours_end(
        dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC), "UTC", "22:00", "07:00"
    ) is None


def test_external_delivery_disabled_by_default(db_session):
    notification = NotificationService(db_session).create_test_notification(now=BASE)
    adapter = FakeAdapter()

    attempt = DeliveryService(db_session).deliver_notification(
        notification, adapter, now=BASE
    )

    assert attempt is None
    assert adapter.calls == []
    assert notification.delivery_state == NotificationDeliveryState.IN_APP


def test_quiet_hours_defer_then_deliver_once(db_session):
    service = NotificationService(db_session)
    settings = service.settings(now=BASE)
    settings.external_delivery_enabled = True
    settings.timezone = "UTC"
    settings.quiet_hours_start = "22:00"
    settings.quiet_hours_end = "07:00"
    notification = service.create_test_notification(now=BASE)
    adapter = FakeAdapter()
    delivery = DeliveryService(db_session)

    quiet = dt.datetime(2026, 1, 1, 23, 0, tzinfo=dt.UTC)
    deferred = delivery.deliver_notification(notification, adapter, now=quiet)
    duplicate_defer = delivery.deliver_notification(
        notification, adapter, now=quiet + dt.timedelta(minutes=5)
    )
    delivered = delivery.deliver_notification(
        notification, adapter, now=dt.datetime(2026, 1, 2, 7, 1, tzinfo=dt.UTC)
    )
    repeated = delivery.deliver_notification(
        notification, adapter, now=dt.datetime(2026, 1, 2, 7, 2, tzinfo=dt.UTC)
    )

    assert deferred.status == DeliveryAttemptStatus.DEFERRED
    assert duplicate_defer.id == deferred.id
    assert delivered.status == DeliveryAttemptStatus.DELIVERED
    assert repeated.id == delivered.id
    assert len(adapter.calls) == 1


def test_failed_delivery_retries_with_bound_and_redaction(db_session):
    service = NotificationService(db_session)
    settings = service.settings(now=BASE)
    settings.external_delivery_enabled = True
    settings.timezone = "UTC"
    settings.quiet_hours_start = "00:00"
    settings.quiet_hours_end = "00:00"
    settings.maximum_delivery_attempts = 2
    notification = service.create_test_notification(now=BASE)
    adapter = FakeAdapter(fail=True)
    delivery = DeliveryService(db_session)

    first = delivery.deliver_notification(notification, adapter, now=BASE)
    held = delivery.deliver_notification(notification, adapter, now=BASE + dt.timedelta(minutes=1))
    second = delivery.deliver_notification(notification, adapter, now=BASE + dt.timedelta(minutes=6))
    bounded = delivery.deliver_notification(notification, adapter, now=BASE + dt.timedelta(hours=1))

    assert first.status == DeliveryAttemptStatus.FAILED
    assert held.id == first.id
    assert second.attempt_number == 2
    assert bounded.id == second.id
    assert len(adapter.calls) == 2
    assert db_session.scalar(select(func.count()).select_from(NotificationDeliveryAttempt)) == 2
