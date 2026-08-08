"""Origin Graph (v1.0.0 Candidate Intelligence, Phase 2).

This module does not invent a new independence signal -- it structures
the *existing* `SignalIndependenceGroup`/`SignalIndependenceGroupMember`
data (semi_intel/signals/independence.py) as an explicit provenance graph:
one origin node per group, edges from origin to every echo in that group,
plus lineage edges (quote/reply) wherever the underlying SignalItems
record one, even across group boundaries -- so a chain like

    original Weibo -> Golden Pig -> VideoCardz -> forum repost -> Reddit

renders as an actual path, not just a flat "N sources" count. Never
infers independence from timestamps alone; every edge here traces back to
one of independence.py's four concrete rules (same_url, same_author,
lineage, citation) or an explicit quote/reply reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.models import (
    CandidateSignalItem,
    SignalCandidate,
    SignalIndependenceGroup,
    SignalIndependenceGroupMember,
    SignalItem,
    Source,
)


@dataclass
class OriginNode:
    signal_item_id: int
    source_id: int
    source_name: str
    title: str | None
    url: str | None
    posted_at: str | None
    is_origin: bool
    group_id: int

    def to_dict(self) -> dict:
        return {
            "signal_item_id": self.signal_item_id, "source_id": self.source_id,
            "source_name": self.source_name, "title": self.title, "url": self.url,
            "posted_at": self.posted_at, "is_origin": self.is_origin, "group_id": self.group_id,
        }


@dataclass
class OriginEdge:
    from_signal_item_id: int
    to_signal_item_id: int
    reason: str

    def to_dict(self) -> dict:
        return {"from": self.from_signal_item_id, "to": self.to_signal_item_id, "reason": self.reason}


@dataclass
class OriginGraph:
    origin: OriginNode | None
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    independent_confirmations: int = 0
    echoes: int = 0

    @property
    def summary(self) -> str:
        if not self.nodes:
            return "No evidence yet."
        conf_word = "confirmation" if self.independent_confirmations == 1 else "confirmations"
        echo_word = "echo" if self.echoes == 1 else "echoes"
        return (
            f"{self.independent_confirmations} independent {conf_word}, "
            f"{self.echoes} {echo_word} of the earliest report"
        )

    def to_dict(self) -> dict:
        return {
            "origin": self.origin.to_dict() if self.origin else None,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "independent_confirmations": self.independent_confirmations,
            "echoes": self.echoes,
            "summary": self.summary,
        }


def build_origin_graph(session: Session, candidate: SignalCandidate) -> OriginGraph:
    rows = list(session.execute(
        select(SignalItem, Source.name)
        .join(CandidateSignalItem, CandidateSignalItem.signal_item_id == SignalItem.id)
        .join(Source, Source.id == SignalItem.source_id)
        .where(CandidateSignalItem.candidate_id == candidate.id)
    ))
    if not rows:
        return OriginGraph(origin=None)

    items_by_id = {si.id: (si, source_name) for si, source_name in rows}

    groups = list(session.scalars(
        select(SignalIndependenceGroup).where(SignalIndependenceGroup.candidate_id == candidate.id)
    ))
    members_by_group = {
        group.id: [row[0] for row in session.execute(
            select(SignalIndependenceGroupMember.signal_item_id)
            .where(SignalIndependenceGroupMember.group_id == group.id)
        )]
        for group in groups
    }

    def _node(signal_item_id: int, group_id: int, is_origin: bool) -> OriginNode:
        si, source_name = items_by_id[signal_item_id]
        return OriginNode(
            signal_item_id=si.id, source_id=si.source_id, source_name=source_name,
            title=si.title, url=si.url,
            posted_at=si.posted_at.isoformat() if si.posted_at else None,
            is_origin=is_origin, group_id=group_id,
        )

    nodes: list[OriginNode] = []
    edges: list[OriginEdge] = []
    echoes = 0
    if groups:
        for group in groups:
            member_ids = members_by_group.get(group.id, [])
            origin_id = group.origin_signal_item_id or (member_ids[0] if member_ids else None)
            for member_id in member_ids:
                nodes.append(_node(member_id, group.id, is_origin=(member_id == origin_id)))
                if origin_id is not None and member_id != origin_id:
                    edges.append(OriginEdge(origin_id, member_id, reason=group.reason))
                    echoes += 1
    else:
        # recompute_independence_groups() hasn't run for this candidate yet
        # (e.g. it was just clustered and independence grouping is a
        # separate step) -- fall back to independence.py's own definition
        # of "no group data": every item is its own singleton independent
        # group, per its module docstring ("Everything left ungrouped is
        # its own singleton independent group"). No echoes can be claimed
        # without real grouping data to back them.
        for signal_item_id in items_by_id:
            nodes.append(_node(signal_item_id, group_id=0, is_origin=True))

    # Lineage edges the underlying SignalItems recorded explicitly, even
    # when the two items landed in different independence groups (e.g. a
    # different-domain repost that cites the original by URL/quote but
    # didn't match any of independence.py's same-group rules).
    for si, _ in rows:
        for parent_id, reason in (
            (si.quoted_signal_item_id, "quotes"), (si.reply_to_signal_item_id, "replies to"),
        ):
            if parent_id and parent_id in items_by_id:
                edges.append(OriginEdge(parent_id, si.id, reason=reason))

    # The overall candidate origin: the earliest-posted origin node across
    # all groups (falls back to the earliest node overall if no group has
    # an origin recorded, which recompute_independence_groups always sets
    # when there's at least one item).
    origin_candidates = [n for n in nodes if n.is_origin] or nodes
    origin = min(
        origin_candidates,
        key=lambda n: (n.posted_at or "9999", n.signal_item_id),
    )

    return OriginGraph(
        origin=origin, nodes=nodes, edges=edges,
        independent_confirmations=len(groups) if groups else len(nodes), echoes=echoes,
    )
