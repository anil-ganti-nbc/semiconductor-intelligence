"""Attention scoring for Signal Candidates (brief section 11).

A separate, explainable score from claim confidence or editorial interest --
those are different concepts (claim confidence is about factual reliability
of a specific assertion; editorial interest is about an already-canonical
story; attention is about whether an unpromoted candidate deserves a human's
attention at all). Every component is stored with its raw value, weight,
and contribution so nothing here is ever an unexplained number.

Default weights (persisted, configurable via AttentionScoringSettings,
brief section 11):
  topic relevance     0.30
  novelty             0.20
  momentum            0.15
  source diversity    0.15
  artifact strength   0.15
  source quality      0.05

Weights are normalized to sum to 1.0 at scoring time regardless of what's
stored (defensive against a bad manual edit), not merely validated.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semi_intel.domain.models import (
    AttentionScoringSettings,
    CandidateSignalItem,
    MonitoredTopic,
    SignalCandidate,
    SignalIndependenceGroup,
    SignalItem,
    Source,
)

# Conservative artifact-strength ranking (brief section 11): official
# announcements outrank hardware/driver/firmware/PCI evidence, which
# outranks benchmark listings, which outrank retail/leak/rumor material.
# Nothing here is inferred from an RSS parser's own "official" label --
# it is driven only by the mention/label types this codebase itself
# produced (semi_intel/signals/analysis.py).
ARTIFACT_STRENGTH_RANK: dict[str, float] = {
    "pci_id": 0.85,
    "benchmark": 0.75,
    "product": 0.5,
    "codename": 0.4,
    "version": 0.35,
    "retailer": 0.3,
}
LABEL_STRENGTH_BONUS: dict[str, float] = {
    "Official Announcement": 0.95,
    "Firmware": 0.15,
    "Driver": 0.1,
    "Retail Listing": 0.1,
    "Leak": 0.05,
    "Rumor": -0.1,
    "Opinion": -0.15,
    "Meme": -0.3,
    "Off Topic": -0.4,
}


def get_scoring_settings(session: Session) -> AttentionScoringSettings:
    settings = session.get(AttentionScoringSettings, 1)
    if not settings:
        settings = AttentionScoringSettings(id=1)
        session.add(settings)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            settings = session.get(AttentionScoringSettings, 1)
    return settings


def _normalized_weights(settings: AttentionScoringSettings) -> dict[str, float]:
    raw = {
        "topic_relevance": settings.weight_topic_relevance,
        "novelty": settings.weight_novelty,
        "momentum": settings.weight_momentum,
        "source_diversity": settings.weight_source_diversity,
        "artifact_strength": settings.weight_artifact_strength,
        "source_quality": settings.weight_source_quality,
    }
    total = sum(raw.values())
    if total <= 0:
        return {k: 1.0 / len(raw) for k in raw}
    return {k: v / total for k, v in raw.items()}


@dataclass
class ComponentExplanation:
    raw_value: float
    weight: float
    contribution: float
    detail: str = ""


@dataclass
class ScoreResult:
    total: float
    components: dict[str, ComponentExplanation] = field(default_factory=dict)
    penalties: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({
            "total": round(self.total, 4),
            "components": {
                k: {"raw_value": round(v.raw_value, 4), "weight": round(v.weight, 4),
                    "contribution": round(v.contribution, 4), "detail": v.detail}
                for k, v in self.components.items()
            },
            "penalties": {k: round(v, 4) for k, v in self.penalties.items()},
        })


def _topic_relevance(session: Session, candidate: SignalCandidate) -> tuple[float, str]:
    if candidate.primary_topic_id is None:
        return 0.0, "no monitored-topic match"
    topic = session.get(MonitoredTopic, candidate.primary_topic_id)
    if not topic:
        return 0.0, "primary topic no longer exists"
    return min(0.5 + topic.priority * 0.5, 1.0), f"monitored topic {topic.name!r} (priority {topic.priority:.2f})"


def _novelty(session: Session, candidate: SignalCandidate) -> tuple[float, str]:
    """Proxy: what fraction of the candidate's evidence represents genuinely
    new/independent material versus repetition of the same underlying
    report. "The fifth paraphrase of the same fact is not novel" (brief) --
    a candidate with many items but few independence groups is mostly
    duplication, not new information."""
    if candidate.item_count == 0:
        return 0.0, "no evidence yet"
    ratio = candidate.independent_source_group_count / candidate.item_count
    return ratio, (
        f"{candidate.independent_source_group_count} independent group(s) "
        f"out of {candidate.item_count} item(s)"
    )


def _momentum(session: Session, candidate: SignalCandidate, settings: AttentionScoringSettings,
             now: dt.datetime) -> tuple[float, str]:
    """Real time-based velocity, not total evidence count (brief section
    11): items in the recent window vs items before it. A candidate that's
    all old evidence with nothing recent scores low even if it has a lot of
    total evidence; one that's accelerating scores high."""
    window = dt.timedelta(hours=settings.momentum_window_hours)
    item_ids = [row[0] for row in session.execute(
        select(CandidateSignalItem.signal_item_id).where(CandidateSignalItem.candidate_id == candidate.id)
    )]
    if not item_ids:
        return 0.0, "no evidence yet"
    posted_ats = [
        row[0] for row in session.execute(select(SignalItem.posted_at).where(SignalItem.id.in_(item_ids)))
        if row[0] is not None
    ]
    if not posted_ats:
        return 0.0, "no dated evidence"
    recent = sum(1 for p in posted_ats if now - p <= window)
    older = len(posted_ats) - recent
    if recent == 0:
        return 0.0, f"no items in the last {settings.momentum_window_hours}h window"
    if older == 0:
        return 1.0, f"all {recent} item(s) within the last {settings.momentum_window_hours}h window (new candidate)"
    acceleration = recent / (recent + older)
    return acceleration, f"{recent} recent vs {older} older item(s) in {settings.momentum_window_hours}h window"


def _source_diversity(candidate: SignalCandidate) -> tuple[float, str]:
    """Effective independent groups, not raw source count (brief section
    10) -- this is what actually fixes "twelve articles citing VideoCardz
    count as thirteen confirmations"."""
    score = min(candidate.independent_source_group_count / 3.0, 1.0)
    return score, (
        f"{candidate.independent_source_group_count} independent group(s), "
        f"{candidate.distinct_source_count} raw distinct source(s)"
    )


def _artifact_strength(session: Session, candidate: SignalCandidate) -> tuple[float, str]:
    from semi_intel.domain.models import SignalEntityMention
    from semi_intel.domain.enums import SignalMentionStatus

    item_ids = [row[0] for row in session.execute(
        select(CandidateSignalItem.signal_item_id).where(CandidateSignalItem.candidate_id == candidate.id)
    )]
    if not item_ids:
        return 0.0, "no evidence yet"
    best = 0.0
    detail = "no structured artifact found"
    mentions = session.scalars(
        select(SignalEntityMention).where(
            SignalEntityMention.signal_item_id.in_(item_ids),
            SignalEntityMention.status != SignalMentionStatus.REJECTED,
        )
    )
    for m in mentions:
        rank = ARTIFACT_STRENGTH_RANK.get(m.proposed_entity_type, 0.2)
        if rank > best:
            best = rank
            detail = f"strongest artifact type: {m.proposed_entity_type}"

    from semi_intel.domain.models import SignalLabel
    labels = session.scalars(select(SignalLabel).where(SignalLabel.signal_item_id.in_(item_ids)))
    for label in labels:
        bonus = LABEL_STRENGTH_BONUS.get(label.label, 0.0)
        candidate_score = max(0.0, min(1.0, best + bonus)) if bonus > 0 else best
        if bonus > 0 and candidate_score > best:
            best = candidate_score
            detail = f"{detail}; boosted by label {label.label!r}"

    return best, detail


def _source_quality(session: Session, candidate: SignalCandidate) -> tuple[float, str]:
    item_ids = [row[0] for row in session.execute(
        select(CandidateSignalItem.signal_item_id).where(CandidateSignalItem.candidate_id == candidate.id)
    )]
    if not item_ids:
        return 0.0, "no evidence yet"
    source_ids = {row[0] for row in session.execute(
        select(SignalItem.source_id).where(SignalItem.id.in_(item_ids))
    )}
    weights = [
        row[0] for row in session.execute(select(Source.trust_weight).where(Source.id.in_(source_ids)))
    ]
    if not weights:
        return 0.0, "no source trust data"
    avg = sum(weights) / len(weights)
    return avg, f"average trust_weight {avg:.2f} across {len(weights)} source(s)"


def _penalties(session: Session, candidate: SignalCandidate, settings: AttentionScoringSettings,
              now: dt.datetime) -> dict[str, float]:
    penalties: dict[str, float] = {}

    # Probable syndication/duplication: many items, almost no independence.
    if candidate.item_count >= 3 and candidate.independent_source_group_count == 1:
        penalties["syndication_duplication"] = -0.15

    # Stale material.
    age_days = (now - candidate.latest_observed_at).total_seconds() / 86400 if candidate.latest_observed_at else 0
    if age_days > settings.staleness_days:
        penalties["stale"] = -0.2

    # Already-seen without meaningful new evidence: seen_at set and no
    # activity since (latest_observed_at <= seen_at).
    if candidate.seen_at and candidate.latest_observed_at and candidate.latest_observed_at <= candidate.seen_at:
        penalties["already_seen_no_new_evidence"] = -0.1

    # Unresolved/noisy entities: high mention count but no resolved
    # canonical entity at all is a weak signal for this candidate's rigor.
    from semi_intel.domain.models import CandidateEntity
    resolved_count = len(list(session.scalars(
        select(CandidateEntity.id).where(CandidateEntity.candidate_id == candidate.id)
    )))
    if resolved_count == 0 and candidate.primary_topic_id is None:
        penalties["no_resolved_entities_or_topic"] = -0.1

    return penalties


def compute_attention_score(session: Session, candidate: SignalCandidate, *, now: Optional[dt.datetime] = None) -> ScoreResult:
    now = now or dt.datetime.utcnow()
    settings = get_scoring_settings(session)
    weights = _normalized_weights(settings)

    raw_values: dict[str, tuple[float, str]] = {
        "topic_relevance": _topic_relevance(session, candidate),
        "novelty": _novelty(session, candidate),
        "momentum": _momentum(session, candidate, settings, now),
        "source_diversity": _source_diversity(candidate),
        "artifact_strength": _artifact_strength(session, candidate),
        "source_quality": _source_quality(session, candidate),
    }

    components = {}
    total = 0.0
    for key, (raw, detail) in raw_values.items():
        weight = weights[key]
        contribution = raw * weight
        components[key] = ComponentExplanation(raw_value=raw, weight=weight, contribution=contribution, detail=detail)
        total += contribution

    penalties = _penalties(session, candidate, settings, now)
    total = max(0.0, min(1.0, total + sum(penalties.values())))

    return ScoreResult(total=total, components=components, penalties=penalties)


def rescore_candidate(session: Session, candidate: SignalCandidate, *, now: Optional[dt.datetime] = None) -> ScoreResult:
    result = compute_attention_score(session, candidate, now=now)
    candidate.attention_score = result.total
    candidate.score_explanation = result.to_json()
    return result


def rescore_active_candidates(session: Session, *, limit: Optional[int] = None) -> int:
    from semi_intel.domain.enums import SignalCandidateState

    stmt = select(SignalCandidate).where(SignalCandidate.state == SignalCandidateState.ACTIVE)
    if limit:
        stmt = stmt.limit(limit)
    count = 0
    for candidate in session.scalars(stmt):
        rescore_candidate(session, candidate)
        count += 1
    session.commit()
    return count
