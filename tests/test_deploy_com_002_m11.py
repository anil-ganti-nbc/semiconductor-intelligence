"""M11 regressions for the Alembic persistent-state compatibility barrier."""

from __future__ import annotations

import sqlite3

import pytest

from semi_intel.cli import _session, upgrade_or_stamp_to_head
from semi_intel.db import get_engine, init_db
from semi_intel.schema_guard import SchemaCompatibilityError, inspect_schema


def _set_db(monkeypatch, path):
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{path}")


def test_normal_session_refuses_missing_schema_without_creating_state(tmp_path, monkeypatch):
    db_path = tmp_path / "missing.db"
    _set_db(monkeypatch, db_path)

    with pytest.raises(SchemaCompatibilityError, match="missing"):
        _session()

    assert not db_path.exists()


def test_create_all_database_is_not_stamped_or_admitted(tmp_path, monkeypatch):
    db_path = tmp_path / "create-all.db"
    _set_db(monkeypatch, db_path)
    engine = get_engine()
    init_db(engine)
    engine.dispose()

    with pytest.raises(Exception, match="already exists"):
        upgrade_or_stamp_to_head()
    with pytest.raises(SchemaCompatibilityError, match="observed Alembic head none"):
        _session()

    with sqlite3.connect(db_path) as connection:
        # Alembic may create its tracking table before the first migration
        # fails; the important invariant is that no head is recorded.
        assert connection.execute("select version_num from alembic_version").fetchone() is None


def test_older_and_newer_heads_fail_closed(tmp_path, monkeypatch):
    db_path = tmp_path / "skewed.db"
    _set_db(monkeypatch, db_path)
    upgrade_or_stamp_to_head()

    with sqlite3.connect(db_path) as connection:
        connection.execute("update alembic_version set version_num = ?", ("bf599f950d56",))
        connection.commit()
    status = inspect_schema(get_engine())
    assert status.ready is False
    assert "expected c7d8e9f0a1b2" in (status.reason or "")
    with pytest.raises(SchemaCompatibilityError, match="incompatible"):
        _session()

    with sqlite3.connect(db_path) as connection:
        connection.execute("update alembic_version set version_num = ?", ("future-head",))
        connection.commit()
    with pytest.raises(SchemaCompatibilityError, match="incompatible"):
        _session()


def test_exact_head_allows_normal_session(tmp_path, monkeypatch):
    db_path = tmp_path / "ready.db"
    _set_db(monkeypatch, db_path)
    upgrade_or_stamp_to_head()

    session = _session()
    try:
        assert inspect_schema(session.get_bind()).ready is True
    finally:
        session.close()


def test_runtime_health_does_not_bootstrap_missing_database(tmp_path, monkeypatch):
    db_path = tmp_path / "health-missing.db"
    _set_db(monkeypatch, db_path)

    from semi_intel.runtime_bridge import get_health

    payload = get_health()
    if isinstance(payload, dict):
        assert payload["application_readiness"] is False
        assert any("schema compatibility" in reason for reason in payload["status_reasons"])
    assert not db_path.exists()


def test_gate_refusal_is_distinguishable_from_an_ordinary_query_failure(tmp_path, monkeypatch):
    """A barrier refusal must not read as a broken query.

    STD-DEPLOY-COM-002 asks for evidence sufficient to identify compatibility
    gating as the reason work was refused. get_health() caught
    SchemaCompatibilityError in its blanket handler and reported "database
    query failed", so an operator could not tell a state refused by contract
    from a database that was simply broken -- both fail closed, but only one
    is fixed by running the Alembic upgrade.
    """
    db_path = tmp_path / "gate.db"
    _set_db(monkeypatch, db_path)

    from semi_intel.runtime_bridge import get_health

    payload = get_health()
    if not isinstance(payload, dict):
        pytest.skip("runtime HealthPayload contract in use; dict shape not returned")
    reasons = payload["status_reasons"]

    gate = [r for r in reasons if "schema compatibility gate refused" in r]
    assert gate, f"no reason identifies the compatibility gate: {reasons}"
    # The specific cause survives alongside the category -- naming the gate
    # must not cost the operator the actionable detail.
    assert "run the explicit Alembic upgrade first" in gate[0]
    assert not any(r.startswith("database query failed") for r in reasons)
    assert payload["application_readiness"] is False
    assert not db_path.exists()
