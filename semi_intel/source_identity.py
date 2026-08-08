"""Canonical identities for source registration.

Display names are editorial metadata, not identity.  RSS sources can enter
through either the legacy source form or Signal Radar, so both paths use the
same URL comparison here to avoid registering and polling one feed twice.
"""

from __future__ import annotations

import posixpath
from urllib.parse import parse_qsl, urlencode, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import SourceType
from semi_intel.domain.models import Source

_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


def canonical_feed_key(url: str | None) -> str | None:
    """Return a comparison key for an HTTP(S) feed URL.

    Scheme, ``www.``, fragments, default ports, trailing slashes and common
    tracking parameters do not distinguish feeds.  The path and meaningful
    query parameters remain part of the key because one site may expose
    several genuinely different feeds.
    """

    if not url:
        return None
    value = url.strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None

    host = parsed.hostname.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    port = parsed.port
    if port and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        host = f"{host}:{port}"

    raw_path = parsed.path or "/"
    path = posixpath.normpath("/" + raw_path.lstrip("/"))
    if path != "/":
        path = path.rstrip("/")

    query = urlencode(
        sorted(
            (key, val)
            for key, val in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in _TRACKING_QUERY_KEYS
        ),
        doseq=True,
    )
    return f"{host}{path}" + (f"?{query}" if query else "")


def find_source_by_feed_url(session: Session, url: str | None) -> Source | None:
    """Find an existing RSS source across legacy and Radar registration paths."""

    target = canonical_feed_key(url)
    if target is None:
        return None
    sources = session.scalars(
        select(Source).where(
            Source.type == SourceType.RSS,
            (Source.url.is_not(None)) | (Source.provider_key.is_not(None)),
        )
    )
    for source in sources:
        for candidate in (source.url, source.provider_key):
            if canonical_feed_key(candidate) == target:
                return source
    return None
