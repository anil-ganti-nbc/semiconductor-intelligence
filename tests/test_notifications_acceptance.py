"""Deterministic Phase 8 acceptance scenario without network access."""

from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import func, select

from semi_intel.domain.enums import (
    NotificationEventType, ProviderRunStatus, SignalCandidateState,
)
from semi_intel.domain.models import (
    CandidatePromotionEvent, EditorialStory, Notification, NotificationDigest,
    ProviderRun, SignalCandidate,
)
from semi_intel.notifications.delivery import DeliveryService
from semi_intel.notifications.digest import DigestService
from semi_intel.notifications.service import NotificationService
from tests.test_notifications_digest_delivery import FakeAdapter
from tests.test_notifications_service import seed_topic_source_candidate


BASE = dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.UTC)


def test_phase8_from_now_to_digest_and_delivery_acceptance(db_session):
    service = NotificationService(db_session)
    settings = service.settings(now=BASE)
    settings.timezone = "UTC"
    settings.digest_time = "08:00"
    settings.minimum_attention_score = 0.70
    settings.minimum_score_increase = 0.15
    settings.required_independent_group_count = 2
    settings.provider_failure_threshold = 3

    topic, source, historical = seed_topic_source_candidate(
        db_session, latest=BASE - dt.timedelta(days=30), score=0.95, groups=4
    )
    service.generate(now=BASE)
    assert db_session.scalar(select(func.count()).select_from(Notification)) == 0

    fresh = SignalCandidate(
        fingerprint="phase8-acceptance", title="RTX 50 Super acceptance candidate",
        state=SignalCandidateState.ACTIVE, attention_score=0.50,
        score_explanation=json.dumps({"components": {
            "topic_relevance": {"contribution": .3, "detail": "tracked RTX 50 Super topic"},
            "source_diversity": {"contribution": .1, "detail": "one independent group"},
        }}),
        first_observed_at=BASE + dt.timedelta(minutes=1),
        latest_observed_at=BASE + dt.timedelta(minutes=1),
        item_count=1, distinct_source_count=1, independent_source_group_count=1,
        primary_topic_id=topic.id,
    )
    db_session.add(fresh)
    db_session.flush()
    service.generate(now=BASE + dt.timedelta(minutes=1))
    assert db_session.scalar(select(func.count()).select_from(Notification).where(
        Notification.candidate_id == fresh.id
    )) == 0

    fresh.attention_score = 0.82
    fresh.independent_source_group_count = 2
    fresh.distinct_source_count = 2
    fresh.latest_observed_at = BASE + dt.timedelta(minutes=2)
    service.generate(now=BASE + dt.timedelta(minutes=2))
    types = set(db_session.scalars(select(Notification.event_type).where(
        Notification.candidate_id == fresh.id
    )))
    assert {
        NotificationEventType.HIGH_ATTENTION,
        NotificationEventType.INDEPENDENT_CORROBORATION,
        NotificationEventType.PROMOTION_READY,
    }.issubset(types)
    count_before = db_session.scalar(select(func.count()).select_from(Notification))
    service.generate(now=BASE + dt.timedelta(minutes=3))
    assert db_session.scalar(select(func.count()).select_from(Notification)) == count_before

    first_alert = db_session.scalar(select(Notification).where(
        Notification.candidate_id == fresh.id
    ))
    service.set_read([first_alert.id], read=True)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Notification, first_alert.id).read_at is not None
    service.dismiss(db_session.get(Notification, first_alert.id))
    service.restore(db_session.get(Notification, first_alert.id))

    story = EditorialStory(
        canonical_key="phase8-story", headline=fresh.title,
        latest_at=BASE + dt.timedelta(minutes=4),
    )
    db_session.add(story)
    db_session.flush()
    fresh.state = SignalCandidateState.PROMOTED
    fresh.promoted_story_id = story.id
    db_session.add(CandidatePromotionEvent(
        candidate_id=fresh.id, story_id=story.id, promoted_by="human:acceptance",
        automatic=False, reason="accepted in deterministic Phase 8 scenario",
        created_at=BASE + dt.timedelta(minutes=4),
    ))
    service.generate(now=BASE + dt.timedelta(minutes=5))
    assert db_session.scalar(select(func.count()).select_from(Notification).where(
        Notification.event_type == NotificationEventType.CANDIDATE_PROMOTED,
        Notification.story_id == story.id,
    )) == 1

    for minute in (6, 7):
        db_session.add(ProviderRun(
            provider="rss", source_id=source.id, started_at=BASE + dt.timedelta(minutes=minute),
            finished_at=BASE + dt.timedelta(minutes=minute, seconds=1),
            status=ProviderRunStatus.FAILED, error="timeout",
        ))
    service.generate(now=BASE + dt.timedelta(minutes=8))
    assert db_session.scalar(select(func.count()).select_from(Notification).where(
        Notification.event_type == NotificationEventType.PROVIDER_FAILURE
    )) == 0
    db_session.add(ProviderRun(
        provider="rss", source_id=source.id, started_at=BASE + dt.timedelta(minutes=8),
        finished_at=BASE + dt.timedelta(minutes=8, seconds=1),
        status=ProviderRunStatus.FAILED, error="api_key=never-store-this",
    ))
    service.generate(now=BASE + dt.timedelta(minutes=9))
    assert db_session.scalar(select(func.count()).select_from(Notification).where(
        Notification.event_type == NotificationEventType.PROVIDER_FAILURE
    )) == 1
    db_session.add(ProviderRun(
        provider="rss", source_id=source.id, started_at=BASE + dt.timedelta(minutes=10),
        finished_at=BASE + dt.timedelta(minutes=10, seconds=1),
        status=ProviderRunStatus.OK,
    ))
    service.generate(now=BASE + dt.timedelta(minutes=11))
    assert db_session.scalar(select(func.count()).select_from(Notification).where(
        Notification.event_type == NotificationEventType.PROVIDER_RECOVERY
    )) == 1

    digest_now = BASE + dt.timedelta(days=1, minutes=1)
    digest = DigestService(db_session).generate(now=digest_now)
    assert DigestService(db_session).generate(now=digest_now + dt.timedelta(minutes=10)).id == digest.id
    sections = json.loads(digest.structured_sections)
    assert "fresh_corroboration" in sections
    assert "promotion_activity" in sections
    assert "provider_health" in sections
    assert db_session.scalar(select(func.count()).select_from(NotificationDigest)) == 1
    assert historical.seen_at is None and fresh.seen_at is None and story.seen_at is None

    settings.external_delivery_enabled = True
    settings.quiet_hours_start = "22:00"
    settings.quiet_hours_end = "07:00"
    adapter = FakeAdapter()
    delivery = DeliveryService(db_session)
    deferred = delivery.deliver_notification(
        db_session.get(Notification, first_alert.id), adapter,
        now=dt.datetime(2026, 1, 1, 23, 0, tzinfo=dt.UTC),
    )
    delivered = delivery.deliver_notification(
        db_session.get(Notification, first_alert.id), adapter,
        now=dt.datetime(2026, 1, 2, 7, 1, tzinfo=dt.UTC),
    )
    repeated = delivery.deliver_notification(
        db_session.get(Notification, first_alert.id), adapter,
        now=dt.datetime(2026, 1, 2, 7, 2, tzinfo=dt.UTC),
    )
    assert deferred.status.value == "deferred"
    assert delivered.status.value == "delivered"
    assert repeated.id == delivered.id
    assert len(adapter.calls) == 1
