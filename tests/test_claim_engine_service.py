"""SuggestionService end to end against a real (temp-file sqlite) database:
idempotency, OPEN-claims-only filtering, and accept/reject via the repos."""

from __future__ import annotations

from semi_intel.claim_engine.suggestion_service import SuggestionService
from semi_intel.domain.enums import ClaimStatus, EntityType, EvidenceStance, SourceType, SuggestionStatus
from semi_intel.domain.models import Entity, Evidence, Source
from semi_intel.repository.repositories import (
    ClaimRepository,
    EntityRepository,
    EvidenceRepository,
    SourceRepository,
    SuggestionRepository,
)


def _setup_nova_lake_claim_and_matching_evidence(db_session):
    entity_repo = EntityRepository(db_session)
    nova_lake = entity_repo.add(Entity(type=EntityType.PRODUCT, name="Nova Lake"))
    db_session.commit()

    claim = ClaimRepository(db_session).create("Nova Lake uses Intel 18A-P", subject_entity_id=nova_lake.id)
    db_session.commit()

    src = SourceRepository(db_session).add(Source(name="VideoCardz", type=SourceType.RSS, trust_weight=0.8))
    db_session.commit()
    evidence = EvidenceRepository(db_session).add(
        Evidence(
            source_id=src.id,
            title="Leak: Nova Lake spotted",
            raw_content="New leak shows Nova Lake using Intel's 18A-P node in samples",
            content_hash="hash1",
        )
    )
    db_session.commit()
    return nova_lake, claim, evidence


def test_run_creates_suggestion_for_matching_evidence(db_session):
    _, claim, evidence = _setup_nova_lake_claim_and_matching_evidence(db_session)

    result = SuggestionService(db_session).run()

    assert result.evidence_scanned == 1
    assert result.suggestions_created == 1

    suggestions = SuggestionRepository(db_session).list_by_status()
    assert len(suggestions) == 1
    assert suggestions[0].evidence_id == evidence.id
    assert suggestions[0].claim_id == claim.id
    assert suggestions[0].status == SuggestionStatus.PENDING
    assert suggestions[0].score > 0.6


def test_rerun_does_not_duplicate_suggestions(db_session):
    _setup_nova_lake_claim_and_matching_evidence(db_session)
    service = SuggestionService(db_session)

    first = service.run()
    second = service.run()

    assert first.suggestions_created == 1
    assert second.suggestions_created == 0
    assert second.skipped_existing_pairs == 1
    assert len(SuggestionRepository(db_session).list_by_status()) == 1


def test_unrelated_evidence_produces_no_suggestion(db_session):
    _setup_nova_lake_claim_and_matching_evidence(db_session)
    src = SourceRepository(db_session).find_by_name("VideoCardz")
    EvidenceRepository(db_session).add(
        Evidence(
            source_id=src.id,
            title="Local football team wins championship",
            raw_content="The city celebrated a historic championship victory last night",
            content_hash="hash2",
        )
    )
    db_session.commit()

    result = SuggestionService(db_session).run()

    # only the Nova Lake evidence should have produced a suggestion
    assert result.suggestions_created == 1


def test_resolved_claims_are_not_suggested_against(db_session):
    _, claim, evidence = _setup_nova_lake_claim_and_matching_evidence(db_session)
    claim_repo = ClaimRepository(db_session)
    claim_repo.resolve(claim, ClaimStatus.CONFIRMED, note="launched")
    db_session.commit()

    result = SuggestionService(db_session).run()

    assert result.suggestions_created == 0


def test_accept_creates_real_link_and_updates_confidence(db_session):
    _, claim, evidence = _setup_nova_lake_claim_and_matching_evidence(db_session)
    SuggestionService(db_session).run()
    suggestion = SuggestionRepository(db_session).list_by_status(SuggestionStatus.PENDING)[0]
    baseline_confidence = claim.confidence

    SuggestionRepository(db_session).accept(suggestion, EvidenceStance.SUPPORTS, note="looks solid")
    db_session.commit()

    assert suggestion.status == SuggestionStatus.ACCEPTED
    assert claim.confidence > baseline_confidence
    links = ClaimRepository(db_session).links_for(claim)
    assert len(links) == 1
    assert links[0].evidence_id == evidence.id


def test_reject_leaves_claim_untouched(db_session):
    _, claim, _ = _setup_nova_lake_claim_and_matching_evidence(db_session)
    SuggestionService(db_session).run()
    suggestion = SuggestionRepository(db_session).list_by_status(SuggestionStatus.PENDING)[0]
    baseline_confidence = claim.confidence

    SuggestionRepository(db_session).reject(suggestion, note="not actually relevant")
    db_session.commit()

    assert suggestion.status == SuggestionStatus.REJECTED
    assert claim.confidence == baseline_confidence
    assert ClaimRepository(db_session).links_for(claim) == []
