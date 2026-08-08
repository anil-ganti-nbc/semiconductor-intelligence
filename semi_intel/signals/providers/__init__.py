"""The provider contract every signal source implements.

Ported from Signal Radar's `providers/base.py` (see PHASE0_AUDIT.md section 5)
and adapted to this codebase's synchronous style -- semi_intel has no asyncio
runtime anywhere else (the web layer is FastAPI but every existing service
call, including PipelineService, is plain sync SQLAlchemy), so an async
Provider protocol here would be the only async code in the project for no
benefit. Nothing about the contract itself changed: collect/normalize/
validate, raw payload preserved, cursor is opaque and provider-owned.

Contract:
    collect()   -- fetch new raw items for one source since a cursor. Network
                   I/O lives here and ONLY here. No parsing, no interpretation.
    normalize() -- convert one raw payload to the platform-neutral
                   NormalizedSignal. Pure function of the raw payload; must be
                   re-runnable forever (this is what makes reprocessing safe).
    validate()  -- resolve a pasted handle/URL to a collectable source
                   candidate. Powers the low-friction Add Source flow.

Every provider failure is isolated by the caller (CollectionService), the
same way IngestionService/PipelineService already isolate a bad RSS feed
today -- one dead/misbehaving provider must never abort a collection cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Cursor:
    """Opaque per-source collection position. Providers own the format."""

    value: str


@dataclass(frozen=True)
class RawItem:
    """One collected item exactly as the provider returned it."""

    external_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class CollectResult:
    items: list[RawItem]
    next_cursor: Cursor | None


@dataclass(frozen=True)
class NormalizedSignal:
    """Platform-neutral signal shape. Everything downstream (SignalItem
    persistence, analysis) consumes this, never a provider's raw payload
    directly."""

    external_id: str
    provider: str
    author_handle: str | None
    posted_at: datetime | None
    text: str
    title: str | None = None
    url: str | None = None
    author_display_name: str | None = None
    language: str | None = None
    media: list[dict[str, Any]] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    quoted_external_id: str | None = None
    reply_to_external_id: str | None = None
    fidelity: str = "full"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceCandidate:
    """validate() success: a resolvable, collectable source."""

    provider: str
    provider_key: str
    display_name: str
    profile_url: str


@dataclass(frozen=True)
class ValidationError:
    reason: str


class ProviderUnavailable(Exception):
    """Raised by a provider constructor/collect() when a required optional
    dependency (e.g. Playwright for the X provider) is not installed, or a
    required credential/session is missing. Callers must treat this exactly
    like "this source is skipped, not fatally errored" (brief section 17) --
    never let a missing optional extra crash the base application."""


@runtime_checkable
class Provider(Protocol):
    """The one interface every provider must expose."""

    name: str

    def collect(self, source_handle: str, cursor: Cursor | None) -> CollectResult: ...

    def normalize(self, raw: RawItem) -> NormalizedSignal: ...

    def validate(self, handle_or_url: str) -> SourceCandidate | ValidationError: ...


class ProviderRegistry:
    """Runtime registry. Adding a provider = one register() call at startup."""

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        if provider.name in self._providers:
            raise ValueError(f"provider already registered: {provider.name}")
        self._providers[provider.name] = provider

    def get(self, name: str) -> Provider:
        return self._providers[name]

    def names(self) -> list[str]:
        return sorted(self._providers)
