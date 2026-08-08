"""Dell engine: JSON-LD primary path, text-anchor fallback, silicon
extraction, and the model-code identity that keeps big-brand config churn
from looking like new products."""

import json
from pathlib import Path

import pytest

from oem_radar.core.config import SourceConfig
from oem_radar.core.knownhw import canonicalize
from oem_radar.core.models import FetchedDocument
from oem_radar.engines.dell import DellEngine

LISTING = (Path(__file__).parent / "fixtures" / "dell" / "dell_laptops_listing.html"
           ).read_text(encoding="utf-8")
BASE = "https://www.dell.com"
PATH = "/en-us/shop/dell-laptops/sr/laptops"


class RouteFetcher:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        return FetchedDocument(url=url, status=200, body=self.routes[url])


def make_engine(paths=None):
    src = SourceConfig(id="dell-us-laptops", engine="dell", base_url=BASE,
                       category_paths=paths or [PATH], region="us")
    return DellEngine(src, "Dell")


def routes():
    return {BASE + PATH: LISTING}


def products():
    engine = make_engine()
    out = {}
    for ref in engine.discover(RouteFetcher(routes())):
        doc = FetchedDocument(url=ref.url, status=200, body=json.dumps(ref.inline_payload))
        out[ref.handle] = engine.normalize(engine.parse(doc))
    return out


def test_discovery_reads_jsonld_itemlist():
    refs = list(make_engine().discover(RouteFetcher(routes())))
    codes = {r.handle for r in refs}
    assert codes == {"DC15250", "DX13260", "AC16251", "MC16250"}
    assert all(r.inline_payload is not None for r in refs)  # one fetch, many products
    # every ref carries a real product-page URL for the "View listing" link
    assert all(r.url.startswith("https://www.dell.com/") for r in refs)


def test_normalize_xps_and_alienware():
    p = products()
    xps = p["DX13260"]
    assert xps.manufacturer == "Dell" and xps.series == "XPS"
    assert xps.vendor_sku == "DX13260"
    assert xps.model.startswith("New XPS 13")
    assert xps.display == '13.4"'
    assert xps.prices[0].amount == 699.99 and xps.prices[0].region == "US"
    # Dell catalog names silicon only as a FAMILY ("Core Ultra") — the exact
    # chip is on the config page. So the family is captured but canonicalize
    # honestly returns None (not a specific known chip). This is correct.
    assert "core ultra" in xps.cpu.raw.lower()
    assert canonicalize(xps.cpu.raw) is None

    aw = p["AC16251"]
    assert aw.series == "Alienware"
    assert "rtx" in aw.gpu.raw.lower()  # "RTX 50 Series" captured as a signal


def test_model_code_is_identity():
    """Config churn must not read as new products: identity is the model code,
    carried in vendor_sku, stable across price/spec config changes."""
    p = products()
    assert p["MC16250"].series == "Dell Pro Max"
    assert all(prod.vendor_sku == handle for handle, prod in p.items())


def test_text_fallback_when_no_jsonld():
    """If Dell drops the JSON-LD, the text-anchor path keeps the engine alive."""
    html_no_jsonld = """<html><body>
      <article>Dell 15 Laptop Model DC15250 Display 15.6" Starting at $679.99</article>
      <article>New XPS 13 Laptop Model DX13260 Display 13.4" Starting at $699.99</article>
    </body></html>"""
    engine = make_engine()
    refs = list(engine.discover(RouteFetcher({BASE + PATH: html_no_jsonld})))
    codes = {r.handle for r in refs}
    assert codes == {"DC15250", "DX13260"}
    doc = FetchedDocument(url=BASE, status=200,
                          body=json.dumps(next(r.inline_payload for r in refs
                                               if r.handle == "DX13260")))
    prod = engine.normalize(engine.parse(doc))
    assert prod.prices[0].amount == 699.99 and prod.display == '13.4"'


def test_validate_flags_missing_model_code():
    engine = make_engine()
    doc = FetchedDocument(url=BASE, status=200,
                          body=json.dumps({"name": "Mystery", "offers": {}}))
    prod = engine.normalize(engine.parse(doc))
    issues = engine.validate(prod)
    assert any(i.field == "vendor_sku" and i.fatal for i in issues)


SPEC_PAGE = (Path(__file__).parent / "fixtures" / "dell" / "dell_spec_page.html"
             ).read_text(encoding="utf-8")


def test_deep_crawl_enriches_exact_silicon():
    """Deep-crawl fetches the spec page and pulls EXACT cpu/gpu/ram/storage
    that the vague catalog lacks — the 'unannounced silicon' signal."""
    listing_url = BASE + PATH
    aw_spec_url = ("https://www.dell.com/en-us/shop/gaming-laptops/"
                   "alienware-16x-aurora/spd/alienware-ac16251")
    src = SourceConfig(id="dell-us-laptops", engine="dell", base_url=BASE,
                       category_paths=[PATH], region="us", deep_crawl=True)
    engine = DellEngine(src, "Dell")
    fetcher = RouteFetcher({listing_url: LISTING, aw_spec_url: SPEC_PAGE})

    refs = {r.handle: r for r in engine.discover(fetcher)}
    aw = refs["AC16251"]
    assert aw.inline_payload["_deep"]["cpu"]  # spec page fetched + parsed
    doc = FetchedDocument(url=aw.url, status=200, body=json.dumps(aw.inline_payload))
    prod = engine.normalize(engine.parse(doc))

    assert canonicalize(prod.cpu.raw) == "core-ultra-9-275hx"   # exact, not "RTX 50 Series"
    assert canonicalize(prod.gpu.raw) == "rtx-5080"
    assert prod.memory == "64 GB DDR5"       # top config from the list
    assert prod.storage == "4 TB"
    assert "OLED" in prod.display
    assert prod.raw_data["memory_options"] == ["16 GB DDR5", "32 GB DDR5", "64 GB DDR5"]
    assert len(prod.raw_data["storage_options"]) == 3


def test_deep_crawl_off_by_default():
    """Without deep_crawl, no spec-page fetches happen (one request per page)."""
    engine = make_engine()
    fetcher = RouteFetcher(routes())
    list(engine.discover(fetcher))
    assert fetcher.calls == [BASE + PATH]  # catalog only, no per-model fetches


def test_engine_registered():
    from oem_radar.core.registry import engines
    assert "dell" in engines
