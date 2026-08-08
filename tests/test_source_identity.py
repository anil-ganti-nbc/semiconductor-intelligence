from __future__ import annotations

from semi_intel.domain.enums import SourceType
from semi_intel.domain.models import Source
from semi_intel.source_identity import canonical_feed_key, find_source_by_feed_url


def test_canonical_feed_key_normalizes_transport_and_tracking():
    assert canonical_feed_key(
        "HTTP://www.Example.com:80/feed/?utm_source=x&category=gpu#latest"
    ) == canonical_feed_key(
        "https://example.com/feed?category=gpu"
    )


def test_canonical_feed_key_keeps_distinct_feed_paths_and_queries():
    assert canonical_feed_key("https://example.com/feed/gpu") != canonical_feed_key(
        "https://example.com/feed/cpu"
    )
    assert canonical_feed_key("https://example.com/feed?category=gpu") != canonical_feed_key(
        "https://example.com/feed?category=cpu"
    )


def test_find_source_by_feed_url_crosses_legacy_and_radar_identity(db_session):
    legacy = Source(
        name="Legacy Feed",
        type=SourceType.RSS,
        url="http://www.example.com/feed/?utm_medium=rss",
        provider="manual",
    )
    db_session.add(legacy)
    db_session.commit()

    assert find_source_by_feed_url(db_session, "https://example.com/feed") is legacy
