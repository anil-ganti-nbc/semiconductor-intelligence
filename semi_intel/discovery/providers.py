from __future__ import annotations

import calendar
import datetime as dt
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote_plus

import feedparser

from semi_intel.editorial.service import canonical_domain, canonical_url


@dataclass(frozen=True)
class SearchRequest:
    query: str
    story_id: int
    source_domain: str | None
    topics: list[str]
    earliest: dt.datetime
    latest: dt.datetime
    maximum_results: int
    language: str = "en-US"
    region: str = "US"
    timeout_seconds: int = 8


@dataclass(frozen=True)
class ProviderResult:
    title: str
    url: str
    canonical_url: str
    canonical_domain: str
    publication_name: str | None
    published_at: dt.datetime | None
    snippet: str
    provider: str
    provider_result_id: str | None
    rank: int
    language: str | None = None


class DiscoveryProvider(ABC):
    name: str

    @abstractmethod
    def search(self, request: SearchRequest) -> list[ProviderResult]:
        raise NotImplementedError


FetchFeed = Callable[[str, int], object]


def _fetch_feed(url: str, timeout: int) -> object:
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        return feedparser.parse(url, agent="SemiIntel/2.2 targeted-discovery")
    finally:
        socket.setdefaulttimeout(previous)


def _published(value) -> dt.datetime | None:
    return (
        dt.datetime.fromtimestamp(calendar.timegm(value), tz=dt.timezone.utc).replace(tzinfo=None)
        if value else None
    )


class GoogleNewsRSSProvider(DiscoveryProvider):
    """Small query-driven RSS adapter; it never fetches result articles."""

    name = "google_news_rss"

    def __init__(self, fetch_feed: FetchFeed = _fetch_feed):
        self.fetch_feed = fetch_feed

    def search(self, request: SearchRequest) -> list[ProviderResult]:
        language = request.language
        region = request.region
        language_code = language.split("-")[0]
        url = (
            "https://news.google.com/rss/search?q="
            f"{quote_plus(request.query)}&hl={quote_plus(language)}"
            f"&gl={quote_plus(region)}&ceid={quote_plus(region + ':' + language_code)}"
        )
        parsed = self.fetch_feed(url, request.timeout_seconds)
        results: list[ProviderResult] = []
        for rank, entry in enumerate(getattr(parsed, "entries", []), start=1):
            if rank > request.maximum_results:
                break
            source = entry.get("source") or {}
            source_url = source.get("href") if hasattr(source, "get") else None
            article_url = entry.get("link", "")
            domain_url = source_url or article_url
            domain = canonical_domain(domain_url)
            if not article_url or not domain:
                continue
            # Google links remain the openable result URL; the publisher's
            # source href supplies the canonical domain without crawling it.
            normalized_url = canonical_url(article_url)
            results.append(ProviderResult(
                title=(entry.get("title") or "").strip(),
                url=article_url,
                canonical_url=normalized_url,
                canonical_domain=domain,
                publication_name=(source.get("title") if hasattr(source, "get") else None),
                published_at=_published(entry.get("published_parsed") or entry.get("updated_parsed")),
                snippet=(entry.get("summary") or entry.get("description") or "").strip(),
                provider=self.name,
                provider_result_id=entry.get("id") or article_url,
                rank=rank,
                language=language,
            ))
        return results
