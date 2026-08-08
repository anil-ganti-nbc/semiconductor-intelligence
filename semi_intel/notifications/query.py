"""Bounded, deterministic notification-list query builder.

Both the notification-list API and saved-view application call this exact
module so filter semantics never diverge between routes and the GUI. It
never mutates a notification's read/dismissed/feedback/mute state -- it only
reads.

Filter semantics: multiple values within one category combine with OR
(e.g. important OR urgent severity); different categories combine with AND
(e.g. unread AND that severity match AND the date window AND the search
text). See VALID_STATES/VALID_SORTS/DATE_WINDOW_DAYS for the controlled
vocabularies.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import NotificationEventType, NotificationSeverity
from semi_intel.domain.models import Notification
from semi_intel.notifications.service import utcnow

VALID_STATES = {"unread", "read", "dismissed", "all"}
VALID_SORTS = {"newest", "oldest", "severity"}
DATE_WINDOW_DAYS = {1, 3, 7, 14, 30, 90}

# Explicit deterministic severity order -- do not rely on enum/alphabetical
# ordering (urgent must outrank important even though 'i' < 'u').
SEVERITY_SORT_RANK = {
    NotificationSeverity.URGENT: 0,
    NotificationSeverity.IMPORTANT: 1,
    NotificationSeverity.NOTABLE: 2,
    NotificationSeverity.INFORMATIONAL: 3,
}


@dataclass
class NotificationQueryFilters:
    state: str = "unread"
    event_types: list[str] = field(default_factory=list)
    severities: list[str] = field(default_factory=list)
    topic_ids: list[int] = field(default_factory=list)
    date_window_days: int | None = None
    search_text: str = ""
    sort_order: str = "newest"
    # Existing relation-style narrowing, preserved for backward compatibility
    # with pre-Phase-10B API callers; not part of saved-view composition.
    candidate_id: int | None = None
    story_id: int | None = None
    source_id: int | None = None
    source_suggestion_id: int | None = None
    date_from: dt.datetime | None = None
    date_to: dt.datetime | None = None
    limit: int = 100


class NotificationQueryService:
    """Read-only. Never mutates notification state."""

    def __init__(self, session: Session):
        self.session = session

    def run(
        self, filters: NotificationQueryFilters, *, now: dt.datetime | None = None
    ) -> list[Notification]:
        if filters.state not in VALID_STATES:
            raise ValueError("state must be one of: unread, read, dismissed, all")
        if filters.sort_order not in VALID_SORTS:
            raise ValueError("sort_order must be one of: newest, oldest, severity")
        if filters.date_window_days is not None and filters.date_window_days not in DATE_WINDOW_DAYS:
            raise ValueError(
                "date_window_days must be one of: " + ", ".join(str(d) for d in sorted(DATE_WINDOW_DAYS))
            )
        try:
            event_types = [NotificationEventType(v) for v in filters.event_types]
        except ValueError as exc:
            raise ValueError(f"Unknown event type: {exc}") from exc
        try:
            severities = [NotificationSeverity(v) for v in filters.severities]
        except ValueError as exc:
            raise ValueError(f"Unknown severity: {exc}") from exc

        stmt = select(Notification)
        if filters.state == "unread":
            stmt = stmt.where(Notification.read_at.is_(None), Notification.dismissed_at.is_(None))
        elif filters.state == "read":
            stmt = stmt.where(Notification.read_at.is_not(None), Notification.dismissed_at.is_(None))
        elif filters.state == "dismissed":
            stmt = stmt.where(Notification.dismissed_at.is_not(None))
        if event_types:
            stmt = stmt.where(Notification.event_type.in_(event_types))
        if severities:
            stmt = stmt.where(Notification.severity.in_(severities))
        if filters.topic_ids:
            stmt = stmt.where(Notification.topic_id.in_(filters.topic_ids))
        for column, value in (
            (Notification.candidate_id, filters.candidate_id),
            (Notification.story_id, filters.story_id),
            (Notification.source_id, filters.source_id),
            (Notification.source_suggestion_id, filters.source_suggestion_id),
        ):
            if value is not None:
                stmt = stmt.where(column == value)
        if filters.date_window_days is not None:
            cutoff = (now or utcnow()) - dt.timedelta(days=filters.date_window_days)
            stmt = stmt.where(Notification.event_at >= cutoff)
        if filters.date_from is not None:
            stmt = stmt.where(Notification.event_at >= filters.date_from)
        if filters.date_to is not None:
            stmt = stmt.where(Notification.event_at <= filters.date_to)
        search = (filters.search_text or "").strip()
        if search:
            needle = f"%{search}%"
            stmt = stmt.where(
                Notification.title.ilike(needle) | Notification.body.ilike(needle)
                | Notification.reason.ilike(needle)
            )

        if filters.sort_order == "oldest":
            stmt = stmt.order_by(Notification.event_at.asc(), Notification.id.asc())
        elif filters.sort_order == "severity":
            rank = case(
                *(
                    (Notification.severity == severity, rank)
                    for severity, rank in SEVERITY_SORT_RANK.items()
                ),
                else_=len(SEVERITY_SORT_RANK),
            )
            stmt = stmt.order_by(rank.asc(), Notification.event_at.desc(), Notification.id.desc())
        else:
            stmt = stmt.order_by(Notification.event_at.desc(), Notification.id.desc())

        limit = max(1, min(filters.limit, 500))
        return list(self.session.scalars(stmt.limit(limit)))
