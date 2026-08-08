from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select

from semi_intel.domain.enums import SourceSuggestionStatus, SourceType
from semi_intel.domain.models import (
    EditorialStory, Evidence, MonitoredTopic, Source, SourceSuggestion, StoryEvidence,
)
from semi_intel.editorial.feed_discovery import discover_feeds, valid_feed
from semi_intel.editorial.service import (
    EditorialDiscoveryService, TopicService, canonical_domain, canonical_url,
    is_noise_domain, match_topic, normalize_phrase,
)
from semi_intel.ingestion.hashing import hash_content


def _source(session, name="Hardware Wire", url="https://hardware.example/feed"):
    source = Source(name=name, type=SourceType.RSS, url=url, trust_weight=0.7)
    session.add(source)
    session.flush()
    return source


def _evidence(session, source, title, content="", url="https://hardware.example/story"):
    evidence = Evidence(
        source_id=source.id, title=title, raw_content=content or title,
        content_hash=hash_content(f"{title}|{content}"), url=url,
        observed_at=dt.datetime.utcnow(),
    )
    session.add(evidence)
    session.flush()
    return evidence


@pytest.mark.parametrize("value", ["RDNA5", "RDNA-5", "rdna 5", "RDNA—5"])
def test_topic_normalization_matches_common_variants(db_session, value):
    topic = MonitoredTopic(
        name="RDNA 5", normalized_name="rdna 5", keyword="RDNA 5",
        aliases="[]", category="AMD graphics", priority=.8,
    )
    assert normalize_phrase(value) == "rdna 5"
    assert match_topic(topic, f"Fresh {value} architecture details") == "RDNA 5"


def test_word_boundaries_avoid_arm_inside_unrelated_word(db_session):
    topic = MonitoredTopic(
        name="ARM", normalized_name="arm", keyword="ARM", aliases="[]",
        category="Processors", priority=.5,
    )
    assert match_topic(topic, "New ARM processor") == "ARM"
    assert match_topic(topic, "The company reported alarming results") is None


def test_seed_is_idempotent_and_duplicate_alias_is_rejected(db_session):
    service = TopicService(db_session)
    assert service.seed() > 40
    assert service.seed() == 0
    with pytest.raises(ValueError, match="RDNA 5"):
        service.validate_unique("My topic", "RDNA5", [])


def test_evidence_becomes_ranked_story_with_explanation(db_session):
    source = _source(db_session)
    evidence = _evidence(db_session, source, "New RDNA-5 architecture details emerge")
    story = EditorialDiscoveryService(db_session).process_evidence(evidence)
    db_session.commit()
    assert story is not None
    assert story.interest_score > 0
    assert "Matched: RDNA 5" in story.score_reasons
    assert story.seen_at is None


def test_backfill_and_story_link_are_idempotent(db_session):
    source = _source(db_session)
    _evidence(db_session, source, "Zen6 desktop launch rumor")
    service = EditorialDiscoveryService(db_session)
    first = service.backfill()
    second = service.backfill()
    assert first.stories_created == 1
    assert second.stories_created == 0
    assert db_session.scalar(select(func.count()).select_from(StoryEvidence)) == 1


def test_conservative_clustering_preserves_seen_and_flags_new_coverage(db_session):
    first_source = _source(db_session)
    second_source = _source(db_session, "Second Wire", "https://second.example/rss")
    service = EditorialDiscoveryService(db_session)
    first = service.process_evidence(_evidence(
        db_session, first_source, "RDNA 5 architecture details emerge",
        url="https://hardware.example/rdna",
    ))
    first.seen_at = dt.datetime.utcnow()
    service.process_evidence(_evidence(
        db_session, second_source, "More RDNA 5 architecture details emerge",
        url="https://second.example/rdna",
    ))
    assert db_session.scalar(select(func.count()).select_from(EditorialStory)) == 1
    assert first.seen_at is not None
    assert first.new_coverage_count == 1
    assert first.coverage_count == 2


def test_citations_create_explainable_unknown_source_suggestion(db_session):
    source = _source(db_session)
    service = EditorialDiscoveryService(db_session)
    for number in range(2):
        service.process_evidence(_evidence(
            db_session, source, f"RTX 50 Super report details {number}",
            '<p>According to <a href="https://origin-news.example/report?utm_source=x">Origin News</a>.</p>',
            url=f"https://hardware.example/story-{number}",
        ))
    suggestion = db_session.scalar(
        select(SourceSuggestion).where(SourceSuggestion.domain == "origin-news.example")
    )
    assert suggestion is not None
    assert suggestion.status == SourceSuggestionStatus.PENDING
    assert suggestion.appearances == 2
    assert "Referenced 2 time(s)" in suggestion.reasons


def test_domain_and_url_normalization_and_noise_filter():
    assert canonical_domain("https://www.Example.com/a") == "example.com"
    assert canonical_url("http://www.example.com/a/amp?utm_source=x&id=4") == "https://example.com/a?id=4"
    assert is_noise_domain("cdn.google-analytics.com")
    assert not is_noise_domain("videocardz.com")


def test_feed_autodiscovery_validates_candidates():
    html = b'<link rel="alternate" type="application/rss+xml" href="/news.xml">'
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>X</title>
      <item><title>One</title><link>https://example.com/one</link></item>
      </channel></rss>"""
    payloads = {"https://example.com": html, "https://example.com/news.xml": feed}
    feeds = discover_feeds("https://example.com", fetcher=lambda url: payloads.get(url, b"not a feed"))
    assert feeds == ["https://example.com/news.xml"]
    assert valid_feed(feed)
    assert not valid_feed(b"<html>not a feed</html>")


def test_valid_feed_accepts_entries_despite_non_fatal_bozo():
    """Real-world feeds routinely trip feedparser's `bozo` flag (unescaped
    entities, truncation, encoding quirks) while still yielding usable
    entries -- feedparser is a liberal parser by design. valid_feed() must
    not reject a feed solely because bozo is set if entries were still
    extracted (regression for the Find Feed defect that rejected a real,
    working feed -- chipsandcheese.com/feed -- purely because of a
    truncation-induced bozo flag)."""
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>X</title>
      <item><title>Foo & Bar</title><link>https://example.com/one</link></item>
      </channel></rss>"""
    import feedparser
    parsed = feedparser.parse(feed)
    assert parsed.bozo
    assert parsed.entries
    assert valid_feed(feed)
