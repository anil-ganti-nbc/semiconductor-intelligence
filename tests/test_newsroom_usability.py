"""Focused acceptance tests for the 3.3.8 newsroom usability bridge."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from semi_intel.domain.enums import ClaimStatus
from semi_intel.domain.models import (
    Claim,
    Evidence,
    Notification,
    SignalCandidate,
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "newsroom-usability.db"
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{db_file}")
    from semi_intel.web.app import create_app

    with TestClient(create_app(mutation_authorizer=lambda _value: True)) as api:
        yield api


def _seed_candidate(client: TestClient) -> int:
    # Reuse the existing deterministic, network-free fixture builder.
    from tests.test_web_radar import _seed_candidate_via_client

    return _seed_candidate_via_client(client)


def _session():
    import os

    from semi_intel.db import get_engine, get_sessionmaker

    return get_sessionmaker(get_engine(os.environ["SEMI_INTEL_DB_URL"]))()


def test_candidate_detail_exposes_report_provenance(client):
    candidate_id = _seed_candidate(client)
    detail = client.get(f"/api/radar/candidates/{candidate_id}").json()

    report = detail["timeline"][0]
    assert report["source_id"]
    assert report["source"] == "VideoCardz"
    assert report["topics"][0]["name"] == "RTX 50 Super"
    assert report["independence_groups"]
    assert report["origin_evidence_id"] is None
    assert detail["why_interesting"]


def test_signal_report_to_evidence_is_idempotent(client):
    candidate_id = _seed_candidate(client)
    report_id = client.get(f"/api/radar/candidates/{candidate_id}").json()["timeline"][0]["signal_item_id"]

    first = client.post(f"/api/radar/items/{report_id}/evidence", json={"candidate_id": candidate_id})
    second = client.post(f"/api/radar/items/{report_id}/evidence", json={"candidate_id": candidate_id})

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["evidence"]["id"] == first.json()["evidence"]["id"]
    assert second.json()["evidence"]["radar_candidate_ids"] == [candidate_id]
    with _session() as session:
        assert len(list(session.scalars(select(Evidence)))) == 1


def test_create_claim_from_report_and_link_evidence_without_resolving(client):
    candidate_id = _seed_candidate(client)
    report_id = client.get(f"/api/radar/candidates/{candidate_id}").json()["timeline"][0]["signal_item_id"]
    response = client.post(
        f"/api/radar/candidates/{candidate_id}/claims",
        json={
            "statement": "RTX 50 Super has 24 GB of VRAM.",
            "signal_item_id": report_id,
            "stance": "supports",
            "note": "Leaker report",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["claim"]["status"] == "open"
    assert body["evidence"]["origin_signal_item_id"] == report_id
    assert body["link"]["stance"] == "supports"
    detail = client.get(f"/api/claims/{body['claim']['id']}").json()
    assert detail["evidence_links"][0]["evidence"]["radar_candidate_ids"] == [candidate_id]
    with _session() as session:
        assert session.get(Claim, body["claim"]["id"]).status == ClaimStatus.OPEN


def test_candidate_claim_rejects_report_from_another_candidate(client):
    candidate_id = _seed_candidate(client)
    response = client.post(
        f"/api/radar/candidates/{candidate_id}/claims",
        json={"statement": "A sufficiently long claim", "signal_item_id": 999999},
    )
    assert response.status_code == 404


def test_update_and_remove_evidence_link_preserves_records(client):
    candidate_id = _seed_candidate(client)
    report_id = client.get(f"/api/radar/candidates/{candidate_id}").json()["timeline"][0]["signal_item_id"]
    created = client.post(
        f"/api/radar/candidates/{candidate_id}/claims",
        json={"statement": "RTX 50 Super memory claim", "signal_item_id": report_id},
    ).json()
    claim_id = created["claim"]["id"]
    evidence_id = created["evidence"]["id"]

    changed = client.put(
        f"/api/claims/{claim_id}/evidence/{evidence_id}",
        json={"evidence_id": evidence_id, "stance": "contradicts", "note": "Later correction"},
    )
    assert changed.status_code == 200
    assert changed.json()["stance"] == "contradicts"
    removed = client.delete(f"/api/claims/{claim_id}/evidence/{evidence_id}")
    assert removed.status_code == 204
    assert client.get(f"/api/claims/{claim_id}").json()["evidence_links"] == []
    assert client.get(f"/api/evidence/{evidence_id}").status_code == 200


def test_claim_search_and_status_filters(client):
    client.post("/api/claims", json={"statement": "Intel 14A has a 20 nm metal pitch"})
    client.post("/api/claims", json={"statement": "Unrelated packaging claim"})
    rows = client.get("/api/claims", params={"status": "open", "q": "20 NM"}).json()
    assert [row["statement"] for row in rows] == ["Intel 14A has a 20 nm metal pitch"]


def test_manual_below_threshold_promotion_keeps_automation_disabled(client):
    candidate_id = _seed_candidate(client)
    response = client.post(
        f"/api/radar/candidates/{candidate_id}/promote",
        json={"by": "human:test", "headline": "Editor-approved RTX 50 Super report"},
    )
    assert response.status_code == 200, response.text
    story_id = response.json()["story_id"]
    assert client.get(f"/api/editorial/stories/{story_id}").json()["headline"] == "Editor-approved RTX 50 Super report"

    repeated = client.post(
        f"/api/radar/candidates/{candidate_id}/promote",
        json={"by": "human:test", "headline": "Editor-approved RTX 50 Super report"},
    )
    assert repeated.json()["story_id"] == story_id
    assert client.get("/api/radar/settings").json()["automatic_promotion_enabled"] is False
    with _session() as session:
        assert len(list(session.scalars(select(Notification)))) == 0
        assert session.get(SignalCandidate, candidate_id).promoted_story_id == story_id


def test_dashboard_contains_accessible_candidate_and_unified_workflows():
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    assert 'data-tab="claims">Claims &amp; Evidence' in html
    assert "Radar candidates awaiting editorial review" in html
    assert "View ${c.item_count} report" in html
    assert 'role="button" tabindex="0"' in html
    assert "onkeydown=\"if(event.key==='Enter'||event.key===' ')" in html
    assert 'rel="noopener noreferrer"' in html
    assert "Reports that created this candidate" in html
    assert "Create claim from this report" in html
    assert "Use as evidence" in html
    assert "Promote to Editorial Inbox" in html
    assert "No stories have been promoted yet" in html
    assert "Loading the reports behind this candidate" in html
    assert "Could not load candidate details" in html
