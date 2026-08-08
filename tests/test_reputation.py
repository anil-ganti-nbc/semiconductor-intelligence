"""Source Reputation (v1.0.0 Candidate Intelligence, Phase 1)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from semi_intel.domain.enums import SignalCandidateState, SourceType
from semi_intel.domain.models import (
    CandidateSignalItem,
    SignalCandidate,
    SignalIndependenceGroup,
    SignalIndependenceGroupMember,
    SignalItem,
    Source,
    SourceReputation,
)
from semi_intel.signals.reputation import effective_authority, recompute_source_reputation

BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


def _source(session, name="Golden Pig"):
    src = Source(name=name, type=SourceType.SOCIAL, provider="rss")
    session.add(src)
    session.commit()
    return src


def _candidate(session, state=SignalCandidateState.ACTIVE, topic_id=None):
    cand = SignalCandidate(
        fingerprint=f"fp-{id(object())}", title="Test candidate", state=state,
        first_observed_at=BASE, latest_observed_at=BASE, primary_topic_id=topic_id,
    )
    session.add(cand)
    session.commit()
    return cand


def _item(session, source, candidate, ext_id, *, posted=BASE):
    item = SignalItem(
        source_id=source.id, provider="rss", external_id=ext_id, raw_payload="{}",
        normalized_text="text", content_hash=f"h-{ext_id}", posted_at=posted,
    )
    session.add(item)
    session.commit()
    session.add(CandidateSignalItem(candidate_id=candidate.id, signal_item_id=item.id))
    session.commit()
    return item


def _make_origin_group(session, candidate, origin_item, other_items=(), reason="same_url"):
    group = SignalIndependenceGroup(candidate_id=candidate.id, origin_signal_item_id=origin_item.id, reason=reason)
    session.add(group)
    session.commit()
    session.add(SignalIndependenceGroupMember(group_id=group.id, signal_item_id=origin_item.id))
    for other in other_items:
        session.add(SignalIndependenceGroupMember(group_id=group.id, signal_item_id=other.id))
    session.commit()
    return group


def test_source_with_no_history_gets_neutral_authority(db_session):
    source = _source(db_session)

    rep = recompute_source_reputation(db_session, source.id)
    db_session.commit()

    assert rep.authority == 0.5
    assert rep.items_contributed == 0
    assert rep.editorial_yield == 0.0
    assert rep.noise_rate == 0.0


def test_originating_a_promoted_candidate_increases_verification_count_and_authority(db_session):
    source = _source(db_session)
    candidate = _candidate(db_session, state=SignalCandidateState.PROMOTED)
    origin_item = _item(db_session, source, candidate, "1")
    _make_origin_group(db_session, candidate, origin_item)

    rep = recompute_source_reputation(db_session, source.id)
    db_session.commit()

    assert rep.verification_count == 1
    assert rep.false_positive_count == 0
    assert rep.editorial_yield == 1.0
    assert rep.originality == 1.0  # originated 1 of 1 groups it appeared in
    assert rep.authority > 0.5  # rewarded for a correct, promoted origination


def test_originating_a_dismissed_candidate_increases_false_positive_count_and_lowers_authority(db_session):
    source = _source(db_session)
    candidate = _candidate(db_session, state=SignalCandidateState.DISMISSED)
    origin_item = _item(db_session, source, candidate, "1")
    _make_origin_group(db_session, candidate, origin_item)

    rep = recompute_source_reputation(db_session, source.id)
    db_session.commit()

    assert rep.false_positive_count == 1
    assert rep.verification_count == 0
    assert rep.noise_rate == 1.0
    assert rep.authority < 0.5  # penalized for a dismissed origination


def test_echoing_but_never_originating_gives_zero_originality(db_session):
    """A source that always confirms someone else's report, but never
    breaks a story first, should show low originality even with a
    perfect promotion record -- these are two different questions."""
    origin_source = _source(db_session, "Golden Pig")
    echo_source = _source(db_session, "Echo Blog")
    candidate = _candidate(db_session, state=SignalCandidateState.PROMOTED)
    origin_item = _item(db_session, origin_source, candidate, "1", posted=BASE)
    echo_item = _item(db_session, echo_source, candidate, "2", posted=BASE + dt.timedelta(hours=1))
    _make_origin_group(db_session, candidate, origin_item, other_items=[echo_item])

    rep = recompute_source_reputation(db_session, echo_source.id)
    db_session.commit()

    assert rep.independence_groups_originated == 0
    assert rep.independence_groups_appeared_in == 1
    assert rep.originality == 0.0


def test_lead_time_hours_measures_gap_to_next_confirmation(db_session):
    source = _source(db_session)
    candidate = _candidate(db_session, state=SignalCandidateState.ACTIVE)
    origin_item = _item(db_session, source, candidate, "1", posted=BASE)
    confirming_source = _source(db_session, "Confirmer")
    confirm_item = _item(db_session, confirming_source, candidate, "2", posted=BASE + dt.timedelta(hours=5))
    _make_origin_group(db_session, candidate, origin_item, other_items=[confirm_item])

    rep = recompute_source_reputation(db_session, source.id)
    db_session.commit()

    assert rep.lead_time_hours == 5.0


def test_authority_override_wins_over_computed_value(db_session):
    source = _source(db_session)
    rep = recompute_source_reputation(db_session, source.id)
    db_session.commit()
    assert effective_authority(rep) == 0.5

    rep.authority_override = 0.95
    db_session.commit()

    assert effective_authority(rep) == 0.95
    # recomputing must never clear or fight the override
    recompute_source_reputation(db_session, source.id)
    db_session.commit()
    refreshed = db_session.execute(
        select(SourceReputation).where(SourceReputation.source_id == source.id)
    ).scalar_one()
    assert refreshed.authority_override == 0.95
    assert effective_authority(refreshed) == 0.95


def test_recompute_is_idempotent(db_session):
    source = _source(db_session)
    candidate = _candidate(db_session, state=SignalCandidateState.PROMOTED)
    origin_item = _item(db_session, source, candidate, "1")
    _make_origin_group(db_session, candidate, origin_item)

    first = recompute_source_reputation(db_session, source.id)
    db_session.commit()
    first_authority = first.authority

    second = recompute_source_reputation(db_session, source.id)
    db_session.commit()

    assert second.authority == first_authority
    assert list(db_session.scalars(select(SourceReputation))).__len__() == 1  # one row, not duplicated
