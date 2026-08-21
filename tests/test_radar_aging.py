"""Focused Phase 3.3.10 Signal Radar aging tests."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from semi_intel.domain.enums import SignalCandidateState, SourceType
from semi_intel.domain.models import (
    CandidateSignalItem,
    CandidateTopicMatch,
    MonitoredTopic,
    SignalCandidate,
    SignalIndependenceGroup,
    SignalIndependenceGroupMember,
    SignalItem,
    Source,
)
from semi_intel.signals.aging import CandidateAgingService


NOW = dt.datetime(2026, 8, 2, 12, 0, 0)


def _candidate_with_groups(
    session,
    *,
    title: str,
    group_times: list[list[tuple[dt.datetime | None, dt.datetime]]],
    score: float = 0.5,
    seen: bool = False,
) -> SignalCandidate:
    source = Source(name=f"Source {title}", type=SourceType.SOCIAL, provider="replay")
    session.add(source)
    session.flush()
    all_times = [posted or collected for group in group_times for posted, collected in group]
    candidate = SignalCandidate(
        fingerprint=f"aging-{title}", title=title, state=SignalCandidateState.ACTIVE,
        attention_score=score, first_observed_at=min(all_times), latest_observed_at=max(all_times),
        item_count=len(all_times), seen_at=NOW if seen else None,
    )
    session.add(candidate)
    session.flush()
    item_number = 0
    for group_number, members in enumerate(group_times):
        group = SignalIndependenceGroup(candidate_id=candidate.id, reason="independent")
        session.add(group)
        session.flush()
        for posted_at, collected_at in members:
            item_number += 1
            item = SignalItem(
                source_id=source.id, provider="replay",
                external_id=f"{title}-{group_number}-{item_number}", raw_payload="{}",
                normalized_text=title, content_hash=f"hash-{title}-{group_number}-{item_number}",
                posted_at=posted_at, collected_at=collected_at,
            )
            session.add(item)
            session.flush()
            session.add(CandidateSignalItem(
                candidate_id=candidate.id, signal_item_id=item.id, attach_reasons="[]"
            ))
            session.add(SignalIndependenceGroupMember(group_id=group.id, signal_item_id=item.id))
    session.commit()
    return candidate


def test_six_and_eight_day_classification_and_exact_boundary(db_session):
    six = _candidate_with_groups(
        db_session, title="six", group_times=[[(NOW - dt.timedelta(days=6), NOW)]],
    )
    eight = _candidate_with_groups(
        db_session, title="eight", group_times=[[(NOW - dt.timedelta(days=8), NOW)]],
    )
    boundary = _candidate_with_groups(
        db_session, title="boundary", group_times=[[(NOW - dt.timedelta(days=7), NOW)]],
    )
    ages = CandidateAgingService(db_session, now=NOW).classify_many(
        [six, eight, boundary], age_days=7
    )
    assert ages[six.id].classification == "current"
    assert ages[eight.id].classification == "older"
    # The boundary is inclusive: a candidate becomes older only after seven full days.
    assert ages[boundary.id].classification == "current"


@pytest.mark.parametrize(
    ("window", "age", "expected"),
    [(3, 4, "older"), (7, 4, "current"), (14, 15, "older"), (30, 15, "current")],
)
def test_supported_windows(db_session, window, age, expected):
    candidate = _candidate_with_groups(
        db_session, title=f"window-{window}",
        group_times=[[(NOW - dt.timedelta(days=age), NOW)]],
    )
    result = CandidateAgingService(db_session, now=NOW).classify(candidate, age_days=window)
    assert result.classification == expected


def test_publication_time_precedes_collection_time(db_session):
    candidate = _candidate_with_groups(
        db_session, title="late-collection",
        group_times=[[(NOW - dt.timedelta(days=20), NOW)]],
    )
    result = CandidateAgingService(db_session, now=NOW).classify(candidate)
    assert result.classification == "older"
    assert result.activity_at == NOW - dt.timedelta(days=20)
    assert result.used_collection_fallback is False


def test_collection_fallback_is_disclosed(db_session):
    candidate = _candidate_with_groups(
        db_session, title="missing-publication", group_times=[[(None, NOW - dt.timedelta(days=8))]],
    )
    result = CandidateAgingService(db_session, now=NOW).classify(candidate)
    assert result.classification == "older"
    assert result.used_collection_fallback is True
    assert result.timestamp_source == "collection_fallback"
    assert "collection time used" in result.reason


def test_later_duplicate_in_same_group_does_not_reactivate(db_session):
    old = NOW - dt.timedelta(days=14)
    candidate = _candidate_with_groups(
        db_session, title="dependent-copy", group_times=[[(old, old), (NOW, NOW)]],
    )
    result = CandidateAgingService(db_session, now=NOW).classify(candidate)
    assert result.classification == "older"
    assert result.activity_at == old


def test_new_independent_group_reactivates_after_a_real_gap(db_session):
    candidate = _candidate_with_groups(
        db_session,
        title="corroborated",
        group_times=[
            [(NOW - dt.timedelta(days=20), NOW - dt.timedelta(days=20))],
            [(NOW - dt.timedelta(days=1), NOW - dt.timedelta(days=1))],
        ],
    )
    result = CandidateAgingService(db_session, now=NOW).classify(candidate)
    assert result.classification == "current"
    assert result.reactivated is True
    assert "newly represented independent source group" in result.reason


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    database = tmp_path / "aging-api.db"
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{database}")
    from semi_intel.web.app import create_app

    with TestClient(create_app(mutation_authorizer=lambda _value: True)) as client:
        yield client


def _api_session():
    import os

    from semi_intel.db import get_engine, get_sessionmaker

    return get_sessionmaker(get_engine(os.environ["SEMI_INTEL_DB_URL"]))()


def test_api_age_filter_composes_before_limit_with_seen_topic_score_and_sort(api_client):
    real_now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    with _api_session() as session:
        current = _candidate_with_groups(
            session, title="current-api",
            group_times=[[(real_now - dt.timedelta(days=1), real_now)]], score=0.75,
        )
        old = _candidate_with_groups(
            session, title="old-api",
            group_times=[[(real_now - dt.timedelta(days=20), real_now)]], score=0.99,
        )
        topic = MonitoredTopic(
            name="Age Topic", normalized_name="age topic", keyword="Age Topic", aliases="[]"
        )
        session.add(topic)
        session.flush()
        session.add_all([
            CandidateTopicMatch(candidate_id=current.id, topic_id=topic.id, matched_text="Age Topic"),
            CandidateTopicMatch(candidate_id=old.id, topic_id=topic.id, matched_text="Age Topic"),
        ])
        session.commit()
        current_id, old_id, topic_id = current.id, old.id, topic.id

    rows = api_client.get("/api/radar/candidates", params={
        "state": "unseen", "age": "current", "age_days": 7,
        "topic_id": topic_id, "min_score": 0.7, "sort": "newest", "limit": 1,
    }).json()
    assert [row["id"] for row in rows] == [current_id]
    assert rows[0]["age_classification"] == "current"
    assert "meaningful_activity_at" in rows[0]

    older = api_client.get("/api/radar/candidates", params={
        "state": "active", "age": "older", "age_days": 7,
    }).json()
    assert [row["id"] for row in older] == [old_id]

    api_client.post("/api/radar/candidates/seen", json={"candidate_ids": [current_id], "seen": True})
    unseen = api_client.get("/api/radar/candidates", params={"state": "unseen", "age": "current"}).json()
    assert current_id not in [row["id"] for row in unseen]


def test_api_rejects_invalid_age_inputs_and_handles_empty_data(api_client):
    assert api_client.get("/api/radar/candidates", params={"age": "ancient"}).status_code == 422
    assert api_client.get("/api/radar/candidates", params={"age_days": 9}).status_code == 422
    assert api_client.get("/api/radar/candidates").json() == []


def test_gui_exposes_age_controls_badges_and_editorial_default():
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    for expected in (
        'id="radar-age"', 'id="radar-age-days"',
        'id="editorial-radar-age"', 'id="editorial-radar-age-days"',
        '<option value="current" selected>Current</option>',
        '<option value="older">Older</option>', '<option value="all">All ages</option>',
        "No current candidates within the last ${days} days",
        "age_classification", "age_reason", "Resurfaced", "Meaningful activity",
    ):
        assert expected in html
