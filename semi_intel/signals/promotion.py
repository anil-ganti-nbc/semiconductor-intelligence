"""Editorial promotion: the one path from a SignalCandidate into the
canonical editorial layer (brief section 12).

Reuses `semi_intel.editorial.service.EditorialDiscoveryService` for the
actual story clustering/scoring rule rather than reimplementing it -- the
first Evidence row created for a candidate goes through
`process_evidence()` exactly like any other ingested evidence (so it
interoperates correctly with pre-existing, non-candidate stories); every
subsequent Evidence row from the *same* candidate is then attached directly
to that same story, because our own SignalCandidate clustering already
established these items belong together using richer signals (topic/
artifact/lineage/independence) than editorial's own 0.72-headline-
similarity check alone would reliably agree on. Without this, a candidate's
four items could fragment across two or three separate EditorialStory rows
purely because their individual headlines don't happen to be textually
close enough -- exactly the kind of accidental split-then-merge mess
promotion has to avoid.

Idempotent (brief section 12): `candidate.promoted_story_id` is checked
first; a re-run against an already-promoted candidate is a no-op that
returns the existing story. Per-item idempotency is enforced by
`Evidence.origin_signal_item_id` (unique) -- reprocessing never creates a
second Evidence row for the same SignalItem.

What promotion deliberately does NOT do: create Claims. Claim creation
stays human-authored (see semi_intel/domain/models.py's Claim docstring);
promotion only runs the existing claim-link *suggestion* scanner
(SuggestionService), same as the rest of the pipeline already does for
every evidence row, and triggers bounded discovery only through its own
existing eligibility rules (DiscoveryService), unchanged.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semi_intel.domain.enums import SignalCandidateState
from semi_intel.domain.models import (
    CandidatePromotionEvent,
    CandidatePromotionSettings,
    CandidateSignalItem,
    CandidateTopicMatch,
    EditorialStory,
    Evidence,
    MonitoredTopic,
    SignalCandidate,
    SignalItem,
    Source,
    StoryEvidence,
    TopicMatch,
)
from semi_intel.editorial.service import EditorialDiscoveryService
from semi_intel.ingestion.hashing import hash_content


class PromotionBlocked(Exception):
    """Raised when a candidate is not eligible for promotion right now.
    Callers distinguish "not eligible" from "system failure" -- this is
    normal control flow, not an error condition."""


def get_promotion_settings(session: Session) -> CandidatePromotionSettings:
    settings = session.get(CandidatePromotionSettings, 1)
    if not settings:
        settings = CandidatePromotionSettings(id=1)
        session.add(settings)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            settings = session.get(CandidatePromotionSettings, 1)
    return settings


def evidence_for_signal_item(session: Session, item: SignalItem) -> Evidence:
    """Get-or-create, preserving original URL/external_id/raw content/
    timestamps exactly (brief section 12, points 2-3). Reuses the same
    content-hash dedup rule as every other ingestion path
    (semi_intel.ingestion.hashing.hash_content) so a SignalItem whose exact
    text already exists as Evidence (e.g. imported through a legacy plugin)
    reuses that row instead of duplicating it."""
    existing = session.execute(
        select(Evidence).where(Evidence.origin_signal_item_id == item.id)
    ).scalar_one_or_none()
    if existing:
        return existing

    content = item.normalized_text or item.title or item.external_id
    content_hash = hash_content(content)
    by_hash = session.execute(select(Evidence).where(Evidence.content_hash == content_hash)).scalar_one_or_none()
    if by_hash:
        if by_hash.origin_signal_item_id is None:
            by_hash.origin_signal_item_id = item.id
        return by_hash

    evidence = Evidence(
        source_id=item.source_id,
        entity_id=None,
        title=(item.title or item.normalized_text or item.external_id)[:500],
        raw_content=item.normalized_text or item.raw_payload,
        content_hash=content_hash,
        external_id=item.external_id,
        url=item.url,
        observed_at=item.posted_at,
        collected_at=item.collected_at,
        origin_signal_item_id=item.id,
    )
    session.add(evidence)
    session.flush()
    return evidence


# Kept as a private alias for the older promotion code and third-party callers
# that may have imported the helper while it was implementation-only.
_evidence_for_item = evidence_for_signal_item


def _attach_evidence_to_known_story(session: Session, story: EditorialStory, evidence: Evidence) -> None:
    """Attach directly, bypassing EditorialDiscoveryService's own
    independent re-clustering decision -- see module docstring for why."""
    existing_link = session.execute(
        select(StoryEvidence).where(StoryEvidence.evidence_id == evidence.id)
    ).scalar_one_or_none()
    if existing_link:
        return

    session.add(StoryEvidence(story_id=story.id, evidence_id=evidence.id))
    story.coverage_count += 1
    observed = evidence.observed_at or evidence.collected_at
    if observed and observed > story.latest_at:
        story.latest_at = observed
    if story.seen_at:
        story.new_coverage_count += 1
    session.flush()


def _apply_candidate_topic_matches(session: Session, candidate: SignalCandidate, story: EditorialStory) -> None:
    """Create topic matches with reasons (brief section 12, point 5) from
    the candidate's own topic matches -- covers the case where
    process_evidence() didn't independently find the same topic match on
    the raw evidence text (e.g. an artifact-only candidate whose primary
    topic came from a different member item's text)."""
    for ctm in session.scalars(select(CandidateTopicMatch).where(CandidateTopicMatch.candidate_id == candidate.id)):
        exists = session.execute(
            select(TopicMatch).where(TopicMatch.story_id == story.id, TopicMatch.topic_id == ctm.topic_id)
        ).scalar_one_or_none()
        if exists:
            continue
        topic = session.get(MonitoredTopic, ctm.topic_id)
        if not topic:
            continue
        session.add(TopicMatch(
            story_id=story.id, topic_id=topic.id, matched_text=ctm.matched_text, match_score=topic.priority,
        ))


def promote_candidate(
    session: Session, candidate: SignalCandidate, *, by: str, automatic: bool = False, reason: str = "",
) -> EditorialStory:
    """Manual promotion entry point (also used internally by automatic
    promotion after eligibility checks pass). `by` should be
    "human:<identifier>" for manual promotions or "automatic" for scheduled
    ones -- stored verbatim in the audit event."""
    if candidate.promoted_story_id is not None:
        return session.get(EditorialStory, candidate.promoted_story_id)

    editorial = EditorialDiscoveryService(session)
    item_ids = [row[0] for row in session.execute(
        select(CandidateSignalItem.signal_item_id).where(CandidateSignalItem.candidate_id == candidate.id)
    )]
    items = sorted(
        (session.get(SignalItem, i) for i in item_ids),
        key=lambda it: it.posted_at or it.collected_at,
    )
    if not items:
        raise PromotionBlocked(f"candidate {candidate.id} has no member items")

    story: Optional[EditorialStory] = None
    for item in items:
        evidence = _evidence_for_item(session, item)
        if story is None:
            story = editorial.process_evidence(evidence)
            if story is None:
                # No monitored-topic text match on this evidence (can
                # happen for an artifact-only candidate promoted per the
                # "unusually strong hard artifact" exception in brief
                # section 11) -- create the story directly rather than
                # silently dropping the promotion.
                story = EditorialStory(
                    canonical_key=f"candidate-{candidate.id}-{evidence.id}",
                    headline=evidence.title,
                    summary=evidence.raw_content[:1200],
                    created_at=evidence.observed_at or evidence.collected_at,
                    latest_at=evidence.observed_at or evidence.collected_at,
                )
                session.add(story)
                session.flush()
                session.add(StoryEvidence(story_id=story.id, evidence_id=evidence.id))
                session.flush()
        else:
            _attach_evidence_to_known_story(session, story, evidence)

    _apply_candidate_topic_matches(session, candidate, story)
    session.flush()

    candidate.promoted_story_id = story.id
    candidate.state = SignalCandidateState.PROMOTED
    session.add(CandidatePromotionEvent(
        candidate_id=candidate.id, story_id=story.id, promoted_by=by, automatic=automatic, reason=reason,
    ))
    # Commit the promotion itself (Evidence/Story/StoryEvidence/TopicMatch/
    # candidate state/audit event) BEFORE attempting suggestions/discovery.
    # Those two steps get their own isolated try/except+commit below --
    # critically, a failure there must roll back only ITS OWN pending
    # writes, not the promotion. Committing first is what makes that safe:
    # session.rollback() only ever discards work older than the last
    # commit, and unlike PipelineService's per-stage isolation (independent
    # stages that don't depend on each other), rolling back a step here
    # while the promotion writes were still uncommitted would have undone
    # the promotion too.
    session.commit()

    try:
        from semi_intel.claim_engine.suggestion_service import SuggestionService
        SuggestionService(session).run()
        session.commit()
    except Exception:  # noqa: BLE001 - a suggestion-scan failure must not undo the promotion
        session.rollback()

    try:
        from semi_intel.discovery.service import DiscoveryService
        DiscoveryService(session).run_eligible(automatic=False)
        session.commit()
    except Exception:  # noqa: BLE001 - likewise for discovery
        session.rollback()

    return story


def merge_candidate_into_story(session: Session, candidate: SignalCandidate, story: EditorialStory, *, by: str) -> EditorialStory:
    """Promote into an EXISTING editorial story rather than creating a new
    one -- the operator-chosen merge path (brief section 12)."""
    if candidate.promoted_story_id is not None:
        return session.get(EditorialStory, candidate.promoted_story_id)

    item_ids = [row[0] for row in session.execute(
        select(CandidateSignalItem.signal_item_id).where(CandidateSignalItem.candidate_id == candidate.id)
    )]
    for item in sorted((session.get(SignalItem, i) for i in item_ids), key=lambda it: it.posted_at or it.collected_at):
        evidence = _evidence_for_item(session, item)
        _attach_evidence_to_known_story(session, story, evidence)

    _apply_candidate_topic_matches(session, candidate, story)

    candidate.promoted_story_id = story.id
    candidate.state = SignalCandidateState.PROMOTED
    session.add(CandidatePromotionEvent(
        candidate_id=candidate.id, story_id=story.id, promoted_by=by, automatic=False,
        reason="merged into existing story",
    ))
    session.commit()
    return story


@dataclass
class EligibilityResult:
    eligible: bool
    reasons: list[str]


def check_automatic_eligibility(
    session: Session, candidate: SignalCandidate, settings: CandidatePromotionSettings, *, now: Optional[dt.datetime] = None,
) -> EligibilityResult:
    """Deterministic eligibility check, mirrors DiscoveryService.eligibility()'s
    style: every requirement explicit and explained, never a bare bool."""
    now = now or dt.datetime.utcnow()
    reasons: list[str] = []

    if not settings.automatic_promotion_enabled:
        reasons.append("automatic promotion disabled")
    if candidate.state != SignalCandidateState.ACTIVE:
        reasons.append(f"candidate state is {candidate.state.value}, not active")
    if candidate.attention_score < settings.minimum_attention_score:
        reasons.append(f"attention score {candidate.attention_score:.2f} below minimum {settings.minimum_attention_score:.2f}")
    if settings.required_topic_match and candidate.primary_topic_id is None:
        reasons.append("no monitored-topic match (required_topic_match=True)")
    age_hours = (now - candidate.first_observed_at).total_seconds() / 3600 if candidate.first_observed_at else 0
    if age_hours > settings.maximum_candidate_age_hours:
        reasons.append(f"candidate age {age_hours:.1f}h exceeds maximum {settings.maximum_candidate_age_hours}h")
    if candidate.independent_source_group_count < 1:
        reasons.append("no independent evidence group")

    return EligibilityResult(eligible=not reasons, reasons=reasons)


def _promotions_in_last_hour(session: Session, *, now: dt.datetime) -> int:
    cutoff = now - dt.timedelta(hours=1)
    return len(list(session.scalars(
        select(CandidatePromotionEvent.id).where(
            CandidatePromotionEvent.automatic.is_(True), CandidatePromotionEvent.created_at >= cutoff,
        )
    )))


@dataclass
class AutoPromotionSummary:
    promoted: list[int]
    skipped: list[tuple[int, list[str]]]
    budget_exhausted: bool = False


def run_automatic_promotion(session: Session, *, now: Optional[dt.datetime] = None) -> AutoPromotionSummary:
    """The pipeline's automatic-promotion stage. Defaults to a no-op
    (settings.automatic_promotion_enabled=False) -- see brief section 12:
    "Automatic promotion must default to off after upgrade." Respects an
    hourly promotion budget so a burst of simultaneously-eligible
    candidates can't flood the editorial inbox in one cycle."""
    now = now or dt.datetime.utcnow()
    settings = get_promotion_settings(session)
    summary = AutoPromotionSummary(promoted=[], skipped=[])

    if not settings.automatic_promotion_enabled:
        return summary

    already_this_hour = _promotions_in_last_hour(session, now=now)
    budget_remaining = max(settings.hourly_promotion_budget - already_this_hour, 0)
    if budget_remaining <= 0:
        summary.budget_exhausted = True
        return summary

    from semi_intel.domain.enums import SignalCandidateState as _S
    stmt = (
        select(SignalCandidate)
        .where(SignalCandidate.state == _S.ACTIVE)
        .order_by(SignalCandidate.attention_score.desc())
    )
    for candidate in session.scalars(stmt):
        if budget_remaining <= 0:
            summary.budget_exhausted = True
            break
        result = check_automatic_eligibility(session, candidate, settings, now=now)
        if not result.eligible:
            summary.skipped.append((candidate.id, result.reasons))
            continue
        promote_candidate(session, candidate, by="automatic", automatic=True, reason="conservative automatic promotion")
        summary.promoted.append(candidate.id)
        budget_remaining -= 1

    return summary
