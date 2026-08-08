"""The M0 architecture proof: a full pipeline run composed entirely from
fakes implementing the core protocols. No vendor code, no network, no DB —
if this passes, the seams are where ARCHITECTURE.md says they are.
"""

import json

from pydantic import BaseModel

from oem_radar.core.interfaces import Fetcher, Notifier, SnapshotStore, SourceEngine
from oem_radar.core.config import SourceConfig
from oem_radar.core.models import (
    ChangeEvent,
    ChangeType,
    Component,
    FetchedDocument,
    NormalizedProduct,
    Price,
    ProductRef,
    RawProduct,
    Severity,
)
from oem_radar.core.pipeline import run_source
from oem_radar.core.registry import Registry

CATALOG_V1 = {
    "/p/k12": {"model": "K12", "cpu": "Ryzen AI Max+ 395", "memory": "96 GB", "price": 999.0},
}
CATALOG_V2 = {
    "/p/k12": {"model": "K12", "cpu": "Ryzen AI Max+ 396", "memory": "128 GB", "price": 999.0},
    "/p/k13": {"model": "K13", "cpu": "Ryzen AI Max+ 396", "memory": "64 GB", "price": 799.0},
}


class FakeFetcher:
    def __init__(self, catalog):
        self.catalog = catalog

    def get(self, url: str) -> FetchedDocument:
        return FetchedDocument(url=url, status=200, body=json.dumps(self.catalog[url]),
                               content_type="application/json")


class FakeEngineConfig(BaseModel):
    pass


class FakeEngine:
    config_schema = FakeEngineConfig

    def discover(self, fetcher):
        return [ProductRef(url=u, handle=u.rsplit("/", 1)[-1], hidden=(u == "/p/k13"))
                for u in sorted(fetcher.catalog)]

    def parse(self, doc):
        return RawProduct(source_id="fake", url=doc.url, payload=json.loads(doc.body))

    def normalize(self, raw):
        p = raw.payload
        return NormalizedProduct(
            manufacturer="FakeOEM", model=p["model"], cpu=Component(raw=p["cpu"]),
            memory=p["memory"], prices=[Price(amount=p["price"], currency="USD")],
            source_url=raw.url,
        )

    def validate(self, product):
        return []


class MemoryStore:
    def __init__(self, known: set[str] | None = None):
        self.snapshots: dict[str, list[NormalizedProduct]] = {}
        self.touched: list[str] = []
        self.known: set[str] = known if known is not None else set()
        self.learned: list[tuple[str, str]] = []

    def latest(self, key):
        versions = self.snapshots.get(key)
        return versions[-1] if versions else None

    def resolve_prior(self, key, product):
        prior = self.latest(key)
        if prior is not None:
            return prior, "same_listing"
        for versions in self.snapshots.values():  # cross-listing model match
            if versions and versions[-1].model == product.model:
                return versions[-1], "existing_product"
        return None, "none"

    def append(self, key, product):
        self.snapshots.setdefault(key, []).append(product)

    def touch(self, key):
        self.touched.append(key)

    def known_component(self, canonical):
        return canonical in self.known

    def learn_component(self, kind, canonical, raw):
        self.known.add(canonical)
        self.learned.append((kind, canonical))


class CollectingNotifier:
    def __init__(self):
        self.outbox: list[ChangeEvent] = []

    def enqueue(self, event, product=None):
        self.outbox.append(event)

    def drain(self):
        return 0


SOURCE = SourceConfig(id="fake-src", engine="fake", base_url="https://fake.example")


def test_fakes_satisfy_protocols():
    assert isinstance(FakeFetcher(CATALOG_V1), Fetcher)
    assert isinstance(FakeEngine(), SourceEngine)
    assert isinstance(MemoryStore(), SnapshotStore)
    assert isinstance(CollectingNotifier(), Notifier)


def test_end_to_end_two_runs():
    store, notifier, engine = MemoryStore(), CollectingNotifier(), FakeEngine()

    # Run 1: everything is new
    s1 = run_source(SOURCE, engine, FakeFetcher(CATALOG_V1), store, notifier)
    assert s1.snapshots_written == 1 and s1.errors == []
    assert [e.change_type for e in notifier.outbox] == [ChangeType.NEW_PRODUCT]

    # Run 1 again, unchanged catalog: dedup — no snapshots, no events (ADR-4)
    s1b = run_source(SOURCE, engine, FakeFetcher(CATALOG_V1), store, notifier)
    assert s1b.snapshots_written == 0 and s1b.unchanged == 1
    assert len(store.snapshots["fake-src:k12"]) == 1
    assert store.touched == ["fake-src:k12"]

    # Run 2: CPU+RAM refresh on K12, hidden new K13
    notifier.outbox.clear()
    s2 = run_source(SOURCE, engine, FakeFetcher(CATALOG_V2), store, notifier)
    assert s2.snapshots_written == 2
    by_type = {e.change_type: e for e in notifier.outbox}

    # diff reports canonical slugs (M5 stamping ran in the pipeline)
    assert by_type[ChangeType.COMPONENT_CHANGED].new_value == "ryzen-ai-max+-396"
    assert by_type[ChangeType.COMPONENT_CHANGED].meta["unseen_component"] is True
    assert by_type[ChangeType.SPEC_CHANGED].field == "memory"

    new = by_type[ChangeType.NEW_PRODUCT]
    assert new.severity == Severity.BREAKING
    assert new.meta["hidden"] is True  # sitemap-only discovery is itself a signal (ADR-7)

    # K12 history is append-only: v1 then v2
    v1, v2 = store.snapshots["fake-src:k12"]
    assert v1.memory == "96 GB" and v2.memory == "128 GB"


def test_engine_registry_roundtrip():
    reg = Registry("engine")
    reg.register("fake")(FakeEngine)
    assert reg.get("fake") is FakeEngine
    assert "fake" in reg and reg.names() == ["fake"]
