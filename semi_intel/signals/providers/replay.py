"""Replay provider -- serves recorded/hand-built fixtures through the
Provider interface. Ported from Signal Radar's `providers/replay/`.

Purpose: make the collection -> analysis -> candidate pipeline fully testable
and demoable without any network access, and let the manual-acceptance
scenario (brief section 30) be reproduced deterministically in CI. Fixtures
are lists of raw items keyed by handle, applying the same newer-than-cursor
incremental semantics a real provider would.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from semi_intel.signals.providers import (
    CollectResult,
    Cursor,
    NormalizedSignal,
    RawItem,
    SourceCandidate,
    ValidationError,
)


class ReplayProvider:
    """`name` defaults to "replay" but can masquerade as any provider (e.g.
    "x") so downstream code paths are exercised identically to the real
    thing -- construct with `name="x"` to replay fixtures through the same
    selection query a live X source would use."""

    def __init__(self, name: str = "replay", fixtures: dict[str, list[dict[str, Any]]] | None = None):
        self.name = name
        # fixtures[handle] = [ {external_id, posted_at, text, author, ...}, ... ]
        # ordered oldest -> newest.
        self.fixtures = fixtures or {}

    def set_fixture(self, handle: str, items: list[dict[str, Any]]) -> None:
        self.fixtures[handle] = items

    def collect(self, source_handle: str, cursor: Cursor | None) -> CollectResult:
        items = self.fixtures.get(source_handle, [])
        last_seen = cursor.value if cursor else None
        new: list[RawItem] = []
        for raw in reversed(items):  # newest -> oldest, stop at cursor
            if last_seen is not None and str(raw["external_id"]) == str(last_seen):
                break
            new.append(RawItem(external_id=str(raw["external_id"]), payload=raw))
        new.reverse()  # chronological order for storage
        next_cursor = Cursor(str(items[-1]["external_id"])) if items else cursor
        return CollectResult(items=new, next_cursor=next_cursor)

    def normalize(self, raw: RawItem) -> NormalizedSignal:
        p = raw.payload
        posted = p.get("posted_at")
        posted_at = dt.datetime.fromisoformat(posted.replace("Z", "+00:00")) if posted else None
        return NormalizedSignal(
            external_id=str(p["external_id"]),
            provider=self.name,
            author_handle=p.get("author"),
            author_display_name=p.get("author_display_name") or p.get("author"),
            posted_at=posted_at,
            text=p.get("text", ""),
            title=p.get("title"),
            url=p.get("url"),
            language=p.get("language"),
            media=p.get("media", []),
            links=p.get("links", []),
            quoted_external_id=p.get("quoted_external_id"),
            reply_to_external_id=p.get("reply_to_external_id"),
            fidelity=p.get("fidelity", "full"),
            raw=p,
        )

    def validate(self, handle_or_url: str) -> SourceCandidate | ValidationError:
        handle = handle_or_url.strip().rstrip("/").split("/")[-1].lstrip("@")
        if not handle:
            return ValidationError("empty handle")
        return SourceCandidate(
            provider=self.name, provider_key=handle, display_name=handle,
            profile_url=f"replay://{self.name}/{handle}",
        )
