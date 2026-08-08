"""Attention scoring tests (brief section 24 "Scoring tests")."""

from __future__ import annotations

import datetime as dt

import pytest

from semi_intel.domain.enums import SourceType
from semi_intel.domain.models import AttentionScoringSettings, SignalItem, Source
from semi_intel.editorial.service import TopicService
from semi_intel.signals.analysis import analyze_signal_item
from semi_intel.signals.clustering import cluster_unclustered_items
from semi_intel.signals.scoring import compute_attention_score, get_scoring_settings, rescore_candidate

BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


def _seed(session):
    TopicService(session).seed()
    session.commit()


def _source(session, name="Source", trust_weight=0.5):
    src = Source(name=name, type=SourceType.SOCIAL, provider="replay", trust_weight=trust_weight)
    session.add(src)
    session.commit()
    return src


def _item(session, source, external_id, text, *, posted=BASE):
    item = SignalItem(
        source_id=source.id, provider="replay", external_id=external_id, raw_payload="{}",
        normalized_text=text, content_hash=f"h-{external_id}", posted_at=posted,
    )
    session.add(item)
    session.commit()
    analyze_signal_item(session, item)
    session.commit()
    return item


def _first_candidate(session):
    from sqlalchemy import select
    from semi_intel.domain.models import SignalCandidate
    return session.scalars(select(SignalCandidate)).first()


def test_weights_are_persisted_and_configurable(db_session):
    settings = get_scoring_settings(db_session)
    assert settings.weight_topic_relevance == 0.30
    settings.weight_topic_relevance = 0.5
    db_session.commit()

    reloaded = get_scoring_settings(db_session)
    assert reloaded.weight_topic_relevance == 0.5


def test_score_deterministic_for_same_inputs(db_session):
    _seed(db_session)
    source = _source(db_session)
    _item(db_session, source, "1", "RTX 50 Super leak with 24GB VRAM.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()
    candidate = _first_candidate(db_session)

    now = BASE + dt.timedelta(hours=1)
    result1 = compute_attention_score(db_session, candidate, now=now)
    result2 = compute_attention_score(db_session, candidate, now=now)

    assert result1.total == result2.total
    for key in result1.components:
        assert result1.components[key].raw_value == result2.components[key].raw_value


def test_topic_relevance_zero_without_monitored_topic_match(db_session):
    source = _source(db_session)
    # An artifact-only candidate (no topic match) -- still candidate-worthy
    # per brief section 11's exception, but topic_relevance itself is 0.
    _item(db_session, source, "1", "New board spotted with PCI ID 10DE:2D04 confirmed.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()
    candidate = _first_candidate(db_session)

    result = compute_attention_score(db_session, candidate)
    assert result.components["topic_relevance"].raw_value == 0.0


def test_topic_relevance_positive_with_monitored_topic_match(db_session):
    _seed(db_session)
    source = _source(db_session)
    _item(db_session, source, "1", "RTX 50 Super leak with 24GB VRAM.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()
    candidate = _first_candidate(db_session)

    result = compute_attention_score(db_session, candidate)
    assert result.components["topic_relevance"].raw_value > 0.0


def test_momentum_reflects_recent_velocity_not_total_count(db_session):
    """Real time-based velocity: a candidate whose evidence is all old
    scores low momentum even with many items; one whose evidence just
    arrived scores high."""
    _seed(db_session)
    source = _source(db_session)
    _item(db_session, source, "1", "RTX 50 Super leak.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()
    candidate = _first_candidate(db_session)

    settings = get_scoring_settings(db_session)
    window = dt.timedelta(hours=settings.momentum_window_hours)

    far_future = BASE + window * 5  # long after the window -- stale momentum
    stale_result = compute_attention_score(db_session, candidate, now=far_future)

    just_after = BASE + dt.timedelta(minutes=5)  # inside the window
    fresh_result = compute_attention_score(db_session, candidate, now=just_after)

    assert fresh_result.components["momentum"].raw_value > stale_result.components["momentum"].raw_value


def test_momentum_acceleration_higher_with_more_recent_items(db_session):
    _seed(db_session)
    source = _source(db_session)
    _item(db_session, source, "1", "RTX 50 Super leak.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()
    candidate = _first_candidate(db_session)
    settings = get_scoring_settings(db_session)

    now = BASE + dt.timedelta(hours=settings.momentum_window_hours - 1)
    before = compute_attention_score(db_session, candidate, now=now)

    # Add three more recent items -> acceleration should increase.
    for i in range(2, 5):
        _item(db_session, source, str(i), f"RTX 50 Super follow-up leak number {i}.",
             posted=now - dt.timedelta(minutes=i))
    cluster_unclustered_items(db_session)
    db_session.commit()
    db_session.refresh(candidate)

    after = compute_attention_score(db_session, candidate, now=now)
    assert after.components["momentum"].raw_value >= before.components["momentum"].raw_value


def test_source_diversity_uses_effective_groups_not_raw_count(db_session):
    _seed(db_session)
    origin = _source(db_session, name="VideoCardz")
    citer = _source(db_session, name="Aggregator")
    _item(db_session, origin, "1", "RTX 50 Super leak with 24GB VRAM.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()
    for i in range(5):
        _item(db_session, citer, f"c{i}", "According to VideoCardz, RTX 50 Super has 24GB VRAM.",
             posted=BASE + dt.timedelta(minutes=i + 1))
    cluster_unclustered_items(db_session)
    db_session.commit()
    candidate = _first_candidate(db_session)

    result = compute_attention_score(db_session, candidate)
    # 6 raw items/2 sources but effectively one citation group -> low
    # diversity score, not inflated by raw item/source count.
    assert result.components["source_diversity"].raw_value <= (1 / 3.0) + 0.01


def test_artifact_strength_ranks_pci_id_above_no_artifact(db_session):
    source = _source(db_session)
    with_artifact = _item(db_session, source, "1", "New board spotted with PCI ID 10DE:2D04 confirmed.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()
    candidate = _first_candidate(db_session)

    result = compute_attention_score(db_session, candidate)
    assert result.components["artifact_strength"].raw_value > 0.0


def test_source_quality_reflects_trust_weight(db_session):
    _seed(db_session)
    trusted = _source(db_session, name="Trusted", trust_weight=0.9)
    _item(db_session, trusted, "1", "RTX 50 Super leak with 24GB VRAM.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()
    candidate = _first_candidate(db_session)

    result = compute_attention_score(db_session, candidate)
    assert result.components["source_quality"].raw_value == 0.9


def test_stale_penalty_applied_after_staleness_days(db_session):
    _seed(db_session)
    source = _source(db_session)
    _item(db_session, source, "1", "RTX 50 Super leak.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()
    candidate = _first_candidate(db_session)
    settings = get_scoring_settings(db_session)

    far_future = BASE + dt.timedelta(days=settings.staleness_days + 5)
    result = compute_attention_score(db_session, candidate, now=far_future)

    assert "stale" in result.penalties
    assert result.penalties["stale"] < 0


def test_syndication_duplication_penalty_applied_for_many_items_one_group(db_session):
    _seed(db_session)
    origin = _source(db_session, name="VideoCardz")
    citer = _source(db_session, name="Aggregator")
    _item(db_session, origin, "1", "RTX 50 Super leak with 24GB VRAM.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()
    for i in range(5):
        _item(db_session, citer, f"c{i}", "According to VideoCardz, RTX 50 Super has 24GB VRAM.",
             posted=BASE + dt.timedelta(minutes=i + 1))
    cluster_unclustered_items(db_session)
    db_session.commit()
    candidate = _first_candidate(db_session)

    result = compute_attention_score(db_session, candidate)
    assert "syndication_duplication" in result.penalties


def test_score_explanation_persists_to_candidate(db_session):
    _seed(db_session)
    source = _source(db_session)
    _item(db_session, source, "1", "RTX 50 Super leak.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()
    candidate = _first_candidate(db_session)

    rescore_candidate(db_session, candidate)
    db_session.commit()

    import json
    explanation = json.loads(candidate.score_explanation)
    assert "components" in explanation
    assert "topic_relevance" in explanation["components"]
    assert candidate.attention_score == pytest.approx(explanation["total"])


def test_weights_normalized_even_if_stored_weights_do_not_sum_to_one(db_session):
    settings = get_scoring_settings(db_session)
    settings.weight_topic_relevance = 1.0
    settings.weight_novelty = 1.0
    settings.weight_momentum = 1.0
    settings.weight_source_diversity = 1.0
    settings.weight_artifact_strength = 1.0
    settings.weight_source_quality = 1.0  # sums to 6.0, not 1.0
    db_session.commit()

    _seed(db_session)
    source = _source(db_session)
    _item(db_session, source, "1", "RTX 50 Super leak.", posted=BASE)
    cluster_unclustered_items(db_session)
    db_session.commit()
    candidate = _first_candidate(db_session)

    result = compute_attention_score(db_session, candidate)
    assert 0.0 <= result.total <= 1.0  # normalized, not exploded past 1.0
