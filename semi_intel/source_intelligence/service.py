"""Gathers ClaimEvidenceLink outcomes for a source from the database and
hands them to scoring.build_report. The only place that walks the knowledge
graph (subject entity -> MANUFACTURED_BY -> company) to resolve a claim's
company for the per-company breakdown.
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import EntityType, RelationType
from semi_intel.domain.models import Claim, ClaimEvidenceLink, Entity, Evidence, Relationship, Source
from semi_intel.source_intelligence.scoring import LinkOutcome, SourceAccuracyReport, build_report


class SourceIntelligenceService:
    def __init__(self, session: Session):
        self.session = session

    def report_for(self, source_id: int) -> Optional[SourceAccuracyReport]:
        source = self.session.get(Source, source_id)
        if source is None:
            return None
        outcomes = self._outcomes_for_source(source_id)
        return build_report(source.id, source.name, outcomes)

    def report_all(self) -> List[SourceAccuracyReport]:
        source_ids = [s.id for s in self.session.scalars(select(Source))]
        reports = [self.report_for(sid) for sid in source_ids]
        return [r for r in reports if r is not None]

    def _outcomes_for_source(self, source_id: int) -> List[LinkOutcome]:
        stmt = (
            select(ClaimEvidenceLink, Claim)
            .join(Evidence, ClaimEvidenceLink.evidence_id == Evidence.id)
            .join(Claim, ClaimEvidenceLink.claim_id == Claim.id)
            .where(Evidence.source_id == source_id)
        )
        outcomes: List[LinkOutcome] = []
        for link, claim in self.session.execute(stmt).all():
            company = self._company_for_entity(claim.subject_entity_id) if claim.subject_entity_id else None
            outcomes.append(LinkOutcome(stance=link.stance, claim_status=claim.status, company=company))
        return outcomes

    def _company_for_entity(self, entity_id: int) -> Optional[str]:
        entity = self.session.get(Entity, entity_id)
        if entity is None:
            return None
        if entity.type == EntityType.COMPANY:
            return entity.name
        rel = self.session.scalar(
            select(Relationship).where(
                Relationship.from_entity_id == entity_id,
                Relationship.relation_type == RelationType.MANUFACTURED_BY,
            )
        )
        if rel is None:
            return None
        company_entity = self.session.get(Entity, rel.to_entity_id)
        if company_entity and company_entity.type == EntityType.COMPANY:
            return company_entity.name
        return None
