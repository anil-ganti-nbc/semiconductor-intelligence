"""Deterministic source-accuracy scoring.

A source's evidence "votes" on a claim via its stance. A vote counts as
correct if it matches how the claim was eventually resolved:
supports+confirmed, or contradicts+debunked. Anything else resolved
(supports+debunked, contradicts+confirmed) counts as incorrect.

WEAKENS is directionally ambiguous -- it doesn't assert the claim is true or
false, just that it's less certain -- so it's tracked but excluded from the
accuracy score. OPEN claims have no settled ground truth yet and are
excluded too (tracked as open_claim_count). RETRACTED claims are excluded
from accuracy but counted separately, since a source whose claims keep
getting retracted is itself a signal worth seeing, distinct from being
"wrong."

The per-company breakdown answers "AMD accuracy vs. Intel accuracy vs.
NVIDIA accuracy" from the original brief using nothing but the knowledge
graph M0 already built (a claim's subject entity, walked to its
MANUFACTURED_BY company) -- no new modeling required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence

from semi_intel.domain.enums import ClaimStatus, EvidenceStance


@dataclass(frozen=True)
class LinkOutcome:
    stance: EvidenceStance
    claim_status: ClaimStatus
    company: Optional[str] = None


@dataclass(frozen=True)
class AccuracyBucket:
    total: int = 0
    correct: int = 0
    incorrect: int = 0

    @property
    def accuracy(self) -> Optional[float]:
        return round(self.correct / self.total, 4) if self.total else None

    def with_result(self, is_correct: bool) -> "AccuracyBucket":
        return AccuracyBucket(
            total=self.total + 1,
            correct=self.correct + (1 if is_correct else 0),
            incorrect=self.incorrect + (0 if is_correct else 1),
        )


@dataclass(frozen=True)
class SourceAccuracyReport:
    source_id: int
    source_name: str
    overall: AccuracyBucket
    by_company: Dict[str, AccuracyBucket]
    weakens_count: int
    retracted_claim_count: int
    open_claim_count: int


def _is_correct(stance: EvidenceStance, status: ClaimStatus) -> bool:
    return (stance == EvidenceStance.SUPPORTS and status == ClaimStatus.CONFIRMED) or (
        stance == EvidenceStance.CONTRADICTS and status == ClaimStatus.DEBUNKED
    )


def build_report(source_id: int, source_name: str, outcomes: Sequence[LinkOutcome]) -> SourceAccuracyReport:
    overall = AccuracyBucket()
    by_company: Dict[str, AccuracyBucket] = {}
    weakens_count = 0
    retracted_claim_count = 0
    open_claim_count = 0

    for outcome in outcomes:
        if outcome.claim_status == ClaimStatus.OPEN:
            open_claim_count += 1
            continue
        if outcome.claim_status == ClaimStatus.RETRACTED:
            retracted_claim_count += 1
            continue
        if outcome.stance == EvidenceStance.WEAKENS:
            weakens_count += 1
            continue

        is_correct = _is_correct(outcome.stance, outcome.claim_status)
        overall = overall.with_result(is_correct)
        if outcome.company:
            bucket = by_company.get(outcome.company, AccuracyBucket())
            by_company[outcome.company] = bucket.with_result(is_correct)

    return SourceAccuracyReport(
        source_id=source_id,
        source_name=source_name,
        overall=overall,
        by_company=by_company,
        weakens_count=weakens_count,
        retracted_claim_count=retracted_claim_count,
        open_claim_count=open_claim_count,
    )
