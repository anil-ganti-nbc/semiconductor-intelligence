"""Domain-specific repositories.

ClaimRepository is where the "claim engine" bookkeeping lives: linking
evidence recomputes confidence via services.confidence and appends to the
claim's event log, so every state change is both scored and recorded.

SuggestionRepository is the *only* place a ClaimLinkSuggestion turns into a
real ClaimEvidenceLink (via accept()) -- it always requires a human-supplied
stance, reusing ClaimRepository.link_evidence so accepted suggestions go
through the exact same confidence-recompute/event-log path as manual links.
"""

from __future__ import annotations

import datetime as dt
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import ClaimEventType, ClaimStatus, EvidenceStance, SuggestionStatus
from semi_intel.domain.models import (
    Claim,
    ClaimEvent,
    ClaimEvidenceLink,
    ClaimLinkSuggestion,
    Entity,
    Evidence,
    Relationship,
    Source,
)
from semi_intel.repository.base import Repository
from semi_intel.services.confidence import compute_confidence


class EntityRepository(Repository[Entity]):
    model = Entity

    def find_by_name(self, name: str) -> Optional[Entity]:
        return self.session.scalar(select(Entity).where(Entity.name == name))


class RelationshipRepository(Repository[Relationship]):
    model = Relationship

    def for_entity(self, entity_id: int) -> List[Relationship]:
        stmt = select(Relationship).where(
            (Relationship.from_entity_id == entity_id) | (Relationship.to_entity_id == entity_id)
        )
        return list(self.session.scalars(stmt))


class SourceRepository(Repository[Source]):
    model = Source

    def find_by_name(self, name: str) -> Optional[Source]:
        return self.session.scalar(select(Source).where(Source.name == name))


class EvidenceRepository(Repository[Evidence]):
    model = Evidence

    def find_by_hash(self, content_hash: str) -> Optional[Evidence]:
        return self.session.scalar(select(Evidence).where(Evidence.content_hash == content_hash))

    def for_entity(self, entity_id: int) -> List[Evidence]:
        return list(self.session.scalars(select(Evidence).where(Evidence.entity_id == entity_id)))


class ClaimRepository(Repository[Claim]):
    model = Claim

    def create(self, statement: str, subject_entity_id: Optional[int] = None) -> Claim:
        claim = Claim(statement=statement, subject_entity_id=subject_entity_id)
        self.add(claim)
        self._log_event(claim, ClaimEventType.CREATED, confidence_after=claim.confidence)
        return claim

    def link_evidence(
        self,
        claim: Claim,
        evidence: Evidence,
        stance: EvidenceStance,
        note: Optional[str] = None,
    ) -> ClaimEvidenceLink:
        link = ClaimEvidenceLink(claim_id=claim.id, evidence_id=evidence.id, stance=stance, note=note)
        self.add(link)
        self._recompute_confidence(claim)
        self._log_event(claim, ClaimEventType.EVIDENCE_LINKED, confidence_after=claim.confidence, note=note)
        return link

    def update_evidence_link(
        self,
        claim: Claim,
        evidence: Evidence,
        stance: EvidenceStance,
        note: Optional[str] = None,
    ) -> ClaimEvidenceLink:
        link = self.session.execute(
            select(ClaimEvidenceLink).where(
                ClaimEvidenceLink.claim_id == claim.id,
                ClaimEvidenceLink.evidence_id == evidence.id,
            )
        ).scalar_one_or_none()
        if link is None:
            raise ValueError("Evidence is not linked to this claim")
        link.stance = stance
        link.note = note
        self.session.flush()
        self._recompute_confidence(claim)
        self._log_event(
            claim,
            ClaimEventType.CONFIDENCE_UPDATED,
            confidence_after=claim.confidence,
            note=note or f"Evidence #{evidence.id} stance changed to {stance.value}",
        )
        return link

    def unlink_evidence(self, claim: Claim, evidence: Evidence) -> None:
        link = self.session.execute(
            select(ClaimEvidenceLink).where(
                ClaimEvidenceLink.claim_id == claim.id,
                ClaimEvidenceLink.evidence_id == evidence.id,
            )
        ).scalar_one_or_none()
        if link is None:
            raise ValueError("Evidence is not linked to this claim")
        self.session.delete(link)
        self.session.flush()
        self._recompute_confidence(claim)
        self._log_event(
            claim,
            ClaimEventType.EVIDENCE_UNLINKED,
            confidence_after=claim.confidence,
            note=f"Unlinked evidence #{evidence.id}",
        )

    def links_for(self, claim: Claim) -> List[ClaimEvidenceLink]:
        stmt = select(ClaimEvidenceLink).where(ClaimEvidenceLink.claim_id == claim.id)
        return list(self.session.scalars(stmt))

    def events_for(self, claim: Claim) -> List[ClaimEvent]:
        stmt = (
            select(ClaimEvent)
            .where(ClaimEvent.claim_id == claim.id)
            .order_by(ClaimEvent.created_at, ClaimEvent.id)
        )
        return list(self.session.scalars(stmt))

    def resolve(self, claim: Claim, status: ClaimStatus, note: Optional[str] = None) -> Claim:
        claim.status = status
        claim.resolution_note = note
        claim.resolved_at = dt.datetime.utcnow()
        self.session.flush()
        self._log_event(claim, ClaimEventType.STATUS_CHANGED, confidence_after=claim.confidence, note=note)
        return claim

    def _recompute_confidence(self, claim: Claim) -> None:
        links = self.links_for(claim)
        stances = []
        for link in links:
            evidence = self.session.get(Evidence, link.evidence_id)
            source = self.session.get(Source, evidence.source_id) if evidence else None
            trust = source.trust_weight if source else 0.5
            stances.append((link.stance, evidence.source_id if evidence else None, trust))
        claim.confidence = compute_confidence(stances)
        self.session.flush()

    def add_event(self, claim: Claim, event_type: ClaimEventType, note: Optional[str] = None) -> ClaimEvent:
        """Public entry point for external callers (e.g. contradiction_engine)
        that want to append to a claim's timeline without going through
        link_evidence/resolve. Uses the claim's current confidence -- this
        never changes confidence itself, only records history."""
        return self._log_event(claim, event_type, confidence_after=claim.confidence, note=note)

    def _log_event(
        self,
        claim: Claim,
        event_type: ClaimEventType,
        confidence_after: Optional[float],
        note: Optional[str] = None,
    ) -> ClaimEvent:
        event = ClaimEvent(claim_id=claim.id, event_type=event_type, confidence_after=confidence_after, note=note)
        self.session.add(event)
        self.session.flush()
        return event


class SuggestionRepository(Repository[ClaimLinkSuggestion]):
    model = ClaimLinkSuggestion

    def list_by_status(self, status: Optional[SuggestionStatus] = None) -> List[ClaimLinkSuggestion]:
        stmt = select(ClaimLinkSuggestion)
        if status is not None:
            stmt = stmt.where(ClaimLinkSuggestion.status == status)
        stmt = stmt.order_by(ClaimLinkSuggestion.score.desc(), ClaimLinkSuggestion.id)
        return list(self.session.scalars(stmt))

    def accept(
        self,
        suggestion: ClaimLinkSuggestion,
        stance: EvidenceStance,
        note: Optional[str] = None,
    ) -> ClaimEvidenceLink:
        """The only path from a suggestion to a real link. Reuses
        ClaimRepository.link_evidence so accepted suggestions go through the
        same confidence recompute and event log as a manually-linked claim."""
        claim_repo = ClaimRepository(self.session)
        claim = claim_repo.get(suggestion.claim_id)
        evidence = self.session.get(Evidence, suggestion.evidence_id)
        if claim is None or evidence is None:
            raise ValueError("Suggestion references a missing claim or evidence row.")
        existing_link = self.session.scalar(select(ClaimEvidenceLink.id).where(
            ClaimEvidenceLink.claim_id == claim.id,
            ClaimEvidenceLink.evidence_id == evidence.id,
        ))
        if existing_link is not None:
            raise ValueError("That evidence is already linked to this claim.")
        link = claim_repo.link_evidence(claim, evidence, stance, note)

        suggestion.status = SuggestionStatus.ACCEPTED
        suggestion.resolved_at = dt.datetime.utcnow()
        suggestion.resolved_note = note
        self.session.flush()
        return link

    def reject(self, suggestion: ClaimLinkSuggestion, note: Optional[str] = None) -> ClaimLinkSuggestion:
        suggestion.status = SuggestionStatus.REJECTED
        suggestion.resolved_at = dt.datetime.utcnow()
        suggestion.resolved_note = note
        self.session.flush()
        return suggestion
