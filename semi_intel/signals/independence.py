"""Independence / echo-chamber discounting (brief section 10).

Signal Radar's own implementation counted distinct source IDs as
"independent," despite its documentation implying graph-based discounting --
PHASE0_AUDIT.md flags this explicitly. This module is the actual fix: it
groups a candidate's SignalItems into dependency clusters (an "independence
group") using concrete, explainable evidence, so twelve articles all citing
one VideoCardz report count as one origin plus follow-up coverage, not
twelve independent confirmations.

A group forms when any of these hold between two items:
  - same canonical URL (reuses semi_intel.editorial.service.canonical_url --
    one URL-normalization rule, not two);
  - quote/reply lineage (one item quotes or replies to the other);
  - same author_handle across sources (a cross-post, not independent
    corroboration);
  - explicit attribution: one item's text contains an attribution phrase
    (via/according to/reported by/citing/source/spotted by/hat tip/
    originally published by) immediately followed by wording that matches
    the other item's source name or author handle.

Grouping is transitive (plain union-find): if A cites B and C cites B, A/B/C
land in one group even though A and C don't directly reference each other.
Every group's `reason` records why it formed; the *strongest* reason is kept
when multiple apply. Everything left ungrouped is its own singleton
independent group.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from semi_intel.domain.models import (
    SignalCandidate,
    SignalIndependenceGroup,
    SignalIndependenceGroupMember,
    SignalItem,
    Source,
)
from semi_intel.editorial.service import canonical_url

ATTRIBUTION_PHRASES = (
    "according to", "via", "reported by", "reports", "citing", "source:",
    "spotted by", "hat tip", "h/t", "originally published by", "credit:",
)
_ATTRIBUTION_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in ATTRIBUTION_PHRASES) + r")\b\s*:?\s*([A-Za-z0-9 .]{2,40})",
    re.I,
)


@dataclass
class _Item:
    id: int
    source_id: int
    author_handle: str | None
    url: str | None
    quoted_signal_item_id: int | None
    reply_to_signal_item_id: int | None
    normalized_text: str
    source_name: str
    posted_at: dt.datetime | None = None


class _UnionFind:
    def __init__(self, ids: list[int]):
        self.parent = {i: i for i in ids}
        self.reason: dict[int, str] = {}

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int, reason: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        self.parent[rb] = ra
        # Keep the strongest/first reason recorded for the resulting root.
        self.reason.setdefault(ra, reason)


def extract_attributed_names(text: str) -> list[str]:
    return [m.group(1).strip().lower() for m in _ATTRIBUTION_RE.finditer(text or "")]


def group_items(items: list[_Item]) -> tuple[dict[int, int], dict[int, str]]:
    """Returns (item_id -> group_root_id, group_root_id -> reason)."""
    if not items:
        return {}, {}
    uf = _UnionFind([it.id for it in items])
    by_id = {it.id: it for it in items}
    by_url: dict[str, list[int]] = {}
    by_author: dict[str, list[int]] = {}

    for it in items:
        if it.url:
            by_url.setdefault(canonical_url(it.url), []).append(it.id)
        if it.author_handle:
            by_author.setdefault(it.author_handle.lower(), []).append(it.id)

    for ids in by_url.values():
        for other in ids[1:]:
            uf.union(ids[0], other, "same_url")

    for ids in by_author.values():
        for other in ids[1:]:
            uf.union(ids[0], other, "same_author")

    for it in items:
        for parent_ext in (it.quoted_signal_item_id, it.reply_to_signal_item_id):
            if parent_ext and parent_ext in by_id:
                uf.union(it.id, parent_ext, "lineage")

    # Explicit attribution: item A's text credits item B's source/author name.
    for a in items:
        names = extract_attributed_names(a.normalized_text)
        if not names:
            continue
        for b in items:
            if a.id == b.id:
                continue
            candidates = {b.source_name.lower(), (b.author_handle or "").lower()}
            if any(name in candidates or any(name in c for c in candidates if c) for name in names):
                uf.union(a.id, b.id, "citation")

    roots = {it.id: uf.find(it.id) for it in items}
    reasons = {root: uf.reason.get(root, "independent") for root in set(roots.values())}
    return roots, reasons


def recompute_independence_groups(session: Session, candidate: SignalCandidate) -> None:
    """Idempotent: clears and rebuilds this candidate's groups from its
    current membership. Never deletes SignalItems -- only the derived
    grouping rows."""
    from semi_intel.domain.models import CandidateSignalItem

    rows = list(session.execute(
        select(SignalItem, Source.name)
        .join(CandidateSignalItem, CandidateSignalItem.signal_item_id == SignalItem.id)
        .join(Source, Source.id == SignalItem.source_id)
        .where(CandidateSignalItem.candidate_id == candidate.id)
    ))
    items = [
        _Item(
            id=si.id, source_id=si.source_id, author_handle=si.author_handle, url=si.url,
            quoted_signal_item_id=si.quoted_signal_item_id, reply_to_signal_item_id=si.reply_to_signal_item_id,
            normalized_text=si.normalized_text or "", source_name=source_name, posted_at=si.posted_at,
        )
        for si, source_name in rows
    ]

    session.execute(
        delete(SignalIndependenceGroupMember).where(
            SignalIndependenceGroupMember.group_id.in_(
                select(SignalIndependenceGroup.id).where(SignalIndependenceGroup.candidate_id == candidate.id)
            )
        )
    )
    session.execute(delete(SignalIndependenceGroup).where(SignalIndependenceGroup.candidate_id == candidate.id))

    if not items:
        candidate.distinct_source_count = 0
        candidate.independent_source_group_count = 0
        return

    item_roots, root_reasons = group_items(items)
    by_id = {it.id: it for it in items}

    groups_by_root: dict[int, list[int]] = {}
    for item_id, root in item_roots.items():
        groups_by_root.setdefault(root, []).append(item_id)

    for root, member_ids in groups_by_root.items():
        # Origin = earliest-posted item in the group (best-effort "likely
        # origin" -- promotion/scoring can refine this further). Sort key
        # is (posted_at, id): posted_at is the actual chronological signal
        # (a group can span multiple providers with unrelated row-insertion
        # order); id is only the tiebreaker for items with an identical or
        # missing posted_at, not the primary key -- a prior version of this
        # function used id alone, which silently mispicked "origin" for any
        # group whose earliest-posted item wasn't also the earliest-inserted
        # one (v1.0.0 Candidate Intelligence Phase 2 fix).
        origin_id = min(
            member_ids,
            key=lambda i: (by_id[i].posted_at or dt.datetime.max, i),
        )
        group = SignalIndependenceGroup(
            candidate_id=candidate.id, origin_signal_item_id=origin_id,
            reason=root_reasons.get(root, "independent"),
        )
        session.add(group)
        session.flush()
        for member_id in member_ids:
            session.add(SignalIndependenceGroupMember(group_id=group.id, signal_item_id=member_id))

    candidate.distinct_source_count = len({it.source_id for it in items})
    candidate.independent_source_group_count = len(groups_by_root)
