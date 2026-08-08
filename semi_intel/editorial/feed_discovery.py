from __future__ import annotations

import re
import urllib.request
from typing import Callable
from urllib.parse import urljoin

import feedparser

ALTERNATE_RE = re.compile(
    r"<link\b[^>]*rel=[\"'][^\"']*alternate[^\"']*[\"'][^>]*href=[\"']([^\"']+)[\"'][^>]*>",
    re.I,
)
COMMON_FEEDS = ("/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml")
USER_AGENT = "SemiIntel/2.1 (+local editorial feed discovery)"


def _fetch(url: str, timeout: int = 8) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(1_000_000)


def valid_feed(payload: bytes) -> bool:
    """A feed is usable if feedparser extracted entries from it, even if it
    also set `bozo` for a non-fatal quirk (truncation, unescaped entities,
    minor malformed XML) -- feedparser is a liberal parser by design, and
    real-world feeds routinely trip `bozo` while still yielding good
    entries. Matches the same not-entries-and-bozo policy already used by
    semi_intel/signals/providers/rss.py's validate()."""
    parsed = feedparser.parse(payload)
    return bool(getattr(parsed, "entries", []))


def discover_feeds(site_url: str, fetcher: Callable[[str], bytes] = _fetch) -> list[str]:
    candidates: list[str] = []
    try:
        page = fetcher(site_url).decode("utf-8", errors="replace")
        candidates.extend(urljoin(site_url, match) for match in ALTERNATE_RE.findall(page))
    except Exception:
        page = ""
    candidates.extend(urljoin(site_url, path) for path in COMMON_FEEDS)
    valid: list[str] = []
    for candidate in dict.fromkeys(candidates):
        try:
            if valid_feed(fetcher(candidate)):
                valid.append(candidate)
        except Exception:
            continue
    return valid
