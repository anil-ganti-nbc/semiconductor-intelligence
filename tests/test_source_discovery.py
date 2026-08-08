"""Multi-provider Suggested Sources discovery generators (v0.9.4). Real-
shaped fixtures: SignalItem rows with url/expanded_links/normalized_text
populated the same way the RSS and X providers actually populate them."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from semi_intel.domain.enums import SourceSuggestionKind, SourceSuggestionStatus, SourceType
from semi_intel.domain.models import Source, SourceSuggestion, SignalItem
from semi_intel.signals.source_discovery import (
    discover_domain_candidates,
    discover_github_candidates,
    discover_subreddit_candidates,
    run_source_discovery,
)


def _source(session, name="Aggregator"):
    src = Source(name=name, type=SourceType.SOCIAL, provider="rss")
    session.add(src)
    session.commit()
    return src


def _item(session, source, ext_id, *, url=None, links=None, text=""):
    item = SignalItem(
        source_id=source.id, provider="rss", external_id=ext_id, raw_payload="{}",
        normalized_text=text, content_hash=f"h-{ext_id}", url=url,
        expanded_links=json.dumps(links or []),
    )
    session.add(item)
    session.commit()
    return item


# --- domain generator --------------------------------------------------

def test_domain_below_min_mentions_is_not_suggested(db_session):
    source = _source(db_session)
    for i in range(2):  # below MIN_MENTIONS=3
        _item(db_session, source, str(i), url="https://chipsandcheese.com/article")

    result = discover_domain_candidates(db_session)

    assert result.created == 0
    assert result.rejected == 1
    assert list(db_session.scalars(select(SourceSuggestion))) == []


def test_domain_created_once_threshold_met(db_session):
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), url="https://chipsandcheese.com/article")

    result = discover_domain_candidates(db_session)

    assert result.created == 1
    suggestion = db_session.scalars(select(SourceSuggestion)).one()
    assert suggestion.kind == SourceSuggestionKind.DOMAIN
    assert suggestion.domain == "chipsandcheese.com"
    assert suggestion.platform is None
    assert suggestion.appearances == 4
    assert suggestion.status == SourceSuggestionStatus.PENDING


def test_domain_urls_embedded_in_text_are_also_mined(db_session):
    """SignalItem.url is only one of three places a URL can live -- text
    bodies (e.g. an X post quoting a link) must be mined too."""
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), text="Big leak over at https://chipsandcheese.com/deep-dive today")

    result = discover_domain_candidates(db_session)

    assert result.created == 1
    assert db_session.scalars(select(SourceSuggestion)).one().domain == "chipsandcheese.com"


def test_domain_urls_in_expanded_links_are_mined(db_session):
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), links=["https://chipsandcheese.com/expanded"])

    result = discover_domain_candidates(db_session)

    assert result.created == 1


def test_noise_and_registered_domains_are_excluded(db_session):
    source = _source(db_session)
    db_session.add(Source(name="Registered", type=SourceType.RSS, url="https://already-registered.example/feed"))
    db_session.commit()
    for i in range(5):
        _item(db_session, source, f"noise-{i}", url="https://reddit.com/r/somewhere")  # NOISE_DOMAINS
        _item(db_session, source, f"short-{i}", url="https://bit.ly/abc123")  # shortener
        _item(db_session, source, f"reg-{i}", url="https://already-registered.example/post")  # registered

    result = discover_domain_candidates(db_session)

    assert result.created == 0
    assert list(db_session.scalars(select(SourceSuggestion))) == []


def test_github_and_reddit_homepages_are_not_also_suggested_as_plain_websites(db_session):
    """Regression test: browser acceptance found github.com getting
    double-suggested -- once correctly as the specific owner/repo GitHub
    suggestion, and once uselessly as a generic 'Website' suggestion for
    the bare github.com homepage, since the generic domain generator had
    no awareness that a specialized generator already owns that domain."""
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), url=f"https://github.com/ROCm/ROCm/issues/{i}")

    result = discover_domain_candidates(db_session)

    assert result.created == 0
    assert list(db_session.scalars(select(SourceSuggestion))) == []


def test_forum_shaped_urls_are_tagged_as_forum(db_session):
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), url=f"https://community.example.com/forums/general/threads/{i}")

    result = discover_domain_candidates(db_session)

    assert result.created == 1
    suggestion = db_session.scalars(select(SourceSuggestion)).one()
    assert suggestion.domain == "community.example.com"
    assert suggestion.platform == "forum"


def test_domain_rerun_updates_not_duplicates_and_never_touches_status(db_session):
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), url="https://chipsandcheese.com/a")
    discover_domain_candidates(db_session)
    suggestion = db_session.scalars(select(SourceSuggestion)).one()
    suggestion.status = SourceSuggestionStatus.IGNORED  # operator decision
    db_session.commit()

    for i in range(4, 7):
        _item(db_session, source, str(i), url="https://chipsandcheese.com/b")
    result = discover_domain_candidates(db_session)

    assert result.created == 0
    suggestions = list(db_session.scalars(select(SourceSuggestion)))
    assert len(suggestions) == 1  # updated, not duplicated
    assert suggestions[0].appearances == 7
    assert suggestions[0].status == SourceSuggestionStatus.IGNORED  # untouched


# --- subreddit generator ------------------------------------------------

def test_subreddit_extracted_and_normalized(db_session, monkeypatch):
    import semi_intel.signals.source_discovery as sd
    monkeypatch.setattr(sd, "_validate_deterministic_feed", lambda url: None)  # no network in tests
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), url="https://www.reddit.com/r/hardware/comments/abc123/big_leak/")

    result = discover_subreddit_candidates(db_session)

    assert result.created == 1
    suggestion = db_session.scalars(select(SourceSuggestion)).one()
    assert suggestion.kind == SourceSuggestionKind.DOMAIN
    assert suggestion.platform == "reddit"
    assert suggestion.provider_key == "hardware"
    assert suggestion.domain == "reddit:r/hardware"


def test_subreddit_feed_url_set_when_validation_succeeds(db_session, monkeypatch):
    import semi_intel.signals.source_discovery as sd
    monkeypatch.setattr(sd, "_validate_deterministic_feed", lambda url: url)
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), url="https://www.reddit.com/r/hardware/comments/abc/x/")

    discover_subreddit_candidates(db_session)

    suggestion = db_session.scalars(select(SourceSuggestion)).one()
    assert suggestion.feed_url == "https://www.reddit.com/r/hardware/.rss"


def test_subreddit_generic_paths_are_excluded(db_session, monkeypatch):
    import semi_intel.signals.source_discovery as sd
    monkeypatch.setattr(sd, "_validate_deterministic_feed", lambda url: None)
    source = _source(db_session)
    for i in range(5):
        _item(db_session, source, str(i), url="https://www.reddit.com/r/all/")

    discover_subreddit_candidates(db_session)

    assert list(db_session.scalars(select(SourceSuggestion))) == []


# --- github generator ----------------------------------------------------

def test_github_repository_extracted_and_normalized(db_session, monkeypatch):
    import semi_intel.signals.source_discovery as sd
    monkeypatch.setattr(sd, "_validate_deterministic_feed", lambda url: None)
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), url=f"https://github.com/ROCm/ROCm/issues/{i}")

    result = discover_github_candidates(db_session)

    assert result.created == 1
    suggestion = db_session.scalars(select(SourceSuggestion)).one()
    assert suggestion.platform == "github"
    assert suggestion.provider_key == "rocm/rocm"
    assert suggestion.domain == "github:rocm/rocm"


def test_github_non_repo_paths_are_excluded(db_session, monkeypatch):
    import semi_intel.signals.source_discovery as sd
    monkeypatch.setattr(sd, "_validate_deterministic_feed", lambda url: None)
    source = _source(db_session)
    for i in range(5):
        _item(db_session, source, str(i), url="https://github.com/marketplace/some-action")

    discover_github_candidates(db_session)

    assert list(db_session.scalars(select(SourceSuggestion))) == []


def test_github_feed_url_uses_releases_atom(db_session, monkeypatch):
    import semi_intel.signals.source_discovery as sd
    monkeypatch.setattr(sd, "_validate_deterministic_feed", lambda url: url)
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), url=f"https://github.com/ROCm/ROCm/releases/tag/v{i}")

    discover_github_candidates(db_session)

    suggestion = db_session.scalars(select(SourceSuggestion)).one()
    assert suggestion.feed_url == "https://github.com/rocm/rocm/releases.atom"


# --- orchestrator: fault isolation ---------------------------------------

def test_run_source_discovery_reports_success_when_all_generators_run(db_session, monkeypatch):
    import semi_intel.signals.source_discovery as sd
    monkeypatch.setattr(sd, "_validate_deterministic_feed", lambda url: None)
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), url="https://chipsandcheese.com/a")

    report = run_source_discovery(db_session)

    assert report.overall_status == "SUCCESS"
    assert report.generators["domain_and_forum"].created == 1
    assert report.finished_at is not None
    assert report.duration_seconds >= 0


def test_one_generator_failing_does_not_suppress_or_roll_back_the_others(db_session, monkeypatch):
    """Regression test for Phase 10: a single generator raising must not
    return a false empty-success, and -- because each generator commits
    independently -- must not roll back suggestions another generator
    already created earlier in the same run."""
    import semi_intel.signals.source_discovery as sd
    monkeypatch.setattr(sd, "_validate_deterministic_feed", lambda url: None)

    def _boom(session):
        raise RuntimeError("simulated generator crash")

    monkeypatch.setitem(sd.GENERATORS, "github_repository", _boom)
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), url="https://chipsandcheese.com/a")

    report = run_source_discovery(db_session)

    assert report.overall_status == "PARTIAL"
    assert report.generators["github_repository"].status == "FAILED"
    assert "simulated generator crash" in report.generators["github_repository"].errors[0]
    # the domain generator's earlier, already-committed work survives
    assert report.generators["domain_and_forum"].created == 1
    assert db_session.scalars(select(SourceSuggestion)).all()


def test_run_source_discovery_never_silently_reports_success_when_everything_fails(db_session, monkeypatch):
    import semi_intel.signals.source_discovery as sd

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(sd, "GENERATORS", {"only": _boom})

    report = run_source_discovery(db_session)

    assert report.overall_status == "FAILED"
