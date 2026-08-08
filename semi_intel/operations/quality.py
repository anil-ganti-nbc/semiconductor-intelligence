"""Deterministic notification presets, feedback and saved views."""

from __future__ import annotations

import json
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import (
    NotificationEventType, NotificationFeedbackRating, NotificationSeverity,
)
from semi_intel.domain.models import (
    MonitoredTopic, Notification, NotificationFeedback, SavedNotificationView,
)
from semi_intel.notifications.query import DATE_WINDOW_DAYS, VALID_SORTS, VALID_STATES
from semi_intel.notifications.service import get_settings
from semi_intel.operations.scheduler import get_scheduler_settings

# Sentinel distinguishing "caller did not mention relation_filters" (preserve
# whatever is already stored) from "caller explicitly passed {}" (clear it).
_UNSET = object()


class SavedViewNotFoundError(ValueError):
    """Raised for a missing/deleted saved view id -- routes map this to 404."""

STATE_LABELS = {"unread": "Unread", "read": "Read", "dismissed": "Dismissed", "all": "All statuses"}
SORT_LABELS = {"newest": "Newest first", "oldest": "Oldest first", "severity": "By severity"}
DATE_WINDOW_LABELS = {
    1: "Last 1 day", 3: "Last 3 days", 7: "Last 7 days",
    14: "Last 14 days", 30: "Last 30 days", 90: "Last 90 days",
}


PRESETS = {
    "quiet": {
        "minimum_attention_score": 0.82, "minimum_score_increase": 0.22,
        "required_independent_group_count": 3, "required_topic_match": True,
        "provider_failure_threshold": 5, "maximum_immediate_per_hour": 2,
        "daily_digest_enabled": True,
    },
    "balanced": {
        "minimum_attention_score": 0.70, "minimum_score_increase": 0.15,
        "required_independent_group_count": 2, "required_topic_match": True,
        "provider_failure_threshold": 3, "maximum_immediate_per_hour": 5,
        "daily_digest_enabled": False,
    },
    "breaking_news": {
        "minimum_attention_score": 0.58, "minimum_score_increase": 0.10,
        "required_independent_group_count": 1, "required_topic_match": True,
        "provider_failure_threshold": 2, "maximum_immediate_per_hour": 10,
        "daily_digest_enabled": False,
    },
}

USEFUL_REASONS = {
    "breaking_development", "strong_corroboration", "useful_source_discovery",
    "good_timing", "relevant_monitored_topic", "helped_create_story",
    "provider_issue_required_action", "other",
}
NOT_USEFUL_REASONS = {
    "too_old", "duplicate_coverage", "weak_source", "not_independent",
    "topic_mismatch", "score_too_low", "already_knew", "too_technical",
    "not_relevant", "provider_noise", "other",
}


class NotificationQualityService:
    def __init__(self, session: Session):
        self.session = session

    def presets(self) -> dict:
        return deepcopy(PRESETS)

    def preview_preset(self, name: str) -> dict:
        if name not in PRESETS:
            raise ValueError(f"Unknown preset {name!r}.")
        settings = get_settings(self.session)
        changes = {}
        for field, new_value in PRESETS[name].items():
            old_value = getattr(settings, field)
            if old_value != new_value:
                changes[field] = {"from": old_value, "to": new_value}
        return {
            "name": name, "changes": changes,
            "preserves": ["muted_event_types", "muted_topic_ids", "external_delivery_enabled"],
        }

    def apply_preset(self, name: str) -> dict:
        preview = self.preview_preset(name)
        settings = get_settings(self.session)
        external_before = settings.external_delivery_enabled
        muted_events = settings.muted_event_types
        muted_topics = settings.muted_topic_ids
        for field, value in PRESETS[name].items():
            setattr(settings, field, value)
        settings.external_delivery_enabled = external_before
        settings.muted_event_types = muted_events
        settings.muted_topic_ids = muted_topics
        get_scheduler_settings(self.session).active_notification_preset = name
        self.session.commit()
        return preview

    def feedback(
        self, notification_id: int, rating: NotificationFeedbackRating,
        *, reason: str | None = None, note: str | None = None,
    ) -> NotificationFeedback:
        if self.session.get(Notification, notification_id) is None:
            raise ValueError(f"No notification with id={notification_id}.")
        allowed = USEFUL_REASONS if rating == NotificationFeedbackRating.USEFUL else NOT_USEFUL_REASONS
        if reason and reason not in allowed:
            raise ValueError(f"Reason {reason!r} is not valid for {rating.value}.")
        row = self.session.scalar(select(NotificationFeedback).where(
            NotificationFeedback.notification_id == notification_id
        ))
        if row is None:
            row = NotificationFeedback(
                notification_id=notification_id, rating=rating, reason=reason, note=note
            )
            self.session.add(row)
        else:
            row.rating = rating
            row.reason = reason
            row.note = note
        self.session.commit()
        return row

    def feedback_summary(self) -> dict:
        rows = list(self.session.execute(
            select(NotificationFeedback, Notification)
            .join(Notification, Notification.id == NotificationFeedback.notification_id)
        ))
        summary = {
            "total": len(rows), "useful": 0, "not_useful": 0,
            "by_event_type": {}, "by_severity": {}, "by_topic": {},
            "by_source": {}, "not_useful_reasons": {}, "observations": [],
            "active_preset": get_scheduler_settings(self.session).active_notification_preset,
        }
        for feedback, notification in rows:
            useful = feedback.rating == NotificationFeedbackRating.USEFUL
            summary["useful" if useful else "not_useful"] += 1
            for section, key in (
                ("by_event_type", notification.event_type.value),
                ("by_severity", notification.severity.value),
                ("by_topic", str(notification.topic_id) if notification.topic_id else "none"),
                ("by_source", str(notification.source_id) if notification.source_id else "none"),
            ):
                bucket = summary[section].setdefault(key, {"useful": 0, "not_useful": 0})
                bucket["useful" if useful else "not_useful"] += 1
            if not useful and feedback.reason:
                summary["not_useful_reasons"][feedback.reason] = (
                    summary["not_useful_reasons"].get(feedback.reason, 0) + 1
                )
        for event_type, counts in summary["by_event_type"].items():
            total = counts["useful"] + counts["not_useful"]
            rate = counts["useful"] / total if total else 0
            counts["useful_rate"] = round(rate, 3)
            if total >= 3 and rate < 0.35:
                summary["observations"].append(
                    f"Only {round(rate * 100)}% of {event_type.replace('_', ' ')} alerts were marked useful."
                )
        if summary["not_useful_reasons"]:
            reason = max(summary["not_useful_reasons"], key=summary["not_useful_reasons"].get)
            summary["observations"].append(
                f"The most common not-useful reason was {reason.replace('_', ' ')}."
            )
        return summary


class SavedViewService:
    VALID_STATES = VALID_STATES
    VALID_SORTS = VALID_SORTS
    VALID_DATE_WINDOWS = DATE_WINDOW_DAYS

    def __init__(self, session: Session):
        self.session = session

    def list(self) -> list[SavedNotificationView]:
        return list(self.session.scalars(select(SavedNotificationView).order_by(
            SavedNotificationView.name
        )))

    def get(self, view_id: int) -> SavedNotificationView:
        row = self.session.get(SavedNotificationView, view_id)
        if not row:
            raise SavedViewNotFoundError(f"No saved notification view with id={view_id}.")
        return row

    def save(
        self, *, name: str, state_filter: str = "unread",
        event_types: list[str] | None = None, severities: list[str] | None = None,
        topic_ids: list[int] | None = None, relation_filters: dict | object = _UNSET,
        date_window_days: int | None = None, search_text: str = "",
        sort_order: str = "newest", view_id: int | None = None,
    ) -> SavedNotificationView:
        name = name.strip()
        if not name:
            raise ValueError("Saved view name cannot be empty.")
        if state_filter not in self.VALID_STATES:
            raise ValueError(f"Invalid notification state filter: {state_filter!r}.")
        if sort_order not in self.VALID_SORTS:
            raise ValueError(f"Invalid sort order: {sort_order!r}.")
        event_types = event_types or []
        severities = severities or []
        for value in event_types:
            try:
                NotificationEventType(value)
            except ValueError:
                raise ValueError(f"Invalid event type: {value!r}.")
        for value in severities:
            try:
                NotificationSeverity(value)
            except ValueError:
                raise ValueError(f"Invalid severity: {value!r}.")
        if date_window_days is not None and date_window_days not in self.VALID_DATE_WINDOWS:
            raise ValueError(
                "date_window_days must be one of: "
                + ", ".join(str(d) for d in sorted(self.VALID_DATE_WINDOWS))
            )
        topic_ids = sorted(set(topic_ids or []))
        if topic_ids:
            found = set(self.session.scalars(
                select(MonitoredTopic.id).where(MonitoredTopic.id.in_(topic_ids))
            ))
            missing = [t for t in topic_ids if t not in found]
            if missing:
                raise ValueError(f"Unknown monitored topic id(s): {missing}.")
        if len(search_text) > 500:
            raise ValueError("Search text must be 500 characters or fewer.")
        duplicate = self.session.scalar(select(SavedNotificationView).where(
            SavedNotificationView.name == name,
            SavedNotificationView.id != (view_id or -1),
        ))
        if duplicate:
            raise ValueError(f"A saved view named {name!r} already exists.")
        if view_id is not None:
            row = self.session.get(SavedNotificationView, view_id)
            if row is None:
                raise SavedViewNotFoundError(f"No saved notification view with id={view_id}.")
        else:
            row = SavedNotificationView(name=name)
            self.session.add(row)
        row.name = name
        row.state_filter = state_filter
        row.event_types = json.dumps(sorted(set(event_types)))
        row.severities = json.dumps(sorted(set(severities)))
        row.topic_ids = json.dumps(topic_ids)
        if relation_filters is not _UNSET:
            row.relation_filters = json.dumps(relation_filters or {}, sort_keys=True)
        elif row.relation_filters is None:
            row.relation_filters = "{}"
        row.date_window_days = date_window_days
        row.search_text = search_text
        row.sort_order = sort_order
        self.session.commit()
        return row

    def duplicate(self, view_id: int) -> SavedNotificationView:
        source = self.get(view_id)
        existing_names = {row.name for row in self.list()}
        base = f"{source.name} copy"
        new_name = base
        suffix = 2
        while new_name in existing_names:
            new_name = f"{base} {suffix}"
            suffix += 1
        row = SavedNotificationView(
            name=new_name,
            state_filter=source.state_filter,
            event_types=source.event_types,
            severities=source.severities,
            topic_ids=source.topic_ids,
            relation_filters=source.relation_filters,
            date_window_days=source.date_window_days,
            search_text=source.search_text,
            sort_order=source.sort_order,
        )
        self.session.add(row)
        self.session.commit()
        return row

    def delete(self, view_id: int) -> None:
        row = self.session.get(SavedNotificationView, view_id)
        if not row:
            raise SavedViewNotFoundError(f"No saved notification view with id={view_id}.")
        self.session.delete(row)
        self.session.commit()

    def describe(self, row: SavedNotificationView) -> str:
        """A short, non-technical summary of a saved view's filters."""
        parts = [STATE_LABELS.get(row.state_filter, row.state_filter)]
        severities = json.loads(row.severities or "[]")
        if severities:
            parts.append(" or ".join(s.replace("_", " ") for s in severities))
        event_types = json.loads(row.event_types or "[]")
        if event_types:
            parts.append(" or ".join(e.replace("_", " ") for e in event_types))
        topic_ids = json.loads(row.topic_ids or "[]")
        if topic_ids:
            names = list(self.session.scalars(
                select(MonitoredTopic.name).where(MonitoredTopic.id.in_(topic_ids))
                .order_by(MonitoredTopic.name)
            ))
            if names:
                label = " or ".join(names)
                parts.append(f"{label} topic" if len(names) == 1 else f"{label} topics")
        if row.date_window_days:
            parts.append(DATE_WINDOW_LABELS.get(row.date_window_days, f"Last {row.date_window_days} days"))
        if row.search_text:
            parts.append(f'"{row.search_text}"')
        return " · ".join(parts)
