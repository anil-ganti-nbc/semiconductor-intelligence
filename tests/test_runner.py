"""M4/M5 exit criteria: full stack — config → Shopify engine → SQLite →
diff → known-hardware → Discord outbox — over two simulated crawls, with
the second run proving dedup and an unseen CPU proving the star signal."""

import copy
import json
from pathlib import Path

import pytest

from oem_radar.core.config import (
    ManufacturerConfig,
    OemConfig,
    RadarConfig,
    SourceConfig,
)
from oem_radar.core.knownhw import SEED_COMPONENTS
from oem_radar.core.models import FetchedDocument, Severity
from oem_radar.core.runner import run_all
from oem_radar.engines import shopify  # noqa: F401  (registers engine)
from oem_radar.providers.discord import DiscordNotifier
from oem_radar.providers.sqlite import SqliteStore

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "shopify" / "gmktec_products.json")
    .read_text(encoding="utf-8")
)
BASE = "https://www.gmktec.com"


class RouteFetcher:
    def __init__(self, catalog: dict):
        self.catalog = catalog

    def get(self, url: str) -> FetchedDocument:
        if url == f"{BASE}/products.json?limit=250&page=1":
            return FetchedDocument(url=url, status=200, body=json.dumps(self.catalog))
        if url.startswith(f"{BASE}/products.json"):
            return FetchedDocument(url=url, status=200, body=json.dumps({"products": []}))
        raise KeyError(url)


@pytest.fixture()
def env(tmp_path):
    radar = RadarConfig(db_path=str(tmp_path / "radar.db"), raw_dir=str(tmp_path / "raw"))
    oems = {"GMKtec": OemConfig(
        manufacturer=ManufacturerConfig(name="GMKtec", country="CN"),
        sources=[SourceConfig(id="gmktec-shopify", engine="shopify", base_url=BASE,
                              min_interval="6h", discovery=["products_json"])],
    )}
    store = SqliteStore(radar.db_path, radar.raw_dir)
    store.seed_components(SEED_COMPONENTS)
    sent = []
    notifier = DiscordNotifier(store, "https://hook.example", 3,
                               sender=lambda u, p: (sent.append(p), None) and (True, None))
    yield radar, oems, store, notifier, sent
    store.close()


def test_two_runs_end_to_end(env):
    radar, oems, store, notifier, sent = env

    # Run 1: cold start — 3 new products, all severity 5, all sent
    stats = run_all(radar, oems, store, notifier, RouteFetcher(FIXTURE), force=True)
    assert len(stats) == 1 and stats[0].snapshots_written == 3 and not stats[0].errors
    assert len(sent) == 3
    assert all("NEW PRODUCT" in p["embeds"][0]["title"] for p in sent)

    # due-ness: without force, source is skipped now
    assert run_all(radar, oems, store, notifier, RouteFetcher(FIXTURE)) == []

    # Run 2 (forced), unchanged catalog: zero snapshots, zero sends (ADR-4)
    sent.clear()
    stats = run_all(radar, oems, store, notifier, RouteFetcher(FIXTURE), force=True)
    assert stats[0].snapshots_written == 0 and stats[0].unchanged == 3
    assert sent == []

    # Run 3: K12 silently refreshed with an UNSEEN CPU + more RAM
    catalog = copy.deepcopy(FIXTURE)
    k12 = next(p for p in catalog["products"] if "k12" in p["handle"])
    k12["title"] = "GMKtec K12 Mini PC AMD Ryzen™ AI MAX+ 396"
    k12["variants"][0]["option1"] = "128GB RAM + 2TB SSD"
    sent.clear()
    stats = run_all(radar, oems, store, notifier, RouteFetcher(catalog), force=True)
    assert stats[0].snapshots_written == 1

    titles = [p["embeds"][0]["title"] for p in sent]
    assert any("COMPONENT CHANGED" in t for t in titles)
    cpu_embed = next(p for p in sent if "COMPONENT" in p["embeds"][0]["title"])
    cpu_field = next(f for f in cpu_embed["embeds"][0]["fields"] if f["name"] == "CPU")
    assert "previously unseen" in cpu_field["value"]  # ★★★★★ trigger

    # the platform learned: 396 is now a known component
    assert store.known_component("ryzen-ai-max+-396")

    # full immutable history for the K12 listing
    k12_versions = store.db.execute(
        "SELECT COUNT(*) c FROM snapshots s JOIN listings l ON s.listing_id=l.id "
        "WHERE l.product_key LIKE '%k12%'"
    ).fetchone()["c"]
    assert k12_versions == 2

    # telemetry recorded for every run
    assert len(store.recent_runs()) == 3


def test_baseline_quiet_suppresses_first_crawl(tmp_path):
    radar = RadarConfig(db_path=str(tmp_path / "r.db"), raw_dir=str(tmp_path / "raw"),
                        baseline_quiet=True)
    oems = {"GMKtec": OemConfig(
        manufacturer=ManufacturerConfig(name="GMKtec", country="CN"),
        sources=[SourceConfig(id="gmktec-shopify", engine="shopify", base_url=BASE,
                              discovery=["products_json"])],
    )}
    store = SqliteStore(radar.db_path, radar.raw_dir)
    store.seed_components(SEED_COMPONENTS)
    sent = []
    notifier = DiscordNotifier(store, "https://hook.example", 3,
                               sender=lambda u, p: (sent.append(p), None) and (True, None))

    # First crawl: full history stored, zero pings
    stats = run_all(radar, oems, store, notifier, RouteFetcher(FIXTURE), force=True)
    assert stats[0].snapshots_written == 3 and sent == []
    suppressed = store.db.execute(
        "SELECT COUNT(*) c FROM notifications WHERE status='suppressed'").fetchone()["c"]
    assert suppressed == 3  # audited, not lost

    # Second crawl with a real change: pings normally
    catalog = copy.deepcopy(FIXTURE)
    next(p for p in catalog["products"] if "k12" in p["handle"])["variants"][0]["price"] = "779.99"
    run_all(radar, oems, store, notifier, RouteFetcher(catalog), force=True)
    assert len(sent) == 1 and "PRICE" in sent[0]["embeds"][0]["title"]
    store.close()
