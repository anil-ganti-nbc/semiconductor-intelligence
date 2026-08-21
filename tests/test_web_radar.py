"""Web API tests for the Signal Radar endpoints (brief section 24 "GUI/API
tests"). Uses FastAPI's TestClient; RSS validation is monkeypatched so this
suite never touches the network."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from semi_intel.db import get_engine, get_sessionmaker, init_db
from semi_intel.domain.enums import SourceType
from semi_intel.domain.models import Source, SignalCandidate, SignalItem
from semi_intel.editorial.service import TopicService
from semi_intel.signals.analysis import analyze_signal_item
from semi_intel.signals.clustering import cluster_unclustered_items
from semi_intel.signals.scoring import rescore_active_candidates

BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "web_test.db"
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{db_file}")
    from semi_intel.web.app import create_app
    with TestClient(create_app(mutation_authorizer=lambda _value: True)) as client:
        yield client


@pytest.fixture()
def session(tmp_path, monkeypatch):
    db_file = tmp_path / "session_side.db"
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{db_file}")
    engine = get_engine(f"sqlite:///{db_file}")
    init_db(engine)
    s = get_sessionmaker(engine)()
    yield s
    s.close()


def _seed_candidate_via_client(client):
    """Uses the client's own DB (same env var) via a side session to seed a
    real candidate, then returns its id."""
    from semi_intel.db import get_engine as _get_engine, get_sessionmaker as _get_sessionmaker
    import os
    engine = _get_engine(os.environ["SEMI_INTEL_DB_URL"])
    s = _get_sessionmaker(engine)()
    TopicService(s).seed()
    s.commit()
    source = Source(name="VideoCardz", type=SourceType.SOCIAL, provider="rss")
    s.add(source)
    s.commit()
    item = SignalItem(
        source_id=source.id, provider="rss", external_id="1", raw_payload="{}",
        normalized_text="RTX 50 Super leak: 24GB VRAM confirmed.", content_hash="h1", posted_at=BASE,
    )
    s.add(item)
    s.commit()
    analyze_signal_item(s, item)
    s.commit()
    cluster_unclustered_items(s)
    s.commit()
    rescore_active_candidates(s)
    candidate_id = s.scalars(select(SignalCandidate)).first().id
    s.close()
    return candidate_id


def test_radar_status_on_clean_db(client):
    r = client.get("/api/radar/status")
    assert r.status_code == 200
    body = r.json()
    assert body["collection_enabled"] is False
    assert body["x_provider_enabled"] is False
    assert body["automatic_promotion_enabled"] is False


def test_add_x_source_via_handle(client):
    r = client.post("/api/radar/sources", json={"handle_or_url": "@IanCutress"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["provider"] == "x"
    assert body["provider_key"] == "IanCutress"
    assert body["already_existed"] is False


def test_add_source_is_idempotent(client):
    first = client.post("/api/radar/sources", json={"handle_or_url": "@IanCutress"})
    second = client.post("/api/radar/sources", json={"handle_or_url": "@IanCutress"})
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["already_existed"] is True


def test_add_source_rejects_empty_handle(client):
    r = client.post("/api/radar/sources", json={"handle_or_url": "   "})
    assert r.status_code == 422


def test_add_rss_source_validates_via_injected_fetch(client, monkeypatch):
    import feedparser
    import semi_intel.signals.providers.rss as rss_module

    with open("tests/fixtures/sample_feed.xml", "rb") as f:
        content = f.read()
    monkeypatch.setattr(rss_module, "_default_fetch", lambda url: feedparser.parse(content))

    r = client.post("/api/radar/sources", json={"handle_or_url": "https://example.com/feed"})
    assert r.status_code == 201, r.text
    assert r.json()["provider"] == "rss"


def test_add_rss_source_hostname_containing_x_dot_com_is_not_misdetected_as_x(client, monkeypatch):
    """Regression test: _detect_provider() previously used a naive substring
    check ("x.com/" in low), which false-matched any domain merely
    containing that substring -- e.g. phoronix.com/rss.php contains
    "x.com/" inside "phoroni-x.com/", so it was silently misclassified as
    an X/social source instead of RSS."""
    import feedparser
    import semi_intel.signals.providers.rss as rss_module

    with open("tests/fixtures/sample_feed.xml", "rb") as f:
        content = f.read()
    monkeypatch.setattr(rss_module, "_default_fetch", lambda url: feedparser.parse(content))

    r = client.post("/api/radar/sources", json={"handle_or_url": "https://www.phoronix.com/rss.php"})
    assert r.status_code == 201, r.text
    assert r.json()["provider"] == "rss"


def test_radar_source_add_reuses_legacy_feed_with_normalized_url(client, monkeypatch):
    import feedparser
    import semi_intel.signals.providers.rss as rss_module

    with open("tests/fixtures/sample_feed.xml", "rb") as f:
        content = f.read()
    monkeypatch.setattr(rss_module, "_default_fetch", lambda url: feedparser.parse(content))

    legacy = client.post(
        "/api/sources",
        json={
            "name": "Existing Feed",
            "type": "rss",
            "url": "http://www.example.com/feed/?utm_source=legacy",
            "trust_weight": 0.7,
        },
    )
    assert legacy.status_code == 201, legacy.text

    radar = client.post(
        "/api/radar/sources",
        json={"handle_or_url": "https://example.com/feed"},
    )
    assert radar.status_code == 201, radar.text
    assert radar.json()["id"] == legacy.json()["id"]
    assert radar.json()["already_existed"] is True
    assert radar.json()["provider"] == "manual"


def test_legacy_source_add_rejects_existing_radar_feed(client, monkeypatch):
    import feedparser
    import semi_intel.signals.providers.rss as rss_module

    with open("tests/fixtures/sample_feed.xml", "rb") as f:
        content = f.read()
    monkeypatch.setattr(rss_module, "_default_fetch", lambda url: feedparser.parse(content))

    radar = client.post(
        "/api/radar/sources",
        json={"handle_or_url": "https://example.com/feed/"},
    )
    assert radar.status_code == 201, radar.text

    legacy = client.post(
        "/api/sources",
        json={
            "name": "Duplicate Feed",
            "type": "rss",
            "url": "http://www.example.com/feed?utm_campaign=duplicate",
            "trust_weight": 0.7,
        },
    )
    assert legacy.status_code == 400
    assert "already registered" in legacy.json()["detail"]


def test_candidates_list_and_detail(client):
    candidate_id = _seed_candidate_via_client(client)

    r = client.get("/api/radar/candidates?state=all&age=all")
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()]
    assert candidate_id in ids

    r = client.get(f"/api/radar/candidates/{candidate_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["id"] == candidate_id
    assert len(detail["timeline"]) == 1
    assert "score_explanation" in detail
    assert "components" in detail["score_explanation"]
    assert "automatic_promotion_eligibility" in detail


def test_candidate_detail_404(client):
    r = client.get("/api/radar/candidates/9999")
    assert r.status_code == 404


def test_mark_candidate_seen_and_unseen(client):
    candidate_id = _seed_candidate_via_client(client)

    r = client.post("/api/radar/candidates/seen", json={"candidate_ids": [candidate_id], "seen": True})
    assert r.status_code == 200
    assert r.json()[0]["seen"] is True

    r = client.post("/api/radar/candidates/seen", json={"candidate_ids": [candidate_id], "seen": False})
    assert r.json()[0]["seen"] is False


def test_dismiss_and_restore_candidate(client):
    candidate_id = _seed_candidate_via_client(client)

    r = client.post(f"/api/radar/candidates/{candidate_id}/dismiss", json={"reason": "not relevant"})
    assert r.status_code == 200
    assert r.json()["state"] == "dismissed"
    assert r.json()["dismissed_reason"] == "not relevant"

    r = client.post(f"/api/radar/candidates/{candidate_id}/restore")
    assert r.json()["state"] == "active"


def test_snooze_candidate(client):
    candidate_id = _seed_candidate_via_client(client)
    until = (dt.datetime.utcnow() + dt.timedelta(days=3)).isoformat()

    r = client.post(f"/api/radar/candidates/{candidate_id}/snooze", json={"until": until})
    assert r.status_code == 200
    assert r.json()["state"] == "snoozed"


def test_promote_candidate_via_api(client):
    candidate_id = _seed_candidate_via_client(client)

    r = client.post(f"/api/radar/candidates/{candidate_id}/promote", json={"by": "human:tester"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["candidate"]["state"] == "promoted"
    assert body["story_id"] is not None

    # idempotent re-promote
    r2 = client.post(f"/api/radar/candidates/{candidate_id}/promote", json={"by": "human:tester2"})
    assert r2.json()["story_id"] == body["story_id"]


def test_promote_unknown_candidate_404(client):
    r = client.post("/api/radar/candidates/9999/promote", json={"by": "human:tester"})
    assert r.status_code == 404


def test_radar_settings_roundtrip(client):
    r = client.get("/api/radar/settings")
    assert r.status_code == 200
    settings = r.json()
    settings["automatic_promotion_enabled"] = True
    settings["minimum_attention_score"] = 0.9

    r = client.put("/api/radar/settings", json=settings)
    assert r.status_code == 200
    assert r.json()["automatic_promotion_enabled"] is True
    assert r.json()["minimum_attention_score"] == 0.9

    # persists across a fresh GET
    r2 = client.get("/api/radar/settings")
    assert r2.json()["automatic_promotion_enabled"] is True


def test_radar_cluster_endpoint(client):
    _seed_candidate_via_client(client)  # already clusters as part of seeding
    r = client.post("/api/radar/cluster")
    assert r.status_code == 200
    body = r.json()
    assert "attached_to_existing" in body
    assert "new_candidates" in body


def test_candidate_intelligence_endpoint_returns_structured_payload(client):
    """v1.0.0 Candidate Intelligence: the consolidated /intelligence
    endpoint must return every documented section, not an opaque blob,
    and must persist confidence/editorial_value/timeline_stage onto the
    candidate row."""
    candidate_id = _seed_candidate_via_client(client)

    r = client.get(f"/api/radar/candidates/{candidate_id}/intelligence")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in (
        "origin_graph", "claims", "novelty", "contradictions",
        "confidence", "editorial_value", "verification_checklist", "timeline_stage",
    ):
        assert key in body
    assert body["origin_graph"]["origin"] is not None
    assert 0 <= body["confidence"]["score"] <= 100
    assert 0 <= body["editorial_value"]["score"] <= 100
    assert body["timeline_stage"]["stage"] in (
        "rumor", "emerging", "corroborated", "pre_launch",
        "confirmed", "released", "corrected", "disproven",
    )

    detail = client.get(f"/api/radar/candidates/{candidate_id}").json()
    assert detail["confidence_score"] == body["confidence"]["score"]
    assert detail["editorial_value_score"] == body["editorial_value"]["score"]
    assert detail["timeline_stage"] == body["timeline_stage"]["stage"]


def test_candidate_intelligence_endpoint_404_for_unknown_candidate(client):
    r = client.get("/api/radar/candidates/9999/intelligence")
    assert r.status_code == 404


def test_source_reputation_recompute_and_override_workflow(client):
    _seed_candidate_via_client(client)
    sources = client.get("/api/sources").json()
    source_id = sources[0]["id"]

    r = client.post("/api/radar/source-reputations/recompute")
    assert r.status_code == 200, r.text
    assert r.json()["sources_recomputed"] >= 1

    reps = client.get("/api/radar/source-reputations").json()
    assert any(rep["source_id"] == source_id for rep in reps)

    override = client.put(f"/api/radar/source-reputations/{source_id}/override", json={"authority_override": 0.9})
    assert override.status_code == 200, override.text
    assert override.json()["authority_override"] == 0.9

    reps_after = client.get("/api/radar/source-reputations").json()
    updated = next(rep for rep in reps_after if rep["source_id"] == source_id)
    assert updated["authority_override"] == 0.9
    assert updated["effective_authority"] == 0.9


def test_source_suggestions_review_workflow(client):
    r = client.get("/api/radar/source-suggestions")
    assert r.status_code == 200
    assert r.json() == []

    r = client.post("/api/radar/source-suggestions/refresh")
    assert r.status_code == 200
