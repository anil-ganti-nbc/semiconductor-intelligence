"""Focused 3.3.9 canonical-entity and claim-match workflow tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from semi_intel.claim_engine.suggestion_service import SuggestionService
from semi_intel.domain.enums import (
    EntityType, EvidenceStance, SignalMentionStatus, SourceType,
)
from semi_intel.domain.models import (
    CandidateEntity, CandidateSignalItem, ClaimEvidenceLink, ClaimLinkSuggestion,
    Entity, Evidence, SignalCandidate, SignalEntityMention, SignalItem, Source,
)
from semi_intel.entities.service import CanonicalEntityService
from semi_intel.repository.repositories import ClaimRepository, SuggestionRepository


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "entity-match.db"
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{db_file}")
    from semi_intel.web.app import create_app
    with TestClient(create_app(mutation_authorizer=lambda _value: True)) as api:
        yield api


def _signal_fixture(session, text="Intel 14A"):
    source = Source(name="Fixture source", type=SourceType.RSS, provider="replay", provider_key="fixture")
    session.add(source); session.flush()
    item = SignalItem(source_id=source.id, provider="replay", external_id="one", raw_payload="{}",
                      normalized_text=f"Report about {text}", title=f"Report about {text}", content_hash="one")
    session.add(item); session.flush()
    mention = SignalEntityMention(signal_item_id=item.id, candidate_text=text,
                                  proposed_entity_type="codename", extractor="test:codename",
                                  confidence=.8, status=SignalMentionStatus.CANDIDATE)
    session.add(mention)
    candidate = SignalCandidate(fingerprint="fixture", title=text)
    session.add(candidate); session.flush()
    session.add(CandidateSignalItem(candidate_id=candidate.id, signal_item_id=item.id))
    session.commit()
    return item, mention, candidate


def test_empty_summary_and_no_automatic_creation(db_session):
    _signal_fixture(db_session)
    service = CanonicalEntityService(db_session)
    summary = service.summary()
    assert summary["canonical_entities"] == 0
    assert summary["unresolved_mentions"] == 1
    service.mention_proposals()
    assert list(db_session.scalars(select(Entity))) == []


def test_entity_creation_serializes_aliases_and_attributes(client):
    response = client.post("/api/entities", json={
        "name": "Intel 14A", "type": "foundry_node", "aliases": ["14A"],
        "attributes": {"owner": "Intel"},
    })
    assert response.status_code == 201, response.text
    assert response.json()["aliases"] == ["14A"]
    assert response.json()["attributes"] == {"owner": "Intel"}
    rows = client.get("/api/entities", params={"search": "14a", "type": "foundry_node"}).json()
    assert [row["name"] for row in rows] == ["Intel 14A"]


def test_proposals_group_exact_normalized_spellings(db_session):
    item, _, _ = _signal_fixture(db_session, "Intel-14A")
    db_session.add(SignalEntityMention(signal_item_id=item.id, candidate_text="Intel 14 A",
        proposed_entity_type="codename", extractor="test:second", confidence=.9,
        status=SignalMentionStatus.CANDIDATE))
    db_session.add(SignalEntityMention(signal_item_id=item.id, candidate_text="Intel 14AB",
        proposed_entity_type="codename", extractor="test:other", confidence=.9,
        status=SignalMentionStatus.CANDIDATE)); db_session.commit()
    result = CanonicalEntityService(db_session).mention_proposals()
    assert result["total"] == 2
    grouped = next(row for row in result["items"] if row["normalized_text"] == "intel 14 a")
    assert grouped["mention_count"] == 2


def test_resolve_group_to_new_entity_syncs_candidate(db_session):
    _, mention, candidate = _signal_fixture(db_session)
    service = CanonicalEntityService(db_session)
    entity = service.create_entity(name="Intel 14A", entity_type=EntityType.FOUNDRY_NODE,
                                   aliases=[], attributes={})
    result = service.resolve_group(candidate_text="Intel 14A", proposed_type="codename",
                                   entity=entity, add_alias=False)
    db_session.commit(); db_session.refresh(mention)
    assert mention.status == SignalMentionStatus.RESOLVED
    assert mention.resolved_entity_id == entity.id
    assert result.candidate_links_created == 1
    assert db_session.scalar(select(CandidateEntity).where(
        CandidateEntity.candidate_id == candidate.id)).entity_id == entity.id


def test_resolve_existing_entity_optionally_adds_alias(db_session):
    _signal_fixture(db_session, "14A")
    entity = Entity(name="Intel 14A", type=EntityType.FOUNDRY_NODE, aliases="[]", attributes="{}")
    db_session.add(entity); db_session.flush()
    CanonicalEntityService(db_session).resolve_group(candidate_text="14A", proposed_type="codename",
        entity=entity, add_alias=True); db_session.commit()
    assert json.loads(entity.aliases) == ["14A"]


def test_reject_and_ignore_only_exact_group(db_session):
    item, mention, _ = _signal_fixture(db_session, "Core")
    other = SignalEntityMention(signal_item_id=item.id, candidate_text="Core Ultra",
        proposed_entity_type="codename", extractor="test", confidence=.8,
        status=SignalMentionStatus.CANDIDATE)
    db_session.add(other); db_session.commit()
    CanonicalEntityService(db_session).reject_group(candidate_text="Core", proposed_type="codename",
        status=SignalMentionStatus.REJECTED); db_session.commit()
    assert mention.status == SignalMentionStatus.REJECTED
    assert other.status == SignalMentionStatus.CANDIDATE


def test_scan_diagnostics_when_empty(client):
    readiness = client.get("/api/suggestions/readiness").json()
    assert readiness == {"canonical_entities": 0, "open_claims": 0,
        "claims_without_subject_entities": 0, "canonical_evidence": 0,
        "existing_evidence_links": 0, "pending_suggestions": 0}
    scan = client.post("/api/suggestions/run", json={}).json()
    assert scan["entities_loaded"] == scan["open_claims"] == scan["evidence_scanned"] == 0
    assert scan["pairs_evaluated"] == 0


def test_scan_skips_existing_real_link(db_session):
    entity = Entity(name="Nova Lake", type=EntityType.PRODUCT, aliases="[]", attributes="{}")
    source = Source(name="Source", type=SourceType.RSS)
    db_session.add_all([entity, source]); db_session.flush()
    claim = ClaimRepository(db_session).create("Nova Lake uses Intel 18A", entity.id)
    evidence = Evidence(source_id=source.id, title="Nova Lake sample", raw_content="Nova Lake uses Intel 18A",
                        content_hash="hash")
    db_session.add(evidence); db_session.flush()
    ClaimRepository(db_session).link_evidence(claim, evidence, EvidenceStance.SUPPORTS)
    db_session.commit()
    result = SuggestionService(db_session).run()
    assert result.skipped_existing_links == 1
    assert result.suggestions_created == 0


def test_stale_duplicate_accept_returns_conflict(client):
    entity = client.post("/api/entities", json={"name":"Nova Lake","type":"product"}).json()
    claim = client.post("/api/claims", json={"statement":"Nova Lake uses 18A","subject_entity_id":entity["id"]}).json()
    client.post("/api/sources", json={"name":"Fixture","type":"rss"})
    evidence = client.post("/api/evidence", json={"source_id":1,"title":"Nova Lake","content":"Nova Lake uses 18A"}).json()
    client.post(f"/api/claims/{claim['id']}/link-evidence", json={"evidence_id":evidence["id"],"stance":"supports"})
    # Simulate a historical stale proposal directly; acceptance must fail cleanly.
    import os
    from semi_intel.db import get_engine, get_sessionmaker
    with get_sessionmaker(get_engine(os.environ["SEMI_INTEL_DB_URL"]))() as session:
        row=ClaimLinkSuggestion(claim_id=claim["id"],evidence_id=evidence["id"],score=.8,reasons="[]")
        session.add(row);session.commit();suggestion_id=row.id
    response=client.post(f"/api/suggestions/{suggestion_id}/accept",json={"stance":"supports"})
    assert response.status_code == 409
    assert "already linked" in response.json()["detail"]


def test_enriched_suggestion_response(client):
    entity=client.post("/api/entities",json={"name":"Nova Lake","type":"product"}).json()
    client.post("/api/sources",json={"name":"VideoCardz","type":"rss"})
    claim=client.post("/api/claims",json={"statement":"Nova Lake uses Intel 18A","subject_entity_id":entity["id"]}).json()
    client.post("/api/evidence",json={"source_id":1,"title":"Nova Lake leak","content":"Nova Lake uses Intel 18A in samples"})
    assert client.post("/api/suggestions/run",json={}).json()["suggestions_created"] == 1
    row=client.get("/api/suggestions",params={"status":"pending","q":"videocardz"}).json()[0]
    assert row["claim_statement"] == "Nova Lake uses Intel 18A"
    assert row["subject_entity_name"] == "Nova Lake"
    assert row["evidence_title"] == "Nova Lake leak"
    assert row["source_name"] == "VideoCardz"


def test_gui_exposes_canonical_workflow_and_searchable_subject_controls():
    html=Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    for required in ("Claim Matches", "Unresolved Radar mentions", "Create canonical entity",
                     "mention-resolution-dialog", "workspace-claim-entity", "radar-claim-entity",
                     "Scan for", "skipped_existing_links"):
        assert required in html
