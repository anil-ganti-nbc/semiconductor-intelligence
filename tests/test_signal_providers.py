"""Provider-level tests (brief section 24 "Provider tests"): fixture-driven,
never touch the network. Covers RSS fixture parsing, replay normalization,
cursor advancement, provider validation, and confirms the optional X extra
degrades gracefully when Playwright isn't installed."""

from __future__ import annotations

import os
from pathlib import Path

import feedparser
import pytest

from semi_intel.signals.providers import Cursor, ProviderUnavailable, ValidationError
from semi_intel.signals.providers.replay import ReplayProvider
from semi_intel.signals.providers.rss import RSSProvider

SAMPLE_FEED_PATH = "tests/fixtures/sample_feed.xml"


def _fixture_fetch_fn():
    with open(SAMPLE_FEED_PATH, "rb") as f:
        content = f.read()

    def fetch_fn(url: str):
        return feedparser.parse(content)

    return fetch_fn


def test_rss_provider_parses_fixture_and_normalizes():
    provider = RSSProvider(fetch_fn=_fixture_fetch_fn())
    result = provider.collect("https://example.com/feed", cursor=None)

    assert len(result.items) == 2
    assert result.next_cursor is not None

    signals = [provider.normalize(item) for item in result.items]
    titles = {s.title for s in signals}
    assert "Nova Lake spotted with 18A-P process node" in titles
    assert all(s.provider == "rss" for s in signals)
    assert all(s.posted_at is not None for s in signals)


def test_rss_provider_cursor_advances_and_dedupes():
    provider = RSSProvider(fetch_fn=_fixture_fetch_fn())
    first = provider.collect("https://example.com/feed", cursor=None)
    assert len(first.items) == 2

    # Re-collecting with the returned cursor yields nothing new -- an
    # unattended scheduler re-polling an unchanged feed is a cheap no-op.
    second = provider.collect("https://example.com/feed", cursor=first.next_cursor)
    assert second.items == []


def test_rss_provider_validate_rejects_non_url():
    provider = RSSProvider(fetch_fn=_fixture_fetch_fn())
    result = provider.validate("not-a-url")
    assert isinstance(result, ValidationError)


def test_rss_provider_validate_accepts_parseable_feed():
    provider = RSSProvider(fetch_fn=_fixture_fetch_fn())
    result = provider.validate("https://example.com/feed")
    assert result.provider == "rss"
    assert result.provider_key == "https://example.com/feed"


def test_replay_provider_incremental_collection():
    provider = ReplayProvider(fixtures={
        "ian": [
            {"external_id": "1", "posted_at": "2026-01-01T00:00:00Z", "text": "first", "author": "ian"},
            {"external_id": "2", "posted_at": "2026-01-02T00:00:00Z", "text": "second", "author": "ian"},
            {"external_id": "3", "posted_at": "2026-01-03T00:00:00Z", "text": "third", "author": "ian"},
        ]
    })

    first = provider.collect("ian", cursor=None)
    assert [i.external_id for i in first.items] == ["1", "2", "3"]
    assert first.next_cursor == Cursor("3")

    # Collecting again with the returned cursor should be a no-op.
    second = provider.collect("ian", cursor=first.next_cursor)
    assert second.items == []

    # A cursor mid-way through only returns what's newer.
    resumed = provider.collect("ian", cursor=Cursor("1"))
    assert [i.external_id for i in resumed.items] == ["2", "3"]


def test_replay_provider_normalize_preserves_lineage():
    provider = ReplayProvider(fixtures={})
    from semi_intel.signals.providers import RawItem

    raw = RawItem(external_id="9", payload={
        "external_id": "9", "text": "quoting a leak", "author": "ian",
        "quoted_external_id": "5", "reply_to_external_id": None,
        "posted_at": "2026-01-01T00:00:00Z",
    })
    signal = provider.normalize(raw)
    assert signal.quoted_external_id == "5"
    assert signal.reply_to_external_id is None


def test_replay_provider_validate():
    provider = ReplayProvider()
    ok = provider.validate("https://x.com/IanCutress")
    assert ok.provider_key == "IanCutress"
    bad = provider.validate("   ")
    assert isinstance(bad, ValidationError)


def test_x_provider_unavailable_without_playwright(monkeypatch):
    """Playwright is not installed in the test environment (it's an optional
    extra) -- constructing XProvider must raise ProviderUnavailable, not
    ImportError, and must never crash anything that merely imports the
    package (see the module-level import at the top of this file's sibling
    test_signal_collection.py, which imports semi_intel.signals.collection
    -- itself importing semi_intel.signals.providers.x -- without issue)."""
    import sys
    monkeypatch.setitem(sys.modules, "playwright", None)
    from semi_intel.signals.providers.x import XProvider

    with pytest.raises(ProviderUnavailable):
        XProvider()


def test_frozen_x_session_reuses_existing_windows_browser_cache(tmp_path, monkeypatch):
    import sys
    from semi_intel.signals.providers.x.session import configure_frozen_browser_cache

    local_app_data = tmp_path / "LocalAppData"
    cache = local_app_data / "ms-playwright"
    cache.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    assert configure_frozen_browser_cache() == cache
    assert Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]) == cache


def test_frozen_x_session_respects_explicit_browser_path(tmp_path, monkeypatch):
    import sys
    from semi_intel.signals.providers.x.session import configure_frozen_browser_cache

    explicit = tmp_path / "custom-browsers"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(explicit))

    assert configure_frozen_browser_cache() is None
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(explicit)
