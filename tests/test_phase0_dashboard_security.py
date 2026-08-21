from fastapi.testclient import TestClient


def test_unauthenticated_mutation_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{tmp_path / 'security.db'}")
    monkeypatch.delenv("SEMINTEL_TEST_ALLOW_UNAUTH_MUTATIONS", raising=False)
    monkeypatch.delenv("SEMINTEL_DASHBOARD_AUTH_TOKEN", raising=False)
    from semi_intel.web.app import create_app

    with TestClient(create_app()) as client:
        assert client.post("/api/topics", json={}).status_code == 403


def test_authenticated_mutation_reaches_route_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{tmp_path / 'security-auth.db'}")
    monkeypatch.delenv("SEMINTEL_TEST_ALLOW_UNAUTH_MUTATIONS", raising=False)
    monkeypatch.setenv("SEMINTEL_DASHBOARD_AUTH_TOKEN", "sentinel-token")
    from semi_intel.web.app import create_app

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/topics", json={}, headers={"Authorization": "Bearer sentinel-token"}
        )
    assert response.status_code == 422
