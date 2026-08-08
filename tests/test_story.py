"""Story detection: windowing, distinct-OEM threshold, scoring, dedup, and the
end-to-end demotion of constituent product pings."""

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oem_radar.core.config import (ManufacturerConfig, OemConfig, RadarConfig,
                                   SourceConfig, StoryRule)
from oem_radar.core.knownhw import SEED_COMPONENTS
from oem_radar.core.models import ChangeEvent, ChangeType, FetchedDocument, Severity
from oem_radar.core.runner import run_all
from oem_radar.core.story import detect
from oem_radar.engines import shopify  # noqa: F401
from oem_radar.providers.discord import DiscordNotifier, build_story_embed
from oem_radar.providers.sqlite import SqliteStore

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


def ev(mfr, minutes_ago, cpu="ryzen-ai-max+-396"):
    e = ChangeEvent(product_key=f"{mfr.lower()}:x", change_type=ChangeType.COMPONENT_CHANGED,
                    field="cpu", new_value=cpu, severity=Severity.BREAKING,
                    detected_at=NOW - timedelta(minutes=minutes_ago),
                    meta={"unseen_component": True})
    return (e, mfr, f"{mfr} Box", f"https://{mfr.lower()}.com/x")


RULE = StoryRule(id="unseen", title="{n} OEMs listed {key}",
                 match={"change_type": "component_changed", "unseen_component": True},
                 group_by="new_value", window="7d", min_distinct_manufacturers=2,
                 base_score=75, per_extra_oem=12)


def test_fires_when_enough_distinct_oems():
    stories = detect([ev("GMKtec", 10), ev("Minisforum", 20), ev("Beelink", 30)],
                     [RULE], now=NOW)
    assert len(stories) == 1
    s = stories[0]
    assert s.key == "ryzen-ai-max+-396"
    assert s.manufacturers == ["Beelink", "GMKtec", "Minisforum"]
    assert s.score == 75 + 12  # 3 OEMs = 1 extra over the min of 2
    assert len(s.evidence) == 3


def test_no_story_below_threshold():
    assert detect([ev("GMKtec", 10)], [RULE], now=NOW) == []


def test_same_oem_twice_is_not_two_oems():
    # one manufacturer listing it on two products doesn't make a cross-OEM story
    assert detect([ev("GMKtec", 10), ev("GMKtec", 20)], [RULE], now=NOW) == []


def test_window_excludes_old_events():
    stories = detect([ev("GMKtec", 10), ev("Minisforum", 60 * 24 * 9)],  # 9 days ago
                     [RULE], now=NOW)
    assert stories == []  # second event outside the 7d window


def test_different_chips_dont_merge():
    stories = detect([ev("GMKtec", 10, "chip-a"), ev("Minisforum", 20, "chip-b")],
                     [RULE], now=NOW)
    assert stories == []  # different keys, neither reaches 2 OEMs


def test_score_capped_at_100():
    rows = [ev(m, i * 5) for i, m in enumerate(
        ["A", "B", "C", "D", "E", "F", "G", "H"])]
    s = detect(rows, [RULE], now=NOW)[0]
    assert s.score == 100 and "OEMs" in s.score_reasons[0]


def test_story_embed_has_evidence_links():
    s = detect([ev("GMKtec", 10), ev("Minisforum", 20)], [RULE], now=NOW)[0]
    payload = build_story_embed(s)
    body = json.dumps(payload)
    assert "STORY" in body and "gmktec.com" in body and "75" in body


# ---- end-to-end: story fires and demotes the individual pings ----

FIX = json.loads((Path(__file__).parent / "fixtures" / "shopify" /
                  "gmktec_products.json").read_text())


def _store_event(store, mfr_name, product_key, cpu_slug):
    """Simulate a recorded unseen-component event for a manufacturer."""
    man = store.ensure_manufacturer(mfr_name, "CN", [])
    store.ensure_source(f"{mfr_name.lower()}-src", man, "shopify", "https://x", {})
    from oem_radar.core.models import NormalizedProduct, Component
    p = NormalizedProduct(manufacturer=mfr_name, model=f"{mfr_name} Box",
                          cpu=Component(raw="AMD Ryzen AI MAX+ 396", canonical=cpu_slug,
                                        known=False),
                          source_url=f"https://{mfr_name.lower()}.com/box",
                          vendor_sku=f"{mfr_name}-1")
    store._source_ctx = store.db.execute(
        "SELECT id FROM sources WHERE source_key=?", (f"{mfr_name.lower()}-src",)).fetchone()[0]
    store.append(product_key, p)
    e = ChangeEvent(product_key=product_key, change_type=ChangeType.COMPONENT_CHANGED,
                    field="cpu", new_value=cpu_slug, severity=Severity.BREAKING,
                    meta={"unseen_component": True})
    eid = store.record_event(e)
    store.outbox_put("discord", e.dedup_key(), {"embeds": []}, event_id=eid, status="pending")


def test_end_to_end_story_demotes_pings(tmp_path):
    radar = RadarConfig(db_path=str(tmp_path / "r.db"), raw_dir=str(tmp_path / "raw"),
                        story_rules=[RULE])
    store = SqliteStore(radar.db_path, radar.raw_dir)
    store.seed_components(SEED_COMPONENTS)
    _store_event(store, "GMKtec", "gmktec-src:box", "ryzen-ai-max+-396")
    _store_event(store, "Minisforum", "minisforum-src:box", "ryzen-ai-max+-396")

    sent = []
    notifier = DiscordNotifier(store, "https://hook", 3,
                               sender=lambda u, p: (sent.append(p), None) and (True, None))

    # no sources to crawl; run_all just runs story detection + drain
    run_all(radar, {}, store, notifier, fetcher=None, force=True)

    # exactly one STORY embed sent, and the two product pings were demoted
    titles = [p["embeds"][0].get("title", "") for p in sent]
    assert sum("STORY" in t for t in titles) == 1
    demoted = store.db.execute(
        "SELECT COUNT(*) c FROM notifications WHERE status='demoted'").fetchone()["c"]
    assert demoted == 2
    # story persisted, and re-running does not re-alert the same OEM set
    assert len(store.recent_stories()) == 1
    sent.clear()
    run_all(radar, {}, store, notifier, fetcher=None, force=True)
    assert not any("STORY" in p["embeds"][0].get("title", "") for p in sent)
    store.close()
