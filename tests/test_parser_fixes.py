"""Regression tests for the three live-data bugs the dashboard surfaced
(2026-07-19). Each reproduces the real misparse before asserting the fix.
Fixtures mirror the actual Beelink /products.json shapes that caused them."""

import json

import pytest

from oem_radar.core.config import SourceConfig
from oem_radar.core.models import FetchedDocument
from oem_radar.engines.shopify import ShopifyEngine, _split_mem_storage
from oem_radar.providers.sqlite import SqliteStore, _same_product, model_key

BASE = "https://www.bee-link.com"


def make_engine(**cfg):
    src = SourceConfig(id="beelink-shopify", engine="shopify", base_url=BASE,
                       discovery=["products_json"], **cfg)
    return ShopifyEngine(src, "Beelink")


def normalize_one(payload):
    engine = make_engine()
    doc = FetchedDocument(url="u", status=200, body=json.dumps(payload))
    return engine.normalize(engine.parse(doc))


# ---- Fix 1: memory/storage swap -----------------------------------------

def test_split_mem_storage_reversed_order():
    # The bug: "1TB SSD + 32GB RAM" put 1 TB into memory.
    assert _split_mem_storage("1TB SSD + 32GB RAM") == ("32 GB", "1 TB")
    assert _split_mem_storage("32GB RAM + 1TB SSD") == ("32 GB", "1 TB")
    assert _split_mem_storage("16GB DDR5 5600Mhz+ 512GB Storage") == ("16 GB", "512 GB")
    # untagged "8GB+128GB": smaller is memory, larger is storage
    assert _split_mem_storage("8GB+128GB") == ("8 GB", "128 GB")
    # TB is never RAM even untagged
    mem, stor = _split_mem_storage("2TB + 64GB")
    assert stor == "2 TB" and mem == "64 GB"


def test_memory_never_terabytes_in_product():
    payload = {
        "title": "Beelink SER9 PRO AMD Ryzen AI 9 HX 370", "handle": "beelink-ser9-pro",
        "vendor": "Beelink", "product_type": "Mini PC",
        "variants": [{"title": "1TB SSD + 32GB RAM / US", "option1": "1TB SSD + 32GB RAM",
                      "option2": "US", "sku": "SER9PRO-1", "price": "1149.00",
                      "available": True}],
        "images": [], "options": [],
    }
    p = normalize_one(payload)
    assert p.memory == "32 GB"      # not "1 TB" — the bug is fixed
    assert p.storage == "1 TB"


# ---- Fix 2: non-product listings ----------------------------------------

@pytest.mark.parametrize("title,handle", [
    ("【Contact US】Accessories", "contact-us-accessories"),
    ("Beelink Gift Card", "gift-card"),
    ("HDMI Cable 2m", "hdmi-cable"),
    ("Warranty Extension", "warranty-extension"),
])
def test_non_products_flagged_fatal(title, handle):
    payload = {"title": title, "handle": handle, "vendor": "Beelink",
               "variants": [{"option1": "Default", "price": "1.00", "available": True,
                             "sku": "X"}], "images": [], "options": []}
    engine = make_engine()
    doc = FetchedDocument(url="u", status=200, body=json.dumps(payload))
    prod = engine.normalize(engine.parse(doc))
    issues = engine.validate(prod)
    assert any(i.fatal for i in issues)  # fatal → pipeline skips → never notified
    assert prod.confidence == 0.0


def test_real_product_not_flagged():
    payload = {"title": "Beelink SER9 Mini PC", "handle": "beelink-ser9",
               "vendor": "Beelink", "product_type": "Mini PC",
               "variants": [{"option1": "32GB RAM + 1TB SSD", "price": "999",
                             "available": True, "sku": "SER9-1"}],
               "images": [], "options": []}
    engine = make_engine()
    doc = FetchedDocument(url="u", status=200, body=json.dumps(payload))
    prod = engine.normalize(engine.parse(doc))
    assert not any(i.fatal for i in engine.validate(prod))


def test_custom_non_product_terms_from_config():
    engine = make_engine(non_product_terms=["screwdriver"])
    payload = {"title": "Precision Screwdriver Kit", "handle": "screwdriver",
               "vendor": "Beelink", "variants": [{"option1": "D", "price": "9",
                             "available": True, "sku": "S"}], "images": [], "options": []}
    doc = FetchedDocument(url="u", status=200, body=json.dumps(payload))
    prod = engine.normalize(engine.parse(doc))
    assert any(i.fatal for i in engine.validate(prod))


# ---- Fix 3: resolution collision ----------------------------------------

def test_tier_words_distinguish_products():
    assert _same_product("SER9 Mini PC", "SER9 Mini PC") is True     # rename ok
    assert _same_product("SER9 Mini PC", "SER9 PRO Mini PC") is False  # different product
    assert _same_product("EVO X1", "EVO X1 PLUS") is False
    # coarse key still collides, guard is what separates them:
    assert model_key("Beelink", "SER9 Mini PC") == model_key("Beelink", "SER9 PRO Mini PC")


def test_ser9_and_ser9_pro_do_not_collide(tmp_path):
    from test_models import make_product
    store = SqliteStore(str(tmp_path / "r.db"), str(tmp_path / "raw"))
    ser9 = make_product(model="SER9 Mini PC AMD Ryzen 7 H 255", vendor_sku="SER9-1")
    store.append("beelink:ser9", ser9)

    ser9pro = make_product(model="SER9 PRO AMD Ryzen AI 9 HX 370", vendor_sku="SER9PRO-1")
    prior, relation = store.resolve_prior("beelink:ser9-pro", ser9pro)
    # must NOT resolve the PRO to the base model (that caused the phantom
    # cpu h-255 -> hx-370 diff). It's a distinct, new product.
    assert relation == "none" and prior is None
    store.close()


def test_sku_match_resolves_rename(tmp_path):
    from test_models import make_product
    store = SqliteStore(str(tmp_path / "r.db"), str(tmp_path / "raw"))
    v1 = make_product(model="K12 Mini PC", vendor_sku="K12-7-44S")
    store.append("gmktec:k12-old-url", v1)
    # same SKU, new listing URL + renamed title = genuine rename, should link
    v2 = make_product(model="K12 Mini PC (2026 Refresh)", vendor_sku="K12-7-44S")
    prior, relation = store.resolve_prior("gmktec:k12-new-url", v2)
    assert relation == "existing_product" and prior is not None
    store.close()
