"""Clustering + independence tests (brief section 24). Scenarios are taken
directly from the brief's own worked examples: two items mentioning only
NVIDIA/Xeon must not merge, an RTX 5090 pricing post and an RTX 50 Super
memory post must not merge just because both mention GeForce, a quoted
follow-up inherits lineage, twelve articles citing one VideoCardz report
count as one origin plus follow-ups (not twelve confirmations), and a
separate benchmark counts independently."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from semi_intel.domain.enums import SourceType
from semi_intel.domain.models import CandidateSignalItem, SignalCandidate, SignalItem, Source
from semi_intel.editorial.service import TopicService
from semi_intel.signals.analysis import analyze_signal_item
from semi_intel.signals.clustering import cluster_unclustered_items

BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


def _seed(session):
    TopicService(session).seed()
    session.commit()


def _source(session, name="Source"):
    src = Source(name=name, type=SourceType.SOCIAL, provider="replay")
    session.add(src)
    session.commit()
    return src


def _item(session, source, external_id, text, *, posted=BASE, quoted=None, reply_to=None, author=None):
    item = SignalItem(
        source_id=source.id, provider="replay", external_id=external_id, raw_payload="{}",
        normalized_text=text, content_hash=f"h-{external_id}", posted_at=posted,
        quoted_signal_item_id=quoted, reply_to_signal_item_id=reply_to, author_handle=author,
        url=f"https://example.com/{external_id}",
    )
    session.add(item)
    session.commit()
    analyze_signal_item(session, item)
    session.commit()
    return item


def _candidates(session):
    return list(session.scalars(select(SignalCandidate)))


def _members(session, candidate_id):
    return {row[0] for row in session.execute(
        select(CandidateSignalItem.signal_item_id).where(CandidateSignalItem.candidate_id == candidate_id)
    )}


# --- exact specific topic clusters ------------------------------------------

def test_exact_specific_topic_clusters_together(db_session):
    _seed(db_session)
    source = _source(db_session)
    _item(db_session, source, "1", "RTX 50 Super leak shows 24GB VRAM configuration.", posted=BASE)
    _item(db_session, source, "2", "RTX 50 Super memory config confirmed by second leaker.", posted=BASE + dt.timedelta(hours=3))

    cluster_unclustered_items(db_session)
    db_session.commit()

    candidates = _candidates(db_session)
    assert len(candidates) == 1
    assert candidates[0].item_count == 2


# --- broad company/family does not cluster ----------------------------------

def test_broad_company_mention_alone_does_not_cluster_or_seed_candidate(db_session):
    source = _source(db_session)
    _item(db_session, source, "1", "NVIDIA stock rises on strong earnings report today.")
    _item(db_session, source, "2", "NVIDIA announces new partnership with a cloud provider.")

    summary = cluster_unclustered_items(db_session)
    db_session.commit()

    assert summary.new_candidates == 0
    assert summary.suppressed_no_topic_or_artifact == 2
    assert _candidates(db_session) == []


def test_broad_product_family_alone_does_not_force_merge(db_session):
    """An RTX 5090 pricing post and an RTX 50 Super memory post must not
    merge just because both mention GeForce/RTX generically -- they are
    different specific monitored topics (RTX 50 Series vs RTX 50 Super are
    seeded separately)."""
    _seed(db_session)
    source = _source(db_session)
    _item(db_session, source, "1", "RTX 50 Series pricing leaked for the whole lineup, GeForce cards confirmed.", posted=BASE)
    _item(db_session, source, "2", "RTX 50 Super memory configuration leaked, new GeForce driver strings found.", posted=BASE + dt.timedelta(hours=1))

    cluster_unclustered_items(db_session)
    db_session.commit()

    candidates = _candidates(db_session)
    # Two distinct specific topics -> two distinct candidates, not merged
    # into one just because both are "GeForce"/"RTX".
    assert len(candidates) == 2


def test_xeon_alone_across_two_items_never_merges(db_session):
    source = _source(db_session)
    _item(db_session, source, "1", "Xeon processors dominate the server market this year.")
    _item(db_session, source, "2", "Xeon chip shipments are up according to the report.")

    cluster_unclustered_items(db_session)
    db_session.commit()

    assert _candidates(db_session) == []  # neither even became a candidate


# --- lineage -----------------------------------------------------------------

def test_quoted_follow_up_inherits_parent_candidacy(db_session):
    _seed(db_session)
    source = _source(db_session)
    parent = _item(db_session, source, "1", "RTX 50 Super leak: 24GB VRAM confirmed.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()

    # A bare reply with no topic/artifact of its own, but quoting the parent.
    _item(db_session, source, "2", "confirmed, can verify", posted=BASE + dt.timedelta(hours=1), quoted=parent.id)
    cluster_unclustered_items(db_session)
    db_session.commit()

    candidates = _candidates(db_session)
    assert len(candidates) == 1
    assert candidates[0].item_count == 2


# --- shared artifact clusters -------------------------------------------------

def test_shared_pci_id_clusters_strongly_even_without_topic_match(db_session):
    source = _source(db_session)
    _item(db_session, source, "1", "New board spotted with PCI ID 10DE:2D04 in a driver listing.", posted=BASE)
    _item(db_session, source, "2", "RSS report discusses PCI ID 10DE:2D04 found in the same driver package.", posted=BASE + dt.timedelta(hours=4))

    cluster_unclustered_items(db_session)
    db_session.commit()

    candidates = _candidates(db_session)
    assert len(candidates) == 1
    assert candidates[0].item_count == 2


# --- stale time window remains separate --------------------------------------

def test_items_outside_time_window_remain_separate_candidates(db_session):
    _seed(db_session)
    source = _source(db_session)
    _item(db_session, source, "1", "RTX 50 Super leak appears with new specs.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()

    # Same exact topic, but 10 days later -- outside the 72h window.
    _item(db_session, source, "2", "RTX 50 Super leak resurfaces with new specs.", posted=BASE + dt.timedelta(days=10))
    cluster_unclustered_items(db_session)
    db_session.commit()

    assert len(_candidates(db_session)) == 2


# --- idempotency ---------------------------------------------------------------

def test_stale_candidate_reactivates_via_lineage(db_session):
    """A quote/reply to a since-gone-stale candidate's item must reactivate
    it rather than being orphaned -- lineage is a direct causal link, unlike
    a merely-topically-similar item arriving much later (which correctly
    stays a separate candidate; see the time-window test above)."""
    from semi_intel.domain.enums import SignalCandidateState
    from semi_intel.signals.candidate_state import mark_stale_candidates

    _seed(db_session)
    source = _source(db_session)
    parent = _item(db_session, source, "1", "RTX 50 Super leak with 24GB VRAM.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()
    candidate = _candidates(db_session)[0]

    mark_stale_candidates(db_session, staleness_days=14, now=BASE + dt.timedelta(days=20))
    db_session.commit()
    assert candidate.state == SignalCandidateState.STALE

    _item(db_session, source, "2", "still checks out", posted=BASE + dt.timedelta(days=20), quoted=parent.id)
    cluster_unclustered_items(db_session)
    db_session.commit()

    assert len(_candidates(db_session)) == 1
    assert candidate.state == SignalCandidateState.ACTIVE
    assert candidate.item_count == 2


def test_dismissed_candidate_does_not_silently_regrow(db_session):
    from semi_intel.signals.candidate_state import dismiss

    _seed(db_session)
    source = _source(db_session)
    _item(db_session, source, "1", "RTX 50 Super leak with 24GB VRAM.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()
    candidate = _candidates(db_session)[0]

    dismiss(candidate, reason="not relevant")
    db_session.commit()

    _item(db_session, source, "2", "RTX 50 Super memory config reconfirmed.", posted=BASE + dt.timedelta(hours=2))
    cluster_unclustered_items(db_session)
    db_session.commit()

    # A new candidate forms instead of silently reattaching to a dismissed one.
    candidates = _candidates(db_session)
    assert len(candidates) == 2
    assert candidate.item_count == 1


def test_reclustering_is_idempotent_no_duplicate_membership(db_session):
    _seed(db_session)
    source = _source(db_session)
    _item(db_session, source, "1", "RTX 50 Super leak.", posted=BASE)

    cluster_unclustered_items(db_session)
    db_session.commit()
    first_count = _candidates(db_session)[0].item_count

    # Re-running with nothing new to cluster must not touch anything.
    summary = cluster_unclustered_items(db_session)
    db_session.commit()

    assert summary.items_processed == 0
    assert _candidates(db_session)[0].item_count == first_count


# --- independence grouping ----------------------------------------------------

def test_twelve_citing_articles_count_as_one_origin_plus_followups(db_session):
    """Twelve articles citing one VideoCardz report must not count as twelve
    independent confirmations -- explicit attribution groups them with the
    origin (whose Source is itself named "VideoCardz", the realistic shape
    of this scenario: VideoCardz is a registered/tracked source, and twelve
    OTHER sites explicitly credit it)."""
    _seed(db_session)
    origin_source = _source(db_session, name="VideoCardz")
    citing_source = _source(db_session, name="Aggregator Site")
    _item(db_session, origin_source, "origin", "RTX 50 Super leak: 24GB VRAM confirmed.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()

    for i in range(12):
        _item(
            db_session, citing_source, f"cite-{i}",
            "According to VideoCardz, RTX 50 Super ships with 24GB VRAM.",
            posted=BASE + dt.timedelta(hours=1, minutes=i),
        )
    cluster_unclustered_items(db_session)
    db_session.commit()

    candidates = _candidates(db_session)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.item_count == 13
    # 13 raw items, but explicit citation groups all 12 followups with the
    # origin -- the point under test is the *group* count staying low
    # relative to item count, not 13 separate confirmations.
    assert candidate.independent_source_group_count < candidate.item_count
    assert candidate.independent_source_group_count <= 2


def test_independent_benchmark_evidence_counts_separately(db_session):
    """A separate benchmark-database entry supporting the same claim, with
    no citation/URL/author/lineage tie to the rest, must count as its own
    independent group."""
    _seed(db_session)
    source = _source(db_session)
    _item(db_session, source, "1", "RTX 50 Super leak: 24GB VRAM confirmed.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()

    _item(
        db_session, source, "2",
        "Geekbench listing for an unreleased NVIDIA part shows RTX 50 Super branding.",
        posted=BASE + dt.timedelta(hours=2),
    )
    cluster_unclustered_items(db_session)
    db_session.commit()

    candidate = _candidates(db_session)[0]
    assert candidate.independent_source_group_count == 2


def test_same_url_items_count_as_one_group(db_session):
    _seed(db_session)
    source = _source(db_session)
    item1 = SignalItem(
        source_id=source.id, provider="replay", external_id="1", raw_payload="{}",
        normalized_text="RTX 50 Super leak with 24GB VRAM.", content_hash="h1",
        posted_at=BASE, url="https://videocardz.com/rtx-50-super",
    )
    db_session.add(item1)
    db_session.commit()
    analyze_signal_item(db_session, item1)
    db_session.commit()
    cluster_unclustered_items(db_session)
    db_session.commit()

    item2 = SignalItem(
        source_id=source.id, provider="replay", external_id="2", raw_payload="{}",
        normalized_text="RTX 50 Super leak with 24GB VRAM, syndicated copy.", content_hash="h2",
        posted_at=BASE + dt.timedelta(hours=1), url="https://videocardz.com/rtx-50-super",
    )
    db_session.add(item2)
    db_session.commit()
    analyze_signal_item(db_session, item2)
    db_session.commit()
    cluster_unclustered_items(db_session)
    db_session.commit()

    candidate = _candidates(db_session)[0]
    assert candidate.item_count == 2
    assert candidate.independent_source_group_count == 1  # same URL -> one group


def test_same_author_cross_post_counts_once(db_session):
    _seed(db_session)
    source_x = _source(db_session, name="X account")
    source_bsky = _source(db_session, name="Bluesky mirror")

    item1 = SignalItem(
        source_id=source_x.id, provider="replay", external_id="1", raw_payload="{}",
        normalized_text="RTX 50 Super leak: 24GB VRAM confirmed.", content_hash="h1",
        posted_at=BASE, author_handle="iancutress",
    )
    db_session.add(item1)
    db_session.commit()
    analyze_signal_item(db_session, item1)
    db_session.commit()
    cluster_unclustered_items(db_session)
    db_session.commit()

    item2 = SignalItem(
        source_id=source_bsky.id, provider="replay", external_id="2", raw_payload="{}",
        normalized_text="RTX 50 Super leak: 24GB VRAM confirmed (identical cross-post).", content_hash="h2",
        posted_at=BASE + dt.timedelta(minutes=5), author_handle="iancutress",
    )
    db_session.add(item2)
    db_session.commit()
    analyze_signal_item(db_session, item2)
    db_session.commit()
    cluster_unclustered_items(db_session)
    db_session.commit()

    candidate = _candidates(db_session)[0]
    assert candidate.distinct_source_count == 2  # two different sources/platforms
    assert candidate.independent_source_group_count == 1  # but the same author -> one group


def test_score_explanation_shows_raw_and_effective_source_counts(db_session):
    """Every diversity/confidence explanation must show both raw distinct
    sources and effective independent groups (brief section 10)."""
    _seed(db_session)
    source = _source(db_session)
    _item(db_session, source, "1", "RTX 50 Super leak confirmed.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()

    from semi_intel.signals.scoring import compute_attention_score
    candidate = _candidates(db_session)[0]
    result = compute_attention_score(db_session, candidate)
    detail = result.components["source_diversity"].detail
    assert "independent group" in detail
    assert "raw distinct source" in detail
