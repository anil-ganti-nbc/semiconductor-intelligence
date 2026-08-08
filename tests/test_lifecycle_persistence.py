"""Stabilization Pass 1 -- persistence across a clean restart.

Builds one representative dataset spanning every record type Scenario 2
calls for, closes every engine/session (simulating process shutdown), then
reopens a brand-new engine/session against the same database file
(simulating restart) and confirms everything survives unchanged, with no
duplication from the restart itself.
"""
from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import select

from semi_intel.db import get_engine, get_sessionmaker, init_db
from semi_intel.domain.enums import (
    NotificationEventType, NotificationFeedbackRating, NotificationSeverity,
    OperationalJobStatus, OperationalJobType, OperationalTriggerType, SourceType,
)
from semi_intel.domain.models import (
    CandidateSignalItem, MonitoredTopic, Notification, NotificationFeedback,
    OperationalJobRun, SavedNotificationView, SignalCandidate, SignalItem, Source,
)
from semi_intel.editorial.service import TopicService
from semi_intel.notifications.service import NotificationService
from semi_intel.operations.quality import NotificationQualityService, SavedViewService
from semi_intel.operations.scheduler import get_scheduler_settings
from semi_intel.signals.analysis import analyze_signal_item
from semi_intel.signals.candidate_state import mark_seen
from semi_intel.signals.clustering import cluster_unclustered_items

BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


def _build_representative_dataset(db_path):
    engine = get_engine(f"sqlite:///{db_path}")
    init_db(engine)
    session = get_sessionmaker(engine)()

    TopicService(session).seed()  # at least one topic/keyword

    editorial_source = Source(
        name="AnandTech", type=SourceType.RSS, provider="manual",
        url="https://anandtech.com/feed", trust_weight=0.9,
    )
    radar_source = Source(
        name="Radar Feed", type=SourceType.SOCIAL, provider="replay", provider_key="radar-feed-1",
        polling_enabled=False,
    )
    session.add_all([editorial_source, radar_source])
    session.commit()

    # A deterministic fixture item, analyzed and clustered into a candidate.
    item = SignalItem(
        source_id=radar_source.id, provider="replay", external_id="fixture-1", raw_payload="{}",
        normalized_text="RTX 50 Super leak confirmed by two independent sources.", content_hash="fixture-hash-1",
        posted_at=BASE,
    )
    session.add(item)
    session.commit()
    analyze_signal_item(session, item)
    session.commit()
    cluster_unclustered_items(session)
    session.commit()
    candidate = session.scalars(
        select(SignalCandidate)
        .join(CandidateSignalItem, CandidateSignalItem.candidate_id == SignalCandidate.id)
        .where(CandidateSignalItem.signal_item_id == item.id)
    ).one()
    mark_seen(candidate)  # seen/unseen state change
    session.commit()

    notification = NotificationService(session).create_test_notification()
    session.commit()
    NotificationQualityService(session).feedback(
        notification.id, NotificationFeedbackRating.USEFUL, reason="good_timing", note="restart test",
    )

    topic = session.scalars(select(MonitoredTopic)).first()
    SavedViewService(session).save(
        name="Persistence check view", state_filter="all",
        severities=["informational"], topic_ids=[topic.id] if topic else [],
        date_window_days=30, search_text="leak", sort_order="severity",
    )

    session.add(OperationalJobRun(
        job_type=OperationalJobType.HEALTH_CHECK, trigger_type=OperationalTriggerType.MANUAL_CLI,
        started_at=BASE, finished_at=BASE, status=OperationalJobStatus.SUCCESSFUL,
        owner_identity="lifecycle-test", summary="Deterministic fixture job run.",
    ))

    scheduler_settings = get_scheduler_settings(session)
    scheduler_settings.pipeline_interval_minutes = 45  # non-default, still disabled overall
    scheduler_settings.scheduler_enabled = False        # safe: stays disabled
    session.commit()

    ids = {
        "candidate_id": candidate.id, "notification_id": notification.id,
        "editorial_source_id": editorial_source.id, "radar_source_id": radar_source.id,
        "topic_id": topic.id if topic else None,
    }
    session.close()
    engine.dispose()
    return ids


def _reopen_and_snapshot(db_path):
    """A brand-new engine/session against the same file -- simulating a
    fresh process start, not just a fresh Session on a still-open engine."""
    engine = get_engine(f"sqlite:///{db_path}")
    session = get_sessionmaker(engine)()
    snapshot = {
        "topics": session.scalar(select(__import__("sqlalchemy").func.count()).select_from(MonitoredTopic)),
        "sources": session.scalar(select(__import__("sqlalchemy").func.count()).select_from(Source)),
        "signal_items": session.scalar(select(__import__("sqlalchemy").func.count()).select_from(SignalItem)),
        "candidates": list(session.scalars(select(SignalCandidate))),
        "notifications": list(session.scalars(select(Notification))),
        "feedback": list(session.scalars(select(NotificationFeedback))),
        "saved_views": list(session.scalars(select(SavedNotificationView))),
        "job_runs": list(session.scalars(select(OperationalJobRun))),
        "scheduler_settings": get_scheduler_settings(session),
    }
    return engine, session, snapshot


def test_representative_dataset_survives_a_clean_restart(tmp_path):
    db_path = tmp_path / "persistence.db"
    ids = _build_representative_dataset(db_path)

    engine, session, snapshot = _reopen_and_snapshot(db_path)
    try:
        assert snapshot["topics"] > 0
        assert snapshot["sources"] == 2

        assert len(snapshot["candidates"]) == 1
        candidate = snapshot["candidates"][0]
        assert candidate.id == ids["candidate_id"]
        assert candidate.seen_at is not None  # seen state persisted

        assert len(snapshot["notifications"]) == 1
        assert snapshot["notifications"][0].id == ids["notification_id"]

        assert len(snapshot["feedback"]) == 1
        assert snapshot["feedback"][0].rating == NotificationFeedbackRating.USEFUL
        assert snapshot["feedback"][0].reason == "good_timing"

        assert len(snapshot["saved_views"]) == 1
        view = snapshot["saved_views"][0]
        assert view.name == "Persistence check view"
        assert view.state_filter == "all"
        assert json.loads(view.severities) == ["informational"]
        assert view.date_window_days == 30
        assert view.search_text == "leak"
        assert view.sort_order == "severity"
        if ids["topic_id"] is not None:
            assert json.loads(view.topic_ids) == [ids["topic_id"]]

        assert len(snapshot["job_runs"]) == 1
        assert snapshot["job_runs"][0].status == OperationalJobStatus.SUCCESSFUL

        settings = snapshot["scheduler_settings"]
        assert settings.pipeline_interval_minutes == 45  # non-default value survived
        assert settings.scheduler_enabled is False        # stayed disabled -- no default reset
    finally:
        session.close()
        engine.dispose()


def test_restart_does_not_duplicate_seeded_or_existing_records(tmp_path):
    db_path = tmp_path / "no_dup.db"
    _build_representative_dataset(db_path)

    # Two more "restarts": re-seed topics, re-open the db, repeatedly.
    for _ in range(2):
        engine = get_engine(f"sqlite:///{db_path}")
        session = get_sessionmaker(engine)()
        TopicService(session).seed()
        session.commit()
        session.close()
        engine.dispose()

    engine, session, snapshot = _reopen_and_snapshot(db_path)
    try:
        normalized_names = [
            row[0] for row in session.execute(select(MonitoredTopic.normalized_name))
        ]
        assert len(normalized_names) == len(set(normalized_names)), "topics were duplicated across restarts"
        assert len(snapshot["notifications"]) == 1
        assert len(snapshot["saved_views"]) == 1
        assert len(snapshot["candidates"]) == 1
    finally:
        session.close()
        engine.dispose()


def test_dashboard_presents_same_logical_state_after_restart(tmp_path, monkeypatch):
    """The API-visible state (not just raw rows) must match before and
    after a restart -- exercised through create_app()/TestClient rather
    than direct ORM queries, matching what an operator would actually see."""
    db_path = tmp_path / "dashboard_state.db"
    _build_representative_dataset(db_path)

    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.chdir(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    from semi_intel.web.app import create_app
    from fastapi.testclient import TestClient

    def snapshot():
        client = TestClient(create_app())
        return {
            "notifications": client.get("/api/notifications", params={"state": "all"}).json(),
            "saved_views": client.get("/api/notifications/saved-views").json(),
            "topics": client.get("/api/topics").json(),
            "scheduler": client.get("/api/operations/scheduler").json(),
        }

    before = snapshot()
    after = snapshot()  # a second create_app() call simulates a restart

    assert len(before["notifications"]) == len(after["notifications"]) == 1
    assert [n["id"] for n in before["notifications"]] == [n["id"] for n in after["notifications"]]
    assert len(before["saved_views"]) == len(after["saved_views"]) == 1
    assert before["saved_views"][0]["name"] == after["saved_views"][0]["name"] == "Persistence check view"
    assert len(before["topics"]) == len(after["topics"])
    assert before["scheduler"]["settings"]["pipeline_interval_minutes"] == 45
    assert after["scheduler"]["settings"]["pipeline_interval_minutes"] == 45
    assert before["scheduler"]["enabled"] is False
    assert after["scheduler"]["enabled"] is False
