"""Stable, deterministic daily intelligence digests."""

from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import (
    NotificationDeliveryState,
    NotificationDigestStatus,
    NotificationEventType,
    SignalCandidateState,
)
from semi_intel.domain.models import (
    MonitoredTopic,
    Notification,
    NotificationDigest,
    SignalCandidate,
    Source,
    SourceSuggestion,
)
from semi_intel.notifications.service import aware, get_settings, utcnow


def _parse_clock(value: str) -> dt.time:
    try:
        return dt.time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid time {value!r}; expected HH:MM.") from exc


def digest_window(now: dt.datetime, timezone: str, digest_time: str) -> tuple[dt.datetime, dt.datetime]:
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone {timezone!r}.") from exc
    local_now = aware(now).astimezone(zone)
    clock = _parse_clock(digest_time)
    end_date = local_now.date() if local_now.time() >= clock else local_now.date() - dt.timedelta(days=1)
    end_local = dt.datetime.combine(end_date, clock, tzinfo=zone)
    start_local = end_local - dt.timedelta(days=1)
    return start_local.astimezone(dt.UTC), end_local.astimezone(dt.UTC)


class DigestService:
    def __init__(self, session: Session):
        self.session = session

    def generate(
        self, *, now: dt.datetime | None = None, refresh: bool = False
    ) -> NotificationDigest:
        now = now or utcnow()
        settings = get_settings(self.session, now=now)
        window_start, window_end = digest_window(now, settings.timezone, settings.digest_time)
        dedup_key = f"daily:{settings.timezone}:{window_start.isoformat()}:{window_end.isoformat()}"
        existing = self.session.scalar(
            select(NotificationDigest).where(NotificationDigest.dedup_key == dedup_key)
        )
        if existing and not refresh:
            return existing

        maximum = settings.digest_maximum_items_per_section
        sections: dict[str, list[dict]] = {}

        candidates = list(self.session.scalars(
            select(SignalCandidate)
            .where(
                SignalCandidate.state == SignalCandidateState.ACTIVE,
                SignalCandidate.seen_at.is_(None),
                SignalCandidate.latest_observed_at >= window_start,
                SignalCandidate.latest_observed_at < window_end,
            )
            .order_by(
                SignalCandidate.attention_score.desc(),
                SignalCandidate.latest_observed_at.desc(),
                SignalCandidate.id.asc(),
            )
            .limit(maximum)
        ))
        if candidates:
            sections["top_unseen_candidates"] = [
                {
                    "candidate_id": candidate.id,
                    "title": candidate.title,
                    "score": round(candidate.attention_score, 3),
                    "independent_groups": candidate.independent_source_group_count,
                    "topic_id": candidate.primary_topic_id,
                    "latest_at": aware(candidate.latest_observed_at).isoformat(),
                }
                for candidate in candidates
            ]

        event_sections = {
            "fresh_corroboration": [NotificationEventType.INDEPENDENT_CORROBORATION],
            "tracked_topic_movement": [NotificationEventType.TOPIC_ACTIVITY],
            "promotion_activity": [
                NotificationEventType.PROMOTION_READY,
                NotificationEventType.CANDIDATE_PROMOTED,
            ],
            "source_intelligence": [NotificationEventType.SOURCE_SUGGESTION],
            "provider_health": [
                NotificationEventType.PROVIDER_FAILURE,
                NotificationEventType.PROVIDER_RECOVERY,
            ],
        }
        included_ids: set[int] = set()
        for section_name, event_types in event_sections.items():
            rows = list(self.session.scalars(
                select(Notification)
                .where(
                    Notification.event_type.in_(event_types),
                    Notification.event_at >= window_start,
                    Notification.event_at < window_end,
                    Notification.dismissed_at.is_(None),
                    Notification.muted.is_(False),
                )
                .order_by(Notification.severity.desc(), Notification.event_at.desc(), Notification.id.asc())
                .limit(maximum)
            ))
            if rows:
                sections[section_name] = [self._notification_item(row) for row in rows]
                included_ids.update(row.id for row in rows)

        diagnostics = self._empty_diagnostics(window_start, window_end) if not sections else []
        rendered = self._render(
            sections, settings.timezone, window_start, window_end, diagnostics=diagnostics
        )
        digest = existing or NotificationDigest(
            dedup_key=dedup_key,
            timezone=settings.timezone,
            window_start=window_start,
            window_end=window_end,
            generated_at=now,
            status=NotificationDigestStatus.READY,
            notification_ids="[]",
            structured_sections="{}",
            rendered_text="",
            delivery_state=NotificationDeliveryState.IN_APP,
        )
        digest.timezone = settings.timezone
        digest.window_start = window_start
        digest.window_end = window_end
        digest.generated_at = now
        digest.status = NotificationDigestStatus.READY
        digest.notification_ids = json.dumps(sorted(included_ids))
        digest.structured_sections = json.dumps(sections)
        digest.rendered_text = rendered
        if existing is None:
            self.session.add(digest)
        self.session.flush()
        return digest

    def generate_manual(self, *, now: dt.datetime | None = None) -> tuple[NotificationDigest, dict]:
        """Generate current alert transitions, then deliberately refresh this window.

        Scheduled callers continue to use ``generate()`` and therefore retain
        strict daily idempotency. Refreshing never clears a delivered state, so
        an already-delivered daily digest cannot be sent twice.
        """
        now = now or utcnow()
        from semi_intel.notifications.service import NotificationService

        generated = NotificationService(self.session).generate(now=now)
        digest = self.generate(now=now, refresh=True)
        return digest, {
            "created": generated.created_count,
            "updated": len(generated.updated),
            "seeded_historical_candidates": generated.seeded_historical_candidates,
        }

    def _empty_diagnostics(
        self, window_start: dt.datetime, window_end: dt.datetime
    ) -> list[str]:
        active = list(self.session.scalars(
            select(SignalCandidate).where(SignalCandidate.state == SignalCandidateState.ACTIVE)
        ))
        in_window = [
            candidate for candidate in active
            if aware(candidate.latest_observed_at) >= aware(window_start)
            and aware(candidate.latest_observed_at) < aware(window_end)
        ]
        unseen = [candidate for candidate in in_window if candidate.seen_at is None]
        if not active:
            return ["No active Radar candidates exist yet; collection and analysis may not have produced material."]
        if not in_window:
            return [
                f"{len(active)} active candidate(s) exist, but none were observed inside this digest window."
            ]
        if not unseen:
            return [
                f"{len(in_window)} candidate(s) were observed in this window, but all have already been seen."
            ]
        return [
            "No eligible digest sections were produced from the current candidates and notification events."
        ]

    def _notification_item(self, notification: Notification) -> dict:
        item = {
            "notification_id": notification.id,
            "event_type": notification.event_type.value,
            "severity": notification.severity.value,
            "title": notification.title,
            "body": notification.body,
            "reason": notification.reason,
            "candidate_id": notification.candidate_id,
            "story_id": notification.story_id,
            "topic_id": notification.topic_id,
            "source_suggestion_id": notification.source_suggestion_id,
            "source_id": notification.source_id,
            "event_at": aware(notification.event_at).isoformat(),
        }
        if notification.topic_id:
            topic = self.session.get(MonitoredTopic, notification.topic_id)
            item["topic"] = topic.name if topic else None
        if notification.source_id:
            source = self.session.get(Source, notification.source_id)
            item["source"] = source.name if source else None
        if notification.source_suggestion_id:
            suggestion = self.session.get(SourceSuggestion, notification.source_suggestion_id)
            item["suggested_source"] = suggestion.inferred_name if suggestion else None
        return item

    @staticmethod
    def _render(
        sections: dict[str, list[dict]], timezone: str,
        window_start: dt.datetime, window_end: dt.datetime,
        *, diagnostics: list[str] | None = None,
    ) -> str:
        title = (
            f"Semiconductor Intelligence Digest — {timezone}\n"
            f"{window_start.isoformat()} to {window_end.isoformat()}"
        )
        if not sections:
            detail = "\n".join(f"- {message}" for message in (diagnostics or []))
            return title + "\n\nNothing material crossed your alert thresholds." + (
                f"\n\nWhy this digest is empty\n{detail}" if detail else ""
            )
        labels = {
            "top_unseen_candidates": "Top unseen candidates",
            "fresh_corroboration": "Fresh independent corroboration",
            "tracked_topic_movement": "Tracked-topic movement",
            "promotion_activity": "Promotion activity",
            "source_intelligence": "Source intelligence",
            "provider_health": "Provider health",
        }
        lines = [title]
        for key, items in sections.items():
            lines.extend(["", labels.get(key, key.replace("_", " ").title())])
            for item in items:
                if key == "top_unseen_candidates":
                    lines.append(
                        f"- {item['title']} — score {item['score']:.2f}, "
                        f"{item['independent_groups']} independent group(s)"
                    )
                else:
                    lines.append(f"- {item['title']} — {item['body']}")
        return "\n".join(lines)
