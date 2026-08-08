"""Verifies the Alembic setup actually matches the SQLAlchemy models:
`alembic upgrade head` against a fresh database should produce the exact
same tables/columns as `Base.metadata.create_all()`, and `alembic downgrade
base` should cleanly remove everything the migration added. This is the
test that would catch someone changing a model and forgetting to generate
(or committing a stale) migration.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run_alembic(args, db_url, cwd):
    # Preserve Windows' SystemRoot/WINDIR and networking provider variables.
    # A PATH-only environment can make importing asyncio/_overlapped fail
    # before Alembic even starts.
    env = os.environ.copy()
    env["SEMI_INTEL_DB_URL"] = db_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic"] + args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic {args} failed:\n{result.stdout}\n{result.stderr}"
    return result


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


@pytest.fixture()
def alembic_available():
    if not (PROJECT_ROOT / "alembic.ini").exists():
        pytest.skip("alembic.ini not found -- migrations not part of this checkout")


def test_alembic_upgrade_matches_create_all(tmp_path, alembic_available):
    from semi_intel.db import get_engine, init_db

    create_all_db = tmp_path / "create_all.db"
    init_db(get_engine(f"sqlite:///{create_all_db}"))

    alembic_db = tmp_path / "alembic_head.db"
    _run_alembic(["upgrade", "head"], f"sqlite:///{alembic_db}", cwd=PROJECT_ROOT)

    assert _table_schema(create_all_db) == _table_schema(alembic_db)
    assert "origin_evidence_id" not in {
        name for name, _, _ in _table_schema(alembic_db)["signal_items"]
    }


def test_alembic_downgrade_removes_everything(tmp_path, alembic_available):
    db_path = tmp_path / "roundtrip.db"
    db_url = f"sqlite:///{db_path}"

    _run_alembic(["upgrade", "head"], db_url, cwd=PROJECT_ROOT)
    assert len(_table_schema(db_path)) == 49  # 41 through Phase 8 + 7 Phase 9 operational tables + 1 v1.0.0 source_reputations table

    _run_alembic(["downgrade", "base"], db_url, cwd=PROJECT_ROOT)
    assert _table_schema(db_path) == {}


# Pre-merge Semi Intel 2.2 head, per PHASE0_AUDIT.md. This is the exact
# revision id from the archived project before the Signal Radar absorption
# migration was added -- upgrading a database stopped here reproduces the
# schema of every real pre-merge installation.
PRE_MERGE_HEAD_REVISION = "b71d4e2c9a30"
PHASE8_HEAD_REVISION = "e8b7c2d4a901"


def test_upgrade_from_exact_pre_merge_schema_preserves_data(tmp_path, alembic_available):
    """Simulates a real operator upgrade: a database already at the exact
    pre-merge Semi Intel 2.2 head, holding real rows, upgraded to the new
    signal-layer revision. Existing rows and IDs must survive untouched, and
    every new column must land on a safe, collection-stays-off default (see
    the migration's own server_default choices)."""
    import sqlite3 as _sqlite3

    db_path = tmp_path / "pre_merge.db"
    db_url = f"sqlite:///{db_path}"

    _run_alembic(["upgrade", PRE_MERGE_HEAD_REVISION], db_url, cwd=PROJECT_ROOT)

    con = _sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO sources (id, name, type, url, description, trust_weight, created_at) "
        "VALUES (1, 'videocardz.com', 'RSS', 'https://videocardz.com/feed', NULL, 0.8, '2026-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO monitored_topics "
        "(id, name, normalized_name, keyword, aliases, category, priority, enabled, notes, created_at, updated_at) "
        "VALUES (1, 'RTX 50 Super', 'rtx 50 super', 'RTX 50 Super', '[]', 'gpu', 0.8, 1, NULL, "
        "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
    )
    con.commit()
    con.close()

    _run_alembic(["upgrade", "head"], db_url, cwd=PROJECT_ROOT)

    con = _sqlite3.connect(db_path)
    source_row = con.execute(
        "SELECT id, name, provider, enabled, polling_enabled, priority, languages, provider_metadata "
        "FROM sources WHERE id = 1"
    ).fetchone()
    assert source_row == (1, "videocardz.com", "manual", 1, 0, 3, "[]", "{}")

    topic_row = con.execute("SELECT id, name FROM monitored_topics WHERE id = 1").fetchone()
    assert topic_row == (1, "RTX 50 Super")

    # New tables exist and are empty -- nothing is silently populated/enabled.
    assert con.execute("SELECT COUNT(*) FROM signal_items").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM signal_candidates").fetchone()[0] == 0
    collection_settings = con.execute(
        "SELECT collection_enabled, x_provider_enabled FROM signal_collection_settings"
    ).fetchall()
    assert collection_settings == []  # settings rows are created on first use, not by the migration
    con.close()


def test_phase8_upgrade_preserves_notifications_and_safe_defaults(tmp_path, alembic_available):
    """A real 3.2 database must keep its alert/settings state while Phase 9
    adds only disabled operational controls."""
    db_path = tmp_path / "phase8.db"
    db_url = f"sqlite:///{db_path}"
    _run_alembic(["upgrade", PHASE8_HEAD_REVISION], db_url, cwd=PROJECT_ROOT)

    # Use the historical schema directly: the current ORM correctly includes
    # the post-Phase-8 desktop column that does not exist yet at this revision.
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO notification_settings VALUES ("
        "1,1,0,'2026-01-01',0.7,0.15,2,1,1,1,1,1,1,1,1,1,0,'08:00','UTC',"
        "'22:00','07:00',5,3,3,1,0.65,2,24,90,5,'[]','[]','2026-01-01')"
    )
    con.execute(
        "INSERT INTO notifications (event_type,severity,title,body,reason,dedup_key,"
        "event_metadata,created_at,event_at,first_occurrence_at,latest_occurrence_at,"
        "occurrence_count,muted,delivery_state) VALUES ("
        "'TEST','INFORMATIONAL','Existing Phase 8 alert','Preserve me',"
        "'Migration preservation test','phase8-preserved','{}','2026-01-01',"
        "'2026-01-01','2026-01-01','2026-01-01',1,0,'IN_APP')"
    )
    notification_id = con.execute(
        "SELECT id FROM notifications WHERE dedup_key='phase8-preserved'"
    ).fetchone()[0]
    con.commit()
    con.close()

    _run_alembic(["upgrade", "head"], db_url, cwd=PROJECT_ROOT)
    con = sqlite3.connect(db_path)
    assert con.execute(
        "SELECT title FROM notifications WHERE id = ?", (notification_id,)
    ).fetchone() == ("Existing Phase 8 alert",)
    assert con.execute(
        "SELECT external_delivery_enabled FROM notification_settings WHERE id = 1"
    ).fetchone() == (0,)
    assert con.execute(
        "SELECT windows_desktop_notifications_enabled FROM notification_settings WHERE id = 1"
    ).fetchone() == (0,)
    assert con.execute("SELECT COUNT(*) FROM scheduler_settings").fetchone() == (0,)
    assert con.execute("SELECT COUNT(*) FROM delivery_adapter_status").fetchone() == (0,)
    con.close()
