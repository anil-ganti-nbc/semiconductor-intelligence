from __future__ import annotations

import datetime as dt
import json

import feedparser
from sqlalchemy import select

from semi_intel.discovery.providers import GoogleNewsRSSProvider, ProviderResult
from semi_intel.discovery.service import DiscoveryService, build_queries
from semi_intel.domain.enums import DiscoveryRelationship, DiscoveryRunStatus, SourceSuggestionStatus, SourceType
from semi_intel.domain.models import (
    DiscoveryRun, EditorialStory, Evidence, MonitoredTopic, Source,
    SourceSuggestion, StoryEvidence, TopicMatch,
)
from semi_intel.ingestion.hashing import hash_content


class FakeProvider:
    name = "google_news_rss"

    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = []

    def search(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.results[:request.maximum_results]


def _story(session, headline="VideoCardz reveals RTX 50 Super specifications", seen=False):
    source = session.scalar(select(Source).where(Source.name == "VideoCardz"))
    if not source:
        source = Source(
            name="VideoCardz", type=SourceType.RSS,
            url="https://videocardz.com/feed", trust_weight=.7,
        )
        session.add(source)
        session.flush()
    evidence = Evidence(
        source_id=source.id, title=headline, raw_content=headline,
        content_hash=hash_content(headline + str(source.id)),
        url="https://videocardz.com/newz/rtx-50-super",
        observed_at=dt.datetime.utcnow(),
    )
    topic = session.scalar(select(MonitoredTopic).where(MonitoredTopic.name == "RTX 50 Super"))
    if not topic:
        topic = MonitoredTopic(
            name="RTX 50 Super", normalized_name="rtx 50 super", keyword="RTX 50 Super",
            aliases="[]", category="NVIDIA", priority=.9,
        )
        session.add(topic)
    session.add(evidence)
    session.flush()
    story = EditorialStory(
        canonical_key=hash_content(headline + "story"),
        headline=headline, summary=headline, interest_score=.78,
        score_reasons="[]", coverage_count=1,
        seen_at=dt.datetime.utcnow() if seen else None,
        created_at=dt.datetime.utcnow(), latest_at=dt.datetime.utcnow(),
    )
    session.add(story)
    session.flush()
    session.add_all([
        StoryEvidence(story_id=story.id, evidence_id=evidence.id),
        TopicMatch(story_id=story.id, topic_id=topic.id, matched_text="RTX 50 Super", match_score=.9),
    ])
    session.flush()
    return story


def _results(now=None):
    now = now or dt.datetime.utcnow()
    return [
        ProviderResult(
            title="RTX 50 Super specifications follow VideoCardz report",
            url="https://news.google.com/rss/articles/one",
            canonical_url="https://news.google.com/rss/articles/one",
            canonical_domain="techsite.example", publication_name="Tech Site",
            published_at=now, snippet="According to VideoCardz, the RTX 50 Super is coming.",
            provider="google_news_rss", provider_result_id="one", rank=1,
        ),
        ProviderResult(
            title="NVIDIA quarterly earnings rise",
            url="https://news.google.com/rss/articles/two",
            canonical_url="https://news.google.com/rss/articles/two",
            canonical_domain="business.example", publication_name="Business",
            published_at=now, snippet="NVIDIA financial results and market news.",
            provider="google_news_rss", provider_result_id="two", rank=2,
        ),
    ]


def test_query_builder_is_bounded_specific_and_deduplicated():
    plans = build_queries(
        "VideoCardz reveals RTX 50 Super specifications",
        ["RTX 50 Super", "NVIDIA"], "VideoCardz", maximum=3,
    )
    assert 1 <= len(plans) <= 3
    assert len({plan.query.casefold() for plan in plans}) == len(plans)
    assert any("according to VideoCardz" in plan.query for plan in plans)
    assert all(len(plan.query) <= 180 for plan in plans)


def test_google_news_provider_parses_fixture_without_fetching_articles():
    xml = """<rss><channel><item><title>RTX report - Tech Site</title>
      <link>https://news.google.com/rss/articles/abc</link><guid>abc</guid>
      <description>According to VideoCardz, RTX 50 Super.</description>
      <source url="https://www.techsite.example">Tech Site</source>
      </item></channel></rss>"""
    provider = GoogleNewsRSSProvider(fetch_feed=lambda _url, _timeout: feedparser.parse(xml))
    request = type("Request", (), {
        "query": '"RTX 50 Super"', "maximum_results": 10, "language": "en-US",
        "region": "US", "timeout_seconds": 8,
    })()
    result = provider.search(request)[0]
    assert result.canonical_domain == "techsite.example"
    assert result.publication_name == "Tech Site"
    assert result.url.startswith("https://news.google.com/")


def test_bounded_run_accepts_attribution_rejects_generic_and_suggests_source(db_session):
    story = _story(db_session)
    provider = FakeProvider(_results())
    service = DiscoveryService(db_session, provider=provider)
    run = service.run_story(story)
    db_session.commit()
    assert run.status == DiscoveryRunStatus.COMPLETED
    assert run.request_count <= 3
    assert run.raw_result_count <= 30
    assert run.accepted_result_count == 1
    assert run.filtered_result_count == 1
    accepted = next(item for item in db_session.query(__import__(
        "semi_intel.domain.models", fromlist=["DiscoveryResult"]
    ).DiscoveryResult).all() if item.accepted)
    assert accepted.relationship == DiscoveryRelationship.CITES_KNOWN_SOURCE
    assert "according to VideoCardz" in accepted.supporting_phrase
    suggestion = db_session.scalar(select(SourceSuggestion).where(
        SourceSuggestion.domain == "techsite.example"
    ))
    assert suggestion is not None
    assert "Explicitly cited" in suggestion.reasons


def test_seen_story_is_eligible_but_cooldown_prevents_immediate_repeat(db_session):
    story = _story(db_session, seen=True)
    service = DiscoveryService(db_session, provider=FakeProvider(_results()))
    assert service.eligibility(story).eligible
    first = service.run_story(story)
    second = service.run_story(story)
    assert first.status == DiscoveryRunStatus.COMPLETED
    assert second.status == DiscoveryRunStatus.SKIPPED
    assert "cooldown" in second.eligibility_reason.casefold()


def test_budget_cycle_and_provider_limits_are_persisted(db_session):
    story = _story(db_session)
    service = DiscoveryService(db_session, provider=FakeProvider(_results()))
    settings = service.settings()
    settings.global_cycles_per_hour = 1
    service.run_story(story)
    other = _story(db_session, "VideoCardz details another RTX 50 Super launch")
    eligibility = service.eligibility(other)
    assert not eligibility.eligible
    assert "budget" in eligibility.reason.casefold()


def test_blocked_domain_is_filtered(db_session):
    story = _story(db_session)
    db_session.add(SourceSuggestion(
        domain="techsite.example", inferred_name="Tech Site",
        status=SourceSuggestionStatus.BLOCKED,
    ))
    db_session.flush()
    run = DiscoveryService(db_session, provider=FakeProvider(_results()[:1])).run_story(story)
    assert run.accepted_result_count == 0
    assert run.filtered_result_count == 1


def test_provider_failure_is_recorded_and_does_not_raise(db_session):
    story = _story(db_session)
    run = DiscoveryService(
        db_session, provider=FakeProvider(error=TimeoutError("provider timed out"))
    ).run_story(story)
    assert run.status == DiscoveryRunStatus.FAILED
    assert "timed out" in run.error_message


def test_stale_running_run_is_recovered(db_session):
    story = _story(db_session)
    run = DiscoveryRun(
        story_id=story.id, provider="google_news_rss", status=DiscoveryRunStatus.RUNNING,
        eligibility_reason="eligible", started_at=dt.datetime.utcnow() - dt.timedelta(hours=1),
    )
    db_session.add(run)
    db_session.flush()
    assert DiscoveryService(db_session, provider=FakeProvider()).recover_stale_runs() == 1
    assert run.status == DiscoveryRunStatus.FAILED
    assert "restart" in run.error_message


def test_automatic_mode_is_independently_disabled_by_default(db_session):
    story = _story(db_session)
    service = DiscoveryService(db_session, provider=FakeProvider(_results()))
    assert service.eligibility(story, automatic=False).eligible
    automatic = service.eligibility(story, automatic=True)
    assert not automatic.eligible
    assert "automatic" in automatic.reason.casefold()
