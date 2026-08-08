"""SignalCandidate state-transition tests (brief section 7)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from semi_intel.domain.enums import SignalCandidateState, SourceType
from semi_intel.domain.models import CandidateSignalItem, SignalCandidate, SignalItem, Source
from semi_intel.editorial.service import TopicService
from semi_intel.signals.analysis import analyze_signal_item
from semi_intel.signals.candidate_state import (
    dismiss,
    mark_seen,
    mark_stale_candidates,
    mark_unseen,
    merge_candidates,
    restore,
    snooze,
    wake_snoozed,
)
from semi_intel.signals.clustering import cluster_unclustered_items

BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


def _seed(session):
    TopicService(session).seed()
    session.commit()


_source_counter = [0]


def _candidate(session, text="RTX 50 Super leak.", posted=BASE):
    _source_counter[0] += 1
    n = _source_counter[0]
    source = Source(name=f"Source {n}", type=SourceType.SOCIAL, provider="replay")
    session.add(source)
    session.commit()
    item = SignalItem(
        source_id=source.id, provider="replay", external_id=f"item-{n}", raw_payload="{}",
        normalized_text=text, content_hash=f"h{n}", posted_at=posted,
    )
    session.add(item)
    session.commit()
    analyze_signal_item(session, item)
    session.commit()
    cluster_unclustered_items(session)
    session.commit()
    # Look up the candidate THIS item actually ended up in -- a plain
    # unordered SignalCandidate query would just return whichever candidate
    # happens to sort first once more than one exists.
    return session.scalars(
        select(SignalCandidate)
        .join(CandidateSignalItem, CandidateSignalItem.candidate_id == SignalCandidate.id)
        .where(CandidateSignalItem.signal_item_id == item.id)
    ).one()


def test_mark_seen_and_unseen_do_not_delete_anything(db_session):
    _seed(db_session)
    candidate = _candidate(db_session)

    mark_seen(candidate)
    db_session.commit()
    assert candidate.seen_at is not None

    mark_unseen(candidate)
    db_session.commit()
    assert candidate.seen_at is None
    assert candidate.item_count == 1  # nothing deleted


def test_dismiss_persists_reason_and_state(db_session):
    _seed(db_session)
    candidate = _candidate(db_session)

    dismiss(candidate, reason="not relevant to our coverage")
    db_session.commit()

    assert candidate.state == SignalCandidateState.DISMISSED
    assert candidate.dismissed_reason == "not relevant to our coverage"
    assert candidate.dismissed_at is not None


def test_restore_returns_dismissed_candidate_to_active(db_session):
    _seed(db_session)
    candidate = _candidate(db_session)
    dismiss(candidate, reason="test")
    db_session.commit()

    restore(candidate)
    db_session.commit()

    assert candidate.state == SignalCandidateState.ACTIVE
    assert candidate.dismissed_at is None
    assert candidate.dismissed_reason is None


def test_snooze_and_wake_after_window_elapses(db_session):
    _seed(db_session)
    candidate = _candidate(db_session)
    until = BASE + dt.timedelta(days=2)

    snooze(candidate, until=until)
    db_session.commit()
    assert candidate.state == SignalCandidateState.SNOOZED

    woken_too_early = wake_snoozed(db_session, now=BASE + dt.timedelta(days=1))
    db_session.commit()
    assert woken_too_early == 0
    assert candidate.state == SignalCandidateState.SNOOZED

    woken = wake_snoozed(db_session, now=until + dt.timedelta(minutes=1))
    db_session.commit()
    assert woken == 1
    assert candidate.state == SignalCandidateState.ACTIVE
    assert candidate.snoozed_until is None


def test_mark_stale_candidates_moves_inactive_ones(db_session):
    _seed(db_session)
    candidate = _candidate(db_session, posted=BASE)

    count = mark_stale_candidates(db_session, staleness_days=14, now=BASE + dt.timedelta(days=20))
    db_session.commit()

    assert count == 1
    assert candidate.state == SignalCandidateState.STALE


def test_mark_stale_candidates_leaves_recent_ones_active(db_session):
    _seed(db_session)
    candidate = _candidate(db_session, posted=BASE)

    count = mark_stale_candidates(db_session, staleness_days=14, now=BASE + dt.timedelta(days=2))
    db_session.commit()

    assert count == 0
    assert candidate.state == SignalCandidateState.ACTIVE


def test_merge_is_reversible_and_audited(db_session):
    """Reversible merge: repoints evidence, marks the loser merged_into the
    winner, and records an auditable CandidateRelationship -- nothing is
    deleted (brief section 7)."""
    _seed(db_session)
    winner = _candidate(db_session, text="RTX 50 Super leak from source A.", posted=BASE)

    # A second, separately-created candidate about the same topic but
    # outside the attach window, to simulate two candidates a human decides
    # are actually duplicates.
    loser = _candidate(db_session, text="RTX 50 Super leak from source B.", posted=BASE + dt.timedelta(days=10))
    assert winner.id != loser.id

    loser_items_before = {row[0] for row in db_session.execute(
        select(CandidateSignalItem.signal_item_id).where(CandidateSignalItem.candidate_id == loser.id)
    )}

    merge_candidates(db_session, loser=loser, winner=winner, by="test-operator")
    db_session.commit()

    assert loser.state == SignalCandidateState.MERGED
    winner_items_after = {row[0] for row in db_session.execute(
        select(CandidateSignalItem.signal_item_id).where(CandidateSignalItem.candidate_id == winner.id)
    )}
    assert loser_items_before.issubset(winner_items_after)

    from semi_intel.domain.models import CandidateRelationship
    from semi_intel.domain.enums import CandidateRelationType
    relationship = db_session.scalars(
        select(CandidateRelationship).where(
            CandidateRelationship.from_candidate_id == loser.id,
            CandidateRelationship.to_candidate_id == winner.id,
        )
    ).first()
    assert relationship is not None
    assert relationship.relation_type == CandidateRelationType.MERGED_INTO
    assert "test-operator" in relationship.note

    # Loser's original membership rows still exist -- nothing deleted.
    still_there = {row[0] for row in db_session.execute(
        select(CandidateSignalItem.signal_item_id).where(CandidateSignalItem.candidate_id == loser.id)
    )}
    assert still_there == loser_items_before


def test_merge_recomputes_winner_score(db_session):
    _seed(db_session)
    winner = _candidate(db_session, text="RTX 50 Super leak A.", posted=BASE)
    loser = _candidate(db_session, text="RTX 50 Super leak B.", posted=BASE + dt.timedelta(days=10))

    score_before = winner.attention_score
    merge_candidates(db_session, loser=loser, winner=winner, by="test")
    db_session.commit()

    # A score was (re)computed and persisted as an explanation, whatever
    # its direction moved.
    assert winner.score_explanation != "{}"
