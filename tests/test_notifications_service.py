from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import func, select

from semi_intel.domain.enums import (
    NotificationEventType,
    ProviderRunStatus,
    SignalCandidateState,
    SourceSuggestionKind,
    SourceSuggestionStatus,
    SourceType,
)
from semi_intel.domain.models import (
    CandidatePromotionEvent,
    EditorialStory,
    MonitoredTopic,
    Notification,
    NotificationSettings,
    ProviderIncident,
    ProviderRun,
    SignalCandidate,
    Source,
    SourceSuggestion,
)
from semi_intel.notifications.service import NotificationService


BASE = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)


def seed_topic_source_candidate(db_session, *, latest=BASE, score=0.5, groups=1):
    topic = MonitoredTopic(
        name="RTX 50 Super", normalized_name="rtx 50 super", keyword="RTX 50 Super",
        aliases="[]", category="gpu", priority=0.9, enabled=True,
    )
    source = Source(
        name="Example", type=SourceType.RSS, provider="rss",
        provider_key="https://example.com/feed", polling_enabled=False,
    )
    db_session.add_all([topic, source])
    db_session.flush()
    candidate = SignalCandidate(
        fingerprint=f"candidate-{latest.timestamp()}",
        title="RTX 50 Super specifications",
        state=SignalCandidateState.ACTIVE,
        attention_score=score,
        score_explanation=json.dumps({
            "components": {
                "topic_relevance": {"contribution": 0.4, "detail": "high-priority RTX 50 Super topic"},
                "source_diversity": {"contribution": 0.2, "detail": f"{groups} independent groups"},
            }
        }),
        first_observed_at=latest,
        latest_observed_at=latest,
        item_count=max(groups, 1),
        distinct_source_count=max(groups, 1),
        independent_source_group_count=groups,
        primary_topic_id=topic.id,
    )
    db_session.add(candidate)
    db_session.flush()
    return topic, source, candidate


def test_settings_default_external_off_and_activation_watermark(db_session):
    settings = NotificationService(db_session).settings(now=BASE)

    assert settings.in_app_enabled is True
    assert settings.external_delivery_enabled is False
    assert settings.activation_at == BASE
    assert settings.daily_digest_enabled is False


def test_historical_candidates_seed_watermarks_without_alert_flood(db_session):
    _, _, candidate = seed_topic_source_candidate(
        db_session, latest=BASE - dt.timedelta(days=30), score=0.95, groups=4
    )
    service = NotificationService(db_session)
    service.settings(now=BASE)

    first = service.generate(now=BASE)
    second = service.generate(now=BASE + dt.timedelta(minutes=5))

    assert first.created_count == 0
    assert first.seeded_historical_candidates == 1
    assert second.created_count == 0
    assert db_session.scalar(select(func.count()).select_from(Notification)) == 0


def test_candidate_transitions_create_once_and_unchanged_rerun_deduplicates(db_session):
    topic, _, candidate = seed_topic_source_candidate(
        db_session, latest=BASE + dt.timedelta(minutes=1), score=0.50, groups=1
    )
    service = NotificationService(db_session)
    settings = service.settings(now=BASE)
    settings.minimum_attention_score = 0.70
    settings.minimum_score_increase = 0.15
    settings.required_independent_group_count = 2

    initial = service.generate(now=BASE + dt.timedelta(minutes=1))
    assert initial.created_count == 0

    candidate.attention_score = 0.80
    candidate.independent_source_group_count = 2
    candidate.distinct_source_count = 2
    candidate.latest_observed_at = BASE + dt.timedelta(minutes=2)
    changed = service.generate(now=BASE + dt.timedelta(minutes=2))
    event_types = set(db_session.scalars(select(Notification.event_type)))

    assert changed.created_count == 4
    assert event_types == {
        NotificationEventType.HIGH_ATTENTION,
        NotificationEventType.SCORE_INCREASE,
        NotificationEventType.INDEPENDENT_CORROBORATION,
        NotificationEventType.PROMOTION_READY,
    }
    assert all(row.topic_id == topic.id for row in db_session.scalars(select(Notification)))

    unchanged = service.generate(now=BASE + dt.timedelta(minutes=3))
    assert unchanged.created_count == 0
    assert db_session.scalar(select(func.count()).select_from(Notification)) == 4


def test_promotion_event_is_linked_and_deduplicated(db_session):
    _, _, candidate = seed_topic_source_candidate(
        db_session, latest=BASE + dt.timedelta(minutes=1), score=0.8, groups=2
    )
    story = EditorialStory(
        canonical_key="story", headline="RTX 50 Super", latest_at=BASE,
    )
    db_session.add(story)
    db_session.flush()
    event = CandidatePromotionEvent(
        candidate_id=candidate.id, story_id=story.id, promoted_by="human:test",
        automatic=False, reason="editor approved", created_at=BASE + dt.timedelta(minutes=2),
    )
    db_session.add(event)
    service = NotificationService(db_session)
    service.settings(now=BASE)

    service.generate(now=BASE + dt.timedelta(minutes=3))
    service.generate(now=BASE + dt.timedelta(minutes=4))

    notification = db_session.scalar(select(Notification).where(
        Notification.event_type == NotificationEventType.CANDIDATE_PROMOTED
    ))
    assert notification.candidate_id == candidate.id
    assert notification.story_id == story.id
    assert notification.reason == "editor approved"
    assert db_session.scalar(select(func.count()).select_from(Notification).where(
        Notification.event_type == NotificationEventType.CANDIDATE_PROMOTED
    )) == 1


def test_source_suggestion_threshold_creates_one_review_alert(db_session):
    suggestion = SourceSuggestion(
        domain="example.com", inferred_name="Example News", score=0.8,
        appearances=5, story_count=3, topic_count=2,
        status=SourceSuggestionStatus.PENDING,
        kind=SourceSuggestionKind.DOMAIN,
        first_seen_at=BASE, last_seen_at=BASE + dt.timedelta(minutes=1),
    )
    db_session.add(suggestion)
    service = NotificationService(db_session)
    service.settings(now=BASE)

    service.generate(now=BASE + dt.timedelta(minutes=2))
    service.generate(now=BASE + dt.timedelta(minutes=3))

    rows = list(db_session.scalars(select(Notification).where(
        Notification.event_type == NotificationEventType.SOURCE_SUGGESTION
    )))
    assert len(rows) == 1
    assert rows[0].source_suggestion_id == suggestion.id


def test_provider_failure_threshold_and_recovery_are_transitions(db_session):
    source = Source(
        name="Broken Feed", type=SourceType.RSS, provider="rss",
        provider_key="https://example.com/feed",
    )
    db_session.add(source)
    db_session.flush()
    service = NotificationService(db_session)
    settings = service.settings(now=BASE)
    settings.provider_failure_threshold = 3

    for index in range(2):
        db_session.add(ProviderRun(
            provider="rss", source_id=source.id,
            started_at=BASE + dt.timedelta(minutes=index + 1),
            finished_at=BASE + dt.timedelta(minutes=index + 1, seconds=5),
            status=ProviderRunStatus.FAILED, error="timeout",
        ))
    service.generate(now=BASE + dt.timedelta(minutes=3))
    assert db_session.scalar(select(func.count()).select_from(Notification)) == 0

    third = ProviderRun(
        provider="rss", source_id=source.id,
        started_at=BASE + dt.timedelta(minutes=3),
        finished_at=BASE + dt.timedelta(minutes=3, seconds=5),
        status=ProviderRunStatus.FAILED, error="password=do-not-leak",
    )
    db_session.add(third)
    service.generate(now=BASE + dt.timedelta(minutes=4))

    failure = db_session.scalar(select(Notification).where(
        Notification.event_type == NotificationEventType.PROVIDER_FAILURE
    ))
    assert failure is not None
    assert "do-not-leak" not in failure.reason
    incident = db_session.scalar(select(ProviderIncident))
    assert incident.consecutive_failures == 3

    service.generate(now=BASE + dt.timedelta(minutes=5))
    assert failure.occurrence_count == 1

    success = ProviderRun(
        provider="rss", source_id=source.id,
        started_at=BASE + dt.timedelta(minutes=6),
        finished_at=BASE + dt.timedelta(minutes=6, seconds=5),
        status=ProviderRunStatus.OK,
    )
    db_session.add(success)
    service.generate(now=BASE + dt.timedelta(minutes=7))
    service.generate(now=BASE + dt.timedelta(minutes=8))

    assert incident.resolved_at is not None
    assert db_session.scalar(select(func.count()).select_from(Notification).where(
        Notification.event_type == NotificationEventType.PROVIDER_RECOVERY
    )) == 1


def test_read_dismiss_restore_and_mutes_persist(db_session):
    service = NotificationService(db_session)
    settings = service.settings(now=BASE)
    notification = service.create_test_notification(now=BASE)

    service.set_read([notification.id], read=True, now=BASE + dt.timedelta(minutes=1))
    assert notification.read_at is not None
    service.set_read([notification.id], read=False)
    assert notification.read_at is None
    service.dismiss(notification, now=BASE + dt.timedelta(minutes=2))
    assert notification.dismissed_at is not None
    service.restore(notification)
    assert notification.dismissed_at is None

    settings.muted_event_types = json.dumps([NotificationEventType.HIGH_ATTENTION.value])
    _, _, candidate = seed_topic_source_candidate(
        db_session, latest=BASE + dt.timedelta(minutes=3), score=0.9, groups=3
    )
    service.generate(now=BASE + dt.timedelta(minutes=4))
    high = db_session.scalar(select(Notification).where(
        Notification.event_type == NotificationEventType.HIGH_ATTENTION
    ))
    assert high.muted is True
