"""Deterministic in-app notification generation.

Every alert represents a persisted transition.  NotificationEventState is
the watermark that distinguishes "still above threshold" from "crossed the
threshold", while Notification.dedup_key makes retries and process restarts
safe.  The activation watermark prevents imported historical candidates from
flooding a newly upgraded operator.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semi_intel.domain.enums import (
    NotificationDeliveryState,
    NotificationEventType,
    NotificationSeverity,
    ProviderRunStatus,
    SignalCandidateState,
    SourceSuggestionStatus,
)
from semi_intel.domain.models import (
    CandidatePromotionEvent,
    CandidatePromotionSettings,
    CandidateTopicMatch,
    MonitoredTopic,
    Notification,
    NotificationDeliveryAttempt,
    NotificationEventState,
    NotificationSettings,
    ProviderIncident,
    ProviderRun,
    SignalCandidate,
    Source,
    SourceSuggestion,
)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def safe_error(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.splitlines()[0].strip()
    lowered = cleaned.lower()
    if "launch_persistent_context" in lowered and "spawn eperm" in lowered:
        return "Chromium could not start because Windows denied the browser process."
    if "launch_persistent_context" in lowered and "executable doesn't exist" in lowered:
        return "Playwright Chromium is not installed or could not be found."
    if "no x session imported" in lowered:
        return "No X session is imported. Import or refresh the local X session."
    for marker in ("auth_token", "ct0", "api_key", "password", "webhook"):
        if marker in lowered:
            return "provider returned a redacted authentication/configuration error"
    return cleaned[:240]


@dataclass
class GenerationSummary:
    created: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)
    seeded_historical_candidates: int = 0
    by_type: dict[str, int] = field(default_factory=dict)

    @property
    def created_count(self) -> int:
        return len(self.created)


def get_settings(
    session: Session, *, now: dt.datetime | None = None
) -> NotificationSettings:
    settings = session.get(NotificationSettings, 1)
    if settings is None:
        settings = NotificationSettings(id=1, activation_at=now or utcnow())
        session.add(settings)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            settings = session.get(NotificationSettings, 1)
    return settings


class NotificationService:
    def __init__(self, session: Session):
        self.session = session

    def settings(self, *, now: dt.datetime | None = None) -> NotificationSettings:
        return get_settings(self.session, now=now)

    def reset_activation(self, *, now: dt.datetime | None = None) -> NotificationSettings:
        settings = self.settings(now=now)
        settings.activation_at = now or utcnow()
        # Old transition watermarks could otherwise make post-reset state look
        # like a fresh crossing.  Notifications themselves remain audit data.
        self.session.execute(delete(NotificationEventState))
        self.session.flush()
        return settings

    def set_event_muted(self, event_type: NotificationEventType, *, muted: bool) -> NotificationSettings:
        settings = self.settings()
        values = set(json.loads(settings.muted_event_types or "[]"))
        (values.add if muted else values.discard)(event_type.value)
        settings.muted_event_types = json.dumps(sorted(values))
        self.session.flush()
        return settings

    def set_topic_muted(self, topic_id: int, *, muted: bool) -> NotificationSettings:
        if self.session.get(MonitoredTopic, topic_id) is None:
            raise ValueError(f"No monitored topic with id={topic_id}")
        settings = self.settings()
        values = {int(value) for value in json.loads(settings.muted_topic_ids or "[]")}
        (values.add if muted else values.discard)(topic_id)
        settings.muted_topic_ids = json.dumps(sorted(values))
        self.session.flush()
        return settings

    def generate(self, *, now: dt.datetime | None = None) -> GenerationSummary:
        now = now or utcnow()
        settings = self.settings(now=now)
        summary = GenerationSummary()
        if not settings.in_app_enabled:
            return summary

        muted_types = set(json.loads(settings.muted_event_types or "[]"))
        muted_topics = {int(value) for value in json.loads(settings.muted_topic_ids or "[]")}
        self._candidate_events(settings, muted_types, muted_topics, now, summary)
        self._promotion_events(settings, muted_types, now, summary)
        self._source_suggestion_events(settings, muted_types, now, summary)
        self._provider_events(settings, muted_types, now, summary)
        self._topic_events(settings, muted_types, muted_topics, now, summary)
        self.session.flush()
        return summary

    def _state(
        self, event_type: NotificationEventType, subject_kind: str, subject_id: int
    ) -> NotificationEventState | None:
        return self.session.scalar(select(NotificationEventState).where(
            NotificationEventState.event_type == event_type,
            NotificationEventState.subject_kind == subject_kind,
            NotificationEventState.subject_id == subject_id,
        ))

    def _new_state(
        self,
        event_type: NotificationEventType,
        subject_kind: str,
        subject_id: int,
        *,
        numeric: float | None = None,
        boolean: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> NotificationEventState:
        state = NotificationEventState(
            event_type=event_type,
            subject_kind=subject_kind,
            subject_id=subject_id,
            last_numeric_value=numeric,
            last_boolean_value=boolean,
            state_metadata=json.dumps(metadata or {}),
        )
        self.session.add(state)
        return state

    @staticmethod
    def _sequence(state: NotificationEventState) -> int:
        metadata = json.loads(state.state_metadata or "{}")
        metadata["sequence"] = int(metadata.get("sequence", 0)) + 1
        state.state_metadata = json.dumps(metadata)
        return metadata["sequence"]

    def _emit(
        self,
        *,
        event_type: NotificationEventType,
        severity: NotificationSeverity,
        title: str,
        body: str,
        reason: str,
        dedup_key: str,
        now: dt.datetime,
        summary: GenerationSummary,
        event_at: dt.datetime | None = None,
        candidate_id: int | None = None,
        story_id: int | None = None,
        topic_id: int | None = None,
        source_suggestion_id: int | None = None,
        provider_run_id: int | None = None,
        source_id: int | None = None,
        metadata: dict[str, Any] | None = None,
        muted: bool = False,
        aggregate_existing: bool = False,
    ) -> Notification:
        existing = self.session.scalar(
            select(Notification).where(Notification.dedup_key == dedup_key)
        )
        if existing:
            if aggregate_existing and aware(event_at or now) > aware(existing.latest_occurrence_at):
                existing.occurrence_count += 1
                existing.latest_occurrence_at = event_at or now
                existing.body = body
                existing.reason = reason
                if provider_run_id:
                    existing.provider_run_id = provider_run_id
                summary.updated.append(existing.id)
            return existing
        notification = Notification(
            event_type=event_type,
            severity=severity,
            title=title[:500],
            body=body,
            reason=reason,
            dedup_key=dedup_key,
            candidate_id=candidate_id,
            story_id=story_id,
            topic_id=topic_id,
            source_suggestion_id=source_suggestion_id,
            provider_run_id=provider_run_id,
            source_id=source_id,
            event_metadata=json.dumps(metadata or {}),
            created_at=now,
            event_at=event_at or now,
            first_occurrence_at=event_at or now,
            latest_occurrence_at=event_at or now,
            muted=muted,
            delivery_state=(
                NotificationDeliveryState.SUPPRESSED if muted
                else NotificationDeliveryState.IN_APP
            ),
        )
        self.session.add(notification)
        self.session.flush()
        summary.created.append(notification.id)
        summary.by_type[event_type.value] = summary.by_type.get(event_type.value, 0) + 1
        return notification

    def _candidate_events(
        self,
        settings: NotificationSettings,
        muted_types: set[str],
        muted_topics: set[int],
        now: dt.datetime,
        summary: GenerationSummary,
    ) -> None:
        activation = aware(settings.activation_at)
        candidates = list(self.session.scalars(select(SignalCandidate)))
        from semi_intel.signals.promotion import get_promotion_settings
        promotion_settings = get_promotion_settings(self.session)

        for candidate in candidates:
            recent = aware(candidate.latest_observed_at) >= activation
            eligible_state = candidate.state == SignalCandidateState.ACTIVE
            topic_ok = not settings.required_topic_match or candidate.primary_topic_id is not None
            groups_ok = (
                candidate.independent_source_group_count
                >= settings.required_independent_group_count
            )
            muted_topic = candidate.primary_topic_id in muted_topics

            high = (
                eligible_state
                and candidate.attention_score >= settings.minimum_attention_score
                and topic_ok and groups_ok
            )
            state = self._state(NotificationEventType.HIGH_ATTENTION, "candidate", candidate.id)
            if state is None:
                state = self._new_state(
                    NotificationEventType.HIGH_ATTENTION, "candidate", candidate.id,
                    boolean=high, metadata={"sequence": 0},
                )
                if not recent:
                    summary.seeded_historical_candidates += 1
                elif high and settings.high_score_enabled:
                    sequence = self._sequence(state)
                    state.last_event_at = now
                    self._high_attention(candidate, settings, sequence, muted_types, muted_topic, now, summary)
            else:
                previous = bool(state.last_boolean_value)
                if high and not previous and recent and settings.high_score_enabled:
                    sequence = self._sequence(state)
                    state.last_event_at = now
                    self._high_attention(candidate, settings, sequence, muted_types, muted_topic, now, summary)
                state.last_boolean_value = high

            score_state = self._state(NotificationEventType.SCORE_INCREASE, "candidate", candidate.id)
            if score_state is None:
                self._new_state(
                    NotificationEventType.SCORE_INCREASE, "candidate", candidate.id,
                    numeric=candidate.attention_score, metadata={"sequence": 0},
                )
            else:
                baseline = score_state.last_numeric_value or 0.0
                increase = candidate.attention_score - baseline
                if (
                    recent and eligible_state and settings.score_increase_enabled
                    and increase >= settings.minimum_score_increase
                ):
                    sequence = self._sequence(score_state)
                    detail = self._score_reason(candidate)
                    self._emit(
                        event_type=NotificationEventType.SCORE_INCREASE,
                        severity=NotificationSeverity.NOTABLE,
                        title=f"Attention increased: {candidate.title}",
                        body=f"Score rose from {baseline:.2f} to {candidate.attention_score:.2f}.",
                        reason=detail,
                        dedup_key=f"score:{candidate.id}:{sequence}",
                        candidate_id=candidate.id,
                        topic_id=candidate.primary_topic_id,
                        metadata={"previous_score": baseline, "score": candidate.attention_score},
                        muted=(
                            NotificationEventType.SCORE_INCREASE.value in muted_types
                            or muted_topic
                        ),
                        now=now, summary=summary,
                    )
                    score_state.last_event_at = now
                    score_state.last_numeric_value = candidate.attention_score
                elif candidate.attention_score < baseline:
                    score_state.last_numeric_value = candidate.attention_score

            group_state = self._state(
                NotificationEventType.INDEPENDENT_CORROBORATION, "candidate", candidate.id
            )
            groups = candidate.independent_source_group_count
            if group_state is None:
                self._new_state(
                    NotificationEventType.INDEPENDENT_CORROBORATION,
                    "candidate", candidate.id, numeric=float(groups),
                )
            else:
                previous_groups = int(group_state.last_numeric_value or 0)
                if (
                    recent and eligible_state and settings.corroboration_enabled
                    and groups > previous_groups and previous_groups > 0
                ):
                    self._emit(
                        event_type=NotificationEventType.INDEPENDENT_CORROBORATION,
                        severity=(
                            NotificationSeverity.IMPORTANT if groups >= 3
                            else NotificationSeverity.NOTABLE
                        ),
                        title=f"Independent corroboration: {candidate.title}",
                        body=(
                            f"Independent evidence groups increased from "
                            f"{previous_groups} to {groups}."
                        ),
                        reason="A new evidence group was not attributed to the same origin.",
                        dedup_key=f"corroboration:{candidate.id}:{groups}",
                        candidate_id=candidate.id,
                        topic_id=candidate.primary_topic_id,
                        metadata={"previous_groups": previous_groups, "groups": groups},
                        muted=(
                            NotificationEventType.INDEPENDENT_CORROBORATION.value in muted_types
                            or muted_topic
                        ),
                        now=now, summary=summary,
                    )
                    group_state.last_event_at = now
                group_state.last_numeric_value = float(groups)

            ready = self._promotion_ready(candidate, promotion_settings, now)
            ready_state = self._state(
                NotificationEventType.PROMOTION_READY, "candidate", candidate.id
            )
            if ready_state is None:
                ready_state = self._new_state(
                    NotificationEventType.PROMOTION_READY, "candidate", candidate.id,
                    boolean=ready, metadata={"sequence": 0},
                )
                previous_ready = False
            else:
                previous_ready = bool(ready_state.last_boolean_value)
            if (
                ready and not previous_ready and recent
                and settings.promotion_ready_enabled
            ):
                sequence = self._sequence(ready_state)
                self._emit(
                    event_type=NotificationEventType.PROMOTION_READY,
                    severity=NotificationSeverity.IMPORTANT,
                    title=f"Ready for editorial review: {candidate.title}",
                    body=(
                        f"Score {candidate.attention_score:.2f}; "
                        f"{candidate.independent_source_group_count} independent group(s)."
                    ),
                    reason="Candidate newly satisfies the configured promotion thresholds.",
                    dedup_key=f"promotion-ready:{candidate.id}:{sequence}",
                    candidate_id=candidate.id,
                    topic_id=candidate.primary_topic_id,
                    muted=(
                        NotificationEventType.PROMOTION_READY.value in muted_types
                        or muted_topic
                    ),
                    now=now, summary=summary,
                )
                ready_state.last_event_at = now
            ready_state.last_boolean_value = ready

    def _high_attention(
        self, candidate: SignalCandidate, settings: NotificationSettings,
        sequence: int, muted_types: set[str], muted_topic: bool,
        now: dt.datetime, summary: GenerationSummary,
    ) -> None:
        self._emit(
            event_type=NotificationEventType.HIGH_ATTENTION,
            severity=(
                NotificationSeverity.IMPORTANT
                if candidate.attention_score >= 0.85
                else NotificationSeverity.NOTABLE
            ),
            title=f"High-attention candidate: {candidate.title}",
            body=(
                f"Score {candidate.attention_score:.2f} with "
                f"{candidate.independent_source_group_count} independent group(s)."
            ),
            reason=self._score_reason(candidate),
            dedup_key=f"high:{candidate.id}:{settings.minimum_attention_score:.3f}:{sequence}",
            candidate_id=candidate.id,
            topic_id=candidate.primary_topic_id,
            metadata={
                "score": candidate.attention_score,
                "threshold": settings.minimum_attention_score,
                "independent_groups": candidate.independent_source_group_count,
            },
            muted=(
                NotificationEventType.HIGH_ATTENTION.value in muted_types or muted_topic
            ),
            now=now, summary=summary,
        )

    @staticmethod
    def _score_reason(candidate: SignalCandidate) -> str:
        explanation = json.loads(candidate.score_explanation or "{}")
        components = explanation.get("components", {})
        ranked = sorted(
            components.items(),
            key=lambda item: item[1].get("contribution", 0),
            reverse=True,
        )
        details = [value.get("detail", name) for name, value in ranked[:2]]
        return "; ".join(filter(None, details)) or "Attention threshold crossed."

    @staticmethod
    def _promotion_ready(
        candidate: SignalCandidate,
        settings: CandidatePromotionSettings | None,
        now: dt.datetime,
    ) -> bool:
        if settings is None or candidate.state != SignalCandidateState.ACTIVE:
            return False
        if candidate.attention_score < settings.minimum_attention_score:
            return False
        if settings.required_topic_match and candidate.primary_topic_id is None:
            return False
        if candidate.independent_source_group_count < 1:
            return False
        observed = aware(candidate.first_observed_at)
        if observed and (now - observed).total_seconds() / 3600 > settings.maximum_candidate_age_hours:
            return False
        return True

    def _promotion_events(
        self, settings: NotificationSettings, muted_types: set[str],
        now: dt.datetime, summary: GenerationSummary,
    ) -> None:
        if not settings.promotion_completed_enabled:
            return
        activation = aware(settings.activation_at)
        events = self.session.scalars(select(CandidatePromotionEvent))
        for event in events:
            if aware(event.created_at) < activation:
                continue
            candidate = self.session.get(SignalCandidate, event.candidate_id)
            title = candidate.title if candidate else f"Candidate #{event.candidate_id}"
            self._emit(
                event_type=NotificationEventType.CANDIDATE_PROMOTED,
                severity=NotificationSeverity.NOTABLE,
                title=f"Promoted to editorial story: {title}",
                body=f"Candidate was promoted by {event.promoted_by}.",
                reason=event.reason or "Editorial promotion completed.",
                dedup_key=f"promoted:{event.id}",
                event_at=event.created_at,
                candidate_id=event.candidate_id,
                story_id=event.story_id,
                metadata={"automatic": event.automatic, "promoted_by": event.promoted_by},
                muted=NotificationEventType.CANDIDATE_PROMOTED.value in muted_types,
                now=now, summary=summary,
            )

    def _source_suggestion_events(
        self, settings: NotificationSettings, muted_types: set[str],
        now: dt.datetime, summary: GenerationSummary,
    ) -> None:
        if not settings.source_suggestion_enabled:
            return
        activation = aware(settings.activation_at)
        suggestions = self.session.scalars(select(SourceSuggestion).where(
            SourceSuggestion.status == SourceSuggestionStatus.PENDING,
            SourceSuggestion.score >= settings.source_suggestion_minimum_score,
        ))
        for suggestion in suggestions:
            if aware(suggestion.last_seen_at) < activation:
                continue
            self._emit(
                event_type=NotificationEventType.SOURCE_SUGGESTION,
                severity=NotificationSeverity.NOTABLE,
                title=f"Source worth reviewing: {suggestion.inferred_name}",
                body=(
                    f"Score {suggestion.score:.2f}; "
                    f"{suggestion.appearances} appearance(s)."
                ),
                reason="The pending source suggestion crossed the configured review threshold.",
                dedup_key=f"source-suggestion:{suggestion.id}:{settings.source_suggestion_minimum_score:.3f}",
                source_suggestion_id=suggestion.id,
                metadata={
                    "score": suggestion.score,
                    "appearances": suggestion.appearances,
                    "independent_origins": suggestion.independent_origin_count,
                },
                muted=NotificationEventType.SOURCE_SUGGESTION.value in muted_types,
                now=now, summary=summary,
            )

    def _provider_events(
        self, settings: NotificationSettings, muted_types: set[str],
        now: dt.datetime, summary: GenerationSummary,
    ) -> None:
        if not settings.provider_health_enabled:
            return
        activation = aware(settings.activation_at)
        runs = list(self.session.scalars(
            select(ProviderRun)
            .where(ProviderRun.started_at >= settings.activation_at)
            .order_by(ProviderRun.started_at.asc(), ProviderRun.id.asc())
        ))
        grouped: dict[tuple[str, int | None], list[ProviderRun]] = defaultdict(list)
        for run in runs:
            grouped[(run.provider, run.source_id)].append(run)

        for (provider, source_id), provider_runs in grouped.items():
            latest = provider_runs[-1]
            open_incident = self.session.scalar(select(ProviderIncident).where(
                ProviderIncident.provider == provider,
                ProviderIncident.source_id == source_id,
                ProviderIncident.resolved_at.is_(None),
            ))
            if latest.status == ProviderRunStatus.OK:
                if (
                    open_incident and aware(latest.started_at) > aware(open_incident.opened_at)
                    and settings.provider_recovery_enabled
                ):
                    open_incident.resolved_at = latest.finished_at or latest.started_at
                    notification = self._emit(
                        event_type=NotificationEventType.PROVIDER_RECOVERY,
                        severity=NotificationSeverity.INFORMATIONAL,
                        title=f"Provider recovered: {self._source_name(source_id, provider)}",
                        body="A successful collection run ended the active provider incident.",
                        reason=(
                            f"Recovered after {open_incident.consecutive_failures} "
                            f"consecutive failure(s)."
                        ),
                        dedup_key=f"provider-recovery:{open_incident.id}",
                        event_at=latest.finished_at or latest.started_at,
                        provider_run_id=latest.id,
                        source_id=source_id,
                        metadata={"provider": provider, "incident_id": open_incident.id},
                        muted=NotificationEventType.PROVIDER_RECOVERY.value in muted_types,
                        now=now, summary=summary,
                    )
                    open_incident.recovery_notification_id = notification.id
                continue

            consecutive = 0
            for run in reversed(provider_runs):
                if run.status != ProviderRunStatus.FAILED:
                    break
                consecutive += 1
            if consecutive < settings.provider_failure_threshold:
                continue

            if open_incident is None:
                incident_key = (
                    f"{provider}:{source_id or 'all'}:"
                    f"{aware(provider_runs[-consecutive].started_at).isoformat()}"
                )
                open_incident = ProviderIncident(
                    incident_key=incident_key,
                    provider=provider,
                    source_id=source_id,
                    opened_at=provider_runs[-consecutive].started_at,
                    latest_failure_at=latest.finished_at or latest.started_at,
                    consecutive_failures=consecutive,
                    latest_provider_run_id=latest.id,
                    latest_error_summary=safe_error(latest.error),
                )
                self.session.add(open_incident)
                self.session.flush()
                notification = self._emit(
                    event_type=NotificationEventType.PROVIDER_FAILURE,
                    severity=NotificationSeverity.IMPORTANT,
                    title=f"Persistent provider failure: {self._source_name(source_id, provider)}",
                    body=f"{consecutive} consecutive collection runs failed.",
                    reason=safe_error(latest.error) or "Provider failure threshold reached.",
                    dedup_key=f"provider-failure:{open_incident.id}",
                    event_at=latest.finished_at or latest.started_at,
                    provider_run_id=latest.id,
                    source_id=source_id,
                    metadata={"provider": provider, "consecutive_failures": consecutive},
                    muted=NotificationEventType.PROVIDER_FAILURE.value in muted_types,
                    now=now, summary=summary,
                )
                open_incident.failure_notification_id = notification.id
            elif open_incident.latest_provider_run_id != latest.id:
                open_incident.latest_failure_at = latest.finished_at or latest.started_at
                open_incident.consecutive_failures = consecutive
                open_incident.latest_provider_run_id = latest.id
                open_incident.latest_error_summary = safe_error(latest.error)
                if open_incident.failure_notification_id:
                    notification = self.session.get(Notification, open_incident.failure_notification_id)
                    if notification:
                        notification.occurrence_count += 1
                        notification.latest_occurrence_at = latest.finished_at or latest.started_at
                        notification.body = f"{consecutive} consecutive collection runs failed."
                        notification.reason = safe_error(latest.error) or notification.reason
                        notification.provider_run_id = latest.id
                        summary.updated.append(notification.id)

    def _source_name(self, source_id: int | None, provider: str) -> str:
        source = self.session.get(Source, source_id) if source_id else None
        return source.name if source else provider

    def _topic_events(
        self, settings: NotificationSettings, muted_types: set[str],
        muted_topics: set[int], now: dt.datetime, summary: GenerationSummary,
    ) -> None:
        if not settings.topic_activity_enabled:
            return
        window_start = now - dt.timedelta(hours=settings.topic_activity_window_hours)
        effective_start = max(window_start, aware(settings.activation_at))
        rows = self.session.execute(
            select(CandidateTopicMatch.topic_id, func.count(func.distinct(SignalCandidate.id)),
                   func.max(SignalCandidate.attention_score))
            .join(SignalCandidate, SignalCandidate.id == CandidateTopicMatch.candidate_id)
            .where(
                SignalCandidate.latest_observed_at >= effective_start,
                SignalCandidate.state == SignalCandidateState.ACTIVE,
            )
            .group_by(CandidateTopicMatch.topic_id)
        )
        bucket = int(effective_start.timestamp() // (settings.topic_activity_window_hours * 3600))
        for topic_id, candidate_count, max_score in rows:
            if candidate_count < settings.topic_activity_minimum_candidates:
                continue
            topic = self.session.get(MonitoredTopic, topic_id)
            if not topic:
                continue
            self._emit(
                event_type=NotificationEventType.TOPIC_ACTIVITY,
                severity=(
                    NotificationSeverity.IMPORTANT if max_score >= 0.85
                    else NotificationSeverity.NOTABLE
                ),
                title=f"Tracked-topic activity: {topic.name}",
                body=(
                    f"{candidate_count} active candidate(s) in the last "
                    f"{settings.topic_activity_window_hours} hours; top score {max_score:.2f}."
                ),
                reason="Activity crossed the configured per-topic aggregation threshold.",
                dedup_key=f"topic-activity:{topic_id}:{bucket}",
                topic_id=topic_id,
                metadata={"candidate_count": candidate_count, "maximum_score": max_score},
                muted=(
                    NotificationEventType.TOPIC_ACTIVITY.value in muted_types
                    or topic_id in muted_topics
                ),
                now=now, summary=summary,
            )

    def create_test_notification(self, *, now: dt.datetime | None = None) -> Notification:
        now = now or utcnow()
        summary = GenerationSummary()
        return self._emit(
            event_type=NotificationEventType.TEST,
            severity=NotificationSeverity.INFORMATIONAL,
            title="Test notification",
            body="In-app notifications are working.",
            reason="Created by the operator's test action.",
            dedup_key=f"test:{now.isoformat()}",
            now=now,
            summary=summary,
        )

    def set_read(
        self, notification_ids: Iterable[int], *, read: bool, now: dt.datetime | None = None
    ) -> list[Notification]:
        now = now or utcnow()
        rows: list[Notification] = []
        for notification_id in notification_ids:
            notification = self.session.get(Notification, notification_id)
            if notification:
                notification.read_at = now if read else None
                rows.append(notification)
        self.session.flush()
        return rows

    def dismiss(self, notification: Notification, *, now: dt.datetime | None = None) -> None:
        notification.dismissed_at = now or utcnow()
        self.session.flush()

    def restore(self, notification: Notification) -> None:
        notification.dismissed_at = None
        self.session.flush()

    def cleanup_retention(self, *, now: dt.datetime | None = None) -> int:
        now = now or utcnow()
        settings = self.settings(now=now)
        cutoff = now - dt.timedelta(days=settings.retention_days)
        old_ids = list(self.session.scalars(
            select(Notification.id).where(
                Notification.created_at < cutoff,
                or_(Notification.read_at.is_not(None), Notification.dismissed_at.is_not(None)),
            )
        ))
        if not old_ids:
            return 0
        self.session.execute(delete(NotificationDeliveryAttempt).where(
            NotificationDeliveryAttempt.notification_id.in_(old_ids)
        ))
        self.session.execute(delete(Notification).where(Notification.id.in_(old_ids)))
        self.session.flush()
        return len(old_ids)
