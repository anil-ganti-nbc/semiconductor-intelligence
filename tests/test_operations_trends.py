from __future__ import annotations

import datetime as dt

import pytest

from semi_intel.domain.enums import (
    NotificationEventType,
    NotificationFeedbackRating,
    NotificationSeverity,
    OperationalJobStatus,
    OperationalJobType,
    OperationalTriggerType,
)
from semi_intel.domain.models import Notification, NotificationFeedback, OperationalJobRun
from semi_intel.operations.trends import OperationalTrendService


NOW = dt.datetime(2026, 7, 27, 12, 0, tzinfo=dt.UTC)


def _job(db_session, *, days_ago, job_type, status, duration_minutes):
    started = NOW - dt.timedelta(days=days_ago)
    row = OperationalJobRun(
        job_type=job_type,
        trigger_type=OperationalTriggerType.TEST,
        status=status,
        started_at=started,
        finished_at=started + dt.timedelta(minutes=duration_minutes),
    )
    db_session.add(row)
    return row


def _feedback(db_session, *, days_ago, event_type, rating, reason=None):
    when = NOW - dt.timedelta(days=days_ago)
    notification = Notification(
        event_type=event_type,
        severity=NotificationSeverity.IMPORTANT,
        title=f"{event_type.value} test",
        body="Test",
        reason="Trend test",
        dedup_key=f"trend:{event_type.value}:{days_ago}:{rating.value}",
        event_at=when,
        created_at=when,
        first_occurrence_at=when,
        latest_occurrence_at=when,
    )
    db_session.add(notification)
    db_session.flush()
    db_session.add(NotificationFeedback(
        notification_id=notification.id,
        rating=rating,
        reason=reason,
        created_at=when,
        updated_at=when,
    ))


@pytest.mark.parametrize(("window_days", "expected_jobs"), [(7, 1), (30, 2), (90, 3)])
def test_supported_windows_exclude_older_jobs(db_session, window_days, expected_jobs):
    _job(
        db_session, days_ago=2, job_type=OperationalJobType.PIPELINE,
        status=OperationalJobStatus.SUCCESSFUL, duration_minutes=10,
    )
    _job(
        db_session, days_ago=15, job_type=OperationalJobType.BACKUP,
        status=OperationalJobStatus.FAILED, duration_minutes=4,
    )
    _job(
        db_session, days_ago=45, job_type=OperationalJobType.PIPELINE,
        status=OperationalJobStatus.PARTIAL, duration_minutes=20,
    )
    _job(
        db_session, days_ago=100, job_type=OperationalJobType.PIPELINE,
        status=OperationalJobStatus.SKIPPED, duration_minutes=1,
    )
    db_session.commit()

    summary = OperationalTrendService(db_session).summarize(window_days, now=NOW)

    assert summary["jobs"]["total"] == expected_jobs


def test_job_status_counts_and_average_duration_by_type(db_session):
    _job(
        db_session, days_ago=1, job_type=OperationalJobType.PIPELINE,
        status=OperationalJobStatus.SUCCESSFUL, duration_minutes=10,
    )
    _job(
        db_session, days_ago=2, job_type=OperationalJobType.PIPELINE,
        status=OperationalJobStatus.PARTIAL, duration_minutes=20,
    )
    _job(
        db_session, days_ago=3, job_type=OperationalJobType.BACKUP,
        status=OperationalJobStatus.FAILED, duration_minutes=3,
    )
    _job(
        db_session, days_ago=4, job_type=OperationalJobType.BACKUP,
        status=OperationalJobStatus.SKIPPED, duration_minutes=1,
    )
    db_session.commit()

    jobs = OperationalTrendService(db_session).summarize(7, now=NOW)["jobs"]

    assert jobs["status_counts"] == {
        "successful": 1, "partial": 1, "failed": 1, "skipped": 1,
    }
    assert jobs["by_job_type"]["pipeline"] == {
        "count": 2, "average_duration_seconds": 900.0,
    }
    assert jobs["by_job_type"]["backup"] == {
        "count": 2, "average_duration_seconds": 120.0,
    }


def test_feedback_useful_rates_event_types_and_reasons(db_session):
    _feedback(
        db_session, days_ago=1, event_type=NotificationEventType.HIGH_ATTENTION,
        rating=NotificationFeedbackRating.USEFUL,
    )
    _feedback(
        db_session, days_ago=2, event_type=NotificationEventType.HIGH_ATTENTION,
        rating=NotificationFeedbackRating.NOT_USEFUL, reason="too_old",
    )
    _feedback(
        db_session, days_ago=3, event_type=NotificationEventType.PROVIDER_FAILURE,
        rating=NotificationFeedbackRating.USEFUL,
    )
    _feedback(
        db_session, days_ago=40, event_type=NotificationEventType.PROVIDER_FAILURE,
        rating=NotificationFeedbackRating.NOT_USEFUL, reason="provider_noise",
    )
    db_session.commit()

    feedback = OperationalTrendService(db_session).summarize(30, now=NOW)["feedback"]

    assert feedback["total"] == 3
    assert feedback["useful"] == 2
    assert feedback["not_useful"] == 1
    assert feedback["useful_rate"] == 0.6667
    assert feedback["by_event_type"]["high_attention"]["useful_rate"] == 0.5
    assert feedback["by_event_type"]["provider_failure"]["useful_rate"] == 1.0
    assert feedback["top_not_useful_reasons"] == [{"reason": "too_old", "count": 1}]


def test_empty_summary_and_invalid_window(db_session):
    service = OperationalTrendService(db_session)
    summary = service.summarize(7, now=NOW)
    assert summary["jobs"]["total"] == 0
    assert summary["feedback"]["total"] == 0
    assert summary["feedback"]["useful_rate"] is None
    assert "No job or feedback activity" in summary["headline"]
    with pytest.raises(ValueError):
        service.summarize(14, now=NOW)
