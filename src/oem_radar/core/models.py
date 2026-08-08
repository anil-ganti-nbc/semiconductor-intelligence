"""Normalized product model — the universal contract of the platform.

Everything downstream of an engine's `normalize()` (storage, resolution, diff,
severity, AI, notifications) speaks only these types. Engines never invent
values: unknown is None, and `confidence` reflects parse quality.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum, IntEnum


class StrEnum(str, Enum):
    """3.10-compatible StrEnum (stdlib version arrived in 3.11)."""

    def __str__(self) -> str:  # pragma: no cover
        return self.value
from typing import Any

from pydantic import BaseModel, Field


class Severity(IntEnum):
    NOISE = 1
    MINOR = 2
    NOTABLE = 3
    SIGNIFICANT = 4
    BREAKING = 5


class ProductStatus(StrEnum):
    ACTIVE = "active"
    PRE_RELEASE = "pre_release"
    REMOVED = "removed"
    UNCERTAIN = "uncertain"


class Availability(StrEnum):
    IN_STOCK = "in_stock"
    PREORDER = "preorder"
    SOLD_OUT = "sold_out"
    UNKNOWN = "unknown"


class ChangeType(StrEnum):
    NEW_PRODUCT = "new_product"
    COMPONENT_CHANGED = "component_changed"
    SPEC_CHANGED = "spec_changed"
    PRICE_CHANGED = "price_changed"
    AVAILABILITY_CHANGED = "availability_changed"
    IMAGES_CHANGED = "images_changed"
    DESCRIPTION_CHANGED = "description_changed"
    PRODUCT_RENAMED = "product_renamed"
    REGIONAL_VARIANT = "regional_variant"
    DUPLICATE_LISTING = "duplicate_listing"
    PRODUCT_REMOVED = "product_removed"
    SUPPORT_ARTIFACT_ADDED = "support_artifact_added"
    SOURCE_DEGRADED = "source_degraded"


class Component(BaseModel):
    """A hardware component as seen on a listing.

    `raw` is always the vendor's string. `canonical` is filled by the
    normalization layer's canonicalizer (M5); `known` is stamped by the
    known-hardware DB at diff time. None means "not yet determined".
    """

    raw: str
    canonical: str | None = None
    known: bool | None = None


class Price(BaseModel):
    amount: float
    currency: str
    region: str | None = None
    availability: Availability = Availability.UNKNOWN


class Configuration(BaseModel):
    """One purchasable configuration of a product (a Shopify variant, a JD
    SKU, ...). DESIGN_REVIEW §4: variants must not be flattened — per-config
    truth (which config is sold out, which got the price cut, which SKU) is
    where identity and price intelligence live."""

    label: str | None = None      # vendor option title, e.g. "32GB RAM + 1TB SSD"
    sku: str | None = None
    memory: str | None = None
    storage: str | None = None
    price: float | None = None
    currency: str | None = None
    region: str | None = None
    availability: Availability = Availability.UNKNOWN

    def key(self) -> str:
        """Stable identity of a config for diffing."""
        return self.sku or f"{self.label}|{self.region}"


class NormalizedProduct(BaseModel):
    # identity
    manufacturer: str
    model: str
    series: str | None = None
    category: str | None = None
    # hardware
    cpu: Component | None = None
    gpu: Component | None = None
    npu: Component | None = None
    memory: str | None = None
    storage: str | None = None
    display: str | None = None
    battery: str | None = None
    wireless: str | None = None
    ports: list[str] = Field(default_factory=list)
    operating_system: str | None = None
    # commerce — configurations are the source of truth; prices/memory/storage
    # above are summaries kept for display and diff continuity
    configurations: list[Configuration] = Field(default_factory=list)
    prices: list[Price] = Field(default_factory=list)
    vendor_sku: str | None = None  # primary SKU; strongest vendor identity signal
    region: str | None = None      # listing-level region when the source is regional
    # provenance
    images: list[str] = Field(default_factory=list)
    description: str | None = None
    source_url: str
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    status: ProductStatus = ProductStatus.ACTIVE
    confidence: float = 1.0
    aliases: list[str] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict)

    # Fields that describe *observation*, not the product itself. They must
    # never influence the content hash, or every crawl would look like a
    # change (ADR-4).
    _OBSERVATION_FIELDS = frozenset({"first_seen", "last_seen", "raw_data", "confidence"})

    def canonical_json(self) -> str:
        data = self.model_dump(mode="json", exclude=set(self._OBSERVATION_FIELDS))
        # Components hash on the vendor's raw string only: `canonical` and
        # `known` are derived world-knowledge (canonicalizer versions and the
        # known-hw DB change over time) and must not fabricate product changes.
        for f in ("cpu", "gpu", "npu"):
            if isinstance(data.get(f), dict):
                data[f] = {"raw": data[f]["raw"]}
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class RawProduct(BaseModel):
    """Engine-parsed but not yet normalized. `payload` is vendor-shaped."""

    source_id: str
    url: str
    payload: dict[str, Any]


class ProductRef(BaseModel):
    """A discovered pointer to a product page/endpoint.

    `inline_payload`: bulk discovery endpoints (e.g. Shopify /products.json)
    already carry full product data; the pipeline then skips the per-product
    fetch entirely — 1 request per 250 products instead of 250. Politeness
    by architecture, not just by delay.
    """

    url: str
    handle: str | None = None
    hidden: bool = False  # found via sitemap/JSON but not navigation
    inline_payload: dict | None = None


class FetchedDocument(BaseModel):
    url: str
    status: int
    body: str
    content_type: str = "text/html"
    headers: dict[str, str] = Field(default_factory=dict)
    from_cache: bool = False


class ValidationIssue(BaseModel):
    field: str
    message: str
    fatal: bool = False  # fatal issues zero the confidence, never drop the product


class ChangeEvent(BaseModel):
    product_key: str
    change_type: ChangeType
    field: str | None = None
    old_value: Any = None
    new_value: Any = None
    severity: Severity = Severity.MINOR
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    meta: dict[str, Any] = Field(default_factory=dict)  # unseen_component, direction, magnitude_pct, ...

    def dedup_key(self) -> str:
        basis = f"{self.product_key}|{self.change_type}|{self.field}|{self.old_value!r}|{self.new_value!r}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


class StoryEvidence(BaseModel):
    """One event supporting a story (kept lean for the alert)."""

    manufacturer: str
    model: str
    product_key: str
    detected_at: str
    source_url: str | None = None


class Story(BaseModel):
    """A cross-event correlation ('N OEMs listed the same unseen chip'), built
    deterministically from ChangeEvents. AI (if enabled) only narrates it."""

    rule_id: str
    key: str
    title: str
    score: int
    score_reasons: list[str] = Field(default_factory=list)
    manufacturers: list[str] = Field(default_factory=list)
    evidence: list[StoryEvidence] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def dedup_key(self) -> str:
        oems = ",".join(sorted(self.manufacturers))
        basis = f"{self.rule_id}|{self.key}|{oems}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]
