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
