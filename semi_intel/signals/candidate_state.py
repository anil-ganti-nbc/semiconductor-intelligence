"""SignalCandidate state transitions (brief section 7: active, promoted,
dismissed, snoozed, stale, merged). Kept as small, explicit functions --
mirrors the existing style of semi_intel/editorial/service.py's seen-state
handling (`seen_at` persisted, nothing deleted) rather than introducing a
generic state-machine framework this codebase doesn't otherwise use.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import CandidateRelationType, SignalCandidateState
from semi_intel.domain.models import CandidateEntity, CandidateRelationship, CandidateSignalItem, SignalCandidate


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def mark_seen(candidate: SignalCandidate, *, now: Optional[dt.datetime] = None) -> None:
    candidate.seen_at = now or _now()


def mark_unseen(candidate: SignalCandidate) -> None:
    candidate.seen_at = None


def dismiss(candidate: SignalCandidate, *, reason: str, now: Optional[dt.datetime] = None) -> None:
    candidate.state = SignalCandidateState.DISMISSED
    candidate.dismissed_at = now or _now()
    candidate.dismissed_reason = reason


def restore(candidate: SignalCandidate) -> None:
    """Undo dismiss/snooze -- back to active, per brief's "remains dismissed
    unless manually restored." Never deletes anything."""
    candidate.state = SignalCandidateState.ACTIVE
    candidate.dismissed_at = None
    candidate.dismissed_reason = None
    candidate.snoozed_until = None


def snooze(candidate: SignalCandidate, *, until: dt.datetime) -> None:
    candidate.state = SignalCandidateState.SNOOZED
    candidate.snoozed_until = until


def wake_snoozed(session: Session, *, now: Optional[dt.datetime] = None) -> int:
    """Returns snoozed candidates whose snooze window has elapsed back to
    active. Idempotent, safe to run every pipeline cycle."""
    now = now or _now()
    stmt = select(SignalCandidate).where(
        SignalCandidate.state == SignalCandidateState.SNOOZED,
        SignalCandidate.snoozed_until.is_not(None),
        SignalCandidate.snoozed_until <= now,
    )
    count = 0
    for candidate in session.scalars(stmt):
        candidate.state = SignalCandidateState.ACTIVE
        candidate.snoozed_until = None
        count += 1
    return count


def mark_stale_candidates(session: Session, *, staleness_days: int, now: Optional[dt.datetime] = None) -> int:
    """Active candidates with no new evidence in `staleness_days` move to
    stale -- visible in a dedicated filter (brief section 15), never
    deleted. A direct quote/reply to one of a stale candidate's items still
    reactivates it (`clustering._lineage_parent_candidate` considers STALE
    an attachable state) -- a causal link, not mere topical resemblance.
    A merely topically-similar item arriving long afterward correctly stays
    a *separate* candidate instead (the general attach path's time-window
    guard excludes long-stale candidates), consistent with "stale time
    window remains separate" (brief section 24)."""
    now = now or _now()
    cutoff = now - dt.timedelta(days=staleness_days)
    stmt = select(SignalCandidate).where(
        SignalCandidate.state == SignalCandidateState.ACTIVE,
        SignalCandidate.latest_observed_at < cutoff,
    )
    count = 0
    for candidate in session.scalars(stmt):
        candidate.state = SignalCandidateState.STALE
        count += 1
    return count


def merge_candidates(session: Session, *, loser: SignalCandidate, winner: SignalCandidate, by: str = "human") -> None:
    """Reversible, audited merge (brief section 7): repoints evidence
    membership, mirrors it as a CandidateRelationship for the audit trail,
    marks the loser MERGED. Nothing is deleted -- the loser's own
    memberships stay recorded (via the relationship + its own now-orphaned
    CandidateSignalItem rows), so this can be traced later even though the
    loser no longer accepts new evidence."""
    if loser.id == winner.id:
        return

    existing_winner_items = {
        row[0] for row in session.execute(
            select(CandidateSignalItem.signal_item_id).where(CandidateSignalItem.candidate_id == winner.id)
        )
    }
    for row in session.scalars(select(CandidateSignalItem).where(CandidateSignalItem.candidate_id == loser.id)):
        if row.signal_item_id not in existing_winner_items:
            session.add(CandidateSignalItem(
                candidate_id=winner.id, signal_item_id=row.signal_item_id,
                attach_reasons=row.attach_reasons,
            ))
            winner.item_count += 1

    existing_winner_entities = {
        row[0] for row in session.execute(
            select(CandidateEntity.entity_id).where(CandidateEntity.candidate_id == winner.id)
        )
    }
    for row in session.scalars(select(CandidateEntity).where(CandidateEntity.candidate_id == loser.id)):
        if row.entity_id not in existing_winner_entities:
            session.add(CandidateEntity(candidate_id=winner.id, entity_id=row.entity_id, role=row.role))

    session.add(CandidateRelationship(
        from_candidate_id=loser.id, to_candidate_id=winner.id,
        relation_type=CandidateRelationType.MERGED_INTO, note=f"merged by {by}",
    ))

    loser.state = SignalCandidateState.MERGED
    session.flush()

    from semi_intel.signals.independence import recompute_independence_groups
    recompute_independence_groups(session, winner)

    from semi_intel.signals.scoring import rescore_candidate
    rescore_candidate(session, winner)
