"""M3 exit criteria: schema round-trips, dedup, resolution, outbox, telemetry."""

import pytest

from oem_radar.core.models import ChangeEvent, ChangeType, Component, Severity
from oem_radar.providers.sqlite import SqliteStore, model_key

from test_models import make_product


@pytest.fixture()
def store(tmp_path):
    s = SqliteStore(str(tmp_path / "radar.db"), str(tmp_path / "raw"))
    yield s
    s.close()


def test_model_key_identity():
    assert model_key("GMKtec", "K12 Mini PC AMD Ryzen 7 H 255") == "gmktec::k12"
    assert model_key("GMKtec", "EVO-X1 AI Mini PC AMD Ryzen AI 9 HX 370") == "gmktec::evo-x1"
    assert model_key("GMKtec", "NucBox M7 Ultra Mini PC") == "gmktec::nucbox-m7"
    assert model_key("GMKtec", "Plastic Metal Work Play Mini PC") == "gmktec::plastic-metal"


def test_append_latest_roundtrip_and_dedup(store):
    p1 = make_product()
    store.append("src:k12", p1)
    got = store.latest("src:k12")
    assert got.content_hash() == p1.content_hash()

    store.append("src:k12", p1)  # identical → UNIQUE(listing, hash) ignores
    n = store.db.execute("SELECT COUNT(*) c FROM snapshots").fetchone()["c"]
    assert n == 1

    p2 = make_product(memory="128 GB")
    store.append("src:k12", p2)
    assert store.latest("src:k12").memory == "128 GB"
    n = store.db.execute("SELECT COUNT(*) c FROM snapshots").fetchone()["c"]
    assert n == 2  # append-only history


def test_resolve_prior_cross_listing(store):
    store.append("src-a:k12-old-url", make_product(model="K12 Mini PC AMD Ryzen 7 H 255"))
    prior, relation = store.resolve_prior(
        "src-a:k12-new-url", make_product(model="K12 Mini PC (2026 Refresh)")
    )
    assert relation == "existing_product" and prior is not None  # rename caught

    prior, relation = store.resolve_prior("src-a:k99", make_product(model="K99 Beast"))
    assert relation == "none" and prior is None


def test_components_seed_and_learn(store):
    store.seed_components([("cpu", "ryzen-7-h-255")])
    assert store.known_component("ryzen-7-h-255") is True
    assert store.known_component("ryzen-ai-max+-396") is False
    store.learn_component("cpu", "ryzen-ai-max+-396", "AMD Ryzen AI MAX+ 396")
    assert store.known_component("ryzen-ai-max+-396") is True
    row = store.db.execute(
        "SELECT source FROM components WHERE canonical_name='ryzen-ai-max+-396'"
    ).fetchone()
    assert row["source"] == "discovered"


def test_outbox_dedup_and_marking(store):
    ev = ChangeEvent(product_key="s:k", change_type=ChangeType.NEW_PRODUCT,
                     severity=Severity.BREAKING)
    eid = store.record_event(ev)
    store.outbox_put("discord", ev.dedup_key(), {"embeds": []}, event_id=eid)
    store.outbox_put("discord", ev.dedup_key(), {"embeds": []}, event_id=eid)  # dup ignored
    pending = store.outbox_pending("discord")
    assert len(pending) == 1
    store.outbox_mark(pending[0]["id"], "sent")
    assert store.outbox_pending("discord") == []


def test_prices_observation_stream(store):
    store.append("src:k12", make_product())
    store.append("src:k12", make_product(memory="128 GB"))
    n = store.db.execute("SELECT COUNT(*) c FROM prices").fetchone()["c"]
    assert n == 2  # one observation per snapshot append


def test_source_due(store):
    man = store.ensure_manufacturer("GMKtec", "CN", [])
    store.ensure_source("gmktec-shopify", man, "shopify", "https://x", {})
    assert store.source_due("gmktec-shopify", 3600) is True  # never run
    rid = store.run_started("gmktec-shopify")
    store.run_finished(rid, "ok", {"snapshots": 1})
    assert store.source_due("gmktec-shopify", 3600) is False  # just ran
    assert store.source_due("gmktec-shopify", 0) is True

    runs = store.recent_runs()
    assert runs[0]["status"] == "ok"


def test_raw_payload_on_disk(store, tmp_path):
    store.append("src:k12", make_product(raw_data={"shopify_id": 1}))
    row = store.db.execute("SELECT raw_ref FROM snapshots").fetchone()
    assert row["raw_ref"] and (tmp_path / "raw" / row["raw_ref"]).exists()
