"""M12 exit criteria: dashboard reads the DB offline, renders valid HTML with
clickable store links, and its data layer surfaces the mission-critical
signals (new products, unseen hardware)."""

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
from oem_radar.core.models import FetchedDocument
from oem_radar.core.runner import run_all
from oem_radar.dashboard.data import collect
from oem_radar.dashboard.render import render
from oem_radar.engines import shopify  # noqa: F401  registers engine
from oem_radar.providers.discord import DiscordNotifier
from oem_radar.providers.sqlite import SqliteStore, connect_readonly

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "shopify" / "gmktec_products.json")
    .read_text(encoding="utf-8")
)
BASE = "https://www.gmktec.com"


class RouteFetcher:
    def __init__(self, catalog):
        self.catalog = catalog

    def get(self, url):
        if url == f"{BASE}/products.json?limit=250&page=1":
            return FetchedDocument(url=url, status=200, body=json.dumps(self.catalog))
        return FetchedDocument(url=url, status=200, body=json.dumps({"products": []}))


@pytest.fixture()
def populated_db(tmp_path):
    """A DB with a baseline crawl plus one real change: an unseen CPU on K12."""
    radar = RadarConfig(db_path=str(tmp_path / "r.db"), raw_dir=str(tmp_path / "raw"))
    oems = {"GMKtec": OemConfig(
        manufacturer=ManufacturerConfig(name="GMKtec", country="CN"),
        sources=[SourceConfig(id="gmktec-shopify", engine="shopify", base_url=BASE,
                              discovery=["products_json"])],
    )}
    store = SqliteStore(radar.db_path, radar.raw_dir)
    store.seed_components(SEED_COMPONENTS)
    notifier = DiscordNotifier(store, "https://hook.example", 3,
                               sender=lambda u, p: (True, None))
    run_all(radar, oems, store, notifier, RouteFetcher(FIXTURE), force=True)

    catalog = copy.deepcopy(FIXTURE)
    k12 = next(p for p in catalog["products"] if "k12" in p["handle"])
    k12["title"] = "GMKtec K12 Mini PC AMD Ryzen™ AI MAX+ 396"
    run_all(radar, oems, store, notifier, RouteFetcher(catalog), force=True)
    store.close()
    return radar.db_path


def test_collect_surfaces_signals(populated_db):
    conn = connect_readonly(populated_db)
    data = collect(conn)
    conn.close()

    assert data["summary"]["products"] >= 3
    assert data["summary"]["events"] > 0

    # every event carries a clickable store URL and OEM/model context
    assert data["events"], "expected change events"
    for e in data["events"]:
        assert e["url"] and e["url"].startswith("http")
        assert e["manufacturer"] == "GMKtec"

    # the unseen 396 must appear as an unseen-component signal
    unseen = [e for e in data["events"] if e["unseen_component"]]
    assert unseen and any("396" in (e["cpu"] or "") for e in unseen)

    # and be learned into the discovered-hardware feed
    assert any("396" in c["canonical_name"] for c in data["components"])

    # manufacturers overview populated
    assert any(m["name"] == "GMKtec" for m in data["manufacturers"])
    # run telemetry present
    assert len(data["runs"]) == 2


def test_render_produces_valid_linked_html(populated_db):
    conn = connect_readonly(populated_db)
    data = collect(conn)
    conn.close()
    html = render(data)

    assert html.startswith("<!DOCTYPE html>")
    assert "__DATA__" not in html  # placeholder fully substituted
    assert "OEM" in html and "Radar" in html
    # a real clickable store listing link is present
    assert 'target="_blank"' in html and "gmktec.com/products/" in html
    # embedded payload is parseable back out of the script tag
    start = html.index("const DATA = ") + len("const DATA = ")
    end = html.index(";\n", start)
    parsed = json.loads(html[start:end].replace("<\\/", "</"))
    assert parsed["summary"]["products"] >= 3


def test_readonly_connection_does_not_write(populated_db):
    conn = connect_readonly(populated_db)
    with pytest.raises(Exception):
        conn.execute("INSERT INTO manufacturers(name) VALUES ('X')")
        conn.commit()
    conn.close()


def test_mark_component_seen_removes_from_feed(populated_db):
    from oem_radar.providers.sqlite import SqliteStore

    conn = connect_readonly(populated_db)
    before = collect(conn)
    conn.close()
    target = next(c["canonical_name"] for c in before["components"] if "396" in c["canonical_name"])

    store = SqliteStore(populated_db, populated_db + ".raw")
    changed = store.mark_component_seen([target])
    assert changed == 1
    # still 'known' (won't re-alert) — the row exists, just not 'discovered'
    assert store.known_component(target) is True
    store.close()

    conn = connect_readonly(populated_db)
    after = collect(conn)
    conn.close()
    names = [c["canonical_name"] for c in after["components"]]
    assert target not in names  # left the unseen feed
    assert after["summary"]["unseen_components"] == before["summary"]["unseen_components"] - 1


def test_mark_all_seen(populated_db):
    from oem_radar.providers.sqlite import SqliteStore

    store = SqliteStore(populated_db, populated_db + ".raw")
    n = store.mark_component_seen(None)  # all
    store.close()
    assert n >= 1
    conn = connect_readonly(populated_db)
    data = collect(conn)
    conn.close()
    assert data["components"] == []  # feed cleared
    assert data["summary"]["unseen_components"] == 0


def test_seed_canonicalizes_raw_strings(tmp_path):
    """Seed slugs must match runtime slugs (the bug that flagged known chips)."""
    from oem_radar.core.knownhw import canonicalize
    from oem_radar.providers.sqlite import SqliteStore

    store = SqliteStore(str(tmp_path / "s.db"), str(tmp_path / "raw"))
    store.seed_components([("cpu", "AMD Ryzen 7 8745HS")])
    # a product whose CPU canonicalizes the same way must read as KNOWN
    assert store.known_component(canonicalize("AMD Ryzen 7 8745HS")) is True
    assert store.known_component(canonicalize("AMD Ryzen7 8745HS")) is True  # spelling variant
    store.close()


def test_canonicalize_spelling_variants_converge():
    from oem_radar.core.knownhw import canonicalize
    assert canonicalize("AMD Ryzen9 6900HX") == canonicalize("AMD Ryzen 9 6900HX")
    assert canonicalize("Radeon780M") == canonicalize("Radeon 780M")
    # existing expectations unchanged
    assert canonicalize("Intel® Celeron® N5095 Processor") == "celeron-n5095"
    assert canonicalize("AMD Ryzen™ AI 9 HX 370") == "ryzen-ai-9-hx-370"


def test_collect_empty_db(tmp_path):
    store = SqliteStore(str(tmp_path / "e.db"), str(tmp_path / "raw"))
    store.close()
    conn = connect_readonly(str(tmp_path / "e.db"))
    data = collect(conn)
    conn.close()
    assert data["summary"]["products"] == 0
    assert data["events"] == []
    # render must not choke on an empty dataset
    assert render(data).startswith("<!DOCTYPE html>")


def test_dashboard_survives_pre_v3_db_without_stories_table(tmp_path):
    """The live 500: a DB created before the stories table, opened read-only by
    the dashboard, must degrade to an empty Stories tab — not crash."""
    import sqlite3 as _sq
    from oem_radar.providers.sqlite import connect_readonly
    from oem_radar.dashboard.data import collect
    from oem_radar.dashboard.render import render

    db = tmp_path / "old.db"
    con = _sq.connect(db)
    con.executescript(
        "CREATE TABLE manufacturers(id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE products(id INTEGER PRIMARY KEY, manufacturer_id INT, canonical_model TEXT);"
        "CREATE TABLE listings(id INTEGER PRIMARY KEY, product_key TEXT, url TEXT, product_id INT);"
        "CREATE TABLE snapshots(id INTEGER PRIMARY KEY, listing_id INT, normalized_json TEXT, normalized_zjson BLOB);"
        "CREATE TABLE change_events(id INTEGER PRIMARY KEY, product_key TEXT, change_type TEXT, field TEXT, "
        "old_value_json TEXT, new_value_json TEXT, severity INT, meta_json TEXT, detected_at TEXT);"
        "CREATE TABLE notifications(id INTEGER PRIMARY KEY, change_event_id INT, status TEXT);"
        "CREATE TABLE components(id INTEGER PRIMARY KEY, kind TEXT, canonical_name TEXT, first_raw TEXT, source TEXT, first_seen_at TEXT);"
        "CREATE TABLE crawler_runs(id INTEGER PRIMARY KEY, source_key TEXT, started_at TEXT, finished_at TEXT, status TEXT, stats_json TEXT);"
        # NOTE: no 'stories' table on purpose
    )
    con.commit(); con.close()

    conn = connect_readonly(str(db))
    data = collect(conn)          # must not raise
    conn.close()
    assert data["stories"] == [] and data["summary"]["stories"] == 0
    assert render(data).startswith("<!DOCTYPE html>")


def test_v2_db_migrates_to_v3_creating_stories(tmp_path):
    """Opening an existing v2 DB read-write (a crawl) creates the stories table."""
    import sqlite3 as _sq
    from oem_radar.providers.sqlite import SqliteStore, SCHEMA_VERSION
    db = tmp_path / "v2.db"
    # minimal v2 marker
    con = _sq.connect(db)
    con.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)")
    con.execute("INSERT INTO schema_migrations(version) VALUES (1)")
    con.execute("INSERT INTO schema_migrations(version) VALUES (2)")
    con.commit(); con.close()

    store = SqliteStore(str(db), str(tmp_path / "raw"))  # migrate runs
    versions = [r["version"] for r in
                store.db.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert 3 in versions and 4 in versions and 5 in versions and max(versions) == SCHEMA_VERSION
    # stories table now usable
    assert store.recent_stories() == []
    store.close()
