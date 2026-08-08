from __future__ import annotations

from semi_intel.domain.enums import ClaimEventType, ClaimStatus, EntityType, EvidenceStance, SourceType
from semi_intel.domain.models import Entity, Evidence, Source
from semi_intel.repository.repositories import (
    ClaimRepository,
    EntityRepository,
    EvidenceRepository,
    RelationshipRepository,
    SourceRepository,
)
from semi_intel.domain.enums import RelationType
from semi_intel.domain.models import Relationship


def test_entity_create_and_find(db_session):
    repo = EntityRepository(db_session)
    e = repo.add(Entity(type=EntityType.PRODUCT, name="Nova Lake"))
    db_session.commit()
    assert repo.find_by_name("Nova Lake").id == e.id


def test_evidence_dedup_by_hash(db_session):
    src = SourceRepository(db_session).add(Source(name="Golden Pig", type=SourceType.SOCIAL))
    db_session.commit()
    ev_repo = EvidenceRepository(db_session)
    ev1 = ev_repo.add(
        Evidence(source_id=src.id, title="t1", raw_content="same content", content_hash="abc123")
    )
    db_session.commit()
    assert ev_repo.find_by_hash("abc123").id == ev1.id


def test_relationship_forms_a_traversable_graph(db_session):
    entity_repo = EntityRepository(db_session)
    nova_lake = entity_repo.add(Entity(type=EntityType.PRODUCT, name="Nova Lake"))
    intel = entity_repo.add(Entity(type=EntityType.COMPANY, name="Intel"))
    db_session.commit()

    rel_repo = RelationshipRepository(db_session)
    rel_repo.add(
        Relationship(from_entity_id=nova_lake.id, to_entity_id=intel.id, relation_type=RelationType.MANUFACTURED_BY)
    )
    db_session.commit()

    rels_from_nova = rel_repo.for_entity(nova_lake.id)
    rels_from_intel = rel_repo.for_entity(intel.id)
    assert len(rels_from_nova) == 1
    assert rels_from_nova[0].id == rels_from_intel[0].id


def test_claim_confidence_updates_when_evidence_linked(db_session):
    src = SourceRepository(db_session).add(Source(name="VideoCardz", type=SourceType.RSS, trust_weight=0.8))
    db_session.commit()
    ev_repo = EvidenceRepository(db_session)
    ev = ev_repo.add(Evidence(source_id=src.id, title="leak", raw_content="24GB VRAM", content_hash="h1"))
    db_session.commit()

    claim_repo = ClaimRepository(db_session)
    claim = claim_repo.create("RTX 5080 Super has 24GB VRAM")
    db_session.commit()
    baseline = claim.confidence

    claim_repo.link_evidence(claim, ev, EvidenceStance.SUPPORTS)
    db_session.commit()

    assert claim.confidence > baseline


def test_claim_events_are_recorded(db_session):
    claim_repo = ClaimRepository(db_session)
    claim = claim_repo.create("Test claim")
    db_session.commit()
    claim_repo.resolve(claim, ClaimStatus.CONFIRMED, note="proven at launch")
    db_session.commit()

    events = claim_repo.events_for(claim)
    event_types = [e.event_type for e in events]
    assert ClaimEventType.CREATED in event_types
    assert ClaimEventType.STATUS_CHANGED in event_types
    assert claim.status == ClaimStatus.CONFIRMED
