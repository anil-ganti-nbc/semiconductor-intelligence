"""Stage 4.1: health config wiring, thresholds, multi-run transitions, KAMRUI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from oem_radar.core.config import CollectorHealthConfig, SourceConfig, load_oem_configs, load_radar_config
from oem_radar.core.models import FetchedDocument, ProductRef
from oem_radar.core.pipeline import run_source
from oem_radar.core.runner import run_all
from oem_radar.engines.shopify import ShopifyEngine
from oem_radar.providers.discord import ConsoleNotifier
from oem_radar.providers.sqlite import SqliteStore

FIXTURES = Path(__file__).parent / "fixtures" / "shopify"


@pytest.fixture()
def store(tmp_path):
    s = SqliteStore(str(tmp_path / "h.db"), str(tmp_path / "raw"))
    yield s
    s.close()


class FixedEngine:
    def __init__(self, n: int):
        self.n = n

    def discover(self, fetcher):
        return [ProductRef(url=f"https://ex/p{i}", handle=f"p{i}",
                           inline_payload={"title": f"PC {i}", "handle": f"p{i}",
                                           "variants": [{"price": "99", "available": True}],
                                           "images": []})
                for i in range(self.n)]

    def parse(self, doc):
        from oem_radar.core.models import RawProduct
        data = json.loads(doc.body) if isinstance(doc.body, (bytes, str)) else doc.body
        if isinstance(data, str):
            data = json.loads(data)
        return RawProduct(source_id="t", url=doc.url, payload=data)

    def normalize(self, raw):
        from oem_radar.core.models import NormalizedProduct, Price, Availability
        p = raw.payload
        return NormalizedProduct(
            manufacturer="TestOEM", model=p.get("title") or "X",
            prices=[Price(amount=99, currency="USD", availability=Availability.IN_STOCK)],
            confidence=0.9, source_url=raw.url,
        )

    def validate(self, product):
        return []


def _src(sid="test-src"):
    return SourceConfig(id=sid, engine="shopify", base_url="https://ex.com", enabled=True)


def _seed_ok(store, source_id, discovered):
    store.db.execute(
        "INSERT INTO crawler_runs(source_key, started_at, finished_at, status, stats_json) "
        "VALUES (?,?,?,?,?)",
        (source_id, "2026-01-01", "2026-01-01", "ok",
         json.dumps({"discovered": discovered})),
    )
    store.db.commit()


def test_threshold_validation_rejects_equal_and_inverted():
    with pytest.raises((ValidationError, ValueError)):
        CollectorHealthConfig(
            minimum_fraction_of_previous_catalog=0.7,
            warn_fraction_of_previous_catalog=0.7,
        )
    with pytest.raises((ValidationError, ValueError)):
        CollectorHealthConfig(
            minimum_fraction_of_previous_catalog=0.9,
            warn_fraction_of_previous_catalog=0.5,
        )
    with pytest.raises((ValidationError, ValueError)):
        CollectorHealthConfig(minimum_fraction_of_previous_catalog=1.5)
    # valid
    cfg = CollectorHealthConfig(
        minimum_fraction_of_previous_catalog=0.50,
        warn_fraction_of_previous_catalog=0.85,
    )
    assert cfg.minimum_fraction_of_previous_catalog == 0.50


def test_custom_thresholds_drive_health(store):
    cfg = CollectorHealthConfig(
        unexpected_zero_is_failure=True,
        minimum_fraction_of_previous_catalog=0.50,
        warn_fraction_of_previous_catalog=0.85,
    )
    _seed_ok(store, "test-src", 100)
    notifier = ConsoleNotifier()

    # 90 → ok ( >= 0.85)
    s = run_source(_src(), FixedEngine(90), type("F", (), {"get": lambda *a, **k: None})(),
                   store, notifier, health_cfg=cfg)
    assert s.health == "ok"
    assert s.health_reason in ("HEALTHY_CATALOG", "RECOVERED")

    # 80 → degraded (0.50 <= 0.80 < 0.85)
    s = run_source(_src(), FixedEngine(80), type("F", (), {"get": lambda *a, **k: None})(),
                   store, notifier, health_cfg=cfg)
    assert s.health == "degraded"
    assert s.health_reason == "CATALOG_WARN_THRESHOLD"

    # 40 → failed
    s = run_source(_src(), FixedEngine(40), type("F", (), {"get": lambda *a, **k: None})(),
                   store, notifier, health_cfg=cfg)
    assert s.health == "failed"
    assert s.health_reason == "CATALOG_FAILURE_THRESHOLD"
    assert s.events == 0  # early return — no mass removals

    # 0 → failed
    s = run_source(_src(), FixedEngine(0), type("F", (), {"get": lambda *a, **k: None})(),
                   store, notifier, health_cfg=cfg)
    assert s.health == "failed"
    assert s.health_reason == "UNEXPECTED_ZERO"


def test_zero_allowed_when_configured(store):
    cfg = CollectorHealthConfig(
        unexpected_zero_is_failure=False,
        minimum_fraction_of_previous_catalog=0.35,
        warn_fraction_of_previous_catalog=0.70,
    )
    _seed_ok(store, "test-src", 50)
    s = run_source(_src(), FixedEngine(0), type("F", (), {"get": lambda *a, **k: None})(),
                   store, ConsoleNotifier(), health_cfg=cfg)
    assert s.health == "ok"
    assert s.discovered == 0


def test_sequence_degrade_recover(store):
    cfg = CollectorHealthConfig(minimum_fraction_of_previous_catalog=0.50,
                                warn_fraction_of_previous_catalog=0.85)
    _seed_ok(store, "seq-a", 100)
    n = ConsoleNotifier()
    s1 = run_source(_src("seq-a"), FixedEngine(80), type("F", (), {"get": lambda *a, **k: None})(),
                    store, n, health_cfg=cfg)
    assert s1.health == "degraded"
    # degraded path still processes products — but must not wipe baseline
    # Record a fake failed/ok appropriately
    store.db.execute(
        "INSERT INTO crawler_runs(source_key, started_at, finished_at, status, stats_json) "
        "VALUES (?,?,?,?,?)",
        ("seq-a", "2026-01-02", "2026-01-02", "ok",  # degraded still status ok in runner when health=degraded
         json.dumps({"discovered": 80, "health": "degraded"})),
    )
    store.db.commit()
    # last ok discovered is still 80 now — for recovery test re-seed preferred last-good=100
    # Policy: only status='ok' counts; if we stored degraded as ok, prev becomes 80.
    # Runner marks only health==failed as status failed; degraded is ok.
    s2 = run_source(_src("seq-a"), FixedEngine(90), type("F", (), {"get": lambda *a, **k: None})(),
                    store, n, health_cfg=cfg)
    # 90/80 >= 0.85 → ok
    assert s2.health == "ok"


def test_sequence_collapse_preserves_baseline(store):
    cfg = CollectorHealthConfig(minimum_fraction_of_previous_catalog=0.50,
                                warn_fraction_of_previous_catalog=0.85)
    _seed_ok(store, "seq-b", 100)
    n = ConsoleNotifier()
    s_fail = run_source(_src("seq-b"), FixedEngine(30), type("F", (), {"get": lambda *a, **k: None})(),
                        store, n, health_cfg=cfg)
    assert s_fail.health == "failed"
    assert s_fail.events == 0
    # Simulate runner writing status=failed
    store.db.execute(
        "INSERT INTO crawler_runs(source_key, started_at, finished_at, status, stats_json) "
        "VALUES (?,?,?,?,?)",
        ("seq-b", "2026-01-02", "2026-01-02", "failed",
         json.dumps({"discovered": 30, "health": "failed"})),
    )
    store.db.commit()
    # last successful still 100
    row = store.db.execute(
        "SELECT stats_json FROM crawler_runs WHERE source_key=? AND status='ok' "
        "ORDER BY id DESC LIMIT 1", ("seq-b",)
    ).fetchone()
    prev = json.loads(row["stats_json"])["discovered"]
    assert prev == 100
    s_ok = run_source(_src("seq-b"), FixedEngine(100), type("F", (), {"get": lambda *a, **k: None})(),
                      store, n, health_cfg=cfg)
    assert s_ok.health == "ok"
    assert s_ok.previous_discovered == 100


def test_runner_threads_radar_health(store, tmp_path):
    from oem_radar.core.config import OemConfig, ManufacturerConfig, RadarConfig
    radar = RadarConfig(
        collector_health=CollectorHealthConfig(
            minimum_fraction_of_previous_catalog=0.50,
            warn_fraction_of_previous_catalog=0.85,
        ),
        baseline_quiet=True,
    )
    oem = OemConfig(
        manufacturer=ManufacturerConfig(name="TestOEM"),
        sources=[SourceConfig(id="wired-src", engine="shopify",
                              base_url="https://ex.com", enabled=True)],
    )
    # Register a temporary engine? run_all uses engines.get — use Fixed via monkeypatch
    from oem_radar.core import registry
    # Seed baseline
    store.ensure_manufacturer("TestOEM", None, [])
    store.ensure_source("wired-src", 1, "shopify", "https://ex.com", {})
    _seed_ok(store, "wired-src", 100)

    class _E:
        def __init__(self, source, manufacturer):
            self._inner = FixedEngine(40)
        def discover(self, f):
            return self._inner.discover(f)
        def parse(self, d):
            return self._inner.parse(d)
        def normalize(self, r):
            return self._inner.normalize(r)
        def validate(self, p):
            return []

    # Temporarily register
    engines = registry.engines
    # Use direct call instead of full register if already exists
    original = engines._items.get("shopify")
    engines._items["shopify"] = _E
    try:
        stats = run_all(radar, {"TestOEM": oem}, store, ConsoleNotifier(),
                        type("F", (), {"get": lambda *a, **k: FetchedDocument(url="", status=200, body=b"{}")})(),
                        force=True)
    finally:
        if original:
            engines._items["shopify"] = original
    assert stats
    assert stats[0].health == "failed"
    assert stats[0].health_min_fraction == 0.50
    # run status failed → not new baseline
    last = store.db.execute(
        "SELECT status FROM crawler_runs WHERE source_key='wired-src' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert last["status"] == "failed"


def test_kamrui_enabled_and_fixture():
    oems = load_oem_configs(Path("config/oems"))
    assert "KAMRUI" in oems
    src = next(s for s in oems["KAMRUI"].sources if s.id == "kamrui-shopify")
    assert src.enabled is True
    path = FIXTURES / "kamrui_products_p1.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert len(data["products"]) >= 10
    eng = ShopifyEngine(src, "KAMRUI")
    from oem_radar.core.models import RawProduct
    product = eng.normalize(RawProduct(
        source_id=src.id, url=src.base_url + "/products/x",
        payload=data["products"][0],
    ))
    assert product.manufacturer == "KAMRUI"
    assert product.model


def test_radar_yaml_loads_collector_health():
    cfg = load_radar_config(Path("config/radar.yaml"))
    assert isinstance(cfg.collector_health, CollectorHealthConfig)
    assert 0 <= cfg.collector_health.minimum_fraction_of_previous_catalog < \
           cfg.collector_health.warn_fraction_of_previous_catalog <= 1
