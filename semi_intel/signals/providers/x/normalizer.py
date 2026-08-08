"""The ONLY X-schema-aware code.

Ported from Signal Radar. Converts a raw tweet 'result' object (as found
inside X's GraphQL timeline responses) into a NormalizedSignal. When X
reshapes its payloads, this is the file to fix -- nothing else in the
platform knows these field names. It is intentionally defensive: unfamiliar
shapes raise SchemaDrift so the caller can log it and fall back, rather than
silently dropping data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from semi_intel.signals.providers import NormalizedSignal, RawItem


class SchemaDrift(RuntimeError):
    """Raised when the raw object doesn't match any known X tweet shape."""


def _tweet_result(obj: dict[str, Any]) -> dict[str, Any]:
    r = obj
    if "tweet" in r:
        r = r["tweet"]
    return r


def parse_created_at(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")  # X format
    except ValueError:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None


def extract_media(legacy: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    ext = legacy.get("extended_entities", {}) or legacy.get("entities", {})
    for m in ext.get("media", []) or []:
        kind = {"photo": "image", "video": "video", "animated_gif": "gif"}.get(
            m.get("type", "photo"), "image")
        url = m.get("media_url_https") or m.get("media_url") or ""
        if kind in ("video", "gif"):
            variants = (m.get("video_info", {}) or {}).get("variants", [])
            mp4 = [v for v in variants if v.get("content_type") == "video/mp4"]
            if mp4:
                url = max(mp4, key=lambda v: v.get("bitrate", 0)).get("url", url)
        out.append({"kind": kind, "url": url, "alt_text": m.get("ext_alt_text")})
    return out


def normalize(raw: RawItem) -> NormalizedSignal:
    result = _tweet_result(raw.payload)
    legacy = result.get("legacy")
    core = result.get("core", {})
    if legacy is None:
        raise SchemaDrift("no 'legacy' field on tweet result")

    # X has moved screen_name around over time. Check every known location.
    author = ""
    user_result = (core or {}).get("user_results", {}).get("result", {})
    if user_result:
        author = (
            user_result.get("legacy", {}).get("screen_name")
            or user_result.get("core", {}).get("screen_name")
            or user_result.get("screen_name", "")
        )
    if not author:
        author = legacy.get("screen_name", "") or raw.payload.get("_author", "")

    full_text = legacy.get("full_text") or legacy.get("text") or ""
    ext_id = str(legacy.get("id_str") or result.get("rest_id") or raw.external_id)

    links = [u.get("expanded_url") for u in
             (legacy.get("entities", {}).get("urls", []) or []) if u.get("expanded_url")]

    quoted = legacy.get("quoted_status_id_str")
    reply = legacy.get("in_reply_to_status_id_str")

    return NormalizedSignal(
        external_id=ext_id,
        provider="x",
        author_handle=author,
        author_display_name=author,
        posted_at=parse_created_at(legacy.get("created_at")),
        text=full_text,
        language=legacy.get("lang"),
        media=extract_media(legacy),
        links=links,
        quoted_external_id=quoted,
        reply_to_external_id=reply,
        raw=raw.payload,
    )
