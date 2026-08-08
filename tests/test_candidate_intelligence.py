"""Confidence engine, editorial value engine, verification checklist,
timeline stage classifier, and the orchestrator (v1.0.0 Candidate
Intelligence, Phases 5/6/8/11)."""

from __future__ import annotations

import datetime as dt

from semi_intel.domain.enums import SignalCandidateState, SourceType
from semi_intel.domain.models import CandidateSignalItem, SignalCandidate, SignalItem, SignalLabel, Source
from semi_intel.editorial.service import normalize_phrase
from semi_intel.signals.candidate_intelligence import compute_candidate_intelligence
from semi_intel.signals.confidence_engine import compute_confidence
from semi_intel.signals.editorial_value_engine import compute_editorial_value
from semi_intel.signals.timeline_stage import classify_timeline_stage
from semi_intel.signals.verification import generate_verification_checklist

BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


def _source(session, name="Golden Pig"):
    src = Source(name=name, type=SourceType.SOCIAL, provider="rss")
    session.add(src)
    session.commit()
    return src


def _topic(session, name="RDNA 5", priority=0.8):
    from semi_intel.domain.models import MonitoredTopic
    topic = MonitoredTopic(
        name=name, normalized_name=normalize_phrase(name), keyword=name, aliases="[]",
        category="AMD", priority=priority,
    )
    session.add(topic)
    session.commit()
    return topic


def _candidate(session, state=SignalCandidateState.ACTIVE, topic_id=None, first_observed=BASE,
               strongest_artifact_type=None, independent_source_group_count=0, dismissed_reason=None):
    cand = SignalCandidate(
        fingerprint=f"fp-{id(object())}", title="Test", state=state,
        first_observed_at=first_observed, latest_observed_at=first_observed, primary_topic_id=topic_id,
        strongest_artifact_type=strongest_artifact_type,
        independent_source_group_count=independent_source_group_count,
        dismissed_reason=dismissed_reason,
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


# --- confidence engine -------------------------------------------------

def test_confidence_components_sum_to_the_total(db_session):
    source = _source(db_session)
    candidate = _candidate(db_session, independent_source_group_count=2)
    _item(db_session, source, candidate, "1", "16-core design.")

    result, contradictions = compute_confidence(db_session, candidate)

    assert contradictions == []
    expected = sum(c.contribution for c in result.components.values())
    assert abs(result.total - expected) < 1e-9


def test_confidence_penalized_by_contradictions(db_session):
    source_a = _source(db_session, "A")
    source_b = _source(db_session, "B")
    candidate = _candidate(db_session, independent_source_group_count=1)
    _item(db_session, source_a, candidate, "a", "16-core design.")
    _item(db_session, source_b, candidate, "b", "12-core confirmed.")

    result, contradictions = compute_confidence(db_session, candidate)

    assert len(contradictions) == 1
    assert result.penalties["contradictions"] < 0
    assert result.total >= 0.0  # clamped, never negative


def test_confidence_rewards_official_documentation(db_session):
    source = _source(db_session)
    candidate = _candidate(db_session)
    item = _item(db_session, source, candidate, "1", "Official announcement.")
    db_session.add(SignalLabel(signal_item_id=item.id, label="Official Announcement", rule="test"))
    db_session.commit()

    result, _ = compute_confidence(db_session, candidate)

    assert result.components["official_documentation"].raw_value == 1.0


def test_confidence_flags_inverted_lineage_timestamps(db_session):
    source = _source(db_session)
    candidate = _candidate(db_session)
    parent = _item(db_session, source, candidate, "p", "Original.", posted=BASE)
    child = SignalItem(
        source_id=source.id, provider="rss", external_id="c", raw_payload="{}",
        normalized_text="Reply.", content_hash="hc",
        posted_at=BASE - dt.timedelta(hours=1),  # posted BEFORE what it replies to -- impossible
        reply_to_signal_item_id=parent.id,
    )
    db_session.add(child)
    db_session.commit()
    db_session.add(CandidateSignalItem(candidate_id=candidate.id, signal_item_id=child.id))
    db_session.commit()

    result, _ = compute_confidence(db_session, candidate)

    assert result.components["time_consistency"].raw_value < 1.0


# --- editorial value engine ---------------------------------------------

def test_editorial_value_uses_topic_priority(db_session):
    topic = _topic(db_session, priority=0.9)
    source = _source(db_session)
    candidate = _candidate(db_session, topic_id=topic.id)
    _item(db_session, source, candidate, "1", "16-core design.")

    result = compute_editorial_value(db_session, candidate)

    assert result.components["product_importance"].raw_value == 0.9


def test_editorial_value_rewards_first_appearance_novelty(db_session):
    topic = _topic(db_session)
    source = _source(db_session)
    candidate = _candidate(db_session, topic_id=topic.id)
    _item(db_session, source, candidate, "1", "16-core design.")

    result = compute_editorial_value(db_session, candidate)

    assert result.components["novelty"].raw_value == 1.0


def test_editorial_value_freshness_decays_with_age(db_session):
    topic = _topic(db_session)
    source = _source(db_session)
    old_candidate = _candidate(db_session, topic_id=topic.id, first_observed=BASE)
    _item(db_session, source, old_candidate, "1", "16-core design.")

    now = BASE + dt.timedelta(hours=100)  # well past the 72h freshness window
    result = compute_editorial_value(db_session, old_candidate, now=now)

    assert result.components["freshness"].raw_value == 0.0


def test_editorial_value_never_gates_on_confidence():
    """Editorial value and confidence must be independently computable --
    this test asserts the function signature/behavior never requires a
    confidence score as input, matching the spec's explicit requirement
    that a candidate can be low-confidence and high editorial value."""
    import inspect
    sig = inspect.signature(compute_editorial_value)
    assert "confidence" not in sig.parameters


# --- verification checklist ---------------------------------------------

def test_verification_checklist_flags_missing_official_documentation(db_session):
    source = _source(db_session)
    candidate = _candidate(db_session)
    _item(db_session, source, candidate, "1", "16-core design.")

    steps = generate_verification_checklist(db_session, candidate, contradictions=[])

    assert any("OEM" in s.step for s in steps)


def test_verification_checklist_suggests_pci_id_check_for_pci_artifact(db_session):
    source = _source(db_session)
    candidate = _candidate(db_session, strongest_artifact_type="pci_id")
    _item(db_session, source, candidate, "1", "PCI ID observed.")

    steps = generate_verification_checklist(db_session, candidate, contradictions=[])

    assert any("pci.ids" in s.step for s in steps)


def test_verification_checklist_includes_contradiction_resolution_steps(db_session):
    from semi_intel.signals.claims import extract_candidate_claims, detect_contradictions
    source_a = _source(db_session, "A")
    source_b = _source(db_session, "B")
    candidate = _candidate(db_session)
    _item(db_session, source_a, candidate, "a", "16-core design.")
    _item(db_session, source_b, candidate, "b", "12-core confirmed.")
    contradictions = detect_contradictions(extract_candidate_claims(db_session, candidate))

    steps = generate_verification_checklist(db_session, candidate, contradictions=contradictions)

    assert any("core count" in s.step for s in steps)


def test_verification_checklist_reports_no_gaps_when_everything_checks_out(db_session):
    """A candidate with official documentation, a structured artifact,
    strong independent confirmation, and a resolved entity must not
    silently return an empty (ambiguous) list."""
    from semi_intel.domain.models import CandidateEntity, Entity
    from semi_intel.domain.enums import EntityType
    source = _source(db_session)
    candidate = _candidate(db_session, independent_source_group_count=3)
    item = _item(db_session, source, candidate, "1", "Official announcement.")
    db_session.add(SignalLabel(signal_item_id=item.id, label="Official Announcement", rule="test"))
    entity = Entity(type=EntityType.PRODUCT, name="Test Product")
    db_session.add(entity)
    db_session.commit()
    db_session.add(CandidateEntity(candidate_id=candidate.id, entity_id=entity.id))
    db_session.commit()

    steps = generate_verification_checklist(db_session, candidate, contradictions=[])

    assert len(steps) == 1
    assert "No specific verification gaps" in steps[0].step


# --- timeline stage -------------------------------------------------------

def test_stage_rumor_with_single_unconfirmed_group():
    candidate = _candidate_obj(independent_source_group_count=1)
    result = classify_timeline_stage(candidate, official_documentation=False)
    assert result.stage == "rumor"


def test_stage_emerging_with_two_groups():
    candidate = _candidate_obj(independent_source_group_count=2)
    result = classify_timeline_stage(candidate, official_documentation=False)
    assert result.stage == "emerging"


def test_stage_corroborated_with_three_groups():
    candidate = _candidate_obj(independent_source_group_count=3)
    result = classify_timeline_stage(candidate, official_documentation=False)
    assert result.stage == "corroborated"


def test_stage_pre_launch_with_launch_adjacent_artifact():
    candidate = _candidate_obj(strongest_artifact_type="pci_id", primary_topic_id=1, independent_source_group_count=1)
    result = classify_timeline_stage(candidate, official_documentation=False)
    assert result.stage == "pre_launch"


def test_stage_confirmed_with_official_documentation():
    candidate = _candidate_obj(state=SignalCandidateState.ACTIVE)
    result = classify_timeline_stage(candidate, official_documentation=True)
    assert result.stage == "confirmed"


def test_stage_released_when_promoted_and_official():
    candidate = _candidate_obj(state=SignalCandidateState.PROMOTED)
    result = classify_timeline_stage(candidate, official_documentation=True)
    assert result.stage == "released"


def test_stage_disproven_from_dismissed_reason_keyword():
    candidate = _candidate_obj(state=SignalCandidateState.DISMISSED, dismissed_reason="Turned out to be false")
    result = classify_timeline_stage(candidate, official_documentation=False)
    assert result.stage == "disproven"


def test_stage_corrected_from_dismissed_reason_keyword():
    candidate = _candidate_obj(state=SignalCandidateState.DISMISSED, dismissed_reason="Vendor issued a correction")
    result = classify_timeline_stage(candidate, official_documentation=False)
    assert result.stage == "corrected"


def test_generic_dismissal_does_not_get_mislabeled_as_disproven():
    """A dismissal for an unrelated reason (spam, duplicate, out of scope)
    must not be silently relabeled as 'disproven' -- that would be a false
    claim about the evidence, not the operator's actual intent."""
    candidate = _candidate_obj(state=SignalCandidateState.DISMISSED, dismissed_reason="Not relevant to our coverage",
                                independent_source_group_count=1)
    result = classify_timeline_stage(candidate, official_documentation=False)
    assert result.stage == "rumor"  # falls through to evidence-based classification, not disproven


def _candidate_obj(**kwargs):
    """A plain, unpersisted SignalCandidate for pure-function stage tests
    -- classify_timeline_stage() takes no session, no DB needed."""
    defaults = dict(
        fingerprint="fp", title="t", state=SignalCandidateState.ACTIVE,
        first_observed_at=BASE, latest_observed_at=BASE,
        independent_source_group_count=0, strongest_artifact_type=None,
        primary_topic_id=None, dismissed_reason=None,
    )
    defaults.update(kwargs)
    return SignalCandidate(**defaults)


# --- orchestrator ---------------------------------------------------------

def test_orchestrator_persists_scores_onto_the_candidate(db_session):
    source = _source(db_session)
    candidate = _candidate(db_session)
    _item(db_session, source, candidate, "1", "16-core design.")

    result = compute_candidate_intelligence(db_session, candidate)
    db_session.commit()

    assert candidate.confidence_score is not None
    assert candidate.editorial_value_score is not None
    assert candidate.timeline_stage is not None
    assert 0 <= candidate.confidence_score <= 100
    assert 0 <= candidate.editorial_value_score <= 100
    assert result.confidence["score"] == candidate.confidence_score
    assert result.timeline_stage["stage"] == candidate.timeline_stage


def test_orchestrator_does_not_persist_when_persist_false(db_session):
    source = _source(db_session)
    candidate = _candidate(db_session)
    _item(db_session, source, candidate, "1", "16-core design.")

    compute_candidate_intelligence(db_session, candidate, persist=False)

    assert candidate.confidence_score is None
    assert candidate.timeline_stage is None
