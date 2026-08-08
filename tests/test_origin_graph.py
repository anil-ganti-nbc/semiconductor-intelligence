"""Origin Graph (v1.0.0 Candidate Intelligence, Phase 2)."""

from __future__ import annotations

import datetime as dt

from semi_intel.domain.enums import SignalCandidateState, SourceType
from semi_intel.domain.models import CandidateSignalItem, SignalCandidate, SignalItem, Source
from semi_intel.signals.independence import recompute_independence_groups
from semi_intel.signals.origin_graph import build_origin_graph

BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


def _source(session, name):
    src = Source(name=name, type=SourceType.SOCIAL, provider="rss")
    session.add(src)
    session.commit()
    return src


def _candidate(session):
    cand = SignalCandidate(fingerprint=f"fp-{id(object())}", title="Test", state=SignalCandidateState.ACTIVE,
                            first_observed_at=BASE, latest_observed_at=BASE)
    session.add(cand)
    session.commit()
    return cand


def _attach(session, candidate, item):
    session.add(CandidateSignalItem(candidate_id=candidate.id, signal_item_id=item.id))
    session.commit()


def test_origin_graph_is_empty_for_a_candidate_with_no_items(db_session):
    candidate = _candidate(db_session)

    graph = build_origin_graph(db_session, candidate)

    assert graph.origin is None
    assert graph.nodes == []
    assert graph.summary == "No evidence yet."


def test_origin_is_the_earliest_posted_item_not_the_lowest_id(db_session):
    """Regression test for the origin-selection bug found during v1.0.0
    audit: recompute_independence_groups() previously picked the group's
    origin by SQL row id, contradicting its own 'earliest-posted' claim.
    Inserting the later-posted item FIRST (so it gets the lower id) proves
    the fix actually uses posted_at."""
    candidate = _candidate(db_session)
    source_a = _source(db_session, "Source A")
    source_b = _source(db_session, "Source B")

    later_item = SignalItem(
        source_id=source_a.id, provider="rss", external_id="later", raw_payload="{}",
        normalized_text="text", content_hash="h-later", posted_at=BASE + dt.timedelta(hours=3),
        url="https://example.com/shared-story",
    )
    db_session.add(later_item)
    db_session.commit()
    _attach(db_session, candidate, later_item)

    earlier_item = SignalItem(
        source_id=source_b.id, provider="rss", external_id="earlier", raw_payload="{}",
        normalized_text="text", content_hash="h-earlier", posted_at=BASE,
        url="https://example.com/shared-story",  # same canonical URL -> same independence group
    )
    db_session.add(earlier_item)
    db_session.commit()
    _attach(db_session, candidate, earlier_item)

    assert earlier_item.id > later_item.id  # earlier-posted item has the HIGHER row id

    recompute_independence_groups(db_session, candidate)
    db_session.commit()

    graph = build_origin_graph(db_session, candidate)

    assert graph.origin.signal_item_id == earlier_item.id


def test_echo_via_citation_is_an_edge_from_origin(db_session):
    candidate = _candidate(db_session)
    origin_source = _source(db_session, "Golden Pig")
    echo_source = _source(db_session, "VideoCardz")

    origin_item = SignalItem(
        source_id=origin_source.id, provider="rss", external_id="o", raw_payload="{}",
        normalized_text="RTX 50 Super leak: 24GB VRAM.", content_hash="ho", posted_at=BASE,
    )
    db_session.add(origin_item)
    db_session.commit()
    _attach(db_session, candidate, origin_item)

    echo_item = SignalItem(
        source_id=echo_source.id, provider="rss", external_id="e", raw_payload="{}",
        normalized_text="According to Golden Pig, RTX 50 Super has 24GB VRAM.",
        content_hash="he", posted_at=BASE + dt.timedelta(hours=1),
    )
    db_session.add(echo_item)
    db_session.commit()
    _attach(db_session, candidate, echo_item)

    recompute_independence_groups(db_session, candidate)
    db_session.commit()

    graph = build_origin_graph(db_session, candidate)

    assert graph.independent_confirmations == 1
    assert graph.echoes == 1
    assert graph.origin.signal_item_id == origin_item.id
    edge = graph.edges[0]
    assert edge.from_signal_item_id == origin_item.id
    assert edge.to_signal_item_id == echo_item.id
    assert edge.reason == "citation"
    assert "1 independent confirmation" in graph.summary
    assert "1 echo" in graph.summary


def test_lineage_edge_is_recorded_explicitly(db_session):
    """A reply/quote reference must produce its own graph edge (reason=
    'replies to'/'quotes'), independent of whichever reason
    independence.py's union-find happened to record for the group as a
    whole -- so the UI can always show *why* two items are linked, not
    just that they share a group."""
    candidate = _candidate(db_session)
    source_a = _source(db_session, "Forum Post")
    source_b = _source(db_session, "Unrelated Repost")

    parent_item = SignalItem(
        source_id=source_a.id, provider="rss", external_id="p", raw_payload="{}",
        normalized_text="Original claim.", content_hash="hp", posted_at=BASE,
    )
    db_session.add(parent_item)
    db_session.commit()
    _attach(db_session, candidate, parent_item)

    child_item = SignalItem(
        source_id=source_b.id, provider="rss", external_id="c", raw_payload="{}",
        normalized_text="Different wording, no shared author/url.", content_hash="hc",
        posted_at=BASE + dt.timedelta(hours=1), reply_to_signal_item_id=parent_item.id,
    )
    db_session.add(child_item)
    db_session.commit()
    _attach(db_session, candidate, child_item)

    recompute_independence_groups(db_session, candidate)
    db_session.commit()

    graph = build_origin_graph(db_session, candidate)

    lineage_edges = [e for e in graph.edges if e.reason == "replies to"]
    assert len(lineage_edges) == 1
    assert lineage_edges[0].from_signal_item_id == parent_item.id
    assert lineage_edges[0].to_signal_item_id == child_item.id
