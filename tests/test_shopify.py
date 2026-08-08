"""M2 exit criteria: Shopify engine against captured GMKtec fixtures."""

import json
from pathlib import Path

import pytest

from oem_radar.core.config import SourceConfig
from oem_radar.core.knownhw import canonicalize
from oem_radar.core.models import FetchedDocument
from oem_radar.engines.shopify import ShopifyEngine

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "shopify" / "gmktec_products.json")
    .read_text(encoding="utf-8")
)
BASE = "https://www.gmktec.com"

SITEMAP_INDEX = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>{BASE}/sitemap_products_1.xml</loc></sitemap>
</sitemapindex>"""
SITEMAP_PRODUCTS = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{BASE}/products/gmktec-k12-mini-pc-amd-ryzen™-7-h-255-1</loc></url>
  <url><loc>{BASE}/products/gmktec-k99-unannounced</loc></url>
</urlset>"""


class RouteFetcher:
    def __init__(self, routes: dict[str, str]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str) -> FetchedDocument:
        self.calls.append(url)
        if url not in self.routes:
            raise KeyError(url)
        return FetchedDocument(url=url, status=200, body=self.routes[url])


def make_engine(discovery=None) -> ShopifyEngine:
    src = SourceConfig(id="gmktec-shopify", engine="shopify", base_url=BASE,
                       discovery=discovery or ["products_json"],
                       currency_default="USD", category_map={"intel": "mini_pc"})
    return ShopifyEngine(src, "GMKtec")


def routes(with_sitemap=False):
    r = {
        f"{BASE}/products.json?limit=250&page=1": json.dumps(FIXTURE),
        f"{BASE}/products.json?limit=250&page=2": json.dumps({"products": []}),
    }
    if with_sitemap:
        r[f"{BASE}/sitemap.xml"] = SITEMAP_INDEX
        r[f"{BASE}/sitemap_products_1.xml"] = SITEMAP_PRODUCTS
    return r


def test_discovery_inlines_bulk_data():
    refs = list(make_engine().discover(RouteFetcher(routes())))
    assert len(refs) == 3
    assert all(r.inline_payload is not None for r in refs)  # zero per-product fetches


def test_sitemap_only_products_are_hidden():
    engine = make_engine(["products_json", "sitemap"])
    refs = {r.handle: r for r in engine.discover(RouteFetcher(routes(with_sitemap=True)))}
    assert len(refs) == 4
    assert refs["gmktec-k99-unannounced"].hidden is True
    assert refs["gmktec-k99-unannounced"].inline_payload is None  # must be fetched
    # catalog products keep priority: not marked hidden
    assert refs["gmktec-g5s-mini-pc-intel-celeron-n5095"].hidden is False


@pytest.fixture()
def products():
    engine = make_engine()
    out = {}
    for ref in engine.discover(RouteFetcher(routes())):
        doc = FetchedDocument(url=ref.url, status=200, body=json.dumps(ref.inline_payload))
        out[ref.handle] = engine.normalize(engine.parse(doc))
    return out


def test_normalize_g5s(products):
    p = products["gmktec-g5s-mini-pc-intel-celeron-n5095"]
    assert p.manufacturer == "GMKtec"
    assert p.model.startswith("G5S")
    assert canonicalize(p.cpu.raw) == "celeron-n5095"
    assert p.memory == "8 GB" and p.storage == "128 GB"
    assert {pr.region for pr in p.prices} == {"US", "EU", "UK"}
    assert all(pr.amount == 229.99 for pr in p.prices)
    assert p.category == "mini_pc"  # via category_map on tag 'Intel'
    assert p.confidence == 1.0


def test_normalize_k12_and_evo(products):
    k12 = products["gmktec-k12-mini-pc-amd-ryzen™-7-h-255-1"]
    assert canonicalize(k12.cpu.raw) == "ryzen-7-h-255"
    assert k12.memory == "32 GB" and k12.storage == "1 TB"
    assert k12.prices[0].amount == 879.99

    evo = products["gmktec-evo-x1-ai-mini-pc-amd-ryzen™-ai-9-hx-370-1"]
    assert canonicalize(evo.cpu.raw) == "ryzen-ai-9-hx-370"
    assert evo.model.startswith("EVO-X1")  # 'GMKtec US' vendor quirk handled


def test_snapshot_stability(products):
    """Golden-hash regression: normalize must be deterministic. If this fails
    after an engine change, review the diff and update the goldens knowingly."""
    p = products["gmktec-k12-mini-pc-amd-ryzen™-7-h-255-1"]
    engine = make_engine()
    ref = next(r for r in engine.discover(RouteFetcher(routes()))
               if r.handle == "gmktec-k12-mini-pc-amd-ryzen™-7-h-255-1")
    doc = FetchedDocument(url=ref.url, status=200, body=json.dumps(ref.inline_payload))
    again = engine.normalize(engine.parse(doc))
    assert again.content_hash() == p.content_hash()


def test_cpu_patterns_cover_new_naming_schemes():
    """Regression: real titles seen in live probes must extract a CPU."""
    from oem_radar.engines.shopify import _CPU_PATTERNS, _first_match

    cases = {
        "Beelink ME Pro 2-Bay AI NAS Mini PC Intel® Wildcat Lake 304": "Wildcat Lake 304",
        "Intel Wildcat Lake Core 3 304 chip": "Core 3 304",
        "MINISFORUM MS-03 Intel® Core™ Ultra 9 386H": "Ultra 9 386",
        "AOOSTAR GODZ AMD Ryzen 7 7435HS+AMD Radeon RX 6600M": "Ryzen 7 7435HS",
    }
    for title, expected_fragment in cases.items():
        got = _first_match(_CPU_PATTERNS, title)
        assert got and expected_fragment.lower() in got.lower(), (title, got)


def test_validate_flags_missing_cpu():
    engine = make_engine()
    broken = dict(FIXTURE["products"][0], title="GMKtec Mystery Box", body_html="")
    doc = FetchedDocument(url="u", status=200, body=json.dumps(broken))
    product = engine.normalize(engine.parse(doc))
    issues = engine.validate(product)
    assert any(i.field == "cpu" for i in issues)
    assert product.confidence < 1.0  # engine already lowered it
