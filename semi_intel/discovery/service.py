from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semi_intel.discovery.providers import DiscoveryProvider, GoogleNewsRSSProvider, ProviderResult, SearchRequest
from semi_intel.domain.enums import DiscoveryRelationship, DiscoveryRunStatus, SourceSuggestionStatus
from semi_intel.domain.models import (
    DiscoveryResult, DiscoveryRun, DiscoverySettings, EditorialStory, Evidence,
    MonitoredTopic, Source, SourceSuggestion, StoryEvidence, TopicMatch,
)
from semi_intel.editorial.service import canonical_domain, is_noise_domain, normalize_phrase

GENERIC = {
    "amd", "nvidia", "intel", "gpu", "gpus", "cpu", "cpus", "semiconductor",
    "hardware", "new", "report", "reports", "details", "series",
}
REJECT_PATH_PARTS = ("/search", "/category/", "/tag/", "/product/", "/shop/")
STALE_RUNNING_MINUTES = 30


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reason: str
    next_eligible_at: dt.datetime | None = None


@dataclass(frozen=True)
class QueryPlan:
    query: str
    reason: str


class DiscoverySettingsService:
    def __init__(self, session: Session):
        self.session = session

    def get(self) -> DiscoverySettings:
        settings = self.session.get(DiscoverySettings, 1)
        if not settings:
            settings = DiscoverySettings(id=1)
            self.session.add(settings)
            try:
                self.session.flush()
            except IntegrityError:
                self.session.rollback()
                settings = self.session.get(DiscoverySettings, 1)
        return settings


def build_queries(
    headline: str,
    topics: list[str],
    source_name: str | None,
    maximum: int = 3,
) -> list[QueryPlan]:
    normalized_words = [
        word for word in normalize_phrase(headline).split()
        if len(word) > 2 and word not in GENERIC
    ]
    distinctive = " ".join(normalized_words[:8])
    high_value_topics = [topic for topic in topics if normalize_phrase(topic) not in GENERIC][:2]
    plans: list[QueryPlan] = []
    if len(normalized_words) >= 3:
        plans.append(QueryPlan(f'"{distinctive}"', "Distinctive normalized headline phrase"))
    if high_value_topics and source_name:
        plans.append(QueryPlan(
            f'"{high_value_topics[0]}" "{source_name}"',
            "Primary monitored topic plus originating publication",
        ))
        plans.append(QueryPlan(
            f'"according to {source_name}" "{high_value_topics[0]}"',
            "Explicit attribution phrase plus primary topic",
        ))
    elif high_value_topics:
        plans.append(QueryPlan(f'"{high_value_topics[0]}" "{distinctive}"', "Topic plus distinctive phrase"))
    deduped: list[QueryPlan] = []
    seen: set[str] = set()
    for plan in plans:
        key = normalize_phrase(plan.query)
        if key and key not in seen and len(plan.query) <= 180:
            seen.add(key)
            deduped.append(plan)
    return deduped[:maximum]


class DiscoveryService:
    def __init__(self, session: Session, provider: DiscoveryProvider | None = None):
        self.session = session
        self.provider = provider or GoogleNewsRSSProvider()

    def settings(self) -> DiscoverySettings:
        return DiscoverySettingsService(self.session).get()

    def recover_stale_runs(self, now: dt.datetime | None = None) -> int:
        now = now or dt.datetime.utcnow()
        cutoff = now - dt.timedelta(minutes=STALE_RUNNING_MINUTES)
        rows = list(self.session.scalars(select(DiscoveryRun).where(
            DiscoveryRun.status == DiscoveryRunStatus.RUNNING,
            DiscoveryRun.started_at < cutoff,
        )))
        for run in rows:
            run.status = DiscoveryRunStatus.FAILED
            run.finished_at = now
            run.error_message = "Recovered stale running discovery after application restart."
        self.session.flush()
        return len(rows)

    def eligibility(
        self, story: EditorialStory, now: dt.datetime | None = None, automatic: bool = False
    ) -> Eligibility:
        now = now or dt.datetime.utcnow()
        settings = self.settings()
        if not settings.enabled:
            return Eligibility(False, "Discovery is disabled")
        if automatic and not settings.automatic:
            return Eligibility(False, "Automatic discovery is disabled")
        if story.interest_score < settings.minimum_interest_score:
            return Eligibility(False, f"Score {story.interest_score:.2f} is below {settings.minimum_interest_score:.2f}")
        age_hours = max((now - story.created_at).total_seconds() / 3600, 0)
        if age_hours > settings.maximum_story_age_hours:
            return Eligibility(False, f"Story is older than {settings.maximum_story_age_hours} hours")
        if len([w for w in normalize_phrase(story.headline).split() if w not in GENERIC and len(w) > 2]) < 3:
            return Eligibility(False, "Headline is not distinctive enough for a bounded search")
        completed = list(self.session.scalars(select(DiscoveryRun).where(
            DiscoveryRun.story_id == story.id,
            DiscoveryRun.status.in_([DiscoveryRunStatus.COMPLETED, DiscoveryRunStatus.PARTIAL]),
        ).order_by(DiscoveryRun.finished_at.desc())))
        if len(completed) >= settings.maximum_cycles_per_story:
            return Eligibility(False, f"Already completed {settings.maximum_cycles_per_story} discovery cycles")
        if completed and completed[0].finished_at:
            next_time = completed[0].finished_at + dt.timedelta(hours=settings.cooldown_hours)
            if now < next_time:
                return Eligibility(False, "Discovery cooldown is still active", next_time)
        hour = now - dt.timedelta(hours=1)
        cycles = self.session.scalar(select(func.count()).select_from(DiscoveryRun).where(
            DiscoveryRun.started_at >= hour,
            DiscoveryRun.status.in_([DiscoveryRunStatus.COMPLETED, DiscoveryRunStatus.PARTIAL, DiscoveryRunStatus.FAILED]),
        )) or 0
        if cycles >= settings.global_cycles_per_hour:
            return Eligibility(False, "Global hourly discovery budget exhausted")
        requests = self.session.scalar(select(func.sum(DiscoveryRun.request_count)).where(
            DiscoveryRun.started_at >= hour, DiscoveryRun.provider == settings.provider
        )) or 0
        if requests >= settings.provider_requests_per_hour:
            return Eligibility(False, "Provider hourly request budget exhausted")
        return Eligibility(True, f"Eligible: score {story.interest_score:.2f}, {age_hours:.1f} hours old")

    def run_story(
        self, story: EditorialStory, now: dt.datetime | None = None, automatic: bool = False
    ) -> DiscoveryRun:
        now = now or dt.datetime.utcnow()
        self.recover_stale_runs(now)
        settings = self.settings()
        eligibility = self.eligibility(story, now=now, automatic=automatic)
        topics = list(self.session.scalars(
            select(MonitoredTopic.name).join(TopicMatch, TopicMatch.topic_id == MonitoredTopic.id)
            .where(TopicMatch.story_id == story.id)
        ))
        source = self.session.scalar(
            select(Source).join(Evidence, Evidence.source_id == Source.id)
            .join(StoryEvidence, StoryEvidence.evidence_id == Evidence.id)
            .where(StoryEvidence.story_id == story.id).order_by(Evidence.observed_at).limit(1)
        )
        plans = build_queries(story.headline, topics, source.name if source else None, settings.maximum_queries_per_cycle)
        run = DiscoveryRun(
            story_id=story.id, provider=settings.provider,
            status=DiscoveryRunStatus.RUNNING if eligibility.eligible and plans else DiscoveryRunStatus.SKIPPED,
            eligibility_reason=eligibility.reason if plans else "No safe targeted queries could be generated",
            queries=json.dumps([plan.query for plan in plans]),
            query_reasons=json.dumps([plan.reason for plan in plans]),
            started_at=now,
        )
        self.session.add(run)
        self.session.flush()
        if run.status == DiscoveryRunStatus.SKIPPED:
            run.finished_at = now
            return run

        accepted_urls: set[str] = set()
        errors: list[str] = []
        all_results = 0
        cache_cutoff = now - dt.timedelta(hours=settings.cache_hours)
        for plan in plans:
            if all_results >= settings.maximum_results_per_cycle:
                break
            cached = list(self.session.scalars(select(DiscoveryResult).where(
                DiscoveryResult.query == plan.query,
                DiscoveryResult.provider == settings.provider,
                DiscoveryResult.last_seen_at >= cache_cutoff,
            )))
            if cached:
                run.cache_hit = True
                provider_results = [
                    ProviderResult(
                        title=item.title, url=item.original_url, canonical_url=item.canonical_url,
                        canonical_domain=item.canonical_domain, publication_name=item.publication_name,
                        published_at=item.published_at, snippet=item.snippet, provider=item.provider,
                        provider_result_id=item.provider_result_id, rank=item.provider_rank,
                    ) for item in cached
                ]
            else:
                try:
                    provider_results = self.provider.search(SearchRequest(
                        query=plan.query, story_id=story.id,
                        source_domain=canonical_domain(source.url or "") if source else None,
                        topics=topics,
                        earliest=story.created_at - dt.timedelta(hours=6),
                        latest=story.created_at + dt.timedelta(hours=settings.maximum_story_age_hours),
                        maximum_results=min(
                            settings.results_per_query,
                            settings.maximum_results_per_cycle - all_results,
                        ),
                        language=settings.language, region=settings.region,
                        timeout_seconds=settings.request_timeout_seconds,
                    ))
                    run.request_count += 1
                except Exception as exc:  # provider failure is recorded, not raised
                    errors.append(str(exc)[:500])
                    continue
            run.raw_result_count += len(provider_results)
            for result in provider_results:
                if all_results >= settings.maximum_results_per_cycle:
                    break
                all_results += 1
                if result.canonical_url in accepted_urls:
                    run.duplicate_result_count += 1
                    continue
                accepted_urls.add(result.canonical_url)
                score, accepted, relationship, reasons, phrase = self._score_result(
                    story, result, topics, source, settings, now
                )
                row = DiscoveryResult(
                    run_id=run.id, story_id=story.id, query=plan.query, provider=result.provider,
                    provider_result_id=result.provider_result_id, original_url=result.url,
                    canonical_url=result.canonical_url, canonical_domain=result.canonical_domain,
                    title=result.title[:500], snippet=result.snippet,
                    publication_name=result.publication_name, published_at=result.published_at,
                    provider_rank=result.rank, relevance_score=score, accepted=accepted,
                    relationship=relationship, explanation=json.dumps(reasons),
                    supporting_phrase=phrase, first_seen_at=now, last_seen_at=now,
                )
                self.session.add(row)
                if accepted:
                    run.accepted_result_count += 1
                    self._suggest_source(row, topics, now)
                else:
                    run.filtered_result_count += 1
        run.finished_at = now
        run.error_message = "; ".join(errors) or None
        if errors and run.accepted_result_count:
            run.status = DiscoveryRunStatus.PARTIAL
        elif errors:
            run.status = DiscoveryRunStatus.FAILED
        else:
            run.status = DiscoveryRunStatus.COMPLETED
        run.budget_state = json.dumps(self.budget_status(now))
        self.session.flush()
        return run

    def run_eligible(
        self, limit: int | None = None, now: dt.datetime | None = None, automatic: bool = True
    ) -> list[DiscoveryRun]:
        now = now or dt.datetime.utcnow()
        settings = self.settings()
        limit = limit or settings.global_cycles_per_hour
        stories = list(self.session.scalars(
            select(EditorialStory).order_by(EditorialStory.interest_score.desc(), EditorialStory.latest_at.desc())
        ))
        runs = []
        for story in stories:
            if len(runs) >= limit:
                break
            if self.eligibility(story, now=now, automatic=automatic).eligible:
                runs.append(self.run_story(story, now=now, automatic=automatic))
        return runs

    def budget_status(self, now: dt.datetime | None = None) -> dict:
        now = now or dt.datetime.utcnow()
        settings = self.settings()
        cutoff = now - dt.timedelta(hours=1)
        cycles = self.session.scalar(select(func.count()).select_from(DiscoveryRun).where(
            DiscoveryRun.started_at >= cutoff
        )) or 0
        requests = self.session.scalar(select(func.sum(DiscoveryRun.request_count)).where(
            DiscoveryRun.started_at >= cutoff, DiscoveryRun.provider == settings.provider
        )) or 0
        return {
            "cycles_used": cycles, "cycles_limit": settings.global_cycles_per_hour,
            "provider_requests_used": requests,
            "provider_requests_limit": settings.provider_requests_per_hour,
            "reset_at": (now + dt.timedelta(hours=1)).isoformat(),
        }

    def _score_result(self, story, result, topics, source, settings, now):
        reasons: list[str] = []
        lower = normalize_phrase(f"{result.title} {result.snippet}")
        domain = result.canonical_domain
        if is_noise_domain(domain) or any(part in result.canonical_url.casefold() for part in REJECT_PATH_PARTS):
            return 0.0, False, DiscoveryRelationship.UNKNOWN, ["Rejected: non-editorial or listing URL"], None
        blocked = self.session.scalar(select(SourceSuggestion).where(
            SourceSuggestion.domain == domain, SourceSuggestion.status == SourceSuggestionStatus.BLOCKED
        ))
        if blocked:
            return 0.0, False, DiscoveryRelationship.UNKNOWN, ["Rejected: blocked domain"], None
        existing_url = self.session.scalar(select(Evidence.id).where(Evidence.url == result.canonical_url))
        if existing_url:
            return 0.0, False, DiscoveryRelationship.SYNDICATED, ["Rejected: already stored as direct evidence"], None
        if result.published_at:
            if result.published_at < story.created_at - dt.timedelta(hours=24) or result.published_at > now + dt.timedelta(hours=2):
                return 0.0, False, DiscoveryRelationship.UNKNOWN, ["Rejected: outside relevant publication window"], None
        title_similarity = SequenceMatcher(
            None, normalize_phrase(story.headline), normalize_phrase(result.title)
        ).ratio()
        score = title_similarity * 0.40
        if title_similarity >= 0.55:
            reasons.append("Strong headline similarity")
        matched_topics = [topic for topic in topics if f" {normalize_phrase(topic)} " in f" {lower} "]
        if matched_topics:
            score += min(0.25, len(matched_topics) * 0.15)
            reasons.append(f"Matched: {', '.join(matched_topics)}")
        phrase = None
        source_name = source.name if source else None
        relationship = DiscoveryRelationship.UNKNOWN
        if source_name:
            for attribution in json.loads(settings.attribution_phrases or "[]"):
                candidate = normalize_phrase(f"{attribution} {source_name}")
                if candidate in lower:
                    phrase = f"{attribution} {source_name}"
                    score += 0.25
                    reasons.append(f'Explicit attribution: "{phrase}"')
                    relationship = DiscoveryRelationship.CITES_KNOWN_SOURCE
                    break
        if result.published_at:
            score += 0.08
            reasons.append("Published within the bounded story window")
        if relationship == DiscoveryRelationship.UNKNOWN and title_similarity >= 0.70:
            relationship = DiscoveryRelationship.FOLLOW_UP
        if relationship == DiscoveryRelationship.UNKNOWN and any(
            term in lower for term in ("exclusive", "originally published", "original report")
        ):
            relationship = DiscoveryRelationship.POSSIBLE_ORIGIN
            reasons.append("Possible origin language in available metadata")
        accepted = score >= 0.45 and bool(matched_topics)
        if not accepted:
            reasons.append("Rejected: insufficient specific overlap")
        return round(min(score, 1.0), 4), accepted, relationship, reasons, phrase

    def _suggest_source(self, result: DiscoveryResult, topics: list[str], now: dt.datetime) -> None:
        registered = {
            canonical_domain(source.url or "") for source in self.session.scalars(select(Source)) if source.url
        }
        if result.canonical_domain in registered:
            return
        suggestion = self.session.scalar(select(SourceSuggestion).where(
            SourceSuggestion.domain == result.canonical_domain
        ))
        if suggestion and suggestion.status in (SourceSuggestionStatus.BLOCKED, SourceSuggestionStatus.ADDED):
            return
        if not suggestion:
            suggestion = SourceSuggestion(
                domain=result.canonical_domain,
                inferred_name=result.publication_name or result.canonical_domain.split(".")[0].title(),
            )
            self.session.add(suggestion)
        appearances = self.session.scalar(select(func.count()).select_from(DiscoveryResult).where(
            DiscoveryResult.canonical_domain == result.canonical_domain,
            DiscoveryResult.accepted.is_(True),
        )) or 0
        stories = self.session.scalar(select(func.count(func.distinct(DiscoveryResult.story_id))).where(
            DiscoveryResult.canonical_domain == result.canonical_domain,
            DiscoveryResult.accepted.is_(True),
        )) or 0
        suggestion.appearances = max(suggestion.appearances, appearances + 1)
        suggestion.story_count = max(suggestion.story_count, stories or 1)
        suggestion.topic_count = max(suggestion.topic_count, len(topics))
        suggestion.last_seen_at = now
        relationship_bonus = 0.15 if result.relationship in (
            DiscoveryRelationship.CITES_KNOWN_SOURCE, DiscoveryRelationship.POSSIBLE_ORIGIN
        ) else 0.0
        suggestion.score = round(min(
            1.0, suggestion.appearances * 0.08 + suggestion.story_count * 0.12
            + suggestion.topic_count * 0.05 + relationship_bonus
            + (0.1 if suggestion.feed_url else 0),
        ), 4)
        label = (
            "Explicitly cited a registered source"
            if result.relationship == DiscoveryRelationship.CITES_KNOWN_SOURCE
            else "Found through targeted story discovery"
        )
        suggestion.reasons = json.dumps([
            label,
            f"Appeared in {suggestion.story_count} relevant story cluster(s)",
            f"Covered {suggestion.topic_count} monitored topic(s)",
        ])
