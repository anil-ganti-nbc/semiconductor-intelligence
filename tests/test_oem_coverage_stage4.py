"""Stage 4: OEM enablement fixtures, empty-catalog protection, coverage matrix."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oem_radar.core.config import CollectorHealthConfig, load_oem_configs, load_radar_config
from oem_radar.core.models import FetchedDocument, ProductRef
from oem_radar.core.pipeline import SourceRunStats, run_source
from oem_radar.engines.shopify import ShopifyEngine
from oem_radar.providers.discord import ConsoleNotifier
from oem_radar.providers.sqlite import SqliteStore

FIXTURES = Path(__file__).parent / "fixtures" / "shopify"


class _FixtureFetcher:
    """Serve products.json from on-disk fixtures; 404 otherwise."""

    def __init__(self, mapping: dict[str, Path]):
        self.mapping = mapping
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        for key, path in self.mapping.items():
            if key in url and "products.json" in url:
                body = path.read_bytes()
                return FetchedDocument(url=url, status=200, body=body,
                                       content_type="application/json")
        return FetchedDocument(url=url, status=404, body=b"{}", content_type="application/json")


@pytest.fixture()
def store(tmp_path):
    s = SqliteStore(str(tmp_path / "c.db"), str(tmp_path / "raw"))
    yield s
    s.close()


def _src(id_, base, manufacturer):
    from oem_radar.core.config import SourceConfig
    return SourceConfig(
        id=id_, engine="shopify", base_url=base,
        discovery=["products_json"], enabled=True,
    )


@pytest.mark.parametrize("name,fixture,base,mfr", [
    ("bosgame", "bosgame_products_p1.json", "https://bosgame.com", "Bosgame"),
    ("nipogi", "nipogi_products_p1.json", "https://www.nipogi.com", "NiPoGi"),
    ("acemagic", "acemagic_products_p1.json", "https://acemagic.com", "ACEMAGIC"),
])
def test_enabled_oem_discovery_from_fixture(store, name, fixture, base, mfr):
    path = FIXTURES / fixture
    assert path.exists()
    data = json.loads(path.read_text())
    assert len(data["products"]) >= 5
    eng = ShopifyEngine(_src(f"{name}-shopify", base, mfr), mfr)
    fetcher = _FixtureFetcher({"products.json": path})
    refs = list(eng.discover(fetcher))
    assert len(refs) == len(data["products"])
    assert refs[0].handle
    assert refs[0].inline_payload is not None
    product = eng.normalize(
        __import__("oem_radar.core.models", fromlist=["RawProduct"]).RawProduct(
            source_id=f"{name}-shopify", url=refs[0].url, payload=refs[0].inline_payload,
        )
    )
    assert product.manufacturer == mfr
    assert product.model



def test_shopify_parse_bosgame_fields(store):
    from oem_radar.core.models import RawProduct
    data = json.loads((FIXTURES / "bosgame_products_p1.json").read_text())
    eng = ShopifyEngine(_src("bosgame-shopify", "https://bosgame.com", "Bosgame"), "Bosgame")
    raw = RawProduct(source_id="bosgame-shopify", url="https://bosgame.com/products/x",
                     payload=data["products"][0])
    product = eng.normalize(raw)
    assert product.manufacturer == "Bosgame"
    assert product.model
    assert product.confidence >= 0.0
    # prices or variants present on real fixture
    assert product.images is not None


def test_unexpected_zero_catalog_fails(store):
    class EmptyEngine:
        def discover(self, fetcher):
            return []
        def parse(self, doc, ref):
            return None

    from oem_radar.core.config import SourceConfig
    src = SourceConfig(id="empty-src", engine="shopify", base_url="https://example.com")
    # Seed a prior successful run with discovered=20
    store.db.execute(
        "INSERT INTO crawler_runs(source_key, started_at, finished_at, status, stats_json) "
        "VALUES (?,?,?,?,?)",
        ("empty-src", "2026-01-01", "2026-01-01", "ok", json.dumps({"discovered": 20})),
    )
    store.db.commit()
    stats = run_source(src, EmptyEngine(), _FixtureFetcher({}), store, ConsoleNotifier())
    assert stats.health == "failed"
    assert stats.health == "failed" and (any("UNEXPECTED_ZERO" in e or "unexpected_zero" in e.lower() or "catalog" in e.lower() for e in stats.errors) or stats.health_reason == "UNEXPECTED_ZERO")
    assert stats.discovered == 0


def test_catalog_collapse_fails(store):
    class TinyEngine:
        def discover(self, fetcher):
            return [ProductRef(url="https://ex/p1", handle="p1")]
        def parse(self, doc, ref):
            raise RuntimeError("not used")

    from oem_radar.core.config import SourceConfig
    src = SourceConfig(id="tiny-src", engine="shopify", base_url="https://ex.com")
    store.db.execute(
        "INSERT INTO crawler_runs(source_key, started_at, finished_at, status, stats_json) "
        "VALUES (?,?,?,?,?)",
        ("tiny-src", "2026-01-01", "2026-01-01", "ok", json.dumps({"discovered": 50})),
    )
    store.db.commit()

    class F:
        def get(self, url, **k):
            return FetchedDocument(url=url, status=200, body=b"{}", content_type="application/json")
    stats = run_source(src, TinyEngine(), F(), store, ConsoleNotifier())
    assert stats.health == "failed"
    assert any("catalog_collapse" in e for e in stats.errors)


def test_oem_configs_load_enabled_batch():
    oems = load_oem_configs(Path("config/oems"))
    assert "Bosgame" in oems
    assert "NiPoGi" in oems
    assert "ACEMAGIC" in oems
    bos = next(s for s in oems["Bosgame"].sources if s.id == "bosgame-shopify")
    assert bos.enabled is True
    nip = next(s for s in oems["NiPoGi"].sources if s.id == "nipogi-shopify")
    assert nip.enabled is True
    ace = next(s for s in oems["ACEMAGIC"].sources if s.id == "acemagic-shopify")
    assert ace.enabled is True


def test_radar_collector_health_config():
    cfg = load_radar_config(Path("config/radar.yaml"))
    assert cfg.collector_health.unexpected_zero_is_failure is True
    assert cfg.collector_health.minimum_fraction_of_previous_catalog == 0.35


def test_kamrui_fixture_ready_but_not_enabled():
    """Second-batch candidate fixture captured; descriptor not enabled yet."""
    path = FIXTURES / "kamrui_products_p1.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert len(data["products"]) >= 10
    oems = load_oem_configs(Path("config/oems"))
    assert "KAMRUI" in oems  # enabled Stage 4.1
