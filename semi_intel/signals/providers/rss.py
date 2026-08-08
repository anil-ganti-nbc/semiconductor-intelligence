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
import socket
from typing import Callable, Optional

import feedparser

from semi_intel.signals.providers import (
    CollectResult,
    Cursor,
    NormalizedSignal,
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


def _entry_id(entry) -> str:
    return entry.get("id") or entry.get("link") or entry.get("title", "")


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
        entries = list(getattr(parsed, "entries", []))[: self.max_items]

        stop_id = cursor.value if cursor else None
        items: list[RawItem] = []
        newest = _entry_id(entries[0]) if entries else stop_id
        for entry in entries:
            eid = _entry_id(entry)
            if stop_id and eid == stop_id:
                break
            if eid:
                items.append(RawItem(external_id=eid, payload=dict(entry)))
        items.reverse()  # chronological order for storage
        return CollectResult(items=items, next_cursor=Cursor(newest) if newest else cursor)

    def normalize(self, raw: RawItem) -> NormalizedSignal:
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

    def validate(self, handle_or_url: str) -> SourceCandidate | ValidationError:
        url = handle_or_url.strip()
        if not url.lower().startswith(("http://", "https://")):
            return ValidationError("RSS source must be a feed URL (http:// or https://)")
        parsed = self._fetch_fn(url)
        entries = getattr(parsed, "entries", None)
        bozo = getattr(parsed, "bozo", 0)
        if not entries and bozo:
            reason = str(getattr(parsed, "bozo_exception", "could not be parsed as RSS/Atom"))
            return ValidationError(f"not a valid RSS/Atom feed: {reason}")
        feed_title = getattr(parsed, "feed", {}).get("title", url) if hasattr(parsed, "feed") else url
        return SourceCandidate(provider="rss", provider_key=url, display_name=feed_title, profile_url=url)
