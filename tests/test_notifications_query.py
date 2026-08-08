from __future__ import annotations

import datetime as dt

import pytest

from semi_intel.domain.enums import NotificationEventType, NotificationSeverity
from semi_intel.domain.models import MonitoredTopic, Notification
from semi_intel.notifications.query import NotificationQueryFilters, NotificationQueryService


NOW = dt.datetime(2026, 7, 27, 12, 0, tzinfo=dt.UTC)


def make_topic(db_session, name):
    topic = MonitoredTopic(
        name=name, normalized_name=name.lower(), keyword=name,
        aliases="[]", category="cpu", priority=0.5, enabled=True,
    )
    db_session.add(topic)
    db_session.commit()
    return topic


def make_notification(
    db_session, *, key, event_type=NotificationEventType.HIGH_ATTENTION,
    severity=NotificationSeverity.INFORMATIONAL, topic_id=None,
    event_at=NOW, read_at=None, dismissed_at=None,
    title="Title", body="Body text", reason="Reason text",
):
    row = Notification(
        event_type=event_type, severity=severity, title=title, body=body, reason=reason,
        dedup_key=key, topic_id=topic_id, event_at=event_at,
        read_at=read_at, dismissed_at=dismissed_at,
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_state_filtering(db_session):
    unread = make_notification(db_session, key="u1")
    read = make_notification(db_session, key="r1", read_at=NOW)
    dismissed = make_notification(db_session, key="d1", dismissed_at=NOW)

    svc = NotificationQueryService(db_session)
    assert [n.id for n in svc.run(NotificationQueryFilters(state="unread"))] == [unread.id]
    assert [n.id for n in svc.run(NotificationQueryFilters(state="read"))] == [read.id]
    assert [n.id for n in svc.run(NotificationQueryFilters(state="dismissed"))] == [dismissed.id]
    assert {n.id for n in svc.run(NotificationQueryFilters(state="all"))} == {
        unread.id, read.id, dismissed.id
    }


def test_multiple_event_types_use_or(db_session):
    a = make_notification(db_session, key="a", event_type=NotificationEventType.HIGH_ATTENTION)
    b = make_notification(db_session, key="b", event_type=NotificationEventType.INDEPENDENT_CORROBORATION)
    c = make_notification(db_session, key="c", event_type=NotificationEventType.TOPIC_ACTIVITY)

    svc = NotificationQueryService(db_session)
    rows = svc.run(NotificationQueryFilters(
        state="all",
        event_types=["high_attention", "independent_corroboration"],
    ))
    assert {n.id for n in rows} == {a.id, b.id}


def test_multiple_severities_use_or(db_session):
    a = make_notification(db_session, key="a", severity=NotificationSeverity.IMPORTANT)
    b = make_notification(db_session, key="b", severity=NotificationSeverity.URGENT)
    c = make_notification(db_session, key="c", severity=NotificationSeverity.NOTABLE)

    svc = NotificationQueryService(db_session)
    rows = svc.run(NotificationQueryFilters(state="all", severities=["important", "urgent"]))
    assert {n.id for n in rows} == {a.id, b.id}


def test_multiple_topics_use_or(db_session):
    amd = make_topic(db_session, "AMD")
    nvidia = make_topic(db_session, "NVIDIA")
    intel = make_topic(db_session, "Intel")
    a = make_notification(db_session, key="a", topic_id=amd.id)
    b = make_notification(db_session, key="b", topic_id=nvidia.id)
    c = make_notification(db_session, key="c", topic_id=intel.id)

    svc = NotificationQueryService(db_session)
    rows = svc.run(NotificationQueryFilters(state="all", topic_ids=[amd.id, nvidia.id]))
    assert {n.id for n in rows} == {a.id, b.id}


def test_categories_combine_with_and(db_session):
    amd = make_topic(db_session, "AMD")
    match = make_notification(
        db_session, key="match", severity=NotificationSeverity.URGENT,
        event_type=NotificationEventType.HIGH_ATTENTION, topic_id=amd.id,
    )
    wrong_topic = make_notification(
        db_session, key="wrong_topic", severity=NotificationSeverity.URGENT,
        event_type=NotificationEventType.HIGH_ATTENTION, topic_id=None,
    )
    wrong_severity = make_notification(
        db_session, key="wrong_severity", severity=NotificationSeverity.NOTABLE,
        event_type=NotificationEventType.HIGH_ATTENTION, topic_id=amd.id,
    )

    svc = NotificationQueryService(db_session)
    rows = svc.run(NotificationQueryFilters(
        state="all", severities=["urgent", "important"],
        event_types=["high_attention"], topic_ids=[amd.id],
    ))
    assert [n.id for n in rows] == [match.id]


def test_date_window_cutoff(db_session):
    inside = make_notification(db_session, key="inside", event_at=NOW - dt.timedelta(days=2))
    outside = make_notification(db_session, key="outside", event_at=NOW - dt.timedelta(days=10))

    svc = NotificationQueryService(db_session)
    rows = svc.run(NotificationQueryFilters(state="all", date_window_days=7), now=NOW)
    assert [n.id for n in rows] == [inside.id]


def test_date_window_cutoff_advances_with_request_time(db_session):
    """The view stores only the rule ('7 days'); the cutoff is computed fresh
    each time from the request/application time, so it naturally advances."""
    row = make_notification(db_session, key="a", event_at=NOW - dt.timedelta(days=5))
    svc = NotificationQueryService(db_session)
    filters = NotificationQueryFilters(state="all", date_window_days=7)
    assert [n.id for n in svc.run(filters, now=NOW)] == [row.id]
    much_later = NOW + dt.timedelta(days=3)
    assert svc.run(filters, now=much_later) == []


def test_search_matches_title_body_reason_case_insensitive(db_session):
    a = make_notification(db_session, key="a", title="Nova Lake leak")
    b = make_notification(db_session, key="b", body="mentions nova lake here")
    c = make_notification(db_session, key="c", reason="NOVA LAKE corroboration")
    d = make_notification(db_session, key="d", title="unrelated")

    svc = NotificationQueryService(db_session)
    rows = svc.run(NotificationQueryFilters(state="all", search_text="nova lake"))
    assert {n.id for n in rows} == {a.id, b.id, c.id}


def test_newest_sort(db_session):
    older = make_notification(db_session, key="older", event_at=NOW - dt.timedelta(hours=2))
    newer = make_notification(db_session, key="newer", event_at=NOW)
    svc = NotificationQueryService(db_session)
    rows = svc.run(NotificationQueryFilters(state="all", sort_order="newest"))
    assert [n.id for n in rows] == [newer.id, older.id]


def test_oldest_sort(db_session):
    older = make_notification(db_session, key="older", event_at=NOW - dt.timedelta(hours=2))
    newer = make_notification(db_session, key="newer", event_at=NOW)
    svc = NotificationQueryService(db_session)
    rows = svc.run(NotificationQueryFilters(state="all", sort_order="oldest"))
    assert [n.id for n in rows] == [older.id, newer.id]


def test_explicit_severity_sort_order_and_tiebreak(db_session):
    informational = make_notification(
        db_session, key="info", severity=NotificationSeverity.INFORMATIONAL, event_at=NOW
    )
    notable = make_notification(
        db_session, key="notable", severity=NotificationSeverity.NOTABLE, event_at=NOW
    )
    important = make_notification(
        db_session, key="important", severity=NotificationSeverity.IMPORTANT, event_at=NOW
    )
    urgent_older = make_notification(
        db_session, key="urgent_older", severity=NotificationSeverity.URGENT,
        event_at=NOW - dt.timedelta(hours=1),
    )
    urgent_newer = make_notification(
        db_session, key="urgent_newer", severity=NotificationSeverity.URGENT, event_at=NOW
    )

    svc = NotificationQueryService(db_session)
    rows = svc.run(NotificationQueryFilters(state="all", sort_order="severity"))
    assert [n.id for n in rows] == [
        urgent_newer.id, urgent_older.id, important.id, notable.id, informational.id,
    ]
    # Repeated requests must return the exact same deterministic order.
    rows_again = svc.run(NotificationQueryFilters(state="all", sort_order="severity"))
    assert [n.id for n in rows_again] == [n.id for n in rows]


def test_stable_tiebreak_uses_id_when_timestamps_match(db_session):
    a = make_notification(db_session, key="a", severity=NotificationSeverity.URGENT, event_at=NOW)
    b = make_notification(db_session, key="b", severity=NotificationSeverity.URGENT, event_at=NOW)
    svc = NotificationQueryService(db_session)
    rows = svc.run(NotificationQueryFilters(state="all", sort_order="severity"))
    assert [n.id for n in rows] == sorted([a.id, b.id], reverse=True)


def test_empty_results(db_session):
    make_notification(db_session, key="a", severity=NotificationSeverity.INFORMATIONAL)
    svc = NotificationQueryService(db_session)
    rows = svc.run(NotificationQueryFilters(state="all", severities=["urgent"]))
    assert rows == []


def test_invalid_controlled_values_raise(db_session):
    svc = NotificationQueryService(db_session)
    with pytest.raises(ValueError):
        svc.run(NotificationQueryFilters(state="archived"))
    with pytest.raises(ValueError):
        svc.run(NotificationQueryFilters(sort_order="alphabetical"))
    with pytest.raises(ValueError):
        svc.run(NotificationQueryFilters(date_window_days=5))
    with pytest.raises(ValueError):
        svc.run(NotificationQueryFilters(severities=["catastrophic"]))
    with pytest.raises(ValueError):
        svc.run(NotificationQueryFilters(event_types=["not_a_real_type"]))


def test_query_never_mutates_notification_state(db_session):
    row = make_notification(db_session, key="a")
    before = (row.read_at, row.dismissed_at)
    svc = NotificationQueryService(db_session)
    svc.run(NotificationQueryFilters(state="all"))
    db_session.refresh(row)
    assert (row.read_at, row.dismissed_at) == before
