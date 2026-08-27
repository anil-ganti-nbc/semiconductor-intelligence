"""Human QC feedback on SignalCandidates: the fleet-wide "active lead
queue" review contract, applied to this domain.

Every Clank in the fleet gives an operator the same four-way editorial
verdict on one intelligence item in a working queue -- Useful / Not useful
/ False positive / (Duplicate, in place of Out of stock where "in/out of
stock" has no honest meaning) -- archived separately from the item itself,
never deleted, race-safe against near-simultaneous double submission, and
excluded from the default queue the instant it's reviewed. See Watch
Clank's app/services/qc.py and ai/handoff/HUMAN_QC_FEEDBACK_CONTRACT.md for
the reference implementation this module deliberately mirrors.

CANDIDATE != REVIEW (this fleet's "EVENT != REVIEW" rule, restated for this
domain): a CandidateReview is human editorial feedback about one
SignalCandidate under one evidence state, never a mutation of the
candidate and never a permanent verdict on whatever entities/topics it
touches. SignalCandidate.state (active/promoted/dismissed/snoozed/stale/
merged -- semi_intel/signals/candidate_state.py) keeps tracking this
candidate's own operational lifecycle exactly as before; this module adds
an independent, additive archive on top of it, not a replacement for it.
A human can still dismiss/snooze/promote a candidate through the existing
mechanism regardless of whether it's been QC'd, and vice versa.

Why no OUT_OF_STOCK: that disposition means something concrete for a
retail-inventory watch (Watch Clank's Event) -- it does not for a
semiconductor-intelligence signal candidate, which is a cluster of
articles/posts about a company, chip, or roadmap event, not a stocked
item. Forcing that vocabulary onto this domain would be a fake fit. The
fleet has already faced this exact question once before: Watch Clank's own
SpecialistLead (Layer B editorial leads, not inventory) uses DUPLICATE in
OUT_OF_STOCK's place for the same reason ("editorial leads can be genuine
repeats of already-seen coverage; they are never in/out of stock" --
SpecialistLeadReview's module docstring). This module follows that same,
already-established precedent.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semi_intel.domain.enums import CandidateReviewDisposition, SignalCandidateState
from semi_intel.domain.models import CandidateReview, SignalCandidate

DEFAULT_PAGE_SIZE = 25


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


class InvalidDispositionError(ValueError):
    """Raised when a disposition outside CandidateReviewDisposition is submitted."""


@dataclass(frozen=True)
class CandidateQueueFilters:
    topic_id: Optional[int] = None
    min_score: float = 0.0


def _base_unreviewed_query(filters: CandidateQueueFilters):
    """The default active-lead queue: ACTIVE candidates with no
    CandidateReview row yet. Reviewing a candidate never touches the
    SignalCandidate row itself -- it disappears from this queue purely by
    virtue of the outer join, exactly like the rest of the fleet's
    unreviewed-item queries."""
    stmt = (
        select(SignalCandidate)
        .outerjoin(CandidateReview, CandidateReview.candidate_id == SignalCandidate.id)
        .where(CandidateReview.id.is_(None))
        .where(SignalCandidate.state == SignalCandidateState.ACTIVE)
        .where(SignalCandidate.attention_score >= filters.min_score)
    )
    if filters.topic_id is not None:
        from semi_intel.domain.models import CandidateTopicMatch

        stmt = stmt.where(SignalCandidate.id.in_(
            select(CandidateTopicMatch.candidate_id).where(CandidateTopicMatch.topic_id == filters.topic_id)
        ))
    return stmt


def unreviewed_candidate_count(session: Session, filters: CandidateQueueFilters) -> int:
    stmt = _base_unreviewed_query(filters).with_only_columns(func.count(func.distinct(SignalCandidate.id)))
    return session.scalar(stmt) or 0


def reviewed_today_count(session: Session) -> int:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    return session.scalar(
        select(func.count(func.distinct(CandidateReview.candidate_id))).where(
            CandidateReview.reviewed_at >= start
        )
    ) or 0


def fetch_candidate_review_queue_page(
    session: Session,
    filters: CandidateQueueFilters,
    *,
    before_id: Optional[int] = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> list[SignalCandidate]:
    """Highest-attention-first page of the active, not-yet-reviewed queue.
    ``before_id`` continues past that candidate's id (a stable pagination
    cursor, independent of score)."""
    stmt = _base_unreviewed_query(filters)
    if before_id is not None:
        stmt = stmt.where(SignalCandidate.id < before_id)
    stmt = stmt.order_by(desc(SignalCandidate.attention_score), desc(SignalCandidate.id)).limit(limit)
    return list(session.scalars(stmt).unique().all())


def fetch_review_history_page(
    session: Session,
    *,
    disposition: Optional[CandidateReviewDisposition] = None,
    include_corrected: bool = False,
    before_id: Optional[int] = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> list[CandidateReview]:
    """"Recently QC'd" view: newest-reviewed-first, paginated. Default
    excludes already-corrected reviews (a corrected verdict is "handled"
    and drops out of the working correction queue, matching the rest of
    the fleet's QC History default) -- ``include_corrected=True`` reveals
    them again. Nothing is ever deleted."""
    stmt = select(CandidateReview)
    if not include_corrected:
        stmt = stmt.where(CandidateReview.is_corrected.is_(False))
    if disposition is not None:
        stmt = stmt.where(CandidateReview.disposition == disposition)
    if before_id is not None:
        stmt = stmt.where(CandidateReview.id < before_id)
    stmt = stmt.order_by(desc(CandidateReview.id)).limit(limit)
    return list(session.scalars(stmt).unique().all())


def submit_candidate_review(
    session: Session,
    *,
    candidate: SignalCandidate,
    disposition: CandidateReviewDisposition,
    reason: Optional[str] = None,
) -> CandidateReview:
    """Persist (or correct) the operator's verdict on ``candidate``.

    Idempotent-by-correction: a second submission for the same candidate_id
    never creates a duplicate row -- if the disposition differs from what's
    on file, the prior verdict is appended to
    review_metadata['correction_history'] before being overwritten.  Never
    touches the SignalCandidate row itself (no state/seen_at/dismissed_at
    mutation) -- existing dismiss/restore/snooze/promote workflows are
    completely unaffected by QC review and vice versa.

    Race-safe: `uq_candidate_review_candidate_id` makes a duplicate archive
    row impossible at the DB level. Two near-simultaneous submissions for
    the same candidate both see "no existing review" before either
    commits; the losing insert's IntegrityError is caught, rolled back, and
    retried as a correction against whichever row actually won the race --
    never surfaced as a raw error to the operator.
    """
    if not isinstance(disposition, CandidateReviewDisposition):
        try:
            disposition = CandidateReviewDisposition(disposition)
        except ValueError:
            raise InvalidDispositionError(f"unknown disposition: {disposition!r}")

    now = _now()
    existing = session.scalar(select(CandidateReview).where(CandidateReview.candidate_id == candidate.id))

    if existing is not None:
        if existing.disposition != disposition:
            metadata = json.loads(existing.review_metadata or "{}")
            history = list(metadata.get("correction_history") or [])
            history.append({
                "previous_disposition": existing.disposition.value,
                "previous_reviewed_at": existing.reviewed_at.isoformat(),
                "corrected_at": now.isoformat(),
            })
            metadata["correction_history"] = history
            existing.review_metadata = json.dumps(metadata)
            existing.disposition = disposition
            existing.is_corrected = True
        existing.reason = reason or existing.reason
        session.flush()
        return existing

    review = CandidateReview(
        candidate_id=candidate.id,
        candidate_title=candidate.title,
        attention_score_at_review=candidate.attention_score,
        item_count_at_review=candidate.item_count,
        disposition=disposition,
        reason=reason,
    )
    session.add(review)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        winner = session.scalar(select(CandidateReview).where(CandidateReview.candidate_id == candidate.id))
        if winner is None:
            raise
        return submit_candidate_review(session, candidate=candidate, disposition=disposition, reason=reason)
    return review
