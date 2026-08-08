"""Editorial Value Engine (v1.0.0 Candidate Intelligence, Phase 6).

Answers "should an editor care", a distinct question from confidence
("is this true") and attention score ("does this deserve a human's
attention at all"). A candidate can be low-confidence and high editorial
value simultaneously (a huge but unverified rumor) -- this engine never
gates on confidence, and this score alone must never auto-publish or
auto-promote anything (enforced by callers, not by this module).

Components (weights sum to 1.0):
  product_importance   0.25 -- MonitoredTopic.priority (existing field, 0..1)
  novelty               0.20 -- reuses claims.py's compute_claim_novelty(): any "first_appearance" finding scores highest
  officiality           0.15 -- reuses confidence_engine's official-documentation check
  exclusivity           0.15 -- inverse of how many other candidates on the same topic were promoted in the last 30 days
  freshness             0.15 -- linear decay over 72 hours since first_observed_at
  verification_effort   0.10 -- reuses confidence_engine's structured-identifier rank (already-verified evidence needs less editor effort)
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import SignalCandidateState
from semi_intel.domain.models import CandidateSignalItem, MonitoredTopic, SignalCandidate
from semi_intel.signals.claims import compute_claim_novelty, extract_candidate_claims
from semi_intel.signals.confidence_engine import candidate_item_ids, official_documentation_component, structured_identifiers_component
from semi_intel.signals.scoring import ComponentExplanation, ScoreResult

WEIGHTS = {
    "product_importance": 0.25, "novelty": 0.20, "officiality": 0.15,
    "exclusivity": 0.15, "freshness": 0.15, "verification_effort": 0.10,
}
FRESHNESS_WINDOW_HOURS = 72.0
NOVELTY_SCORE_BY_STATUS = {"first_appearance": 1.0, "updated": 0.5, "repeated": 0.0}


def _product_importance(session: Session, candidate: SignalCandidate) -> tuple[float, str]:
    if not candidate.primary_topic_id:
        return 0.0, "no primary topic assigned"
    topic = session.get(MonitoredTopic, candidate.primary_topic_id)
    if not topic:
        return 0.0, "primary topic not found"
    return topic.priority, f"topic {topic.name!r} priority {topic.priority:.2f}"


def _novelty(session: Session, candidate: SignalCandidate) -> tuple[float, str]:
    observations = extract_candidate_claims(session, candidate)
    findings = compute_claim_novelty(session, candidate, observations)
    if not findings:
        return 0.0, "no extractable claims to compare"
    best = max(findings, key=lambda f: NOVELTY_SCORE_BY_STATUS.get(f.status, 0.0))
    return NOVELTY_SCORE_BY_STATUS.get(best.status, 0.0), best.reason


def _exclusivity(session: Session, candidate: SignalCandidate, now: dt.datetime) -> tuple[float, str]:
    if not candidate.primary_topic_id:
        return 0.5, "no primary topic to compare coverage against"
    window_start = now - dt.timedelta(days=30)
    count = session.execute(
        select(SignalCandidate.id).where(
            SignalCandidate.primary_topic_id == candidate.primary_topic_id,
            SignalCandidate.id != candidate.id,
            SignalCandidate.state == SignalCandidateState.PROMOTED,
            SignalCandidate.latest_observed_at >= window_start,
        )
    ).all()
    n = len(count)
    return max(0.0, 1.0 - min(n / 5.0, 1.0)), f"{n} other candidate(s) on this topic promoted in the last 30 days"


def _freshness(candidate: SignalCandidate, now: dt.datetime) -> tuple[float, str]:
    age_hours = max((now - candidate.first_observed_at).total_seconds() / 3600.0, 0.0)
    raw = max(0.0, 1.0 - age_hours / FRESHNESS_WINDOW_HOURS)
    return raw, f"first observed {age_hours:.1f}h ago"


def compute_editorial_value(session: Session, candidate: SignalCandidate, now: dt.datetime | None = None) -> ScoreResult:
    now = now or dt.datetime.utcnow()
    item_ids = candidate_item_ids(session, candidate)

    raw_values = {
        "product_importance": _product_importance(session, candidate),
        "novelty": _novelty(session, candidate),
        "officiality": official_documentation_component(session, item_ids),
        "exclusivity": _exclusivity(session, candidate, now),
        "freshness": _freshness(candidate, now),
        "verification_effort": structured_identifiers_component(session, candidate),
    }
    components = {
        name: ComponentExplanation(
            raw_value=raw, weight=WEIGHTS[name], contribution=raw * WEIGHTS[name], detail=detail,
        )
        for name, (raw, detail) in raw_values.items()
    }
    total = max(0.0, min(1.0, sum(c.contribution for c in components.values())))
    return ScoreResult(total=total, components=components, penalties={})
