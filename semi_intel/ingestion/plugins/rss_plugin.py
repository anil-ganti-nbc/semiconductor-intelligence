"""RSS/Atom source plugin, for feeds like VideoCardz, Tom's Hardware, etc.

The actual network fetch is injected (`fetch_fn`) rather than hardcoded to
`feedparser.parse(url)`, purely so this class stays unit-testable without
hitting the network: tests pass a `fetch_fn` that calls `feedparser.parse()`
on a fixture string instead of a URL. In production, the default `fetch_fn`
is exactly `feedparser.parse`, which itself handles the HTTP request.
"""

from __future__ import annotations

import calendar
import datetime as dt
import socket
from typing import Callable, Iterable, Optional

import feedparser

from semi_intel.domain.enums import SourceType
from semi_intel.ingestion.base import RawItem, SourcePlugin

FetchFn = Callable[[str], "feedparser.FeedParserDict"]

# feedparser.parse() has no timeout parameter of its own -- under the hood
# it uses urllib, which defaults to blocking forever if the remote host
# accepts the TCP connection but then never responds (a network that
# silently drops packets rather than actively refusing looks exactly like
# this). Without a timeout, a single slow/dead feed can hang `pipeline run`,
# `semintel run`, and `semintel test-source` indefinitely with no feedback
# -- exactly the kind of thing an operator with no Python experience has no
# way to diagnose. socket.setdefaulttimeout() is the only lever feedparser
# exposes for this, so we set it just for the duration of this call and
# restore whatever it was before.
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


class RSSSourcePlugin(SourcePlugin):
    source_type = SourceType.RSS

    def __init__(
        self,
        name: str,
        feed_url: str,
        default_trust_weight: float = 0.5,
        fetch_fn: Optional[FetchFn] = None,
    ):
        self.name = name
        self.feed_url = feed_url
        self.default_trust_weight = default_trust_weight
        self._fetch_fn = fetch_fn or _default_fetch

    def fetch(self) -> Iterable[RawItem]:
        parsed = self._fetch_fn(self.feed_url)
        for entry in getattr(parsed, "entries", []):
            title = entry.get("title", "").strip()
            content = (
                entry.get("summary")
                or entry.get("description")
                or title
            ).strip()
            if not content:
                continue
            yield RawItem(
                title=title or content[:120],
                content=content,
                external_id=entry.get("id") or entry.get("link"),
                url=entry.get("link"),
                observed_at=_to_datetime(entry.get("published_parsed") or entry.get("updated_parsed")),
                raw=dict(entry),
            )
