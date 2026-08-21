"""FastAPI TestClient tests for the read-only dashboard API. Uses the same
SEMI_INTEL_DB_URL env-var mechanism as the CLI (via the cli_env fixture) so
each test gets an isolated sqlite file, and get_session() in web/app.py picks
it up exactly the way the CLI's _session() does.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from typer.testing import CliRunner  # noqa: E402

from semi_intel.cli import app as cli_app  # noqa: E402


def _seed(cli_env):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])
    runner.invoke(cli_app, ["source", "add", "Golden Pig", "--type", "social", "--trust-weight", "0.7"])
    runner.invoke(cli_app, ["entity", "add", "Intel", "--type", "company"])
    runner.invoke(cli_app, ["entity", "add", "Nova Lake", "--type", "product"])
    runner.invoke(cli_app, ["entity", "relate", "2", "1", "--type", "manufactured_by"])
    runner.invoke(
        cli_app,
        ["evidence", "add", "1", "--title", "leak", "--content", "Nova Lake leak content", "--entity-id", "2"],
    )
    runner.invoke(cli_app, ["claim", "create", "Nova Lake uses 18A-P", "--subject-entity-id", "2"])
    runner.invoke(cli_app, ["claim", "link-evidence", "1", "1", "--stance", "supports"])
    runner.invoke(cli_app, ["claim", "resolve", "1", "--status", "confirmed"])


@pytest.fixture()
def client(cli_env):
    from semi_intel.web.app import create_app
    with TestClient(create_app(mutation_authorizer=lambda _value: True)) as c:
        yield c


def test_dashboard_root_serves_html(cli_env, client):
    _seed(cli_env)
    r = client.get("/")
    assert r.status_code == 200
    assert "Semiconductor Intelligence Platform" in r.text


def test_list_claims_endpoint(cli_env, client):
    _seed(cli_env)
    r = client.get("/api/claims")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["statement"] == "Nova Lake uses 18A-P"
    assert data[0]["status"] == "confirmed"


def test_get_claim_detail_includes_evidence_and_timeline(cli_env, client):
    _seed(cli_env)
    r = client.get("/api/claims/1")
    assert r.status_code == 200
    data = r.json()
    assert len(data["evidence_links"]) == 1
    assert data["evidence_links"][0]["evidence"]["title"] == "leak"
    assert any(e["event_type"] == "status_changed" for e in data["timeline"])


def test_get_claim_detail_404(cli_env, client):
    _seed(cli_env)
    r = client.get("/api/claims/999")
    assert r.status_code == 404


def test_list_evidence_endpoint(cli_env, client):
    _seed(cli_env)
    r = client.get("/api/evidence")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_source_rank_endpoint_reflects_resolved_claims(cli_env, client):
    _seed(cli_env)
    r = client.get("/api/sources/rank")
    assert r.status_code == 200
    data = r.json()
    assert data[0]["source_name"] == "Golden Pig"
    assert data[0]["overall"]["accuracy"] == 1.0
    assert data[0]["by_company"]["Intel"]["accuracy"] == 1.0


def test_stories_rank_endpoint_excludes_resolved_claims(cli_env, client):
    _seed(cli_env)
    r = client.get("/api/stories/rank")
    assert r.status_code == 200
    assert r.json() == []  # the only claim is already resolved (confirmed)


def test_entities_endpoint_and_detail_with_relationships(cli_env, client):
    _seed(cli_env)
    r = client.get("/api/entities")
    assert r.status_code == 200
    assert len(r.json()) == 2

    r = client.get("/api/entities/2")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Nova Lake"
    assert len(data["relationships"]) == 1
    assert data["relationships"][0]["other_entity_name"] == "Intel"


def test_graph_related_endpoint(cli_env, client):
    _seed(cli_env)
    r = client.get("/api/graph/related/2")
    assert r.status_code == 200
    names = {n["name"] for n in r.json()}
    assert "Intel" in names


def test_graph_related_404(cli_env, client):
    _seed(cli_env)
    r = client.get("/api/graph/related/999")
    assert r.status_code == 404


def test_graph_find_endpoint(cli_env, client):
    _seed(cli_env)
    r = client.get("/api/graph/find", params={"relation_type": "manufactured_by", "target": "Intel"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["from_entity_name"] == "Nova Lake"


# --- write endpoints ---------------------------------------------------------
# Each of these exercises the exact same repository call the CLI uses (see
# semi_intel/web/app.py's module docstring for the mapping) -- these tests
# aren't re-testing repository behavior (that's tests/test_repositories.py
# and friends), just confirming the HTTP wiring: status codes, validation
# errors surfacing as 400/404 instead of a raw 500, and that a write made
# over HTTP is visible to a subsequent read the same way a CLI write is.


def test_create_source_endpoint(cli_env, client):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])
    r = client.post(
        "/api/sources",
        json={"name": "New Source", "type": "rss", "url": "https://example.com/rss", "trust_weight": 0.6},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["name"] == "New Source"
    assert data["trust_weight"] == 0.6

    listed = client.get("/api/sources").json()
    assert any(s["name"] == "New Source" for s in listed)


def test_create_source_rejects_duplicate_name(cli_env, client):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])
    runner.invoke(cli_app, ["source", "add", "Golden Pig", "--type", "social"])

    r = client.post("/api/sources", json={"name": "Golden Pig", "type": "social"})
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]


def test_create_entity_endpoint(cli_env, client):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])
    r = client.post("/api/entities", json={"name": "Nova Lake", "type": "product"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "Nova Lake"


def test_create_entity_rejects_duplicate_name(cli_env, client):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])
    runner.invoke(cli_app, ["entity", "add", "Intel", "--type", "company"])

    r = client.post("/api/entities", json={"name": "Intel", "type": "company"})
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]


def test_create_evidence_endpoint(cli_env, client):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])
    runner.invoke(cli_app, ["source", "add", "Golden Pig", "--type", "social"])

    r = client.post(
        "/api/evidence",
        json={"source_id": 1, "title": "leak", "content": "Nova Lake leak content"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "leak"


def test_create_evidence_rejects_duplicate_content(cli_env, client):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])
    runner.invoke(cli_app, ["source", "add", "Golden Pig", "--type", "social"])
    runner.invoke(cli_app, ["evidence", "add", "1", "--title", "leak", "--content", "same content"])

    r = client.post("/api/evidence", json={"source_id": 1, "title": "leak again", "content": "same content"})
    assert r.status_code == 400
    assert "Duplicate" in r.json()["detail"]


def test_create_evidence_rejects_missing_source(cli_env, client):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])

    r = client.post("/api/evidence", json={"source_id": 999, "title": "leak", "content": "content"})
    assert r.status_code == 400
    assert "does not exist" in r.json()["detail"]


def test_create_claim_endpoint(cli_env, client):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])
    r = client.post("/api/claims", json={"statement": "Nova Lake uses 18A-P"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "open"


def test_create_claim_rejects_missing_subject_entity(cli_env, client):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])

    r = client.post("/api/claims", json={"statement": "x", "subject_entity_id": 999})
    assert r.status_code == 400
    assert "does not exist" in r.json()["detail"]


def test_link_evidence_endpoint_updates_confidence(cli_env, client):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])
    runner.invoke(cli_app, ["source", "add", "Golden Pig", "--type", "social"])
    runner.invoke(cli_app, ["evidence", "add", "1", "--title", "leak", "--content", "content"])
    runner.invoke(cli_app, ["claim", "create", "Nova Lake uses 18A-P"])

    r = client.post("/api/claims/1/link-evidence", json={"evidence_id": 1, "stance": "supports"})
    assert r.status_code == 200, r.text
    assert r.json()["confidence"] > 0.5


def test_link_evidence_404_for_missing_claim(cli_env, client):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])

    r = client.post("/api/claims/999/link-evidence", json={"evidence_id": 1, "stance": "supports"})
    assert r.status_code == 404


def test_resolve_claim_endpoint(cli_env, client):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])
    runner.invoke(cli_app, ["claim", "create", "Nova Lake uses 18A-P"])

    r = client.post("/api/claims/1/resolve", json={"status": "confirmed", "note": "checks out"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"
    assert r.json()["resolution_note"] == "checks out"


def test_resolve_claim_404(cli_env, client):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])

    r = client.post("/api/claims/999/resolve", json={"status": "confirmed"})
    assert r.status_code == 404


def _seed_open_claim_with_matching_evidence(cli_env):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])
    runner.invoke(cli_app, ["source", "add", "Golden Pig", "--type", "social"])
    runner.invoke(cli_app, ["entity", "add", "Nova Lake", "--type", "product"])
    runner.invoke(cli_app, ["claim", "create", "Nova Lake uses 18A-P", "--subject-entity-id", "1"])
    runner.invoke(
        cli_app,
        ["evidence", "add", "1", "--title", "leak", "--content", "Nova Lake spotted using the 18A-P node"],
    )


def test_suggestions_run_then_list(cli_env, client):
    _seed_open_claim_with_matching_evidence(cli_env)

    run_result = client.post("/api/suggestions/run", json={})
    assert run_result.status_code == 200, run_result.text
    assert run_result.json()["suggestions_created"] == 1

    pending = client.get("/api/suggestions", params={"status": "pending"}).json()
    assert len(pending) == 1
    assert pending[0]["claim_id"] == 1
    assert pending[0]["evidence_id"] == 1


def test_suggestion_accept_creates_link_and_updates_confidence(cli_env, client):
    _seed_open_claim_with_matching_evidence(cli_env)
    client.post("/api/suggestions/run", json={})

    r = client.post("/api/suggestions/1/accept", json={"stance": "supports"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"

    claim = client.get("/api/claims/1").json()
    assert len(claim["evidence_links"]) == 1
    assert claim["confidence"] > 0.5


def test_suggestion_accept_twice_returns_400(cli_env, client):
    _seed_open_claim_with_matching_evidence(cli_env)
    client.post("/api/suggestions/run", json={})
    client.post("/api/suggestions/1/accept", json={"stance": "supports"})

    r = client.post("/api/suggestions/1/accept", json={"stance": "supports"})
    assert r.status_code == 400
    assert "already" in r.json()["detail"]


def test_suggestion_reject(cli_env, client):
    _seed_open_claim_with_matching_evidence(cli_env)
    client.post("/api/suggestions/run", json={})

    r = client.post("/api/suggestions/1/reject", json={"note": "not relevant"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"


def test_suggestion_accept_404_for_missing_suggestion(cli_env, client):
    runner = CliRunner()
    runner.invoke(cli_app, ["init-db"])

    r = client.post("/api/suggestions/999/accept", json={"stance": "supports"})
    assert r.status_code == 404
