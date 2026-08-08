"""Regression tests for a real bug hit in live use: `get_session()` used to
build a brand-new SQLAlchemy engine (and re-run schema reflection) on every
single request, which under the dashboard's own concurrent page-load bursts
produced `sqlite3.OperationalError: database is locked` across many routes.
`create_app()` now overrides the dependency with one engine reused for the
app's lifetime.
"""
from __future__ import annotations

import concurrent.futures

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{tmp_path / 'reuse.db'}")
    from semi_intel.web.app import create_app
    with TestClient(create_app()) as client:
        yield client


def test_get_session_dependency_is_overridden_with_one_reused_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{tmp_path / 'reuse2.db'}")
    from semi_intel.web.app import create_app, get_session

    app = create_app()
    override = app.dependency_overrides[get_session]
    assert override is not get_session, (
        "create_app() must override get_session so requests share one engine "
        "instead of each building a fresh one (the actual live bug)."
    )

    def bound_engine():
        gen = override()
        session = next(gen)
        engine = session.get_bind()
        session.close()
        gen.close()
        return engine

    first = bound_engine()
    second = bound_engine()
    assert first is second, "each request must reuse the same engine, not create a new one"


def test_concurrent_requests_to_a_previously_crashing_route_all_succeed(client):
    """GET /api/operations/scheduler is the exact route that raised
    'UNIQUE constraint failed: scheduler_settings.id' in live use when hit
    concurrently on a brand-new database."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(
            lambda _: client.get("/api/operations/scheduler"), range(12)
        ))
    statuses = [r.status_code for r in responses]
    assert statuses == [200] * 12, statuses


def test_concurrent_dashboard_style_page_load_all_succeed(client):
    """Mirrors the real dashboard's own concurrent Promise.all() burst on
    page load across several tabs' worth of endpoints."""
    routes = [
        "/api/operations/scheduler", "/api/operations/backups",
        "/api/operations/health", "/api/operations/jobs?limit=30",
        "/api/notifications/status", "/api/notifications/saved-views",
        "/api/topics", "/api/radar/status",
    ] * 3
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(routes)) as pool:
        responses = list(pool.map(client.get, routes))
    statuses = [r.status_code for r in responses]
    assert statuses == [200] * len(routes), list(zip(routes, statuses))
