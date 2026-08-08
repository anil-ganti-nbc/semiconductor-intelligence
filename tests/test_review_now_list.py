"""Tests for the DESIGN_REVIEW 'now' list: variant model, compression,
image canonicalization, SKU/region wiring, magnitude/direction rules,
and — critically — a clean v1→v2 migration with no phantom events."""

import json
import sqlite3
import zlib
from pathlib import Path

import pytest

from oem_radar.core.config import SeverityRule, SourceConfig
from oem_radar.core.diff import DEFAULT_RULES, diff, score
from oem_radar.core.models import (
    ChangeEvent,
    ChangeType,
    Configuration,
    NormalizedProduct,
    Price,
    Severity,
)
from oem_radar.engines.shopify import ShopifyEngine
from oem_radar.providers.sqlite import SqliteStore
from engine_harness import EngineHarness

from test_models import make_product

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "shopify" / "gmktec_products.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
BASE = "https://www.gmktec.com"
GOLDENS = Path(__file__).parent / "goldens" / "shopify"


def make_harness():
    src = SourceConfig(id="gmktec-shopify", engine="shopify", base_url=BASE,
                       discovery=["products_json"], currency_default="USD")
    engine = ShopifyEngine(src, "GMKtec")
    routes = {
        f"{BASE}/products.json?limit=250&page=1": json.dumps(FIXTURE),
        f"{BASE}/products.json?limit=250&page=2": json.dumps({"products": []}),
    }
    return EngineHarness(engine, routes, GOLDENS)


# ---- variant-level model -------------------------------------------------

def test_configurations_populated_with_skus_and_regions():
    products = make_harness().normalize_all()
    g5s = products["gmktec-g5s-mini-pc-intel-celeron-n5095"]
    assert len(g5s.configurations) == 3
    assert {c.region for c in g5s.configurations} == {"US", "EU", "UK"}
    assert g5s.configurations[0].sku == "G5S-2-21S"
    assert g5s.configurations[0].memory == "8 GB"
    assert g5s.vendor_sku == "G5S-2-21S"

    k12 = products["gmktec-k12-mini-pc-amd-ryzen™-7-h-255-1"]
    assert k12.configurations[0].storage == "1 TB"
    assert k12.configurations[0].price == 879.99


def test_config_key_prefers_sku():
    assert Configuration(sku="X-1", label="whatever").key() == "X-1"
    assert Configuration(label="32GB", region="US").key() == "32GB|US"


def test_image_urls_canonicalized():
    products = make_harness().normalize_all()
    for p in products.values():
        assert all("?" not in url for url in p.images)


def test_engine_goldens():
    h = make_harness()
    h.assert_goldens(h.normalize_all())


def test_engine_config_schema_rejects_garbage():
    make_harness().assert_config_rejected({"max_pages": "lots"})


# ---- diff: migration boundary, configs, magnitude, direction -------------

def _old_style(product: NormalizedProduct) -> NormalizedProduct:
    """Simulate a v1 snapshot: no configurations, images with ?v= params."""
    data = product.model_dump()
    data["configurations"] = []
    data["vendor_sku"] = None
    data["images"] = [u + "?v=12345" for u in data["images"]]
    return NormalizedProduct(**data)


def test_migration_boundary_no_phantom_events():
    new = make_harness().normalize_all()["gmktec-k12-mini-pc-amd-ryzen™-7-h-255-1"]
    old = _old_style(new)
    assert old.content_hash() != new.content_hash()  # snapshot will be written...
    assert diff(old, new, "k") == []                 # ...but nothing pings


def test_config_added_is_an_event():
    before = make_product(configurations=[Configuration(sku="A")])
    after = make_product(configurations=[Configuration(sku="A"), Configuration(sku="B")])
    events = diff(before, after, "k")
    assert len(events) == 1
    e = events[0]
    assert e.field == "configurations" and e.meta["added"] == ["B"]
    assert e.severity == Severity.NOTABLE  # DEFAULT_RULES


def test_price_magnitude_and_operator_rules():
    before = make_product(prices=[Price(amount=999.0, currency="USD")])
    after = make_product(prices=[Price(amount=879.0, currency="USD")])
    (e,) = diff(before, after, "k")
    assert e.change_type == ChangeType.PRICE_CHANGED
    assert e.meta["magnitude_pct"] == 12.0 and e.meta["direction"] == "down"

    big_drop_rule = [SeverityRule(match={"change_type": "price_changed",
                                         "magnitude_pct": ">10"}, severity=4),
                     SeverityRule(match={}, severity=1)]
    assert score(e, big_drop_rule) == Severity.SIGNIFICANT
    small = ChangeEvent(product_key="k", change_type=ChangeType.PRICE_CHANGED,
                        field="prices", meta={"magnitude_pct": 3.0})
    assert score(small, big_drop_rule) == Severity.NOISE


def test_spec_direction_meta_and_rule():
    before = make_product(memory="96 GB")
    after = make_product(memory="128 GB")
    (e,) = diff(before, after, "k")
    assert e.meta["direction"] == "up"
    up_only = [SeverityRule(match={"change_type": "spec_changed", "field": "memory",
                                   "direction": "up"}, severity=4),
               SeverityRule(match={}, severity=2)]
    assert score(e, up_only) == Severity.SIGNIFICANT
    (down,) = diff(after, before, "k")
    assert score(down, up_only) == Severity.MINOR

    # TB > GB comparison works: 1 TB > 512 GB
    (e2,) = diff(make_product(storage="512 GB"), make_product(storage="1 TB"), "k")
    assert e2.meta["direction"] == "up"


# ---- storage: compression + v1→v2 migration ------------------------------

def test_snapshots_stored_compressed(tmp_path):
    store = SqliteStore(str(tmp_path / "r.db"), str(tmp_path / "raw"))
    p = make_product()
    store.append("s:k", p)
    row = store.db.execute("SELECT * FROM snapshots").fetchone()
    assert row["normalized_json"] == "" and row["normalized_zjson"] is not None
    assert len(row["normalized_zjson"]) < len(p.model_dump_json())
    assert store.latest("s:k").content_hash() == p.content_hash()
    store.close()


V1_DDL = """
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT '');
INSERT INTO schema_migrations(version) VALUES (1);
CREATE TABLE manufacturers (id INTEGER PRIMARY KEY, name TEXT UNIQUE, country TEXT,
  aliases_json TEXT DEFAULT '[]', created_at TEXT DEFAULT '');
CREATE TABLE sources (id INTEGER PRIMARY KEY, source_key TEXT UNIQUE, manufacturer_id INTEGER,
  engine TEXT, base_url TEXT, config_json TEXT DEFAULT '{}', enabled INTEGER DEFAULT 1,
  created_at TEXT DEFAULT '');
CREATE TABLE products (id INTEGER PRIMARY KEY, manufacturer_id INTEGER, canonical_model TEXT,
  series TEXT, category TEXT, status TEXT DEFAULT 'active', first_seen_at TEXT DEFAULT '',
  UNIQUE(manufacturer_id, canonical_model));
CREATE TABLE listings (id INTEGER PRIMARY KEY, source_id INTEGER, product_id INTEGER,
  product_key TEXT UNIQUE, url TEXT, vendor_handle TEXT,
  resolution_method TEXT DEFAULT 'url', resolution_confidence REAL DEFAULT 1.0,
  needs_review INTEGER DEFAULT 0, first_seen_at TEXT DEFAULT '', last_seen_at TEXT DEFAULT '');
CREATE TABLE snapshots (id INTEGER PRIMARY KEY, listing_id INTEGER, content_hash TEXT,
  normalized_json TEXT NOT NULL, confidence REAL DEFAULT 1.0,
  validation_issues_json TEXT DEFAULT '[]', raw_ref TEXT, captured_at TEXT DEFAULT '',
  UNIQUE(listing_id, content_hash));
"""


def test_v1_database_migrates_and_old_snapshots_load(tmp_path):
    db_path = tmp_path / "old.db"
    old_product = _old_style(make_product())
    con = sqlite3.connect(db_path)
    con.executescript(V1_DDL)
    con.execute("INSERT INTO manufacturers(name) VALUES ('GMKtec')")
    con.execute("INSERT INTO sources(source_key, manufacturer_id, engine, base_url) "
                "VALUES ('src', 1, 'shopify', 'x')")
    con.execute("INSERT INTO products(manufacturer_id, canonical_model) VALUES (1, 'gmktec::k12')")
    con.execute("INSERT INTO listings(source_id, product_id, product_key, url) "
                "VALUES (1, 1, 'src:k12', 'x')")
    con.execute("INSERT INTO snapshots(listing_id, content_hash, normalized_json) "
                "VALUES (1, ?, ?)", (old_product.content_hash(), old_product.model_dump_json()))
    con.commit()
    con.close()

    store = SqliteStore(str(db_path), str(tmp_path / "raw"))  # migrate() runs here
    versions = [r["version"] for r in
                store.db.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == [1, 2, 3, 4, 5]  # v4 (feedback reviews) added later

    # old uncompressed snapshot still loads through the new read path
    loaded = store.latest("src:k12")
    assert loaded is not None and loaded.configurations == []

    # new-model append works against the migrated schema, and the hash-epoch
    # transition produces no diff events (proven separately above)
    new_product = make_product(configurations=[Configuration(sku="K12-7-44S")],
                               vendor_sku="K12-7-44S")
    store.append("src:k12", new_product)
    row = store.db.execute(
        "SELECT vendor_sku FROM listings WHERE product_key='src:k12'").fetchone()
    assert row["vendor_sku"] == "K12-7-44S"
    assert store.latest("src:k12").vendor_sku == "K12-7-44S"

    # reopening is idempotent
    store.close()
    store2 = SqliteStore(str(db_path), str(tmp_path / "raw"))
    assert [r["version"] for r in
            store2.db.execute("SELECT version FROM schema_migrations")] == [1, 2, 3, 4, 5]
    store2.close()
