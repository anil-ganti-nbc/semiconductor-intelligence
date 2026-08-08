from __future__ import annotations

from semi_intel.domain.enums import EvidenceStance
from semi_intel.services.confidence import BASELINE, MAX_CONFIDENCE, MIN_CONFIDENCE, compute_confidence


def test_no_evidence_returns_baseline():
    assert compute_confidence([]) == BASELINE


def test_single_strong_support_raises_confidence():
    score = compute_confidence([(EvidenceStance.SUPPORTS, 1, 1.0)])
    assert score > BASELINE


def test_contradiction_lowers_confidence_more_than_weaken():
    weakened = compute_confidence([(EvidenceStance.WEAKENS, 1, 1.0)])
    contradicted = compute_confidence([(EvidenceStance.CONTRADICTS, 1, 1.0)])
    assert contradicted < weakened < BASELINE


def test_distinct_sources_earn_a_corroboration_bonus():
    two_sources = compute_confidence(
        [(EvidenceStance.SUPPORTS, 1, 0.6), (EvidenceStance.SUPPORTS, 2, 0.6)]
    )
    one_source_twice = compute_confidence(
        [(EvidenceStance.SUPPORTS, 1, 0.6), (EvidenceStance.SUPPORTS, 1, 0.6)]
    )
    assert two_sources > one_source_twice


def test_confidence_is_clamped():
    huge_support = compute_confidence([(EvidenceStance.SUPPORTS, i, 1.0) for i in range(50)])
    huge_contradict = compute_confidence([(EvidenceStance.CONTRADICTS, i, 1.0) for i in range(50)])
    assert huge_support <= MAX_CONFIDENCE
    assert huge_contradict >= MIN_CONFIDENCE


def test_untrusted_source_moves_confidence_less_than_trusted_source():
    low_trust = compute_confidence([(EvidenceStance.SUPPORTS, 1, 0.1)])
    high_trust = compute_confidence([(EvidenceStance.SUPPORTS, 1, 0.9)])
    assert BASELINE < low_trust < high_trust
