"""RSS/Atom signal provider -- the recommended low-risk default (brief
section 17). Ported from Signal Radar's `providers/rss/`, but built on
`feedparser` + the same timeout-guarding fetch pattern as
`semi_intel/ingestion/plugins/rss_plugin.py` instead of adding `httpx` as a
new required runtime dependency: `feedparser` is already a base dependency
of this project and the timeout handling here is proven in production.

This is a *separate* code path from `RSSSourcePlugin`
(`semi_intel/ingestion/plugins/rss_plugin.py`): that plugin still writes
curated feeds directly to canonical `Evidence` for sources with
`type=RSS` (unchanged, zero regression risk). This provider instead feeds
the new `SignalItem` -> analysis -> candidate pipeline, for sources
opted into signal collection via `provider="rss"` + `polling_enabled=True`.
Nothing forces a given feed down one path or the other; an operator chooses
per source.
"""

from __future__ import annotations

import calendar
import datetime as dt
import html
import socket
from typing import Callable, Optional
from urllib.parse import urlsplit

import feedparser

from semi_intel.editorial.service import URL_RE
from semi_intel.signals.providers import (
    CollectResult,
    Cursor,
    NormalizedSignal,
    ProviderUnavailable,
    RawItem,
    SourceCandidate,
    ValidationError,
)

FetchFn = Callable[[str], "feedparser.FeedParserDict"]

DEFAULT_FETCH_TIMEOUT_SECONDS = 15


def _default_fetch(url: str) -> "feedparser.FeedParserDict":
    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(DEFAULT_FETCH_TIMEOUT_SECONDS)
    try:
        return feedparser.parse(url)
    finally:
        socket.setdefaulttimeout(previous_timeout)


def _to_datetime(struct_time) -> Optional[dt.datetime]:
    if not struct_time:
        return None
    return dt.datetime.fromtimestamp(calendar.timegm(struct_time), tz=dt.timezone.utc).replace(tzinfo=None)


def is_reddit_feed(url: str) -> bool:
    host = (urlsplit(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host == "reddit.com" or host.endswith(".reddit.com")


def _http_status(parsed) -> int | None:
    status = getattr(parsed, "status", None)
    if status is None and hasattr(parsed, "get"):
        status = parsed.get("status")
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _entry_id(entry, *, reddit: bool = False) -> str:
    """Observation identity is id, then permalink. Reddit never falls back to title."""
    eid = (entry.get("id") or "").strip()
    if eid:
        return eid
    link = (entry.get("link") or "").strip()
    if link:
        return link
    if reddit:
        return ""
    return (entry.get("title") or "").strip()


_FEED_URL_PAYLOAD_KEY = "_semintel_feed_url"


def _reddit_entry_html(entry) -> str:
    """Atom HTML body for Reddit only. Identical duplicate fields are kept once."""
    parts: list[str] = []
    for value in (entry.get("summary"), entry.get("description")):
        text = (value or "").strip()
        if text and text not in parts:
            parts.append(text)
    for block in entry.get("content") or []:
        if isinstance(block, dict):
            text = (block.get("value") or "").strip()
        else:
            text = str(block).strip()
        if text and text not in parts:
            parts.append(text)
    return "\n".join(parts)


def _looks_like_feed(parsed) -> bool:
    version = (getattr(parsed, "version", None) or "").lower()
    return version.startswith(("rss", "atom", "rdf"))


def _raise_if_unusable_feed(source_handle: str, parsed) -> None:
    """Fail closed on HTTP/parser evidence. Empty non-Reddit RSS stays valid."""
    status = _http_status(parsed)
    if status is not None and status >= 400:
        if status == 429:
            raise ProviderUnavailable(f"HTTP 429 rate-limited fetching {source_handle}")
        if status in {401, 403}:
            raise ProviderUnavailable(f"HTTP {status} blocked fetching {source_handle}")
        raise ProviderUnavailable(f"HTTP {status} fetching {source_handle}")

    entries = list(getattr(parsed, "entries", []) or [])
    bozo = bool(getattr(parsed, "bozo", 0))
    if bozo and not entries:
        reason = str(getattr(parsed, "bozo_exception", "malformed feed"))
        raise ProviderUnavailable(f"malformed RSS/Atom response: {reason}")
    if is_reddit_feed(source_handle) and not entries:
        if not _looks_like_feed(parsed):
            raise ProviderUnavailable(
                f"malformed RSS/Atom response fetching {source_handle}"
            )
        raise ProviderUnavailable(
            f"suspicious empty Reddit listing fetching {source_handle}"
        )


class RSSProvider:
    name = "rss"

    def __init__(self, max_items: int = 100, fetch_fn: Optional[FetchFn] = None):
        self.max_items = max_items
        self._fetch_fn = fetch_fn or _default_fetch

    def collect(self, source_handle: str, cursor: Cursor | None) -> CollectResult:
        """`source_handle` is the feed URL. Feeds are newest-first; collection
        stops at the last-seen entry id so re-polling an unchanged feed is a
        cheap no-op, matching IngestionService's existing dedup philosophy."""
        parsed = self._fetch_fn(source_handle)
        _raise_if_unusable_feed(source_handle, parsed)
        reddit = is_reddit_feed(source_handle)
        entries = list(getattr(parsed, "entries", []))[: self.max_items]

        stop_id = cursor.value if cursor else None
        items: list[RawItem] = []
        newest = _entry_id(entries[0], reddit=reddit) if entries else stop_id
        seen_ids: set[str] = set()
        for entry in entries:
            eid = _entry_id(entry, reddit=reddit)
            if stop_id and eid == stop_id:
                break
            if not eid or eid in seen_ids:
                continue
            seen_ids.add(eid)
            payload = dict(entry)
            payload[_FEED_URL_PAYLOAD_KEY] = source_handle
            items.append(RawItem(external_id=eid, payload=payload))
        items.reverse()  # chronological order for storage
        return CollectResult(items=items, next_cursor=Cursor(newest) if newest else cursor)

    def normalize(self, raw: RawItem) -> NormalizedSignal:
        e = raw.payload
        feed_url = str(e.get(_FEED_URL_PAYLOAD_KEY) or "")
        if is_reddit_feed(feed_url):
            return self._normalize_reddit(raw)
        return self._normalize_rss(raw)

    def _normalize_rss(self, raw: RawItem) -> NormalizedSignal:
        """Pre-M0 RSS normalisation. Ordinary feeds must keep this contract."""
        e = raw.payload
        title = (e.get("title") or "").strip()
        summary = (e.get("summary") or e.get("description") or "").strip()
        text = f"{title}\n\n{summary}".strip() if summary else title
        return NormalizedSignal(
            external_id=raw.external_id,
            provider=self.name,
            author_handle=e.get("author"),
            author_display_name=e.get("author"),
            posted_at=_to_datetime(e.get("published_parsed") or e.get("updated_parsed")),
            text=text,
            title=title or None,
            url=e.get("link"),
            links=[e["link"]] if e.get("link") else [],
            raw=e,
        )

    def _normalize_reddit(self, raw: RawItem) -> NormalizedSignal:
        e = raw.payload
        title = (e.get("title") or "").strip()
        summary = _reddit_entry_html(e).strip()
        text = f"{title}\n\n{summary}".strip() if summary else title
        permalink = (e.get("link") or "").strip() or None
        links: list[str] = []
        if permalink:
            links.append(permalink)
        for match in URL_RE.findall(html.unescape(summary)):
            if match not in links:
                links.append(match)
        author = e.get("author")
        if not author:
            detail = e.get("author_detail") or {}
            author = detail.get("name") if isinstance(detail, dict) else None
        return NormalizedSignal(
            external_id=raw.external_id,
            provider=self.name,
            author_handle=author,
            author_display_name=author,
            posted_at=_to_datetime(e.get("published_parsed") or e.get("updated_parsed")),
            text=text,
            title=title or None,
            url=permalink,
            links=links,
            raw=e,
        )

    def validate(self, handle_or_url: str) -> SourceCandidate | ValidationError:
        url = handle_or_url.strip()
        if not url.lower().startswith(("http://", "https://")):
            return ValidationError("RSS source must be a feed URL (http:// or https://)")
        parsed = self._fetch_fn(url)
        try:
            _raise_if_unusable_feed(url, parsed)
        except ProviderUnavailable as exc:
            return ValidationError(str(exc))
        entries = getattr(parsed, "entries", None)
        bozo = getattr(parsed, "bozo", 0)
        if not entries and bozo:
            reason = str(getattr(parsed, "bozo_exception", "could not be parsed as RSS/Atom"))
            return ValidationError(f"not a valid RSS/Atom feed: {reason}")
        feed_title = getattr(parsed, "feed", {}).get("title", url) if hasattr(parsed, "feed") else url
        return SourceCandidate(provider="rss", provider_key=url, display_name=feed_title, profile_url=url)
