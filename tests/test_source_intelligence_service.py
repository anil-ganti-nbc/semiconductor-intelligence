"""SourceIntelligenceService against a real (temp-file sqlite) database,
including the per-company breakdown walked through the knowledge graph."""

from __future__ import annotations

from semi_intel.domain.enums import ClaimStatus, EntityType, EvidenceStance, RelationType, SourceType
from semi_intel.domain.models import Entity, Evidence, Relationship, Source
from semi_intel.repository.repositories import ClaimRepository, EntityRepository, EvidenceRepository
from semi_intel.source_intelligence.service import SourceIntelligenceService


def _build_graph(db_session):
    entity_repo = EntityRepository(db_session)
    intel = entity_repo.add(Entity(type=EntityType.COMPANY, name="Intel"))
    nova_lake = entity_repo.add(Entity(type=EntityType.PRODUCT, name="Nova Lake"))
    db_session.commit()
    db_session.add(Relationship(from_entity_id=nova_lake.id, to_entity_id=intel.id, relation_type=RelationType.MANUFACTURED_BY))
    db_session.commit()
    return intel, nova_lake


def test_report_for_unknown_source_is_none(db_session):
    assert SourceIntelligenceService(db_session).report_for(999) is None


def test_report_for_computes_accuracy_and_company_breakdown(db_session):
    intel, nova_lake = _build_graph(db_session)
    source = Source(name="Golden Pig", type=SourceType.SOCIAL, trust_weight=0.7)
    db_session.add(source)
    db_session.commit()

    claim_repo = ClaimRepository(db_session)
    ev_repo = EvidenceRepository(db_session)

    # one claim golden pig correctly supported (confirmed)
    claim1 = claim_repo.create("Nova Lake uses 18A-P", subject_entity_id=nova_lake.id)
    ev1 = ev_repo.add(Evidence(source_id=source.id, title="t1", raw_content="c1", content_hash="h1"))
    db_session.commit()
    claim_repo.link_evidence(claim1, ev1, EvidenceStance.SUPPORTS)
    claim_repo.resolve(claim1, ClaimStatus.CONFIRMED)
    db_session.commit()

    # one claim golden pig incorrectly supported (debunked)
    claim2 = claim_repo.create("Nova Lake has 64 cores", subject_entity_id=nova_lake.id)
    ev2 = ev_repo.add(Evidence(source_id=source.id, title="t2", raw_content="c2", content_hash="h2"))
    db_session.commit()
    claim_repo.link_evidence(claim2, ev2, EvidenceStance.SUPPORTS)
    claim_repo.resolve(claim2, ClaimStatus.DEBUNKED)
    db_session.commit()

    # one still-open claim (should not affect accuracy)
    claim3 = claim_repo.create("Nova Lake launches in 2027", subject_entity_id=nova_lake.id)
    ev3 = ev_repo.add(Evidence(source_id=source.id, title="t3", raw_content="c3", content_hash="h3"))
    db_session.commit()
    claim_repo.link_evidence(claim3, ev3, EvidenceStance.SUPPORTS)
    db_session.commit()

    report = SourceIntelligenceService(db_session).report_for(source.id)

    assert report.overall.total == 2
    assert report.overall.correct == 1
    assert report.overall.accuracy == 0.5
    assert report.open_claim_count == 1
    assert report.by_company["Intel"].total == 2
    assert report.by_company["Intel"].correct == 1


def test_report_all_returns_every_source(db_session):
    db_session.add(Source(name="Source A", type=SourceType.RSS))
    db_session.add(Source(name="Source B", type=SourceType.FORUM))
    db_session.commit()

    reports = SourceIntelligenceService(db_session).report_all()
    assert {r.source_name for r in reports} == {"Source A", "Source B"}
    assert all(r.overall.accuracy is None for r in reports)
