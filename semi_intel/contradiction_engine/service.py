"""Wires memory_rules into Claim/ClaimEvent.

Creating a structured memory-spec claim always runs the check immediately
and records the result as a claim event, so the contradiction (or lack of
one) is part of the claim's permanent history -- not something a journalist
has to remember to go check later. The check never changes the claim's
status or confidence; it only surfaces what the rules engine found. Deciding
what a contradiction *means* for the claim (mark it debunked? note it as a
likely typo? keep investigating?) stays a human editorial decision.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

from sqlalchemy.orm import Session

from semi_intel.contradiction_engine.memory_rules import MemoryConfigCheck, check_memory_configuration
from semi_intel.domain.enums import ClaimEventType
from semi_intel.domain.models import Claim, MemorySpecClaim
from semi_intel.repository.repositories import ClaimRepository


class MemorySpecClaimResult(NamedTuple):
    claim: Claim
    spec: MemorySpecClaim
    check: MemoryConfigCheck


class MemorySpecClaimService:
    def __init__(self, session: Session):
        self.session = session
        self.claim_repo = ClaimRepository(session)

    def create(
        self,
        statement: str,
        bus_width_bits: int,
        chip_density_gbit: float,
        claimed_total_gb: float,
        subject_entity_id: Optional[int] = None,
        memory_type: Optional[str] = None,
    ) -> MemorySpecClaimResult:
        claim = self.claim_repo.create(statement, subject_entity_id)
        spec = MemorySpecClaim(
            claim_id=claim.id,
            memory_type=memory_type,
            bus_width_bits=bus_width_bits,
            chip_density_gbit=chip_density_gbit,
            claimed_total_gb=claimed_total_gb,
        )
        self.session.add(spec)
        self.session.flush()

        check = check_memory_configuration(bus_width_bits, chip_density_gbit, claimed_total_gb)
        event_type = (
            ClaimEventType.CONTRADICTION_CHECK_PASSED
            if check.is_consistent
            else ClaimEventType.CONTRADICTION_DETECTED
        )
        self.claim_repo.add_event(claim, event_type, note=check.explanation)

        return MemorySpecClaimResult(claim=claim, spec=spec, check=check)

    def recheck(self, claim_id: int) -> Optional[MemorySpecClaimResult]:
        """Re-run the check against the stored spec without creating anything
        new -- useful after the fact, e.g. to display in `claim memory-spec`."""
        claim = self.claim_repo.get(claim_id)
        if claim is None:
            return None
        spec = self.session.query(MemorySpecClaim).filter_by(claim_id=claim_id).one_or_none()
        if spec is None:
            return None
        check = check_memory_configuration(spec.bus_width_bits, spec.chip_density_gbit, spec.claimed_total_gb)
        return MemorySpecClaimResult(claim=claim, spec=spec, check=check)
