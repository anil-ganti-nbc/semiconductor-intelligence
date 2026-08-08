"""Core protocols. Structural typing (`Protocol`): engines and providers
implement these without importing core internals — only `models`.

Rules enforced by review and tests, not just types:
- Engines do I/O only through the injected Fetcher.
- Engines never touch storage, notification, or AI.
- Providers never import engines, and vice versa.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from .models import (
    ChangeEvent,
    FetchedDocument,
    NormalizedProduct,
    ProductRef,
    RawProduct,
    ValidationIssue,
)


@runtime_checkable
class Fetcher(Protocol):
    """Owned by core: politeness budgets, jittered delays, backoff,
    conditional GETs, caching (M1). Engines just call get()."""

    def get(self, url: str) -> FetchedDocument: ...


@runtime_checkable
class SourceEngine(Protocol):
    """One implementation per storefront *platform* (ADR-2), configured per
    source via YAML validated against `config_schema`."""

    config_schema: type  # pydantic model for the YAML source block

    def discover(self, fetcher: Fetcher) -> Iterable[ProductRef]: ...
    def parse(self, doc: FetchedDocument) -> RawProduct: ...
    def normalize(self, raw: RawProduct) -> NormalizedProduct: ...
    def validate(self, product: NormalizedProduct) -> list[ValidationIssue]: ...


@runtime_checkable
class SnapshotStore(Protocol):
    """Append-only snapshot storage with content-hash dedup (ADR-4)."""

    def latest(self, product_key: str) -> NormalizedProduct | None: ...
    def append(self, product_key: str, product: NormalizedProduct) -> None: ...
    def touch(self, product_key: str) -> None: ...  # observation only: bump last_seen

    def resolve_prior(
        self, product_key: str, product: NormalizedProduct
    ) -> tuple[NormalizedProduct | None, str]:
        """Entity resolution v1 (ADR-3). Returns (prior_snapshot, relation):
        relation ∈ {'same_listing', 'existing_product', 'none'}.
        'existing_product' means a *different* listing already maps to this
        product identity — a variant/duplicate, not breaking news."""
        ...

    def known_component(self, canonical: str) -> bool: ...
    def learn_component(self, kind: str, canonical: str, raw: str) -> None: ...


@runtime_checkable
class Notifier(Protocol):
    """Outbox semantics (ADR-1): enqueue is transactional with the crawl;
    drain happens separately and retries across runs. The product is passed
    so the rendered payload can be built and persisted at enqueue time."""

    def enqueue(self, event: ChangeEvent, product: NormalizedProduct | None = None) -> None: ...
    def drain(self) -> int: ...  # returns number sent


@runtime_checkable
class Summarizer(Protocol):
    """AI or template renderer. Receives diff *facts*, never raw pages;
    output is grounding-validated upstream of delivery (ADR-5)."""

    def render(
        self,
        events: list[ChangeEvent],
        before: NormalizedProduct | None,
        after: NormalizedProduct,
    ) -> str: ...


@runtime_checkable
class DiscoveryStrategy(Protocol):
    """Registered by name; sources list which strategies to run (ADR-7)."""

    def discover(self, fetcher: Fetcher, base_url: str) -> Iterable[ProductRef]: ...
