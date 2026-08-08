"""CLI-level tests for `semi-intel db ...`. These exercise the in-process
Alembic wiring (alembic.command called directly from cli.py, not a separate
`alembic` executable) -- the thing that lets a frozen/standalone build of
this CLI manage its own schema without also needing a standalone `alembic`
binary. test_migrations.py already proves the migration itself is correct
(schema parity with create_all(), clean upgrade/downgrade round trip) by
shelling out to `python -m alembic`; these tests prove the CLI's own
`db upgrade`/`db downgrade`/`db stamp`/`db current` commands reach the same
Alembic config and produce the same result.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from semi_intel.cli import app

runner = CliRunner()
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def alembic_available():
    if not (PROJECT_ROOT / "alembic.ini").exists():
        pytest.skip("alembic.ini not found -- migrations not part of this checkout")


def _table_schema(db_path: Path) -> dict:
    con = sqlite3.connect(db_path)
    tables = {}
    for (name,) in con.execute(
        "select name from sqlite_master where type='table' and name != 'alembic_version'"
    ):
        cols = sorted((r[1], r[2], r[3]) for r in con.execute(f"pragma table_info({name})"))
        tables[name] = cols
    con.close()
    return tables


def test_db_upgrade_creates_full_schema(cli_env, alembic_available):
    r = runner.invoke(app, ["db", "upgrade"])
    assert r.exit_code == 0, r.output
    assert "Upgraded to head" in r.output

    assert len(_table_schema(cli_env)) == 49  # 41 through Phase 8 + 7 Phase 9 operational tables + 1 v1.0.0 source_reputations table


def test_db_upgrade_matches_create_all(cli_env, alembic_available):
    from semi_intel.db import get_engine, init_db

    create_all_db = cli_env.parent / "create_all_comparison.db"
    init_db(get_engine(f"sqlite:///{create_all_db}"))

    r = runner.invoke(app, ["db", "upgrade"])
    assert r.exit_code == 0, r.output

    assert _table_schema(create_all_db) == _table_schema(cli_env)


def test_db_downgrade_removes_everything(cli_env, alembic_available):
    runner.invoke(app, ["db", "upgrade"])
    assert len(_table_schema(cli_env)) == 49

    r = runner.invoke(app, ["db", "downgrade"])
    assert r.exit_code == 0, r.output
    assert "Downgraded to base" in r.output
    assert _table_schema(cli_env) == {}


def test_db_current_and_stamp(cli_env, alembic_available):
    r = runner.invoke(app, ["db", "current"])
    assert r.exit_code == 0, r.output  # no schema yet -- should not crash, just show nothing

    runner.invoke(app, ["db", "upgrade"])
    r = runner.invoke(app, ["db", "current"])
    assert r.exit_code == 0, r.output

    # simulate a database that was created with init-db and needs to switch
    # to Alembic-managed migrations: downgrade the tracking table only isn't
    # possible via stamp, so just prove stamp doesn't error against an
    # up-to-date database.
    r = runner.invoke(app, ["db", "stamp", "head"])
    assert r.exit_code == 0, r.output
    assert "Stamped head" in r.output
