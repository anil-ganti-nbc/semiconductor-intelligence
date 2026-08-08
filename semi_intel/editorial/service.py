from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import SourceSuggestionStatus, SourceType
from semi_intel.domain.models import (
    Citation,
    EditorialStory,
    Evidence,
    MonitoredTopic,
    Source,
    SourceSuggestion,
    StoryEvidence,
    TopicMatch,
)


SEED_TOPICS: dict[str, list[str]] = {
    "AMD graphics": [
        "RDNA 4", "RDNA 5", "Radeon RX 9000", "Radeon RX 10000", "Radeon AI",
        "FSR 4", "FSR 5", "ROCm", "Instinct", "UDNA",
    ],
    "AMD processors": [
        "Zen 5", "Zen 5c", "Zen 6", "Zen 6c", "Zen 7", "Ryzen 9000",
        "Ryzen 10000", "Threadripper", "EPYC", "Strix Point", "Strix Halo",
        "Krackan Point", "Medusa Point",
    ],
    "NVIDIA": [
        "RTX 50 Series", "RTX 50 Super", "RTX 60 Series", "Blackwell",
        "Blackwell Ultra", "Rubin", "Vera Rubin", "DLSS", "CUDA", "GeForce",
        "NVIDIA AI accelerators",
    ],
    "Intel": [
        "Arc Battlemage", "Arc Celestial", "Arc Druid", "Lunar Lake",
        "Arrow Lake", "Panther Lake", "Nova Lake", "Intel 18A", "Intel 14A",
        "XeSS", "Gaudi", "Foundry Services",
    ],
    "Processors and platforms": [
        "x86", "ARM", "RISC-V", "Snapdragon X", "Apple Silicon", "DDR6",
        "GDDR7", "HBM3E", "HBM4", "PCIe 6.0", "PCIe 7.0", "CXL", "chiplets",
        "advanced packaging",
    ],
    "Foundries and manufacturing": [
        "TSMC N2", "TSMC A16", "Samsung 2nm", "Intel 18A", "High-NA EUV",
        "ASML", "CoWoS", "SoIC", "Foveros", "semiconductor tariffs",
        "export controls", "China semiconductor industry",
    ],
}

NOISE_DOMAINS = {
    "google.com", "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "youtube.com", "youtu.be", "tiktok.com", "reddit.com", "discord.com",
    "amazon.com", "amzn.to", "ebay.com", "sharethis.com", "addthis.com",
    "cloudfront.net", "cloudflare.com", "gravatar.com", "wp.com",
}
TRACKING_QUERY_PREFIXES = ("utm_", "fbclid", "gclid", "mc_")
URL_RE = re.compile(r"https?://[^\s<>'\"()\]]+", re.I)
HREF_RE = re.compile(
    r"<a\b[^>]*?href=[\"'](?P<url>https?://[^\"']+)[\"'][^>]*>(?P<text>.*?)</a>",
    re.I | re.S,
)


def normalize_phrase(value: str) -> str:
    """Normalize spelling variants while retaining word boundaries."""
    value = unicodedata.normalize("NFKC", html.unescape(value)).casefold()
    value = value.replace("–", "-").replace("—", "-").replace("_", " ")
    value = re.sub(r"(?<=[a-z])[-\s]*(?=\d)", " ", value)
    value = re.sub(r"(?<=\d)[-\s]*(?=[a-z])", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def canonical_domain(url_or_domain: str) -> str:
    parsed = urlparse(url_or_domain if "://" in url_or_domain else f"https://{url_or_domain}")
    domain = (parsed.hostname or "").casefold().strip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    if domain.startswith("m."):
        domain = domain[2:]
    return domain


def canonical_url(value: str) -> str:
    parsed = urlparse(html.unescape(value))
    query = [
        (key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith(TRACKING_QUERY_PREFIXES)
    ]
    path = re.sub(r"/amp/?$", "", parsed.path, flags=re.I) or "/"
    return urlunparse(("https", canonical_domain(value), path.rstrip("/") or "/", "", urlencode(query), ""))


def is_noise_domain(domain: str) -> bool:
    return not domain or any(domain == item or domain.endswith(f".{item}") for item in NOISE_DOMAINS)


def topic_terms(topic: MonitoredTopic) -> list[str]:
    aliases = json.loads(topic.aliases or "[]")
    return [topic.keyword, *aliases]


def match_topic(topic: MonitoredTopic, text: str) -> str | None:
    haystack = f" {normalize_phrase(text)} "
    for term in topic_terms(topic):
        needle = normalize_phrase(term)
        if needle and f" {needle} " in haystack:
            return term
    return None


def default_aliases(name: str) -> list[str]:
    aliases: list[str] = []
    compact = re.sub(r"(?<=[A-Za-z])\s+(?=\d)", "", name)
    hyphenated = re.sub(r"(?<=[A-Za-z])\s+(?=\d)", "-", name)
    if compact != name:
        aliases.extend([compact, hyphenated])
    if name.startswith("RTX "):
        aliases.append(f"GeForce {name}")
    return list(dict.fromkeys(aliases))


class TopicService:
    def __init__(self, session: Session):
        self.session = session

    def seed(self) -> int:
        existing = {value for value in self.session.scalars(select(MonitoredTopic.normalized_name))}
        created = 0
        for category, names in SEED_TOPICS.items():
            for name in names:
                normalized = normalize_phrase(name)
                if normalized in existing:
                    continue
                self.session.add(MonitoredTopic(
                    name=name,
                    normalized_name=normalized,
                    keyword=name,
                    aliases=json.dumps(default_aliases(name)),
                    category=category,
                    priority=0.6 if any(token in name for token in ("RDNA 5", "Zen 6", "RTX 50 Super", "RTX 60")) else 0.5,
                ))
                existing.add(normalized)
                created += 1
        self.session.flush()
        return created

    def validate_unique(self, name: str, keyword: str, aliases: Iterable[str], exclude_id: int | None = None) -> None:
        requested = {normalize_phrase(value) for value in [name, keyword, *aliases] if value.strip()}
        for topic in self.session.scalars(select(MonitoredTopic)):
            if exclude_id == topic.id:
                continue
            occupied = {topic.normalized_name, normalize_phrase(topic.keyword)}
            occupied.update(normalize_phrase(alias) for alias in json.loads(topic.aliases or "[]"))
            overlap = requested & occupied
            if overlap:
                raise ValueError(f"Already used by monitored topic '{topic.name}': {sorted(overlap)[0]}")


@dataclass(frozen=True)
class BackfillResult:
    evidence_scanned: int
    stories_created: int
    matches_created: int
    citations_created: int


class EditorialDiscoveryService:
    """Idempotently transforms evidence into an explainable editorial inbox."""

    def __init__(self, session: Session):
        self.session = session

    def process_evidence(self, evidence: Evidence) -> EditorialStory | None:
        existing_link = self.session.scalar(select(StoryEvidence).where(StoryEvidence.evidence_id == evidence.id))
        if existing_link:
            return self.session.get(EditorialStory, existing_link.story_id)

        TopicService(self.session).seed()
        text = f"{evidence.title}\n{evidence.raw_content}"
        matches = [
            (topic, matched)
            for topic in self.session.scalars(select(MonitoredTopic).where(MonitoredTopic.enabled.is_(True)))
            if (matched := match_topic(topic, text))
        ]
        citations = self._capture_citations(evidence)
        if not matches:
            self._refresh_source_suggestions()
            return None

        story = self._find_cluster(evidence, [topic.id for topic, _ in matches])
        is_new = story is None
        if story is None:
            key = hashlib.sha256(f"{normalize_phrase(evidence.title)}|{evidence.id}".encode()).hexdigest()
            observed = evidence.observed_at or evidence.collected_at
            story = EditorialStory(
                canonical_key=key,
                headline=evidence.title,
                summary=evidence.raw_content[:1200],
                created_at=observed,
                latest_at=observed,
            )
            self.session.add(story)
            self.session.flush()

        self.session.add(StoryEvidence(story_id=story.id, evidence_id=evidence.id))
        for topic, matched in matches:
            exists = self.session.scalar(select(TopicMatch).where(
                TopicMatch.story_id == story.id, TopicMatch.topic_id == topic.id
            ))
            if not exists:
                self.session.add(TopicMatch(
                    story_id=story.id, topic_id=topic.id, matched_text=matched, match_score=topic.priority
                ))
        if not is_new:
            story.coverage_count += 1
            story.latest_at = max(story.latest_at, evidence.observed_at or evidence.collected_at)
            if story.seen_at:
                story.new_coverage_count += 1
        self.session.flush()
        self._score(story, citations)
        self._refresh_source_suggestions()
        return story

    def backfill(self) -> BackfillResult:
        before_stories = self.session.scalar(select(func.count()).select_from(EditorialStory)) or 0
        before_matches = self.session.scalar(select(func.count()).select_from(TopicMatch)) or 0
        before_citations = self.session.scalar(select(func.count()).select_from(Citation)) or 0
        evidence = list(self.session.scalars(select(Evidence).order_by(Evidence.id)))
        for item in evidence:
            self.process_evidence(item)
        self.session.flush()
        return BackfillResult(
            evidence_scanned=len(evidence),
            stories_created=(self.session.scalar(select(func.count()).select_from(EditorialStory)) or 0) - before_stories,
            matches_created=(self.session.scalar(select(func.count()).select_from(TopicMatch)) or 0) - before_matches,
            citations_created=(self.session.scalar(select(func.count()).select_from(Citation)) or 0) - before_citations,
        )

    def _find_cluster(self, evidence: Evidence, topic_ids: list[int]) -> EditorialStory | None:
        cutoff = (evidence.observed_at or evidence.collected_at) - dt.timedelta(days=3)
        candidates = list(self.session.scalars(
            select(EditorialStory).where(EditorialStory.latest_at >= cutoff).order_by(EditorialStory.latest_at.desc())
        ))
        normalized_title = normalize_phrase(evidence.title)
        for candidate in candidates:
            candidate_topics = set(self.session.scalars(
                select(TopicMatch.topic_id).where(TopicMatch.story_id == candidate.id)
            ))
            if not candidate_topics.intersection(topic_ids):
                continue
            ratio = SequenceMatcher(None, normalized_title, normalize_phrase(candidate.headline)).ratio()
            if ratio >= 0.72:
                return candidate
        return None

    def _score(self, story: EditorialStory, recent_citations: list[Citation]) -> None:
        topic_rows = list(self.session.execute(
            select(MonitoredTopic, TopicMatch)
            .join(TopicMatch, TopicMatch.topic_id == MonitoredTopic.id)
            .where(TopicMatch.story_id == story.id)
        ))
        priorities = [topic.priority for topic, _ in topic_rows]
        topic_score = min(0.45, (max(priorities, default=0.0) * 0.35) + min(len(priorities), 3) * 0.04)
        age_hours = max((dt.datetime.utcnow() - story.latest_at).total_seconds() / 3600, 0)
        recency_score = max(0.0, 0.25 * (1 - age_hours / (7 * 24)))
        coverage_score = min(0.15, max(story.coverage_count - 1, 0) * 0.05)
        source_ids = list(self.session.scalars(
            select(Evidence.source_id).join(StoryEvidence, StoryEvidence.evidence_id == Evidence.id)
            .where(StoryEvidence.story_id == story.id).distinct()
        ))
        trusts = [self.session.get(Source, source_id).trust_weight for source_id in source_ids]
        trust_score = (max(trusts, default=0.5)) * 0.10
        citation_score = min(0.05, len(recent_citations) * 0.01)
        story.interest_score = round(min(topic_score + recency_score + coverage_score + trust_score + citation_score, 1), 4)
        names = [topic.name for topic, _ in topic_rows]
        reasons = [f"Matched: {', '.join(names)}"]
        if story.coverage_count > 1:
            reasons.append(f"Covered by {story.coverage_count} articles")
        if age_hours <= 24:
            reasons.append(f"New in the last {max(1, round(age_hours))} hours")
        if max(priorities, default=0) >= 0.7:
            reasons.append("High-priority topic")
        if recent_citations:
            reasons.append(f"{len(recent_citations)} editorial citation(s) detected")
        story.score_reasons = json.dumps(reasons)
        story.updated_at = dt.datetime.utcnow()
        self.session.flush()

    def _capture_citations(self, evidence: Evidence) -> list[Citation]:
        found: dict[str, str | None] = {}
        for match in HREF_RE.finditer(evidence.raw_content):
            found[canonical_url(match.group("url"))] = re.sub("<[^>]+>", "", match.group("text")).strip() or None
        for url in URL_RE.findall(evidence.raw_content):
            found.setdefault(canonical_url(url.rstrip(".,;:")), None)
        own_domain = canonical_domain(evidence.url or "")
        created: list[Citation] = []
        for url, link_text in found.items():
            domain = canonical_domain(url)
            if domain == own_domain or is_noise_domain(domain):
                continue
            existing = self.session.scalar(select(Citation).where(
                Citation.evidence_id == evidence.id, Citation.destination_url == url
            ))
            if existing:
                continue
            citation = Citation(
                evidence_id=evidence.id, destination_url=url, destination_domain=domain,
                link_text=link_text, is_editorial=True,
            )
            self.session.add(citation)
            created.append(citation)
        self.session.flush()
        return created

    def _refresh_source_suggestions(self) -> None:
        registered = {
            canonical_domain(source.url or "") for source in self.session.scalars(select(Source))
            if source.url
        }
        rows = self.session.execute(
            select(Citation.destination_domain, func.count(Citation.id), func.count(func.distinct(StoryEvidence.story_id)))
            .outerjoin(StoryEvidence, StoryEvidence.evidence_id == Citation.evidence_id)
            .where(Citation.is_editorial.is_(True))
            .group_by(Citation.destination_domain)
        )
        now = dt.datetime.utcnow()
        for domain, appearances, story_count in rows:
            if domain in registered or is_noise_domain(domain):
                continue
            suggestion = self.session.scalar(select(SourceSuggestion).where(SourceSuggestion.domain == domain))
            if not suggestion:
                suggestion = SourceSuggestion(
                    domain=domain, inferred_name=domain.split(".")[0].replace("-", " ").title()
                )
                self.session.add(suggestion)
            topic_count = self.session.scalar(
                select(func.count(func.distinct(TopicMatch.topic_id)))
                .join(StoryEvidence, StoryEvidence.story_id == TopicMatch.story_id)
                .join(Citation, Citation.evidence_id == StoryEvidence.evidence_id)
                .where(Citation.destination_domain == domain)
            ) or 0
            suggestion.appearances = appearances
            suggestion.story_count = story_count or 0
            suggestion.topic_count = topic_count
            suggestion.last_seen_at = now
            suggestion.score = round(min(
                1.0, min(appearances, 5) * 0.10 + min(story_count or 0, 4) * 0.12
                + min(topic_count, 4) * 0.08 + (0.1 if suggestion.feed_url else 0)
            ), 4)
            suggestion.reasons = json.dumps([
                f"Referenced {appearances} time(s)",
                f"Appeared across {story_count or 0} relevant story cluster(s)",
                f"Covered {topic_count} monitored topic(s)",
            ])
        self.session.flush()


def delete_topic(session: Session, topic: MonitoredTopic) -> None:
    session.execute(delete(TopicMatch).where(TopicMatch.topic_id == topic.id))
    session.delete(topic)
