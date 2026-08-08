"""Turns evidence into claim-link suggestions -- proposals, never facts.

A suggestion is written to claim_link_suggestions with status=pending and
sits there until a human runs `suggest accept` or `suggest reject`. This
module never creates a ClaimEvidenceLink directly; see
repository/repositories.py SuggestionRepository.accept() for the only path
from suggestion to an actual link.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.claim_engine.entity_matcher import find_entity_mentions
from semi_intel.claim_engine.scoring import score_claim
from semi_intel.domain.enums import ClaimStatus
from semi_intel.domain.models import Claim, ClaimEvidenceLink, ClaimLinkSuggestion, Entity, Evidence

MIN_SUGGESTION_SCORE = 0.2


@dataclass
class SuggestionRunResult:
    entities_loaded: int = 0
    open_claims: int = 0
    claims_without_subject_entities: int = 0
    evidence_scanned: int = 0
    pairs_evaluated: int = 0
    pairs_below_threshold: int = 0
    suggestions_created: int = 0
    skipped_existing_pairs: int = 0
    skipped_existing_links: int = 0


class SuggestionService:
    def __init__(self, session: Session):
        self.session = session

    def run(self, evidence_ids: Optional[List[int]] = None) -> SuggestionRunResult:
        result = SuggestionRunResult()

        entities = list(self.session.scalars(select(Entity)))
        open_claims = list(self.session.scalars(select(Claim).where(Claim.status == ClaimStatus.OPEN)))
        result.entities_loaded = len(entities)
        result.open_claims = len(open_claims)
        result.claims_without_subject_entities = sum(
            1 for claim in open_claims if claim.subject_entity_id is None
        )

        evidence_stmt = select(Evidence)
        if evidence_ids is not None:
            evidence_stmt = evidence_stmt.where(Evidence.id.in_(evidence_ids))

        for evidence in self.session.scalars(evidence_stmt):
            result.evidence_scanned += 1
            self._score_one(evidence, entities, open_claims, result)

        self.session.commit()
        return result

    def _score_one(self, evidence: Evidence, entities, open_claims, result: SuggestionRunResult) -> None:
        text = f"{evidence.title}\n{evidence.raw_content}"
        mentions = find_entity_mentions(text, entities)
        mentioned_ids = {m.entity_id for m in mentions}

        for claim in open_claims:
            result.pairs_evaluated += 1
            if self._link_exists(evidence.id, claim.id):
                result.skipped_existing_links += 1
                continue
            if self._suggestion_exists(evidence.id, claim.id):
                result.skipped_existing_pairs += 1
                continue

            outcome = score_claim(claim.statement, claim.subject_entity_id, text, mentioned_ids)
            if outcome.score < MIN_SUGGESTION_SCORE:
                result.pairs_below_threshold += 1
                continue

            self.session.add(
                ClaimLinkSuggestion(
                    evidence_id=evidence.id,
                    claim_id=claim.id,
                    score=outcome.score,
                    reasons=json.dumps(outcome.reasons),
                )
            )
            self.session.flush()
            result.suggestions_created += 1

    def _suggestion_exists(self, evidence_id: int, claim_id: int) -> bool:
        stmt = select(ClaimLinkSuggestion.id).where(
            ClaimLinkSuggestion.evidence_id == evidence_id,
            ClaimLinkSuggestion.claim_id == claim_id,
        )
        return self.session.scalar(stmt) is not None

    def _link_exists(self, evidence_id: int, claim_id: int) -> bool:
        stmt = select(ClaimEvidenceLink.id).where(
            ClaimEvidenceLink.evidence_id == evidence_id,
            ClaimEvidenceLink.claim_id == claim_id,
        )
        return self.session.scalar(stmt) is not None
