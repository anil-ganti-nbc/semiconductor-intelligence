"""Atomic claim extraction, novelty, contradiction detection (v1.0.0
Candidate Intelligence, Phases 3/4/7)."""

from __future__ import annotations

import datetime as dt

from semi_intel.domain.enums import SignalCandidateState, SourceType
from semi_intel.domain.models import CandidateSignalItem, SignalCandidate, SignalItem, Source
from semi_intel.signals.claims import (
    compute_claim_novelty,
    detect_contradictions,
    extract_candidate_claims,
    extract_numeric_claims_from_text,
)

BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


def _source(session, name="Golden Pig"):
    src = Source(name=name, type=SourceType.SOCIAL, provider="rss")
    session.add(src)
    session.commit()
    return src


def _candidate(session, topic_id=None, first_observed=BASE):
    cand = SignalCandidate(
        fingerprint=f"fp-{id(object())}", title="Test", state=SignalCandidateState.ACTIVE,
        first_observed_at=first_observed, latest_observed_at=first_observed, primary_topic_id=topic_id,
    )
    session.add(cand)
    session.commit()
    return cand


def _item(session, source, candidate, ext_id, text, *, posted=BASE):
    item = SignalItem(
        source_id=source.id, provider="rss", external_id=ext_id, raw_payload="{}",
        normalized_text=text, content_hash=f"h-{ext_id}", posted_at=posted,
    )
    session.add(item)
    session.commit()
    session.add(CandidateSignalItem(candidate_id=candidate.id, signal_item_id=item.id))
    session.commit()
    return item


# --- extraction -----------------------------------------------------------

def test_extract_core_count():
    assert extract_numeric_claims_from_text("This chip has 16-core design.") == [("core_count", 16.0)]


def test_extract_memory_size():
    assert extract_numeric_claims_from_text("Ships with 24GB VRAM.") == [("memory_size_gb", 24.0)]


def test_memory_bandwidth_gb_per_s_is_not_misread_as_memory_size():
    assert extract_numeric_claims_from_text("896 GB/s of bandwidth.") == []


def test_extract_clock_speed():
    assert extract_numeric_claims_from_text("Boosts to 2.5GHz.") == [("clock_speed_ghz", 2.5)]


def test_extract_multiple_claims_from_one_text():
    claims = extract_numeric_claims_from_text("16-core GPU, 24GB VRAM, 2.5GHz boost clock.")
    assert set(claims) == {("core_count", 16.0), ("memory_size_gb", 24.0), ("clock_speed_ghz", 2.5)}


def test_extract_candidate_claims_reads_from_all_member_items(db_session):
    source = _source(db_session)
    candidate = _candidate(db_session)
    _item(db_session, source, candidate, "1", "16-core design confirmed.")
    _item(db_session, source, candidate, "2", "Also ships with 24GB VRAM.")

    observations = extract_candidate_claims(db_session, candidate)

    types = {obs.claim_type for obs in observations}
    assert types == {"core_count", "memory_size_gb"}


# --- contradiction detection -----------------------------------------------

def test_no_contradiction_when_all_sources_agree(db_session):
    source_a = _source(db_session, "A")
    source_b = _source(db_session, "B")
    candidate = _candidate(db_session)
    item_a = _item(db_session, source_a, candidate, "a", "16-core design.")
    item_b = _item(db_session, source_b, candidate, "b", "16-core confirmed.")

    observations = extract_candidate_claims(db_session, candidate)
    contradictions = detect_contradictions(observations)

    assert contradictions == []


def test_contradiction_detected_when_sources_disagree(db_session):
    source_a = _source(db_session, "Golden Pig")
    source_b = _source(db_session, "VideoCardz")
    source_c = _source(db_session, "OEM Listing")
    candidate = _candidate(db_session)
    _item(db_session, source_a, candidate, "a", "16-core design.")
    _item(db_session, source_b, candidate, "b", "12-core confirmed.")
    _item(db_session, source_c, candidate, "c", "12-core official spec.")

    observations = extract_candidate_claims(db_session, candidate)
    contradictions = detect_contradictions(observations)

    assert len(contradictions) == 1
    contradiction = contradictions[0]
    assert contradiction.claim_type == "core_count"
    assert contradiction.stronger_value == 12.0  # two independent sources vs one
    assert "12" in contradiction.reason


def test_contradiction_stronger_value_is_none_on_a_tie(db_session):
    source_a = _source(db_session, "A")
    source_b = _source(db_session, "B")
    candidate = _candidate(db_session)
    _item(db_session, source_a, candidate, "a", "16-core design.")
    _item(db_session, source_b, candidate, "b", "12-core confirmed.")

    observations = extract_candidate_claims(db_session, candidate)
    contradictions = detect_contradictions(observations)

    assert contradictions[0].stronger_value is None
    assert "evenly split" in contradictions[0].reason


# --- novelty ----------------------------------------------------------------

def test_novelty_first_appearance_when_no_topic_assigned(db_session):
    source = _source(db_session)
    candidate = _candidate(db_session, topic_id=None)
    _item(db_session, source, candidate, "1", "16-core design.")

    observations = extract_candidate_claims(db_session, candidate)
    findings = compute_claim_novelty(db_session, candidate, observations)

    assert findings == []  # no topic to compare against -- correctly reports nothing, not a fabricated finding


def test_novelty_first_appearance_with_topic_and_no_prior_candidates(db_session, monkeypatch):
    from semi_intel.domain.models import MonitoredTopic
    from semi_intel.editorial.service import normalize_phrase
    topic = MonitoredTopic(name="RDNA 5", normalized_name=normalize_phrase("RDNA 5"), keyword="RDNA 5", aliases="[]", category="AMD", priority=0.8)
    db_session.add(topic)
    db_session.commit()

    source = _source(db_session)
    candidate = _candidate(db_session, topic_id=topic.id)
    _item(db_session, source, candidate, "1", "16-core design.")

    observations = extract_candidate_claims(db_session, candidate)
    findings = compute_claim_novelty(db_session, candidate, observations)

    assert len(findings) == 1
    assert findings[0].status == "first_appearance"
    assert findings[0].claim_type == "core_count"


def test_novelty_repeated_when_prior_candidate_had_the_same_value(db_session):
    from semi_intel.domain.models import MonitoredTopic
    from semi_intel.editorial.service import normalize_phrase
    topic = MonitoredTopic(name="RDNA 5", normalized_name=normalize_phrase("RDNA 5"), keyword="RDNA 5", aliases="[]", category="AMD", priority=0.8)
    db_session.add(topic)
    db_session.commit()
    source = _source(db_session)

    earlier = _candidate(db_session, topic_id=topic.id, first_observed=BASE)
    _item(db_session, source, earlier, "1", "16-core design.")

    later = _candidate(db_session, topic_id=topic.id, first_observed=BASE + dt.timedelta(days=1))
    _item(db_session, source, later, "2", "16-core confirmed again.")

    observations = extract_candidate_claims(db_session, later)
    findings = compute_claim_novelty(db_session, later, observations)

    assert findings[0].status == "repeated"
    assert findings[0].previous_value == 16.0


def test_novelty_updated_when_prior_candidate_had_a_different_value(db_session):
    """The 'launch window updated' example from the spec, but with the
    numeric claim types this pass actually implements."""
    from semi_intel.domain.models import MonitoredTopic
    from semi_intel.editorial.service import normalize_phrase
    topic = MonitoredTopic(name="RDNA 5", normalized_name=normalize_phrase("RDNA 5"), keyword="RDNA 5", aliases="[]", category="AMD", priority=0.8)
    db_session.add(topic)
    db_session.commit()
    source = _source(db_session)

    earlier = _candidate(db_session, topic_id=topic.id, first_observed=BASE)
    _item(db_session, source, earlier, "1", "16-core design.")

    later = _candidate(db_session, topic_id=topic.id, first_observed=BASE + dt.timedelta(days=1))
    _item(db_session, source, later, "2", "Now reportedly 12-core.")

    observations = extract_candidate_claims(db_session, later)
    findings = compute_claim_novelty(db_session, later, observations)

    assert findings[0].status == "updated"
    assert findings[0].previous_value == 16.0
    assert findings[0].new_value == 12.0
    assert "changed from 16" in findings[0].reason
