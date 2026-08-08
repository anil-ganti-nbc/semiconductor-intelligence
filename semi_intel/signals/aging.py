"""Read-only Signal Radar age classification.

Age is derived from the first observed report in each existing independence
group.  Later members of the same dependency/citation group therefore cannot
make an old candidate look new, while the first report in a genuinely new
independent group can.  This service never changes candidate state or score.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.models import (
    CandidateSignalItem,
    SignalCandidate,
    SignalIndependenceGroup,
    SignalIndependenceGroupMember,
    SignalItem,
)


SUPPORTED_AGE_WINDOWS = (3, 7, 14, 30)
SUPPORTED_AGE_MODES = ("current", "older", "all")


def _utc_naive(value: dt.datetime) -> dt.datetime:
    """Normalize mixed SQLite/fixture datetimes for deterministic comparison."""
    if value.tzinfo is not None:
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value


@dataclass(frozen=True)
class CandidateAge:
    classification: str
    activity_at: dt.datetime
    age_days: float
    used_collection_fallback: bool
    timestamp_source: str
    reason: str
    reactivated: bool
    meaningful_group_count: int

    def to_dict(self) -> dict:
        return {
            "age_classification": self.classification,
            "meaningful_activity_at": self.activity_at.isoformat(),
            "age_days": self.age_days,
            "activity_timestamp_fallback": self.used_collection_fallback,
            "activity_timestamp_source": self.timestamp_source,
            "age_reason": self.reason,
            "resurfaced": self.reactivated,
            "meaningful_group_count": self.meaningful_group_count,
        }


class CandidateAgingService:
    """Classify candidates without mutating persisted workflow state."""

    def __init__(self, session: Session, *, now: dt.datetime | None = None):
        self.session = session
        self.now = _utc_naive(now or dt.datetime.now(dt.timezone.utc))

    @staticmethod
    def validate_window(age_days: int) -> None:
        if age_days not in SUPPORTED_AGE_WINDOWS:
            raise ValueError(f"Age window must be one of {SUPPORTED_AGE_WINDOWS}.")

    def classify(self, candidate: SignalCandidate, *, age_days: int = 7) -> CandidateAge:
        return self.classify_many([candidate], age_days=age_days)[candidate.id]

    def classify_many(
        self, candidates: Iterable[SignalCandidate], *, age_days: int = 7
    ) -> dict[int, CandidateAge]:
        self.validate_window(age_days)
        candidate_list = list(candidates)
        candidate_ids = [candidate.id for candidate in candidate_list]
        if not candidate_ids:
            return {}

        # Existing independence groups are the authoritative dependency map.
        # A group's activity is its earliest member observation, not its latest
        # syndicated/citing copy.
        group_members: dict[int, dict[int, list[tuple[dt.datetime, bool]]]] = {}
        rows = self.session.execute(
            select(
                SignalIndependenceGroup.candidate_id,
                SignalIndependenceGroup.id,
                SignalItem.posted_at,
                SignalItem.collected_at,
            )
            .join(
                SignalIndependenceGroupMember,
                SignalIndependenceGroupMember.group_id == SignalIndependenceGroup.id,
            )
            .join(SignalItem, SignalItem.id == SignalIndependenceGroupMember.signal_item_id)
            .where(SignalIndependenceGroup.candidate_id.in_(candidate_ids))
        )
        for candidate_id, group_id, posted_at, collected_at in rows:
            timestamp = posted_at or collected_at
            if timestamp is not None:
                group_members.setdefault(candidate_id, {}).setdefault(group_id, []).append(
                    (_utc_naive(timestamp), posted_at is None)
                )

        # Old/recovered databases can temporarily lack derived group rows. In
        # that case use the earliest member report as one conservative group;
        # this deliberately avoids treating every ungrouped late copy as fresh.
        missing_ids = [candidate_id for candidate_id in candidate_ids if not group_members.get(candidate_id)]
        conservative_members: dict[int, list[tuple[dt.datetime, bool]]] = {}
        if missing_ids:
            fallback_rows = self.session.execute(
                select(
                    CandidateSignalItem.candidate_id,
                    SignalItem.posted_at,
                    SignalItem.collected_at,
                )
                .join(SignalItem, SignalItem.id == CandidateSignalItem.signal_item_id)
                .where(CandidateSignalItem.candidate_id.in_(missing_ids))
            )
            for candidate_id, posted_at, collected_at in fallback_rows:
                timestamp = posted_at or collected_at
                if timestamp is not None:
                    conservative_members.setdefault(candidate_id, []).append(
                        (_utc_naive(timestamp), posted_at is None)
                    )

        cutoff = self.now - dt.timedelta(days=age_days)
        window = dt.timedelta(days=age_days)
        result: dict[int, CandidateAge] = {}
        for candidate in candidate_list:
            per_group: list[tuple[dt.datetime, bool]] = []
            for members in group_members.get(candidate.id, {}).values():
                per_group.append(min(members, key=lambda row: row[0]))

            grouping_fallback = not per_group
            if grouping_fallback and conservative_members.get(candidate.id):
                per_group = [min(conservative_members[candidate.id], key=lambda row: row[0])]
            if not per_group:
                stored = candidate.latest_observed_at or candidate.first_observed_at or candidate.created_at
                per_group = [(_utc_naive(stored), True)]

            per_group.sort(key=lambda row: row[0])
            activity_at, used_collection = per_group[-1]
            classification = "current" if activity_at >= cutoff else "older"
            elapsed_seconds = max(0.0, (self.now - activity_at).total_seconds())
            numeric_age = round(elapsed_seconds / 86400.0, 2)

            # A gap longer than the chosen window proves the candidate would
            # have aged out before the newest independent group appeared.
            reactivated = False
            if classification == "current" and len(per_group) > 1:
                previous_at = per_group[-2][0]
                reactivated = activity_at - previous_at > window

            whole_days = int(elapsed_seconds // 86400)
            if classification == "older":
                reason = f"Older: no meaningful activity for {whole_days} day(s)"
            elif reactivated:
                reason = "Current: reactivated by a newly represented independent source group"
            else:
                reason = f"Current: independent report observed {whole_days} day(s) ago"
            if used_collection:
                reason += "; collection time used because publication time was unavailable"
            if grouping_fallback:
                reason += "; independence groups unavailable, using conservative candidate fallback"

            result[candidate.id] = CandidateAge(
                classification=classification,
                activity_at=activity_at,
                age_days=numeric_age,
                used_collection_fallback=used_collection,
                timestamp_source="collection_fallback" if used_collection else "publication_or_observation",
                reason=reason,
                reactivated=reactivated,
                meaningful_group_count=len(per_group),
            )
        return result
