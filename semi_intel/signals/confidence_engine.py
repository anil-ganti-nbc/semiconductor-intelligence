"""Confidence Engine (v1.0.0 Candidate Intelligence, Phase 5).

Answers "how likely is this candidate's claim to be correct" -- a
different question from `signals/scoring.py`'s attention score ("should a
human look at this at all") and from `services/confidence.py`'s Claim
confidence (factual reliability of one specific, human-authored Claim
statement). Reuses `ComponentExplanation`/`ScoreResult` from
`signals/scoring.py` for the exact same {total, components, penalties}
JSON shape the UI already knows how to render -- not a new format to
learn, just a new set of components under it.

Components (weights sum to 1.0):
  source_authority          0.25  -- avg effective SourceReputation.authority across contributing sources
  independent_confirmations 0.25  -- reuses candidate.independent_source_group_count (independence.py), min(count/3, 1.0)
  official_documentation    0.15  -- 1.0 if any item carries the "Official Announcement" SignalLabel
  structured_identifiers    0.15  -- reuses ARTIFACT_STRENGTH_RANK from scoring.py (pci_id/benchmark/product/...)
  historical_source_accuracy 0.10 -- avg SourceReputation.historical_accuracy where known, else neutral 0.5
  time_consistency           0.10 -- 1.0 minus a penalty for chronologically inverted quote/reply lineage

Penalty: -0.15 per detected within-candidate contradiction (claims.py),
clamped so confidence never goes negative.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.models import CandidateSignalItem, SignalCandidate, SignalItem, SignalLabel, Source, SourceReputation
from semi_intel.signals.claims import Contradiction, detect_contradictions, extract_candidate_claims
from semi_intel.signals.reputation import effective_authority
from semi_intel.signals.scoring import ARTIFACT_STRENGTH_RANK, ComponentExplanation, ScoreResult

WEIGHTS = {
    "source_authority": 0.25, "independent_confirmations": 0.25, "official_documentation": 0.15,
    "structured_identifiers": 0.15, "historical_source_accuracy": 0.10, "time_consistency": 0.10,
}
CONTRADICTION_PENALTY = 0.15


def candidate_item_ids(session: Session, candidate: SignalCandidate) -> list[int]:
    return [row[0] for row in session.execute(
        select(CandidateSignalItem.signal_item_id).where(CandidateSignalItem.candidate_id == candidate.id)
    )]


def _source_authority(session: Session, source_ids: set[int]) -> tuple[float, str]:
    if not source_ids:
        return 0.5, "no contributing sources yet"
    reps = {
        rep.source_id: rep for rep in session.scalars(
            select(SourceReputation).where(SourceReputation.source_id.in_(source_ids))
        )
    }
    values = [effective_authority(reps.get(sid)) for sid in source_ids]
    avg = sum(values) / len(values)
    return avg, f"average authority {avg:.2f} across {len(source_ids)} source(s)"


def _independent_confirmations(candidate: SignalCandidate) -> tuple[float, str]:
    raw = min(candidate.independent_source_group_count / 3.0, 1.0)
    return raw, f"{candidate.independent_source_group_count} independent confirmation group(s)"


def official_documentation_component(session: Session, item_ids: list[int]) -> tuple[float, str]:
    if not item_ids:
        return 0.0, "no evidence yet"
    has_official = session.execute(
        select(SignalLabel.id).where(
            SignalLabel.signal_item_id.in_(item_ids), SignalLabel.label == "Official Announcement",
        ).limit(1)
    ).first()
    return (1.0, "official announcement label present") if has_official else (0.0, "no official documentation found")


def structured_identifiers_component(session: Session, candidate: SignalCandidate) -> tuple[float, str]:
    if not candidate.strongest_artifact_type:
        return 0.0, "no structured artifact identified"
    rank = ARTIFACT_STRENGTH_RANK.get(candidate.strongest_artifact_type, 0.2)
    return rank, f"strongest structured artifact: {candidate.strongest_artifact_type}"


def _historical_source_accuracy(session: Session, source_ids: set[int]) -> tuple[float, str]:
    if not source_ids:
        return 0.5, "no contributing sources yet"
    reps = list(session.scalars(
        select(SourceReputation).where(SourceReputation.source_id.in_(source_ids))
    ))
    known = [r.historical_accuracy for r in reps if r.historical_accuracy is not None]
    if not known:
        return 0.5, "no historical accuracy data yet (neutral default)"
    avg = sum(known) / len(known)
    return avg, f"average historical accuracy {avg:.2f} across {len(known)} source(s) with a track record"


def _time_consistency(session: Session, item_ids: list[int]) -> tuple[float, str]:
    if not item_ids:
        return 1.0, "no evidence yet"
    items = {si.id: si for si in session.scalars(select(SignalItem).where(SignalItem.id.in_(item_ids)))}
    inversions = 0
    for item in items.values():
        parent_id = item.quoted_signal_item_id or item.reply_to_signal_item_id
        parent = items.get(parent_id) if parent_id else None
        if parent and item.posted_at and parent.posted_at and item.posted_at < parent.posted_at:
            inversions += 1
    if not any((si.quoted_signal_item_id or si.reply_to_signal_item_id) for si in items.values()):
        return 1.0, "no quote/reply lineage to check"
    penalty = min(inversions * 0.25, 1.0)
    detail = (
        "all lineage timestamps chronologically consistent" if inversions == 0
        else f"{inversions} item(s) timestamped earlier than what they quote/reply to"
    )
    return 1.0 - penalty, detail


def compute_confidence(session: Session, candidate: SignalCandidate) -> tuple[ScoreResult, list[Contradiction]]:
    item_ids = candidate_item_ids(session, candidate)
    source_ids = {row[0] for row in session.execute(
        select(SignalItem.source_id).where(SignalItem.id.in_(item_ids))
    )} if item_ids else set()

    raw_values = {
        "source_authority": _source_authority(session, source_ids),
        "independent_confirmations": _independent_confirmations(candidate),
        "official_documentation": official_documentation_component(session, item_ids),
        "structured_identifiers": structured_identifiers_component(session, candidate),
        "historical_source_accuracy": _historical_source_accuracy(session, source_ids),
        "time_consistency": _time_consistency(session, item_ids),
    }
    components = {
        name: ComponentExplanation(
            raw_value=raw, weight=WEIGHTS[name], contribution=raw * WEIGHTS[name], detail=detail,
        )
        for name, (raw, detail) in raw_values.items()
    }
    total = sum(c.contribution for c in components.values())

    observations = extract_candidate_claims(session, candidate)
    contradictions = detect_contradictions(observations)
    penalties = {}
    if contradictions:
        penalties["contradictions"] = -min(len(contradictions) * CONTRADICTION_PENALTY, total)
        total += penalties["contradictions"]

    total = max(0.0, min(1.0, total))
    return ScoreResult(total=total, components=components, penalties=penalties), contradictions
