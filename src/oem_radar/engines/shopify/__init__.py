"""Shopify platform engine (M2). Covers most boutique OEMs (ADR-2).

Discovery: /products.json bulk pages (data comes inline — one request per
250 products) plus product sitemaps; sitemap-only URLs are flagged hidden.
"""

from __future__ import annotations

import html as html_mod
import json
import re
import xml.etree.ElementTree as ET
from typing import Iterable

from pydantic import BaseModel, Field

from ...core.config import SourceConfig
from ...core.interfaces import Fetcher
from ...core.models import (
    Availability,
    Component,
    Configuration,
    NormalizedProduct,
    Price,
    ProductRef,
    RawProduct,
    ValidationIssue,
)


def _canon_image(url: str) -> str:
    """Strip volatile query params (?v=<timestamp>): Shopify bumps them on
    republish for ALL images at once, which would fake images_changed events
    (DESIGN_REVIEW §5)."""
    return url.split("?", 1)[0]
from ...core.registry import engines

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_CPU_PATTERNS = [
    re.compile(r"(?:AMD\s*)?Ryzen[™®\s]*(?:AI\s*)?(?:MAX\+?\s*)?(?:\d\s*)?(?:PRO\s*)?"
               r"(?:HX\s*)?[A-Z]{0,2}\s*\d{3,4}[A-Z]*", re.IGNORECASE),
    re.compile(r"Intel[®\s]*Celeron[®\s]*[A-Z]\d{4}", re.IGNORECASE),
    re.compile(r"(?:Intel[®\s]*)?Core[™®\s]*Ultra\s*\d\s*\d{3}[A-Z]*", re.IGNORECASE),
    re.compile(r"(?:Intel[®\s]*)?Core[™®\s]*i\d[- ]\d{4,5}[A-Z]*", re.IGNORECASE),
    re.compile(r"Intel[®\s]*(?:Processor\s*)?\bN\d{2,3}\b", re.IGNORECASE),
    # Wildcat Lake era naming: "Intel Wildcat Lake Core 3 304", "Core 3 304"
    re.compile(r"(?:Intel[®\s]*)?(?:Wildcat\s*Lake\s*)?Core\s*[3579]\s+\d{3}[A-Z]*\b",
               re.IGNORECASE),
    re.compile(r"Wildcat\s*Lake\s*\d{3}[A-Z]*\b", re.IGNORECASE),
    re.compile(r"Snapdragon[™®\s]*[A-Z0-9 ]*\d{2,4}", re.IGNORECASE),
    # Bare-family fallbacks (only fire when nothing more specific matched):
    # some stores list "Intel Core Ultra 7" / "AMD Ryzen 5" with no model number.
    re.compile(r"(?:Intel[®\s]*)?Core[™®\s]*Ultra\s*[3579]", re.IGNORECASE),
    re.compile(r"(?:AMD\s*)?Ryzen[™®\s]*(?:AI\s*)?[3579]\b", re.IGNORECASE),
]
_GPU_PATTERNS = [
    re.compile(r"Radeon[™®\s]*\d{3,4}[SM]?", re.IGNORECASE),
    re.compile(r"(?:RTX|GTX)\s*\d{4}\s*(?:Ti|SUPER)?", re.IGNORECASE),
    re.compile(r"Arc[™®\s]*[A-Z]?\d{3}[A-Z]?", re.IGNORECASE),
]
_SIZE_RE = re.compile(r"(\d+)\s*(TB|GB)", re.IGNORECASE)
_MEM_BODY_RE = re.compile(r"(\d+)\s*GB\s*(?:RAM|LPDDR\w*|DDR\w*)", re.IGNORECASE)
_STORAGE_BODY_RE = re.compile(r"(\d+)\s*(TB|GB)\s*(?:M\.2\s*)?[\w .]{0,12}?(?:SSD|eMMC|UFS)", re.IGNORECASE)
_REGIONS = {"US", "EU", "UK", "AU", "CA", "JP", "DE", "FR", "CN", "HK"}
# Size tokens with a nearby keyword, e.g. "16GB DDR5", "1TB SSD", "512GB Storage".
_SIZE_TAGGED_RE = re.compile(
    r"(\d+)\s*(TB|GB)\s*"
    r"(RAM|DDR\w*|LPDDR\w*|MEMORY|SSD|HDD|NVME|EMMC|UFS|STORAGE|ROM|M\.?2)?",
    re.IGNORECASE,
)
_MEM_WORDS = {"ram", "ddr", "lpddr", "memory"}
_STORAGE_WORDS = {"ssd", "hdd", "nvme", "emmc", "ufs", "storage", "rom", "m.2", "m2"}


def _split_mem_storage(option: str) -> tuple[str | None, str | None]:
    """Assign size tokens to memory vs storage by UNIT and nearby KEYWORD, not
    by position. Fixes the "1 TB memory" bug on orderings like
    "1TB SSD + 32GB RAM" or "128GB Storage + 8GB". Rules, in order:
      1. A token tagged with a RAM/DDR word is memory; SSD/HDD/etc is storage.
      2. Untagged TB tokens are storage (no laptop ships TB of RAM).
      3. Untagged GB tokens: the SMALLER is memory, the LARGER is storage
         (RAM ≤ storage holds across the boutique mini-PC space)."""
    memory = storage = None
    untagged_gb: list[int] = []
    for num, unit, tag in _SIZE_TAGGED_RE.findall(option):
        val = f"{num} {unit.upper()}"
        key = (tag or "").lower().replace(".", "")
        if key in _MEM_WORDS or key.startswith("ddr") or key.startswith("lpddr"):
            memory = memory or val
        elif key in _STORAGE_WORDS or key == "m2":
            storage = storage or val
        elif unit.upper() == "TB":
            storage = storage or val  # TB is always storage
        else:
            untagged_gb.append(int(num))
    if untagged_gb:
        untagged_gb.sort()
        if memory is None:
            memory = f"{untagged_gb[0]} GB"
        if storage is None and len(untagged_gb) >= 2:
            storage = f"{untagged_gb[-1]} GB"
    return memory, storage


def _strip_html(s: str) -> str:
    return _WS_RE.sub(" ", html_mod.unescape(_TAG_RE.sub(" ", s))).strip()


def _first_match(patterns: list[re.Pattern], *texts: str) -> str | None:
    for text in texts:
        for pat in patterns:
            m = pat.search(text)
            if m:
                return _WS_RE.sub(" ", m.group(0).replace("®", "").replace("™", "")).strip()
    return None


# Titles/handles/types that aren't monitorable PC products. A boutique store's
# /products.json is full of these; they must not become ★★★★★ "new product"
# signals ("【Contact US】Accessories" was doing exactly that).
_DEFAULT_NON_PRODUCT = [
    "contact", "accessor", "gift card", "cable", "adapter", "warranty",
    "coupon", "sample", "sticker", "bundle only", "docking station",
    "power supply", "replacement", "spare part", "mount", "bracket",
    "sunglasses", "t-shirt", "keychain", "lanyard",
]


class ShopifySourceConfig(BaseModel):
    model_config = {"extra": "ignore"}
    currency_default: str = "USD"
    category_map: dict[str, str] = Field(default_factory=dict)
    max_pages: int = 20  # 20 × 250 products; safety valve, not a limit you'll hit
    # Extra denylist substrings appended to the built-in one (config, not code).
    non_product_terms: list[str] = Field(default_factory=list)


@engines.register("shopify")
class ShopifyEngine:
    config_schema = ShopifySourceConfig

    def __init__(self, source: SourceConfig, manufacturer: str) -> None:
        self.source = source
        self.manufacturer = manufacturer
        self.cfg = ShopifySourceConfig.model_validate(source.model_dump())
        self.base = source.base_url.rstrip("/")

    # -- discovery -----------------------------------------------------------

    def discover(self, fetcher: Fetcher) -> Iterable[ProductRef]:
        strategies = self.source.discovery or ["products_json"]
        seen: dict[str, ProductRef] = {}
        if "products_json" in strategies:
            for ref in self._discover_products_json(fetcher):
                seen[ref.handle or ref.url] = ref
        if "sitemap" in strategies:
            for ref in self._discover_sitemap(fetcher):
                key = ref.handle or ref.url
                if key not in seen:
                    ref.hidden = True  # in sitemap but not the public catalog
                    seen[key] = ref
        return list(seen.values())

    def _discover_products_json(self, fetcher: Fetcher) -> Iterable[ProductRef]:
        for page in range(1, self.cfg.max_pages + 1):
            doc = fetcher.get(f"{self.base}/products.json?limit=250&page={page}")
            products = json.loads(doc.body).get("products", [])
            if not products:
                break
            for p in products:
                yield ProductRef(
                    url=f"{self.base}/products/{p['handle']}",
                    handle=p["handle"],
                    inline_payload=p,
                )

    def _discover_sitemap(self, fetcher: Fetcher) -> Iterable[ProductRef]:
        try:
            index = fetcher.get(f"{self.base}/sitemap.xml")
        except Exception:
            return
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        try:
            root = ET.fromstring(index.body)
        except ET.ParseError:
            return
        product_sitemaps = [
            loc.text for loc in root.findall(".//sm:loc", ns)
            if loc.text and "_products_" in loc.text
        ]
        for sm_url in product_sitemaps:
            try:
                sm = fetcher.get(sm_url)
                sm_root = ET.fromstring(sm.body)
            except Exception:
                continue
            for loc in sm_root.findall(".//sm:loc", ns):
                if loc.text and "/products/" in loc.text:
                    handle = loc.text.rstrip("/").rsplit("/", 1)[-1]
                    yield ProductRef(url=f"{loc.text.rstrip('/')}.json", handle=handle)

    # -- parse / normalize / validate ---------------------------------------

    def parse(self, doc) -> RawProduct:
        data = json.loads(doc.body)
        if "product" in data:  # /products/<handle>.json wraps it
            data = data["product"]
        return RawProduct(source_id=self.source.id, url=doc.url, payload=data)

    def normalize(self, raw: RawProduct) -> NormalizedProduct:
        p = raw.payload
        title = _WS_RE.sub(" ", p.get("title", "")).strip()
        body = _strip_html(p.get("body_html") or "")
        confidence = 1.0

        model = title
        for prefix in (self.manufacturer, p.get("vendor") or ""):
            if prefix and model.lower().startswith(prefix.lower()):
                model = model[len(prefix):].strip()

        # Some OEMs (e.g. Minisforum) put the CPU in variant options, not the title.
        variants_text = " ".join(
            str(v.get(k) or "") for v in p.get("variants", [])
            for k in ("option1", "option2", "option3", "title")
        )
        cpu_raw = _first_match(_CPU_PATTERNS, title, body, variants_text)
        gpu_raw = _first_match(_GPU_PATTERNS, title, body, variants_text)
        if cpu_raw is None:
            confidence -= 0.3

        variants = p.get("variants", [])
        prices, configurations, regions_seen = [], [], set()
        memory = storage = None
        for v in variants:
            opts = " ".join(str(v.get(k) or "") for k in ("option1", "option2", "option3"))
            region = next((r for r in _REGIONS if r in opts.split()), None)
            if region:
                regions_seen.add(region)
            try:
                amount = float(v.get("price") or 0)
            except (TypeError, ValueError):
                continue
            availability = (Availability.IN_STOCK if v.get("available")
                            else Availability.SOLD_OUT)
            prices.append(Price(
                amount=amount, currency=self.cfg.currency_default, region=region,
                availability=availability,
            ))
            # Unit- and keyword-aware split (not positional): handles both
            # "32GB RAM + 1TB SSD" and the reversed "1TB SSD + 32GB RAM".
            v_memory, v_storage = _split_mem_storage(v.get("option1") or opts)
            configurations.append(Configuration(
                label=(v.get("title") or v.get("option1") or None),
                sku=(v.get("sku") or None),
                memory=v_memory, storage=v_storage,
                price=amount, currency=self.cfg.currency_default,
                region=region, availability=availability,
            ))
            if memory is None:
                memory = v_memory
            if storage is None:
                storage = v_storage
        if memory is None and (m := _MEM_BODY_RE.search(body)):
            memory = f"{m.group(1)} GB"
        if storage is None and (s := _STORAGE_BODY_RE.search(body)):
            storage = f"{s.group(1)} {s.group(2).upper()}"
        if not prices:
            confidence -= 0.3

        category = None
        cat_keys = [t.lower() for t in p.get("tags", [])] + [(p.get("product_type") or "").lower()]
        for key, mapped in self.cfg.category_map.items():
            if key.lower() in cat_keys:
                category = mapped
                break

        available_any = any(v.get("available") for v in variants)
        is_non_product = self._is_non_product(title, p.get("handle") or "",
                                               p.get("product_type") or "")
        if is_non_product:
            confidence = 0.0  # validate() will flag fatal; never notified
        return NormalizedProduct(
            manufacturer=self.manufacturer,
            model=model or title,
            category=category,
            cpu=Component(raw=cpu_raw) if cpu_raw else None,
            gpu=Component(raw=gpu_raw) if gpu_raw else None,
            memory=memory,
            storage=storage,
            configurations=configurations,
            prices=prices,
            vendor_sku=next((c.sku for c in configurations if c.sku), None),
            images=[_canon_image(img["src"])
                    for img in p.get("images", []) if img.get("src")][:10],
            description=body[:2000] or None,
            source_url=f"{self.base}/products/{p.get('handle', '')}",
            confidence=(0.0 if is_non_product else max(confidence, 0.3)),
            aliases=[t for t in [p.get("handle")] if t],
            raw_data={"shopify_id": p.get("id"), "vendor": p.get("vendor"),
                      "skus": [v.get("sku") for v in variants if v.get("sku")],
                      "regions": sorted(regions_seen),
                      "published_at": p.get("published_at"),
                      "available": available_any,
                      "non_product": is_non_product},
        )

    def _is_non_product(self, title: str, handle: str, product_type: str) -> bool:
        hay = f"{title} {handle} {product_type}".lower()
        terms = _DEFAULT_NON_PRODUCT + [t.lower() for t in self.cfg.non_product_terms]
        return any(term in hay for term in terms)

    def validate(self, product: NormalizedProduct) -> list[ValidationIssue]:
        issues = []
        if product.raw_data.get("non_product"):
            issues.append(ValidationIssue(
                field="model", message="not a monitorable PC product (denylist match)",
                fatal=True))
        if not product.model:
            issues.append(ValidationIssue(field="model", message="empty model", fatal=True))
        if product.cpu is None:
            issues.append(ValidationIssue(field="cpu", message="no CPU recognized in title/body"))
        if not product.prices:
            issues.append(ValidationIssue(field="prices", message="no parsable variants"))
        return issues
