"""Dell catalog engine — the first big-brand (non-boutique) engine.

Unlike the Shopify engine (one JSON endpoint per store), Dell exposes
server-rendered category listing pages. Feasibility probe (2026-07-22)
confirmed the /sr/laptops listing is static HTML — no browser needed — and
embeds a JSON-LD ItemList of products, which is the robust parse target.

Two extraction paths, primary then fallback (see DESIGN_REVIEW: prefer
structured data over DOM scraping):
  1. JSON-LD `ItemList` of `Product` objects — stable, vendor-provided.
  2. Text-anchor scrape ("Model <code>", "Starting at $<price>") — fallback
     if Dell drops the JSON-LD; keeps the engine alive through markup churn.

Signal scope (owner decision): Dell sells hundreds of near-identical configs,
so this engine tracks MODEL-LEVEL products (one per Dell model code like
DX13260), keyed by that code. New model codes and previously-unseen silicon
are the news; per-config price churn is deliberately not surfaced at the
catalog level. Deep per-config crawling is a documented future step.
"""

from __future__ import annotations

import json
import re
from typing import Iterable

from pydantic import BaseModel, Field

from ...core.config import SourceConfig
from ...core.interfaces import Fetcher
from ...core.models import (
    Availability,
    Component,
    NormalizedProduct,
    Price,
    ProductRef,
    RawProduct,
    ValidationIssue,
)
from ...core.registry import engines

_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
# Fallback text anchors (from the flattened listing capture).
_MODEL_RE = re.compile(r"Model\s+([A-Z]{2}\d{4,6})\b")
_PRICE_RE = re.compile(r"Starting at\s*\$([\d,]+(?:\.\d{2})?)")
_DISPLAY_RE = re.compile(r"Display\s+([\d.]+)\s*\"")

# Silicon extraction from Dell's description prose.
_CPU_PATTERNS = [
    # Specific model number first (e.g. "Core Ultra 9 285H"), then the bare
    # family Dell often uses on listings ("Core Ultra Processors").
    re.compile(r"(?:Intel\s+)?Core\s+Ultra\s+(?:Plus\s+)?\d\s*\d{2,3}[A-Z]*", re.I),
    re.compile(r"(?:Intel\s+)?Core\s+i[3579][- ]\d{4,5}[A-Z]*", re.I),
    re.compile(r"AMD\s+Ryzen(?:\s+AI)?\s+\d\s+[A-Z0-9]+", re.I),
    re.compile(r"Snapdragon\s+X\s+(?:Elite|Plus)", re.I),
    re.compile(r"(?:Intel\s+)?Core\s+Ultra(?:\s+Plus)?", re.I),  # bare family fallback
    re.compile(r"AMD\s+Ryzen\s+AI", re.I),
]
_GPU_PATTERNS = [
    re.compile(r"(?:NVIDIA\s+)?(?:GeForce\s+)?RTX\s+(?:PRO\s+)?\d{3,4}\s*(?:Ti|Super)?", re.I),
    re.compile(r"(?:NVIDIA\s+)?(?:GeForce\s+)?RTX\s+\d{2}\s+Series", re.I),  # "RTX 50 Series"
    re.compile(r"Radeon\s+[A-Z0-9]+", re.I),
    re.compile(r"Intel\s+Arc\b", re.I),
]


def _first(patterns, *texts):
    for t in texts:
        for p in patterns:
            m = p.search(t or "")
            if m:
                return re.sub(r"\s+", " ", m.group(0)).strip()
    return None


class DellSourceConfig(BaseModel):
    model_config = {"extra": "ignore"}
    currency_default: str = "USD"
    region: str = "us"
    category_paths: list[str] = Field(default_factory=lambda: ["/en-us/shop/dell-laptops/sr/laptops"])
    category: str = "laptop"
    # Opt-in: fetch each model's static spec page for EXACT silicon/memory/
    # storage/display. Costs one extra request per model (politeness), so it's
    # off by default. Gaming models name exact CPU+GPU; all models give exact
    # RAM/storage/display. See docs/BIG_BRANDS.md.
    deep_crawl: bool = False
    deep_crawl_limit: int = 60  # safety cap on spec-page fetches per run


# Labelled spec sections on Dell /spd/ pages (static HTML, verified 2026-07-22).
_MEM_LINE = re.compile(r"(\d+)\s*GB,[^<\n]*?(LPDDR\w+|DDR\d[A-Za-z]*)", re.I)
_STOR_LINE = re.compile(r"(\d+)\s*(GB|TB),[^<\n]*?(?:SSD|eMMC|NVMe)", re.I)
_DISPLAY_LINE = re.compile(r"(\d+(?:\.\d+)?)[- ]inch[^<\n]*", re.I)


@engines.register("dell")
class DellEngine:
    config_schema = DellSourceConfig

    def __init__(self, source: SourceConfig, manufacturer: str) -> None:
        self.source = source
        self.manufacturer = manufacturer
        self.cfg = DellSourceConfig.model_validate(source.model_dump())
        self.base = source.base_url.rstrip("/")

    # -- discovery: catalog pages yield inline product payloads --------------

    def discover(self, fetcher: Fetcher) -> Iterable[ProductRef]:
        seen: set[str] = set()
        deep_budget = self.cfg.deep_crawl_limit
        for path in self.cfg.category_paths:
            doc = fetcher.get(self.base + path)
            for item in self._extract_items(doc.body):
                code = item.get("sku")
                if not code or code in seen:
                    continue
                seen.add(code)
                url = item.get("url") or self.base + path
                # Opt-in second pass: enrich with exact specs from the model's
                # static spec page. Costs one polite request per model.
                if self.cfg.deep_crawl and url and deep_budget > 0:
                    deep_budget -= 1
                    try:
                        item["_deep"] = self._deep_specs(fetcher.get(url).body)
                    except Exception:
                        pass  # deep enrichment is best-effort; catalog data stands
                yield ProductRef(url=url, handle=code, inline_payload=item)

    def _deep_specs(self, html: str) -> dict:
        """Extract exact silicon/memory/storage/display from a /spd/ spec page.
        Returns top (largest) memory/storage config plus full lists in raw."""
        text = re.sub(r"<[^>]+>", "\n", html)
        text = text.replace("®", "").replace("™", "").replace("©", "")
        cpu = _first(_CPU_PATTERNS, text)
        gpu = _first(_GPU_PATTERNS, text)
        # Trim GPU to the model token (drop "Laptop GPU, 16 GB GDDR7" tail).
        if gpu:
            gm = re.search(r"RTX\s+(?:PRO\s+)?\d{3,4}\s*(?:Ti|Super)?|Radeon\s+\w+|Arc\s*\w*",
                           gpu, re.I)
            gpu = gm.group(0).strip() if gm else gpu
        mems = [(int(a), b) for a, b in _MEM_LINE.findall(text)]
        stors = []
        for a, unit in _STOR_LINE.findall(text):
            gb = int(a) * (1024 if unit.upper() == "TB" else 1)
            stors.append((gb, f"{a} {unit.upper()}"))
        display = _DISPLAY_LINE.search(text)
        top_mem = max(mems, key=lambda x: x[0]) if mems else None
        top_stor = max(stors, key=lambda x: x[0]) if stors else None
        return {
            "cpu": cpu, "gpu": gpu,
            "memory": (f"{top_mem[0]} GB {top_mem[1]}" if top_mem else None),
            "storage": (top_stor[1] if top_stor else None),
            "display": (display.group(0).strip() if display else None),
            "memory_options": [f"{a} GB {b}" for a, b in mems],
            "storage_options": [s for _, s in stors],
        }

    def _extract_items(self, html: str) -> list[dict]:
        items = self._from_jsonld(html)
        if items:
            return items
        return self._from_text(html)  # fallback

    def _from_jsonld(self, html: str) -> list[dict]:
        out: list[dict] = []
        for block in _JSONLD_RE.findall(html):
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            for node in data if isinstance(data, list) else [data]:
                if node.get("@type") == "ItemList":
                    for el in node.get("itemListElement", []):
                        prod = el.get("item", el) if isinstance(el, dict) else {}
                        if prod.get("@type") == "Product" or "sku" in prod:
                            out.append(prod)
        return out

    def _from_text(self, html: str) -> list[dict]:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        out = []
        for m in _MODEL_RE.finditer(text):
            window = text[m.start(): m.start() + 200]
            price = _PRICE_RE.search(window)
            display = _DISPLAY_RE.search(window)
            out.append({
                "sku": m.group(1),
                "name": None,
                "_display": display.group(1) + '"' if display else None,
                "offers": {"price": price.group(1).replace(",", ""),
                           "priceCurrency": self.cfg.currency_default} if price else {},
            })
        return out

    # -- parse / normalize / validate ---------------------------------------

    def parse(self, doc) -> RawProduct:
        return RawProduct(source_id=self.source.id, url=doc.url,
                          payload=json.loads(doc.body))

    def normalize(self, raw: RawProduct) -> NormalizedProduct:
        p = raw.payload
        code = p.get("sku")
        name = p.get("name") or (f"Dell {code}" if code else "Dell (unknown)")
        desc = p.get("description") or ""
        confidence = 1.0

        offers = p.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price_amt = None
        try:
            price_amt = float(offers.get("price")) if offers.get("price") else None
        except (TypeError, ValueError):
            price_amt = None
        avail_raw = str(offers.get("availability", "")).lower()
        availability = (Availability.IN_STOCK if "instock" in avail_raw
                        else Availability.UNKNOWN)
        prices = ([Price(amount=price_amt, currency=offers.get("priceCurrency",
                  self.cfg.currency_default), region=self.cfg.region.upper(),
                  availability=availability)] if price_amt else [])

        # Deep-crawl values (exact, from the spec page) take precedence over
        # the vague catalog description when present.
        deep = p.get("_deep") or {}
        cpu = deep.get("cpu") or _first(_CPU_PATTERNS, name, desc)
        gpu = deep.get("gpu") or _first(_GPU_PATTERNS, name, desc)
        if cpu is None:
            confidence -= 0.2  # Dell often names silicon only on the config page

        memory = deep.get("memory")
        storage = deep.get("storage")

        display = deep.get("display") or p.get("_display")
        if not display:
            dm = re.search(r"([\d.]+)[- ]?inch", desc, re.I)
            display = (dm.group(1) + '"') if dm else None

        images = []
        if p.get("image"):
            images = [p["image"]] if isinstance(p["image"], str) else list(p["image"])[:3]

        return NormalizedProduct(
            manufacturer=self.manufacturer,
            model=name,
            series=self._series(name),
            category=self.cfg.category,
            cpu=Component(raw=cpu) if cpu else None,
            gpu=Component(raw=gpu) if gpu else None,
            memory=memory,
            storage=storage,
            display=display,
            prices=prices,
            region=self.cfg.region.upper(),
            vendor_sku=code,
            images=[i.split("?")[0] for i in images],
            description=desc[:2000] or None,
            source_url=p.get("url") or self.base,
            confidence=max(confidence, 0.3),
            aliases=[code] if code else [],
            raw_data={"dell_model_code": code, "region": self.cfg.region,
                      "memory_options": deep.get("memory_options", []),
                      "storage_options": deep.get("storage_options", [])},
        )

    @staticmethod
    def _series(name: str) -> str | None:
        for s in ("XPS", "Alienware", "Dell Pro Max", "Dell Pro", "Inspiron",
                  "Latitude", "Precision", "Chromebook"):
            if s.lower() in (name or "").lower():
                return s
        return None

    def validate(self, product: NormalizedProduct) -> list[ValidationIssue]:
        issues = []
        if not product.vendor_sku:
            issues.append(ValidationIssue(field="vendor_sku",
                          message="no Dell model code", fatal=True))
        if product.cpu is None:
            issues.append(ValidationIssue(field="cpu",
                          message="no CPU in catalog description (normal for Dell listing)"))
        return issues
