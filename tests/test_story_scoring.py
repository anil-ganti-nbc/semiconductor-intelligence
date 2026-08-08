"""Pure unit tests for story scoring -- no database."""

from __future__ import annotations

import datetime as dt

from semi_intel.story_scoring.scoring import (
    MOMENTUM_WINDOW_DAYS,
    NOVELTY_WINDOW_DAYS,
    score_story,
)

NOW = dt.datetime(2026, 7, 18, 12, 0, 0)


def test_brand_new_claim_has_full_novelty():
    score = score_story(created_at=NOW, distinct_supporting_sources=0, recent_event_count=0, now=NOW)
    assert score.novelty == 1.0


def test_novelty_decays_to_zero_past_the_window():
    old = NOW - dt.timedelta(days=NOVELTY_WINDOW_DAYS + 5)
    score = score_story(created_at=old, distinct_supporting_sources=0, recent_event_count=0, now=NOW)
    assert score.novelty == 0.0


def test_novelty_decays_linearly_partway_through_window():
    halfway = NOW - dt.timedelta(days=NOVELTY_WINDOW_DAYS / 2)
    score = score_story(created_at=halfway, distinct_supporting_sources=0, recent_event_count=0, now=NOW)
    assert 0.4 < score.novelty < 0.6


def test_corroboration_is_capped():
    low = score_story(created_at=NOW, distinct_supporting_sources=1, recent_event_count=0, now=NOW)
    high = score_story(created_at=NOW, distinct_supporting_sources=10, recent_event_count=0, now=NOW)
    assert low.corroboration < high.corroboration
    assert high.corroboration == 1.0


def test_momentum_is_capped():
    low = score_story(created_at=NOW, distinct_supporting_sources=0, recent_event_count=1, now=NOW)
    high = score_story(created_at=NOW, distinct_supporting_sources=0, recent_event_count=100, now=NOW)
    assert low.momentum < high.momentum
    assert high.momentum == 1.0


def test_total_combines_all_three_signals():
    score = score_story(created_at=NOW, distinct_supporting_sources=3, recent_event_count=5, now=NOW)
    assert score.total == 1.0  # full credit on all three, weights sum to 1.0


def test_zero_signal_claim_scores_zero():
    old = NOW - dt.timedelta(days=NOVELTY_WINDOW_DAYS * 2)
    score = score_story(created_at=old, distinct_supporting_sources=0, recent_event_count=0, now=NOW)
    assert score.total == 0.0
    assert score.reasons == []


def test_reasons_are_populated_when_signals_are_present():
    score = score_story(created_at=NOW, distinct_supporting_sources=2, recent_event_count=3, now=NOW)
    assert any("novelty" in r for r in score.reasons)
    assert any("distinct supporting source" in r for r in score.reasons)
    assert any(f"last {MOMENTUM_WINDOW_DAYS} days" in r for r in score.reasons)
