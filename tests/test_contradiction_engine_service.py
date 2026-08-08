"""MemorySpecClaimService against a real (temp-file sqlite) database: the
check always runs at creation, is recorded in the claim's event log, and
never touches confidence or status either way."""

from __future__ import annotations

from semi_intel.contradiction_engine.service import MemorySpecClaimService
from semi_intel.domain.enums import ClaimEventType, ClaimStatus
from semi_intel.repository.repositories import ClaimRepository


def test_create_with_contradiction_logs_event_but_does_not_change_status_or_confidence(db_session):
    service = MemorySpecClaimService(db_session)
    result = service.create(
        statement="Leaked slides: 384-bit bus, 16GB VRAM, 16Gbit chips",
        bus_width_bits=384,
        chip_density_gbit=16,
        claimed_total_gb=16,
    )
    db_session.commit()

    assert result.check.is_consistent is False
    assert result.claim.status == ClaimStatus.OPEN
    baseline_confidence = result.claim.confidence

    events = ClaimRepository(db_session).events_for(result.claim)
    event_types = [e.event_type for e in events]
    assert ClaimEventType.CONTRADICTION_DETECTED in event_types
    contradiction_event = next(e for e in events if e.event_type == ClaimEventType.CONTRADICTION_DETECTED)
    assert "24" in contradiction_event.note  # the valid standard total

    # confidence must be untouched by the contradiction check itself
    assert result.claim.confidence == baseline_confidence


def test_create_with_consistent_spec_logs_passed_event(db_session):
    service = MemorySpecClaimService(db_session)
    result = service.create(
        statement="RTX 4090-style: 384-bit, 24GB, 16Gbit chips",
        bus_width_bits=384,
        chip_density_gbit=16,
        claimed_total_gb=24,
    )
    db_session.commit()

    assert result.check.is_consistent is True
    events = ClaimRepository(db_session).events_for(result.claim)
    event_types = [e.event_type for e in events]
    assert ClaimEventType.CONTRADICTION_CHECK_PASSED in event_types
    assert ClaimEventType.CONTRADICTION_DETECTED not in event_types


def test_recheck_reads_back_the_stored_spec(db_session):
    service = MemorySpecClaimService(db_session)
    created = service.create(
        statement="test",
        bus_width_bits=256,
        chip_density_gbit=8,
        claimed_total_gb=8,
    )
    db_session.commit()

    rechecked = service.recheck(created.claim.id)
    assert rechecked is not None
    assert rechecked.check.is_consistent == created.check.is_consistent
    assert rechecked.spec.id == created.spec.id


def test_recheck_returns_none_for_claim_without_a_spec(db_session):
    claim = ClaimRepository(db_session).create("A plain claim with no structured spec")
    db_session.commit()

    service = MemorySpecClaimService(db_session)
    assert service.recheck(claim.id) is None
