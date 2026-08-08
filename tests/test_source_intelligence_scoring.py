"""Pure unit tests for source accuracy scoring -- no database."""

from __future__ import annotations

from semi_intel.domain.enums import ClaimStatus, EvidenceStance
from semi_intel.source_intelligence.scoring import LinkOutcome, build_report


def test_supports_confirmed_is_correct():
    report = build_report(1, "Golden Pig", [LinkOutcome(EvidenceStance.SUPPORTS, ClaimStatus.CONFIRMED)])
    assert report.overall.correct == 1
    assert report.overall.total == 1
    assert report.overall.accuracy == 1.0


def test_supports_debunked_is_incorrect():
    report = build_report(1, "Golden Pig", [LinkOutcome(EvidenceStance.SUPPORTS, ClaimStatus.DEBUNKED)])
    assert report.overall.incorrect == 1
    assert report.overall.accuracy == 0.0


def test_contradicts_debunked_is_correct():
    report = build_report(1, "Golden Pig", [LinkOutcome(EvidenceStance.CONTRADICTS, ClaimStatus.DEBUNKED)])
    assert report.overall.correct == 1


def test_contradicts_confirmed_is_incorrect():
    report = build_report(1, "Golden Pig", [LinkOutcome(EvidenceStance.CONTRADICTS, ClaimStatus.CONFIRMED)])
    assert report.overall.incorrect == 1


def test_weakens_is_tracked_but_excluded_from_accuracy():
    report = build_report(1, "Golden Pig", [LinkOutcome(EvidenceStance.WEAKENS, ClaimStatus.CONFIRMED)])
    assert report.overall.total == 0
    assert report.weakens_count == 1


def test_open_claims_are_tracked_but_excluded():
    report = build_report(1, "Golden Pig", [LinkOutcome(EvidenceStance.SUPPORTS, ClaimStatus.OPEN)])
    assert report.overall.total == 0
    assert report.open_claim_count == 1


def test_retracted_claims_are_tracked_but_excluded():
    report = build_report(1, "Golden Pig", [LinkOutcome(EvidenceStance.SUPPORTS, ClaimStatus.RETRACTED)])
    assert report.overall.total == 0
    assert report.retracted_claim_count == 1


def test_accuracy_is_none_with_no_track_record():
    report = build_report(1, "Golden Pig", [])
    assert report.overall.accuracy is None


def test_by_company_breakdown():
    outcomes = [
        LinkOutcome(EvidenceStance.SUPPORTS, ClaimStatus.CONFIRMED, company="Intel"),
        LinkOutcome(EvidenceStance.SUPPORTS, ClaimStatus.DEBUNKED, company="Intel"),
        LinkOutcome(EvidenceStance.SUPPORTS, ClaimStatus.CONFIRMED, company="AMD"),
    ]
    report = build_report(1, "Golden Pig", outcomes)
    assert report.overall.total == 3
    assert report.by_company["Intel"].total == 2
    assert report.by_company["Intel"].correct == 1
    assert report.by_company["AMD"].total == 1
    assert report.by_company["AMD"].correct == 1


def test_outcomes_without_a_company_do_not_pollute_by_company():
    report = build_report(1, "Golden Pig", [LinkOutcome(EvidenceStance.SUPPORTS, ClaimStatus.CONFIRMED, company=None)])
    assert report.overall.total == 1
    assert report.by_company == {}
