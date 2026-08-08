from __future__ import annotations

import json

import pytest

from semi_intel.domain.enums import NotificationFeedbackRating
from semi_intel.domain.models import MonitoredTopic
from semi_intel.operations.quality import (
    NotificationQualityService, SavedViewNotFoundError, SavedViewService,
)
from semi_intel.notifications.service import NotificationService


def make_topic(db_session, name="AMD", **overrides):
    topic = MonitoredTopic(
        name=name, normalized_name=name.lower(), keyword=name,
        aliases="[]", category="cpu", priority=0.5, enabled=True,
        **overrides,
    )
    db_session.add(topic)
    db_session.commit()
    return topic


def test_preset_preview_apply_is_idempotent_and_preserves_safety(db_session):
    notification_settings = NotificationService(db_session).settings()
    notification_settings.external_delivery_enabled = False
    notification_settings.muted_event_types = json.dumps(["provider_failure"])
    service = NotificationQualityService(db_session)

    preview = service.preview_preset("quiet")
    assert "minimum_attention_score" in preview["changes"]
    assert notification_settings.minimum_attention_score == 0.70
    service.apply_preset("quiet")
    second = service.apply_preset("quiet")

    assert second["changes"] == {}
    assert notification_settings.minimum_attention_score == 0.82
    assert notification_settings.external_delivery_enabled is False
    assert json.loads(notification_settings.muted_event_types) == ["provider_failure"]


def test_feedback_updates_one_record_and_summary_is_advisory(db_session):
    notification = NotificationService(db_session).create_test_notification()
    quality = NotificationQualityService(db_session)
    first = quality.feedback(
        notification.id, NotificationFeedbackRating.NOT_USEFUL, reason="too_old"
    )
    second = quality.feedback(
        notification.id, NotificationFeedbackRating.USEFUL, reason="good_timing"
    )
    assert first.id == second.id
    summary = quality.feedback_summary()
    assert summary["total"] == 1
    assert summary["useful"] == 1
    assert summary["not_useful"] == 0

    with pytest.raises(ValueError):
        quality.feedback(
            notification.id, NotificationFeedbackRating.USEFUL, reason="too_old"
        )


def test_saved_views_validate_and_protect_duplicate_names(db_session):
    amd = make_topic(db_session, "AMD")
    nvidia = make_topic(db_session, "NVIDIA")
    views = SavedViewService(db_session)
    row = views.save(
        name="Unread important", state_filter="unread",
        severities=["important", "urgent"], event_types=["high_attention"],
        topic_ids=[nvidia.id, amd.id, amd.id], date_window_days=7,
    )
    assert json.loads(row.topic_ids) == sorted([amd.id, nvidia.id])
    assert len(views.list()) == 1
    with pytest.raises(ValueError):
        views.save(name="Unread important")
    views.delete(row.id)
    assert views.list() == []


def test_saved_view_create_with_complete_filter_composition(db_session):
    amd = make_topic(db_session, "AMD")
    views = SavedViewService(db_session)
    row = views.save(
        name="Full view", state_filter="unread",
        event_types=["high_attention", "independent_corroboration"],
        severities=["important", "urgent"], topic_ids=[amd.id],
        date_window_days=7, search_text="leak", sort_order="severity",
    )
    assert json.loads(row.event_types) == ["high_attention", "independent_corroboration"]
    assert json.loads(row.severities) == ["important", "urgent"]
    assert json.loads(row.topic_ids) == [amd.id]
    assert row.date_window_days == 7
    assert row.sort_order == "severity"


def test_saved_view_normalizes_duplicate_selections_deterministically(db_session):
    views = SavedViewService(db_session)
    row = views.save(
        name="Dupe selections",
        severities=["urgent", "important", "urgent", "important"],
        event_types=["high_attention", "high_attention"],
    )
    assert json.loads(row.severities) == ["important", "urgent"]
    assert json.loads(row.event_types) == ["high_attention"]


def test_saved_view_rejects_invalid_controlled_values(db_session):
    views = SavedViewService(db_session)
    with pytest.raises(ValueError):
        views.save(name="Bad state", state_filter="archived")
    with pytest.raises(ValueError):
        views.save(name="Bad sort", sort_order="alphabetical")
    with pytest.raises(ValueError):
        views.save(name="Bad window", date_window_days=5)
    with pytest.raises(ValueError):
        views.save(name="Bad severity", severities=["catastrophic"])
    with pytest.raises(ValueError):
        views.save(name="Bad event type", event_types=["not_a_real_type"])
    with pytest.raises(ValueError):
        views.save(name="Bad topic", topic_ids=[999])


def test_saved_view_complete_update_and_relation_filter_preservation(db_session):
    views = SavedViewService(db_session)
    row = views.save(
        name="Original", severities=["urgent"], relation_filters={"story_id": "4"},
    )
    assert json.loads(row.relation_filters) == {"story_id": "4"}

    updated = views.save(
        view_id=row.id, name="Original", severities=["important"],
        search_text="new search",
    )
    assert json.loads(updated.severities) == ["important"]
    assert updated.search_text == "new search"
    assert json.loads(updated.relation_filters) == {"story_id": "4"}, (
        "editing unrelated fields must not silently discard stored relation data"
    )

    cleared = views.save(view_id=row.id, name="Original", relation_filters={})
    assert json.loads(cleared.relation_filters) == {}


def test_saved_view_duplicate_proposes_safe_name(db_session):
    views = SavedViewService(db_session)
    original = views.save(name="Unread important", severities=["urgent"])
    copy1 = views.duplicate(original.id)
    assert copy1.name == "Unread important copy"
    assert copy1.severities == original.severities
    assert copy1.id != original.id

    copy2 = views.duplicate(original.id)
    assert copy2.name == "Unread important copy 2"


def test_saved_view_missing_id_operations_fail_cleanly(db_session):
    views = SavedViewService(db_session)
    with pytest.raises(SavedViewNotFoundError):
        views.get(999)
    with pytest.raises(SavedViewNotFoundError):
        views.save(view_id=999, name="Ghost")
    with pytest.raises(SavedViewNotFoundError):
        views.delete(999)
    with pytest.raises(SavedViewNotFoundError):
        views.duplicate(999)


def test_saved_view_human_readable_description(db_session):
    amd = make_topic(db_session, "AMD")
    nvidia = make_topic(db_session, "NVIDIA")
    views = SavedViewService(db_session)
    row = views.save(
        name="Composed", state_filter="unread",
        severities=["important", "urgent"], event_types=["high_attention"],
        topic_ids=[amd.id, nvidia.id], date_window_days=7,
    )
    description = views.describe(row)
    assert description.startswith("Unread")
    assert "important or urgent" in description
    assert "high attention" in description
    assert "AMD or NVIDIA" in description or "NVIDIA or AMD" in description
    assert "Last 7 days" in description


def test_existing_3_3_x_saved_view_rows_remain_readable(db_session):
    """Rows shaped like those written by 3.3.0-3.3.2 (no relation_filters/
    date_window_days set beyond defaults) must still list/describe cleanly."""
    from semi_intel.domain.models import SavedNotificationView

    legacy = SavedNotificationView(
        name="Legacy view", state_filter="unread",
        event_types=json.dumps(["high_attention"]), severities=json.dumps(["important"]),
        topic_ids=json.dumps([]), relation_filters=json.dumps({}),
        date_window_days=None, search_text="", sort_order="newest",
    )
    db_session.add(legacy)
    db_session.commit()

    views = SavedViewService(db_session)
    listed = views.list()
    assert listed[0].name == "Legacy view"
    description = views.describe(listed[0])
    assert "Unread" in description
    assert "important" in description
