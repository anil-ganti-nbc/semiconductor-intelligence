"""Candidate Intelligence orchestrator (v1.0.0, Phases 1-8 + 11).

Single entry point tying together origin graph, claim extraction, novelty,
confidence, editorial value, contradictions, and verification checklist
for one SignalCandidate. Computed on-demand (same pattern as the existing
`automatic_promotion_eligibility` live recheck in web/app.py), but
confidence/editorial-value/timeline-stage are also written back onto the
candidate row so they're available for list-view sorting without
recomputing every candidate on every list request.

Phase 9 (optional LLM editorial brief) is deliberately not implemented --
per the milestone's own instruction, it may only be built once Phases 1-8
are complete and validated, and this module never calls out to an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from semi_intel.domain.models import SignalCandidate
from semi_intel.signals.claims import (
    compute_claim_novelty,
    detect_contradictions,
    extract_candidate_claims,
)
from semi_intel.signals.confidence_engine import candidate_item_ids, compute_confidence, official_documentation_component
from semi_intel.signals.editorial_value_engine import compute_editorial_value
from semi_intel.signals.origin_graph import build_origin_graph
from semi_intel.signals.timeline_stage import classify_timeline_stage
from semi_intel.signals.verification import generate_verification_checklist


@dataclass
class CandidateIntelligence:
    candidate_id: int
    origin_graph: dict
    claims: list
    novelty: list
    contradictions: list
    confidence: dict
    editorial_value: dict
    verification_checklist: list
    timeline_stage: dict

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "origin_graph": self.origin_graph,
            "claims": self.claims,
            "novelty": self.novelty,
            "contradictions": self.contradictions,
            "confidence": self.confidence,
            "editorial_value": self.editorial_value,
            "verification_checklist": self.verification_checklist,
            "timeline_stage": self.timeline_stage,
        }


def compute_candidate_intelligence(session: Session, candidate: SignalCandidate, *, persist: bool = True) -> CandidateIntelligence:
    origin_graph = build_origin_graph(session, candidate)

    observations = extract_candidate_claims(session, candidate)
    contradictions = detect_contradictions(observations)
    novelty = compute_claim_novelty(session, candidate, observations)

    confidence_result, _ = compute_confidence(session, candidate)
    editorial_value_result = compute_editorial_value(session, candidate)

    item_ids = candidate_item_ids(session, candidate)
    official_raw, _ = official_documentation_component(session, item_ids)
    stage = classify_timeline_stage(candidate, official_documentation=official_raw >= 1.0)

    checklist = generate_verification_checklist(session, candidate, contradictions)

    if persist:
        candidate.confidence_score = round(confidence_result.total * 100, 2)
        candidate.confidence_explanation = confidence_result.to_json()
        candidate.editorial_value_score = round(editorial_value_result.total * 100, 2)
        candidate.editorial_value_explanation = editorial_value_result.to_json()
        candidate.timeline_stage = stage.stage

    import json
    return CandidateIntelligence(
        candidate_id=candidate.id,
        origin_graph=origin_graph.to_dict(),
        claims=[obs.to_dict() for obs in observations],
        novelty=[finding.to_dict() for finding in novelty],
        contradictions=[c.to_dict() for c in contradictions],
        confidence={
            "score": round(confidence_result.total * 100, 2),
            **json.loads(confidence_result.to_json()),
        },
        editorial_value={
            "score": round(editorial_value_result.total * 100, 2),
            **json.loads(editorial_value_result.to_json()),
        },
        verification_checklist=[step.to_dict() for step in checklist],
        timeline_stage=stage.to_dict(),
    )
