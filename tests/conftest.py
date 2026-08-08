from __future__ import annotations

import pytest

from semi_intel.db import get_engine, get_sessionmaker, init_db


@pytest.fixture()
def db_session(tmp_path):
    """A fresh sqlite file per test -- avoids in-memory sqlite's per-connection
    pooling quirks while staying fast."""
    db_file = tmp_path / "test.db"
    engine = get_engine(f"sqlite:///{db_file}")
    init_db(engine)
    session = get_sessionmaker(engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    """Points the CLI (which reads SEMI_INTEL_DB_URL per-invocation) at a
    scratch sqlite file for the duration of the test."""
    db_file = tmp_path / "cli_test.db"
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{db_file}")
    return db_file
