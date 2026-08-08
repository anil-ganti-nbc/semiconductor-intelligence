"""Conservative Signal Candidate clustering (brief section 9).

The rule this module exists to enforce: sharing one broad entity is never
sufficient evidence that two items belong together. "NVIDIA" alone must not
merge two items; "Xeon" alone must not merge two items; an RTX 5090 pricing
post and an RTX 50 Super memory-config post must not merge just because both
mention GeForce. This is a direct structural fix for Signal Radar's
single-shared-entity story engine (`core/story_engine._find_story`,
documented in PHASE0_AUDIT.md section 3) -- attachment here is always a
weighted combination of several signals, never a single boolean match, and
an ambiguous score creates a new (small, reviewable) candidate rather than
risking a wrong merge. False separation is preferred to combining unrelated
items -- the same principle semi_intel.editorial.service's own conservative
clustering already uses for EditorialStory.

Attachment scoring, all weighted 0..1:
  - topic_overlap (0.35): candidate and item share >=1 specific monitored
    topic. This is the single strongest, most specific signal available.
  - entity_overlap (0.20): Jaccard overlap of resolved *subject-worthy*
    entities (codename/product/pci_id/benchmark -- never company/retailer,
    which are the broad ones this rule exists to discount).
  - artifact_identity (0.20): an exact structured-artifact match (same PCI
    ID / benchmark identifier text) between the item and any existing
    candidate member -- the strongest possible corroboration signal.
  - lineage (0.15): the item quotes/replies to a member already in the
    candidate.
  - text_similarity (0.10): SequenceMatcher ratio between normalized texts,
    reusing semi_intel.editorial.service.normalize_phrase (same function
    the editorial layer's own 0.72-threshold clustering uses, at a much
    lower weight here since text similarity alone is a weak signal for
    social-length text).

A score >= ATTACH_THRESHOLD attaches; a score in the ambiguous band creates
a new candidate rather than guessing (brief: "Ambiguous attachments should
go to review, not silently merge" -- the review surface here is simply that
the new candidate exists and is visible, low-scored, and unattached, not a
separate review-queue table).

Negative evidence is enforced structurally, not bolted on afterward: a
candidate is only even considered for attachment if it is `state=ACTIVE`
and within `MAX_CANDIDATE_AGE_HOURS` of the item's posted_at (time-window
guard), and broad-only overlap (company/retailer entities, or no topic and
no artifact) cannot reach ATTACH_THRESHOLD on its own because
topic_overlap/artifact_identity -- the two heaviest weights -- are exactly
zero in that case.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import SignalCandidateState, SignalMentionStatus
from semi_intel.domain.models import (
    CandidateEntity,
    CandidateSignalItem,
    CandidateTopicMatch,
    SignalCandidate,
    SignalEntityMention,
    SignalItem,
    SignalTopicMatch,
)
from semi_intel.editorial.service import normalize_phrase
from semi_intel.signals.independence import recompute_independence_groups

CLUSTERING_VERSION = 1

# Only entity types that can anchor a story-worthy candidate. Company/
# retailer are deliberately excluded -- these are exactly the "broad" types
# the brief's negative-evidence rules say must not drive clustering alone.
SUBJECT_ENTITY_TYPES = {"codename", "product", "pci_id", "benchmark"}
ARTIFACT_ENTITY_TYPES = {"pci_id", "benchmark"}

# Calibration: a specific monitored-topic match OR an exact structured-
# artifact match is, on its own, sufficient to attach within the time
# window -- these are the two signals the brief explicitly calls
# "strong"/"should cluster strongly" on their own (an RTX 50 Super leak and
# its follow-up; an X post and an RSS report sharing one PCI ID). Quote/
# reply lineage is handled as an even stronger short-circuit in
# `_find_best_candidate` (a reply "inherits" its parent's candidate
# directly, per the brief's own wording, without going through scoring at
# all). Entity overlap and text similarity alone are deliberately weak --
# neither can reach ATTACH_THRESHOLD by itself, so "NVIDIA" or "Xeon" alone
# (which don't even pass `_is_candidate_worthy` without a topic/artifact
# anyway) or vaguely-similar wording alone can never force a merge.
WEIGHT_TOPIC = 0.40
WEIGHT_ARTIFACT = 0.40
WEIGHT_ENTITY = 0.15
WEIGHT_TEXT = 0.10

ATTACH_THRESHOLD = 0.40
MAX_CANDIDATE_AGE_HOURS = 72  # time-window guard: stale time window stays separate

# semi_intel.editorial.service.SEED_TOPICS mixes genuinely specific
# codenames/products (RTX 50 Super, Zen 6, Strix Halo) with broad umbrella
# brand/technology terms (GeForce, CUDA, x86, ARM) in the same table --
# reasonable for editorial's own clustering, which additionally requires
# 0.72 headline similarity as a second gate, but NOT reasonable as a
# standalone attach signal here: an RTX 50 Series pricing post and an RTX 50
# Super memory post both matching the generic "GeForce" topic must not
# merge just because both happen to say "GeForce" (the exact "broad product
# family" failure mode the brief warns against). A shared BROAD topic alone
# earns no attachment bonus; a shared SPECIFIC topic does.
BROAD_TOPIC_NAMES = [
    "GeForce", "Radeon", "CUDA", "ROCm", "DLSS", "Instinct", "UDNA",
    "Threadripper", "EPYC", "x86", "ARM", "RISC-V", "Apple Silicon", "CXL",
    "chiplets", "ASML", "CoWoS", "SoIC", "Foveros", "XeSS", "Gaudi",
    "Foundry Services", "advanced packaging", "semiconductor tariffs",
    "export controls", "China semiconductor industry", "NVIDIA AI accelerators",
    "Core Ultra", "Snapdragon X",
]
BROAD_TOPIC_NORMALIZED_NAMES = {normalize_phrase(n) for n in BROAD_TOPIC_NAMES}


def _specific_topic_overlap(item_sig: "_ItemSignals", cand_sig: "_ItemSignals") -> set[int]:
    overlap = item_sig.topic_ids & cand_sig.topic_ids
    return {
        t for t in overlap
        if normalize_phrase(item_sig.topic_texts.get(t, "")) not in BROAD_TOPIC_NORMALIZED_NAMES
    }


@dataclass
class _ItemSignals:
    topic_ids: set[int] = field(default_factory=set)
    topic_texts: dict[int, str] = field(default_factory=dict)
    subject_entity_texts: set[str] = field(default_factory=set)  # normalized candidate_text, subject types only
    artifact_texts: set[str] = field(default_factory=set)  # normalized candidate_text, artifact types only
    # normalized -> original candidate_text, for human-readable reason strings
    # (matching/set-membership always uses the normalized form above).
    display_text: dict[str, str] = field(default_factory=dict)
    resolved_entity_ids: set[int] = field(default_factory=set)
    text: str = ""


def _collect_item_signals(session: Session, item: SignalItem) -> _ItemSignals:
    sig = _ItemSignals(text=item.normalized_text or "")
    for tm in session.scalars(select(SignalTopicMatch).where(SignalTopicMatch.signal_item_id == item.id)):
        sig.topic_ids.add(tm.topic_id)
        sig.topic_texts[tm.topic_id] = tm.matched_text
    for m in session.scalars(select(SignalEntityMention).where(SignalEntityMention.signal_item_id == item.id)):
        if m.status == SignalMentionStatus.REJECTED:
            continue
        normalized = normalize_phrase(m.candidate_text)
        sig.display_text[normalized] = m.candidate_text
        if m.proposed_entity_type in ARTIFACT_ENTITY_TYPES:
            sig.artifact_texts.add(normalized)
        elif m.proposed_entity_type in SUBJECT_ENTITY_TYPES:
            sig.subject_entity_texts.add(normalized)
        if m.resolved_entity_id:
            sig.resolved_entity_ids.add(m.resolved_entity_id)
    return sig


def _is_candidate_worthy(session: Session, item: SignalItem, sig: _ItemSignals) -> bool:
    """brief section 11: no monitored-topic match -> ordinarily suppressed,
    unless there's an unusually strong hard artifact (a structured artifact
    mention) or the item is a quote/reply to something already clustered
    (a reply inherits its parent's candidacy even with no topic/artifact of
    its own -- e.g. a bare "confirmed" reply to a leak post)."""
    if sig.topic_ids or sig.artifact_texts:
        return True
    return _lineage_parent_candidate(session, item) is not None


# States a new SignalItem may still attach to. STALE is included
# deliberately: a candidate going stale from inactivity must not become a
# dead end -- genuinely new related evidence reactivates it (see _attach)
# rather than spawning a disconnected duplicate. PROMOTED/DISMISSED/
# SNOOZED/MERGED are not attachment targets; promotion and merge have their
# own explicit flows, and a dismissed/snoozed candidate shouldn't silently
# regrow without a human/scheduler decision to restore/wake it first.
ATTACHABLE_STATES = (SignalCandidateState.ACTIVE, SignalCandidateState.STALE)


def _lineage_parent_candidate(session: Session, item: SignalItem) -> Optional[SignalCandidate]:
    for parent_id in (item.quoted_signal_item_id, item.reply_to_signal_item_id):
        if not parent_id:
            continue
        row = session.execute(
            select(CandidateSignalItem.candidate_id).where(CandidateSignalItem.signal_item_id == parent_id)
        ).first()
        if row:
            candidate = session.get(SignalCandidate, row[0])
            if candidate and candidate.state in ATTACHABLE_STATES:
                return candidate
    return None


def _score_attachment(item_sig: _ItemSignals, cand_sig: _ItemSignals) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0

    specific_overlap = _specific_topic_overlap(item_sig, cand_sig)
    if specific_overlap:
        score += WEIGHT_TOPIC
        names = ", ".join(sorted(item_sig.topic_texts.get(t, str(t)) for t in specific_overlap))
        reasons.append(f"exact topic match: {names}")
    elif item_sig.topic_ids & cand_sig.topic_ids:
        broad_names = ", ".join(sorted(
            item_sig.topic_texts.get(t, str(t)) for t in (item_sig.topic_ids & cand_sig.topic_ids)
        ))
        reasons.append(f"broad topic match only (no attach credit): {broad_names}")

    if cand_sig.subject_entity_texts:
        overlap = item_sig.subject_entity_texts & cand_sig.subject_entity_texts
        union = item_sig.subject_entity_texts | cand_sig.subject_entity_texts
        if union:
            jaccard = len(overlap) / len(union)
            if jaccard > 0:
                score += WEIGHT_ENTITY * jaccard
                display = ", ".join(sorted(item_sig.display_text.get(t, t) for t in overlap)) or "partial overlap"
                reasons.append(f"shared subject entity: {display}")

    artifact_overlap = item_sig.artifact_texts & cand_sig.artifact_texts
    if artifact_overlap:
        score += WEIGHT_ARTIFACT
        display = ", ".join(sorted(item_sig.display_text.get(t, t) for t in artifact_overlap))
        reasons.append(f"shared artifact: {display}")

    if item_sig.text and cand_sig.text:
        ratio = SequenceMatcher(None, normalize_phrase(item_sig.text), normalize_phrase(cand_sig.text)).ratio()
        if ratio >= 0.5:
            score += WEIGHT_TEXT * ratio
            reasons.append(f"headline similarity: {ratio:.2f}")

    return min(score, 1.0), reasons


def _make_fingerprint(item: SignalItem) -> str:
    digest = hashlib.sha256(f"candidate:{item.provider}:{item.id}:{item.external_id}".encode("utf-8")).hexdigest()
    return digest[:32]


def _title_for(sig: _ItemSignals, item: SignalItem) -> str:
    if sig.topic_texts:
        names = list(dict.fromkeys(sig.topic_texts.values()))
        return " / ".join(names[:3])
    if sig.subject_entity_texts:
        return " / ".join(sorted(sig.subject_entity_texts)[:3]).title()
    if item.title:
        return item.title
    return (item.normalized_text[:80] + "...") if len(item.normalized_text or "") > 80 else (item.normalized_text or "Untitled signal")


@dataclass
class ClusteringSummary:
    items_processed: int = 0
    attached_to_existing: int = 0
    new_candidates: int = 0
    suppressed_no_topic_or_artifact: int = 0


def cluster_unclustered_items(session: Session, *, limit: Optional[int] = None) -> ClusteringSummary:
    """Idempotent, re-runnable: only considers SignalItems not already a
    member of any candidate (LEFT JOIN ... IS NULL), so calling this every
    pipeline cycle never reprocesses or duplicates existing memberships."""
    summary = ClusteringSummary()

    stmt = (
        select(SignalItem)
        .outerjoin(CandidateSignalItem, CandidateSignalItem.signal_item_id == SignalItem.id)
        .where(CandidateSignalItem.id.is_(None), SignalItem.processed_at.is_not(None))
        .order_by(SignalItem.posted_at.asc().nulls_last())
    )
    if limit:
        stmt = stmt.limit(limit)

    for item in session.scalars(stmt):
        summary.items_processed += 1
        item_sig = _collect_item_signals(session, item)
        if not _is_candidate_worthy(session, item, item_sig):
            summary.suppressed_no_topic_or_artifact += 1
            continue

        lineage_parent = _lineage_parent_candidate(session, item)
        if lineage_parent is not None:
            _attach(session, lineage_parent, item, ["quote/reply lineage inherits candidacy"])
            summary.attached_to_existing += 1
            continue

        best_candidate, best_score, best_reasons = _find_best_candidate(session, item, item_sig)

        if best_candidate is not None and best_score >= ATTACH_THRESHOLD:
            _attach(session, best_candidate, item, best_reasons)
            summary.attached_to_existing += 1
        else:
            _create_candidate(session, item, item_sig)
            summary.new_candidates += 1

    session.flush()
    return summary


def _find_best_candidate(
    session: Session, item: SignalItem, item_sig: _ItemSignals
) -> tuple[Optional[SignalCandidate], float, list[str]]:
    if not item.posted_at:
        cutoff = None
    else:
        cutoff = item.posted_at - dt.timedelta(hours=MAX_CANDIDATE_AGE_HOURS)

    stmt = select(SignalCandidate).where(SignalCandidate.state.in_(ATTACHABLE_STATES))
    if item_sig.topic_ids:
        stmt = stmt.join(CandidateTopicMatch, CandidateTopicMatch.candidate_id == SignalCandidate.id).where(
            CandidateTopicMatch.topic_id.in_(item_sig.topic_ids)
        )
    if cutoff is not None:
        stmt = stmt.where(SignalCandidate.latest_observed_at >= cutoff)

    best: Optional[SignalCandidate] = None
    best_score = 0.0
    best_reasons: list[str] = []
    seen_ids: set[int] = set()

    for candidate in session.scalars(stmt.distinct()):
        if candidate.id in seen_ids:
            continue
        seen_ids.add(candidate.id)

        cand_sig = _collect_candidate_signals(session, candidate)
        score, reasons = _score_attachment(item_sig, cand_sig)

        gap_hours = None
        if item.posted_at and candidate.latest_observed_at:
            gap_hours = abs((item.posted_at - candidate.latest_observed_at).total_seconds()) / 3600
            reasons.append(f"publication gap: {gap_hours:.1f} hours")

        if score > best_score:
            best, best_score, best_reasons = candidate, score, reasons

    return best, best_score, best_reasons


def _collect_candidate_signals(session: Session, candidate: SignalCandidate) -> _ItemSignals:
    sig = _ItemSignals()
    for row in session.execute(
        select(CandidateTopicMatch.topic_id, CandidateTopicMatch.matched_text)
        .where(CandidateTopicMatch.candidate_id == candidate.id)
    ):
        sig.topic_ids.add(row[0])
        sig.topic_texts[row[0]] = row[1]

    member_ids = [row[0] for row in session.execute(
        select(CandidateSignalItem.signal_item_id).where(CandidateSignalItem.candidate_id == candidate.id)
    )]
    texts = []
    for member_id in member_ids:
        member = session.get(SignalItem, member_id)
        if not member:
            continue
        texts.append(member.normalized_text or "")
        for m in session.scalars(select(SignalEntityMention).where(SignalEntityMention.signal_item_id == member_id)):
            if m.status == SignalMentionStatus.REJECTED:
                continue
            normalized = normalize_phrase(m.candidate_text)
            sig.display_text[normalized] = m.candidate_text
            if m.proposed_entity_type in ARTIFACT_ENTITY_TYPES:
                sig.artifact_texts.add(normalized)
            elif m.proposed_entity_type in SUBJECT_ENTITY_TYPES:
                sig.subject_entity_texts.add(normalized)
    sig.text = " ".join(texts)[:2000]
    return sig


def _attach(session: Session, candidate: SignalCandidate, item: SignalItem, reasons: list[str]) -> None:
    if candidate.state == SignalCandidateState.STALE:
        candidate.state = SignalCandidateState.ACTIVE  # new evidence reactivates a stale candidate
    session.add(CandidateSignalItem(
        candidate_id=candidate.id, signal_item_id=item.id,
        attach_reasons=json.dumps(reasons),
    ))
    candidate.item_count += 1
    if item.posted_at and (not candidate.latest_observed_at or item.posted_at > candidate.latest_observed_at):
        candidate.latest_observed_at = item.posted_at
    if item.posted_at and (not candidate.first_observed_at or item.posted_at < candidate.first_observed_at):
        candidate.first_observed_at = item.posted_at
    candidate.clustering_version = CLUSTERING_VERSION

    _sync_candidate_topics_and_entities(session, candidate, item)
    recompute_independence_groups(session, candidate)

    # brief section 15: new evidence resurfaces a seen candidate only when
    # it's genuinely new (its own independence group), not a duplicate/
    # dependent follow-up of something already accounted for.
    if candidate.seen_at is not None:
        from semi_intel.domain.models import SignalIndependenceGroupMember, SignalIndependenceGroup
        group_id = session.execute(
            select(SignalIndependenceGroupMember.group_id).where(
                SignalIndependenceGroupMember.signal_item_id == item.id
            )
        ).scalar_one_or_none()
        if group_id is not None:
            group_size = len(list(session.scalars(
                select(SignalIndependenceGroupMember.id).where(SignalIndependenceGroupMember.group_id == group_id)
            )))
            if group_size == 1:  # item is its own independent group -> genuinely new
                candidate.seen_at = None


def _create_candidate(session: Session, item: SignalItem, item_sig: _ItemSignals) -> SignalCandidate:
    candidate = SignalCandidate(
        fingerprint=_make_fingerprint(item),
        title=_title_for(item_sig, item),
        state=SignalCandidateState.ACTIVE,
        first_observed_at=item.posted_at or item.collected_at,
        latest_observed_at=item.posted_at or item.collected_at,
        item_count=0,
        clustering_version=CLUSTERING_VERSION,
    )
    session.add(candidate)
    session.flush()
    _attach(session, candidate, item, ["seeded a new candidate"])
    return candidate


def _sync_candidate_topics_and_entities(session: Session, candidate: SignalCandidate, item: SignalItem) -> None:
    for tm in session.scalars(select(SignalTopicMatch).where(SignalTopicMatch.signal_item_id == item.id)):
        existing = session.execute(
            select(CandidateTopicMatch.id).where(
                CandidateTopicMatch.candidate_id == candidate.id, CandidateTopicMatch.topic_id == tm.topic_id
            )
        ).first()
        if not existing:
            session.add(CandidateTopicMatch(candidate_id=candidate.id, topic_id=tm.topic_id, matched_text=tm.matched_text))
            if candidate.primary_topic_id is None:
                candidate.primary_topic_id = tm.topic_id

    for m in session.scalars(select(SignalEntityMention).where(SignalEntityMention.signal_item_id == item.id)):
        if not m.resolved_entity_id:
            if m.proposed_entity_type in ARTIFACT_ENTITY_TYPES and not candidate.strongest_artifact_type:
                candidate.strongest_artifact_type = m.proposed_entity_type
            continue
        existing = session.execute(
            select(CandidateEntity.id).where(
                CandidateEntity.candidate_id == candidate.id, CandidateEntity.entity_id == m.resolved_entity_id
            )
        ).first()
        if not existing:
            from semi_intel.domain.enums import CandidateEntityRole
            role = CandidateEntityRole.SUBJECT if m.proposed_entity_type in SUBJECT_ENTITY_TYPES else CandidateEntityRole.MENTIONED
            session.add(CandidateEntity(candidate_id=candidate.id, entity_id=m.resolved_entity_id, role=role))
