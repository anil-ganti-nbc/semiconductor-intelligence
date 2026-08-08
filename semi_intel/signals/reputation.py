"""Source Reputation (v1.0.0 Candidate Intelligence, Phase 1).

Every field on `SourceReputation` is a deterministic counter or ratio
derived from data this project already collects -- independence-group
origination (semi_intel/signals/independence.py) and candidate promotion/
dismissal outcomes. No machine learning, no hidden heuristics: everything
here can be recomputed from scratch at any time and will reproduce the
same numbers.

`authority` is the one composite value -- a fixed, documented weighted
blend of the three ratio components below, not a learned weight:

    authority = 0.4 * originality + 0.4 * editorial_yield + 0.2 * (1 - noise_rate)

clamped to [0, 1], defaulting to 0.5 (neutral) for a source with no
candidate history yet. `authority_override` lets an operator pin a value
(e.g. "this is the OEM's own account") without deleting the computed
`authority` -- both are always stored and shown.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import SignalCandidateState
from semi_intel.domain.models import (
    CandidateSignalItem,
    MonitoredTopic,
    SignalCandidate,
    SignalIndependenceGroup,
    SignalIndependenceGroupMember,
    SignalItem,
    Source,
    SourceReputation,
)


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


@dataclass
class _SourceStats:
    items_contributed: int = 0
    candidates_contributed: set = None
    candidates_promoted: set = None
    candidates_dismissed: set = None
    groups_originated: int = 0
    groups_appeared_in: int = 0
    verification_count: int = 0
    false_positive_count: int = 0
    lead_times_hours: list = None
    topic_counter: Counter = None

    def __post_init__(self):
        self.candidates_contributed = set()
        self.candidates_promoted = set()
        self.candidates_dismissed = set()
        self.lead_times_hours = []
        self.topic_counter = Counter()


def get_or_create_reputation(session: Session, source_id: int) -> SourceReputation:
    rep = session.execute(
        select(SourceReputation).where(SourceReputation.source_id == source_id)
    ).scalar_one_or_none()
    if not rep:
        rep = SourceReputation(source_id=source_id)
        session.add(rep)
        session.flush()
    return rep


def recompute_source_reputation(session: Session, source_id: int) -> SourceReputation:
    """Recomputes and persists one Source's reputation from scratch.
    Idempotent -- calling this repeatedly with unchanged data always
    produces the same result."""
    rep = get_or_create_reputation(session, source_id)
    stats = _SourceStats()

    item_ids = [row[0] for row in session.execute(
        select(SignalItem.id).where(SignalItem.source_id == source_id)
    )]
    stats.items_contributed = len(item_ids)

    if item_ids:
        for candidate_id, state, primary_topic_id in session.execute(
            select(SignalCandidate.id, SignalCandidate.state, SignalCandidate.primary_topic_id)
            .join(CandidateSignalItem, CandidateSignalItem.candidate_id == SignalCandidate.id)
            .where(CandidateSignalItem.signal_item_id.in_(item_ids))
            .distinct()
        ):
            stats.candidates_contributed.add(candidate_id)
            if state == SignalCandidateState.PROMOTED:
                stats.candidates_promoted.add(candidate_id)
            elif state == SignalCandidateState.DISMISSED:
                stats.candidates_dismissed.add(candidate_id)
            if primary_topic_id:
                stats.topic_counter[primary_topic_id] += 1

        appeared_in_group_ids = {row[0] for row in session.execute(
            select(SignalIndependenceGroupMember.group_id)
            .join(SignalItem, SignalItem.id == SignalIndependenceGroupMember.signal_item_id)
            .where(SignalItem.source_id == source_id)
        )}
        stats.groups_appeared_in = len(appeared_in_group_ids)

        origin_rows = list(session.execute(
            select(SignalIndependenceGroup, SignalCandidate.state)
            .join(SignalItem, SignalItem.id == SignalIndependenceGroup.origin_signal_item_id)
            .join(SignalCandidate, SignalCandidate.id == SignalIndependenceGroup.candidate_id)
            .where(SignalItem.source_id == source_id)
        ))
        stats.groups_originated = len(origin_rows)
        for group, state in origin_rows:
            if state == SignalCandidateState.PROMOTED:
                stats.verification_count += 1
            elif state == SignalCandidateState.DISMISSED:
                stats.false_positive_count += 1

            origin_item = session.get(SignalItem, group.origin_signal_item_id)
            if origin_item and origin_item.posted_at:
                other_times = [
                    si.posted_at for si in session.scalars(
                        select(SignalItem).join(
                            SignalIndependenceGroupMember,
                            SignalIndependenceGroupMember.signal_item_id == SignalItem.id,
                        ).where(
                            SignalIndependenceGroupMember.group_id == group.id,
                            SignalItem.id != origin_item.id,
                        )
                    )
                    if si.posted_at and si.posted_at > origin_item.posted_at
                ]
                if other_times:
                    delta_hours = (min(other_times) - origin_item.posted_at).total_seconds() / 3600.0
                    stats.lead_times_hours.append(delta_hours)

    rep.items_contributed = stats.items_contributed
    rep.independence_groups_originated = stats.groups_originated
    rep.independence_groups_appeared_in = stats.groups_appeared_in
    rep.verification_count = stats.verification_count
    rep.false_positive_count = stats.false_positive_count

    rep.originality = (
        stats.groups_originated / stats.groups_appeared_in if stats.groups_appeared_in else 0.0
    )
    rep.editorial_yield = (
        len(stats.candidates_promoted) / len(stats.candidates_contributed)
        if stats.candidates_contributed else 0.0
    )
    rep.noise_rate = (
        len(stats.candidates_dismissed) / len(stats.candidates_contributed)
        if stats.candidates_contributed else 0.0
    )
    rep.lead_time_hours = (
        sum(stats.lead_times_hours) / len(stats.lead_times_hours) if stats.lead_times_hours else None
    )

    if stats.candidates_contributed:
        rep.authority = max(0.0, min(1.0, (
            0.4 * rep.originality + 0.4 * rep.editorial_yield + 0.2 * (1.0 - rep.noise_rate)
        )))
    else:
        rep.authority = 0.5  # neutral -- no history yet, not "bad"

    if stats.topic_counter:
        top_topics = [
            session.get(MonitoredTopic, topic_id).name
            for topic_id, _ in stats.topic_counter.most_common(3)
            if session.get(MonitoredTopic, topic_id)
        ]
        rep.specializations = json.dumps(top_topics)

    rep.last_updated = _now()
    return rep


def effective_authority(rep: SourceReputation | None) -> float:
    """The value everything else (confidence engine, UI) should actually
    read -- an operator override always wins over the computed value."""
    if rep is None:
        return 0.5
    if rep.authority_override is not None:
        return rep.authority_override
    return rep.authority


def recompute_all_source_reputations(session: Session) -> int:
    source_ids = [row[0] for row in session.execute(select(Source.id))]
    for source_id in source_ids:
        recompute_source_reputation(session, source_id)
    session.commit()
    return len(source_ids)
