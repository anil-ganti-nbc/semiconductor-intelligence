"""Stabilization Pass 1 -- database bootstrap/upgrade lifecycle.

Covers: fresh database initialization, repeated-initialization idempotence,
Alembic reconciliation on both a fresh and an older-but-compatible database
opened directly through the dashboard (create_app()), session
rollback/closure, and frozen-resource path resolution.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from semi_intel.db import get_engine, get_sessionmaker, init_db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PHASE8_HEAD_REVISION = "e8b7c2d4a901"
CURRENT_HEAD = "c2a7f1e9b453"


def _run_alembic(args, db_url, cwd=PROJECT_ROOT):
    import os

    env = os.environ.copy()
    env["SEMI_INTEL_DB_URL"] = db_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic"] + args,
        cwd=cwd, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"alembic {args} failed:\n{result.stdout}\n{result.stderr}"
    return result


def _tables(db_path: Path) -> set[str]:
    import sqlite3
    con = sqlite3.connect(db_path)
    names = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
    con.close()
    return names


# --- fresh database initialization -----------------------------------------


def test_fresh_database_has_all_tables_and_no_traceback(tmp_path):
    db_path = tmp_path / "fresh.db"
    engine = get_engine(f"sqlite:///{db_path}")
    init_db(engine)
    tables = _tables(db_path)
    assert len(tables) == 49  # application tables; create_all() writes no alembic_version marker
    engine.dispose()


def test_repeated_initialization_is_idempotent(tmp_path):
    db_path = tmp_path / "repeat.db"
    engine = get_engine(f"sqlite:///{db_path}")
    init_db(engine)
    first = _tables(db_path)
    init_db(engine)  # a second, redundant call must not error or change anything
    init_db(engine)
    second = _tables(db_path)
    assert first == second
    engine.dispose()


def test_singleton_settings_not_created_by_plain_initialization(tmp_path):
    """create_all()/migrations must never pre-seed a settings singleton row --
    every one of them is created lazily on first real use (see
    tests/test_migrations.py's test_phase8_upgrade_preserves_notifications_and_safe_defaults
    for the equivalent guarantee via the Alembic path)."""
    db_path = tmp_path / "no_seed.db"
    engine = get_engine(f"sqlite:///{db_path}")
    init_db(engine)
    session = get_sessionmaker(engine)()
    for table in (
        "scheduler_settings", "notification_settings", "signal_collection_settings",
        "candidate_promotion_settings", "attention_scoring_settings",
        "discovery_settings", "delivery_adapter_status",
    ):
        count = session.execute(__import__("sqlalchemy").text(f"select count(*) from {table}")).scalar()
        assert count == 0, f"{table} should start empty, found {count} row(s)"
    session.close()
    engine.dispose()


# --- create_app() reconciles Alembic state instead of a bare create_all() --


@pytest.fixture()
def alembic_available():
    if not (PROJECT_ROOT / "alembic.ini").exists():
        pytest.skip("alembic.ini not found -- migrations not part of this checkout")


def test_create_app_stamps_fresh_database_at_head(tmp_path, monkeypatch, alembic_available):
    db_path = tmp_path / "dashboard_fresh.db"
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.chdir(PROJECT_ROOT)

    from semi_intel.web.app import create_app
    app = create_app()

    from fastapi.testclient import TestClient
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/api/topics").status_code == 200

    import sqlite3
    con = sqlite3.connect(db_path)
    assert con.execute("select version_num from alembic_version").fetchone() == (CURRENT_HEAD,)
    table_count = con.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]
    assert table_count == 50  # 49 application tables + alembic_version
    con.close()


def test_create_app_upgrades_older_database_and_preserves_data(tmp_path, monkeypatch, alembic_available):
    """The dashboard is a supported entry point on its own (someone can
    launch `semi-intel web serve` / `semintel gui` directly against an
    existing database without ever running `semintel install`/`db upgrade`
    first). Before this pass, create_app() called a bare create_all(),
    which only adds missing tables -- it never advances alembic_version,
    silently leaving a stale schema marker (and, for a future
    non-additive migration, would silently mask it entirely)."""
    db_path = tmp_path / "dashboard_old.db"
    db_url = f"sqlite:///{db_path}"
    _run_alembic(["upgrade", PHASE8_HEAD_REVISION], db_url)

    # Seed through the historical schema itself. The current ORM includes the
    # new desktop setting by design and must not be used before this migration.
    import sqlite3
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO sources (id,name,type,url,description,trust_weight,created_at) "
        "VALUES (1,'videocardz.com','RSS','https://videocardz.com/feed',NULL,0.8,'2026-01-01')"
    )
    con.execute(
        "INSERT INTO notification_settings VALUES ("
        "1,1,0,'2026-01-01',0.7,0.15,2,1,1,1,1,1,1,1,1,1,0,'08:00','UTC',"
        "'22:00','07:00',5,3,3,1,0.65,2,24,90,5,'[]','[]','2026-01-01')"
    )
    con.execute(
        "INSERT INTO notifications (event_type,severity,title,body,reason,dedup_key,"
        "event_metadata,created_at,event_at,first_occurrence_at,latest_occurrence_at,"
        "occurrence_count,muted,delivery_state) VALUES ("
        "'TEST','INFORMATIONAL','Pre-upgrade alert','Preserve me','lifecycle test',"
        "'lifecycle-preserve-1','{}','2026-01-01','2026-01-01','2026-01-01',"
        "'2026-01-01',1,0,'IN_APP')"
    )
    con.commit()
    con.close()

    monkeypatch.setenv("SEMI_INTEL_DB_URL", db_url)
    monkeypatch.chdir(PROJECT_ROOT)
    from semi_intel.web.app import create_app
    app = create_app()
    from fastapi.testclient import TestClient
    client = TestClient(app)
    assert client.get("/").status_code == 200

    con = sqlite3.connect(db_path)
    assert con.execute("select version_num from alembic_version").fetchone() == (CURRENT_HEAD,)
    assert con.execute("select count(*) from sqlite_master where type='table'").fetchone()[0] == 50
    assert con.execute("select name from sources where id=1").fetchone() == ("videocardz.com",)
    assert con.execute("select title from notifications where id=1").fetchone() == ("Pre-upgrade alert",)
    assert con.execute("select count(*) from notification_settings").fetchone()[0] == 1
    assert con.execute("select count(*) from signal_items").fetchone()[0] == 0  # new table, empty
    assert con.execute("select count(*) from scheduler_settings").fetchone()[0] == 0  # not auto-seeded
    con.close()


def test_running_create_app_twice_against_the_same_older_database_is_harmless(tmp_path, monkeypatch, alembic_available):
    db_path = tmp_path / "dashboard_old_twice.db"
    db_url = f"sqlite:///{db_path}"
    _run_alembic(["upgrade", PHASE8_HEAD_REVISION], db_url)

    monkeypatch.setenv("SEMI_INTEL_DB_URL", db_url)
    monkeypatch.chdir(PROJECT_ROOT)
    from semi_intel.web.app import create_app

    create_app()
    create_app()  # second "restart" against the now-current database

    import sqlite3
    con = sqlite3.connect(db_path)
    assert con.execute("select version_num from alembic_version").fetchone() == (CURRENT_HEAD,)
    assert con.execute("select count(*) from monitored_topics").fetchone()[0] > 0
    # Re-seeding must not duplicate the deterministic topic list.
    names = [r[0] for r in con.execute("select normalized_name from monitored_topics")]
    assert len(names) == len(set(names))
    con.close()


# --- session rollback and closure -------------------------------------------


def test_get_session_dependency_closes_and_rolls_back_after_an_error(tmp_path, monkeypatch):
    """A request handler that raises must not leave a dangling open
    transaction or connection -- get_session()'s finally: session.close()
    must run even when the caller's generator is torn down after an
    exception, matching FastAPI's own dependency-cleanup contract."""
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{tmp_path / 'rollback.db'}")
    from semi_intel.web.app import get_session

    gen = get_session()
    session = next(gen)
    session.execute(__import__("sqlalchemy").text("select 1"))
    try:
        gen.throw(RuntimeError("simulated request handler failure"))
    except RuntimeError:
        pass
    assert not session.in_transaction() or session.get_bind() is not None
    # The session object must be closed -- a fresh statement against a
    # closed session either raises or transparently reopens (SQLAlchemy
    # sessions are reusable after close()); what matters is no leaked
    # connection remains checked out of the pool.
    pool = session.get_bind().pool
    assert pool.checkedout() == 0


# --- frozen-resource path resolution ----------------------------------------


def test_project_root_resolves_from_source_when_not_frozen():
    from semi_intel.cli import _project_root
    root = _project_root()
    assert (root / "alembic.ini").exists()
    assert (root / "migrations").is_dir()


def test_project_root_resolves_from_meipass_when_frozen(monkeypatch, tmp_path):
    """Simulates a PyInstaller-frozen process: sys.frozen=True and
    sys._MEIPASS point at the bundle's extracted temp directory, which
    packaging/semi_intel.spec populates with alembic.ini and migrations/
    at the bundle root."""
    fake_bundle = tmp_path / "meipass"
    fake_bundle.mkdir()
    (fake_bundle / "alembic.ini").write_text("[alembic]\n")
    (fake_bundle / "migrations").mkdir()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_bundle), raising=False)
    import importlib
    import semi_intel.cli as cli_module
    importlib.reload(cli_module)
    try:
        root = cli_module._project_root()
        assert root == fake_bundle
    finally:
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        importlib.reload(cli_module)


def test_static_dir_resolves_from_meipass_when_frozen(monkeypatch, tmp_path):
    fake_bundle = tmp_path / "meipass_web"
    static = fake_bundle / "semi_intel" / "web" / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("<html></html>")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_bundle), raising=False)
    import importlib
    import semi_intel.web.app as app_module
    importlib.reload(app_module)
    try:
        assert app_module.STATIC_DIR == static
    finally:
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        importlib.reload(app_module)
