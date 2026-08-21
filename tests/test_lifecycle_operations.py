"""Stabilization Pass 1 -- interrupted operations, concurrent access, paths
with spaces, and backup/restore into a separate disposable location.
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import func, select, text

from semi_intel.db import get_engine, get_sessionmaker, init_db
from semi_intel.domain.enums import (
    OperationalJobStatus, OperationalJobType, OperationalTriggerType,
)
from semi_intel.domain.models import OperationalJobLease, OperationalJobRun
from semi_intel.notifications.service import utcnow
from semi_intel.operations.health import HealthService
from semi_intel.operations.scheduler import LeaseManager, OperationalScheduler, get_scheduler_settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKER_SCRIPT = Path(__file__).resolve().parent / "fixtures" / "interrupted_job_worker.py"


def _migrate_to_head(db_url: str, monkeypatch) -> None:
    """Builds a database the way `semintel install` actually would (real
    Alembic migrations, so alembic_version is stamped) rather than a bare
    create_all() -- BackupService.create()'s own verification requires an
    alembic_version table, matching how every real installed database is
    created in practice."""
    monkeypatch.setenv("SEMI_INTEL_DB_URL", db_url)
    monkeypatch.chdir(PROJECT_ROOT)
    from semi_intel.cli import upgrade_or_stamp_to_head
    upgrade_or_stamp_to_head()


# --- interrupted-operation recovery -----------------------------------------


def test_stale_running_job_is_flagged_by_health_check(tmp_path):
    """A process killed outright leaves its OperationalJobRun row stuck at
    RUNNING with no finished_at -- nothing else ever revisits it. This is
    the exact defect stale_run_threshold_minutes exists for but was never
    wired into HealthService until this pass."""
    engine = get_engine(f"sqlite:///{tmp_path / 'stale_run.db'}")
    init_db(engine)
    session = get_sessionmaker(engine)()

    now = utcnow()
    settings = get_scheduler_settings(session)
    settings.stale_run_threshold_minutes = 60
    session.add(OperationalJobRun(
        job_type=OperationalJobType.PIPELINE, trigger_type=OperationalTriggerType.MANUAL_CLI,
        started_at=now - dt.timedelta(minutes=120), status=OperationalJobStatus.RUNNING,
        owner_identity="crashed-worker",
    ))
    session.commit()

    report = HealthService(session).report(now=now)
    assert report["scheduler"]["stale_running_jobs"] == 1
    assert any("RUNNING for over 60 minutes" in issue["explanation"] for issue in report["issues"])
    assert report["overall"] == "degraded"
    session.close()
    engine.dispose()


def test_stale_running_job_within_threshold_is_not_flagged(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'fresh_run.db'}")
    init_db(engine)
    session = get_sessionmaker(engine)()
    now = utcnow()
    session.add(OperationalJobRun(
        job_type=OperationalJobType.PIPELINE, trigger_type=OperationalTriggerType.MANUAL_CLI,
        started_at=now - dt.timedelta(minutes=5), status=OperationalJobStatus.RUNNING,
        owner_identity="in-progress-worker",
    ))
    session.commit()
    report = HealthService(session).report(now=now)
    assert report["scheduler"]["stale_running_jobs"] == 0
    session.close()
    engine.dispose()


def test_partial_record_does_not_masquerade_as_successful(tmp_path):
    """The stuck row's own status must stay RUNNING -- never silently
    flip to SUCCESSFUL/PARTIAL just because time passed."""
    engine = get_engine(f"sqlite:///{tmp_path / 'no_masquerade.db'}")
    init_db(engine)
    session = get_sessionmaker(engine)()
    now = utcnow()
    session.add(OperationalJobRun(
        job_type=OperationalJobType.BACKUP, trigger_type=OperationalTriggerType.MANUAL_CLI,
        started_at=now - dt.timedelta(hours=5), status=OperationalJobStatus.RUNNING,
        owner_identity="crashed-worker",
    ))
    session.commit()
    HealthService(session).report(now=now)
    row = session.scalar(select(OperationalJobRun))
    assert row.status == OperationalJobStatus.RUNNING
    assert row.finished_at is None
    session.close()
    engine.dispose()


def test_hard_killed_worker_process_recovers_on_next_attempt(tmp_path):
    """Real subprocess termination (not an injected exception) simulating a
    genuine crash: a worker acquires a lease and writes a RUNNING job row,
    then gets killed outright. On the next startup/attempt: the database
    still opens normally, health/status still work, the stale lease
    self-heals (LeaseManager.acquire()'s existing takeover logic) instead
    of permanently blocking new work, and a brand-new legitimate operation
    completes successfully."""
    db_path = tmp_path / "interrupted.db"
    db_url = f"sqlite:///{db_path}"
    engine = get_engine(db_url)
    init_db(engine)
    engine.dispose()  # the worker subprocess opens its own connection

    proc = subprocess.Popen(
        [sys.executable, str(WORKER_SCRIPT), db_url, "pipeline", str(PROJECT_ROOT)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 15
        ready = False
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if line.strip() == "READY":
                ready = True
                break
        assert ready, "worker never reached its RUNNING/lease-acquired state"
    finally:
        proc.kill()  # hard termination -- run_job()'s finally: never runs
        proc.wait(timeout=10)

    # -- next startup: the database must open normally and report health --
    engine = get_engine(db_url)
    session = get_sessionmaker(engine)()
    report = HealthService(session).report()
    assert report["overall"] in {"healthy", "attention_needed", "degraded", "disabled"}

    stuck_job = session.scalar(select(OperationalJobRun).where(
        OperationalJobRun.job_type == OperationalJobType.PIPELINE
    ))
    assert stuck_job.status == OperationalJobStatus.RUNNING  # left honestly incomplete, not masqueraded
    assert stuck_job.finished_at is None

    lease = session.scalar(select(OperationalJobLease).where(
        OperationalJobLease.job_type == OperationalJobType.PIPELINE
    ))
    assert lease is not None  # the crashed worker's lease is still on disk

    # -- simulate enough time passing for the lease to go stale, then prove
    # a brand-new legitimate operation is not permanently blocked --
    future = utcnow() + dt.timedelta(hours=1)
    result = LeaseManager(session).acquire(
        OperationalJobType.PIPELINE, duration_minutes=30, now=future,
    )
    assert result.acquired, "a stale lease from a hard-killed worker must not permanently block new work"
    assert result.recovered_owner is not None  # the crashed worker's identity, recovered and logged
    LeaseManager(session).release(result.lease)

    new_job = OperationalScheduler(session).run_job(
        OperationalJobType.HEALTH_CHECK, trigger=OperationalTriggerType.MANUAL_CLI, now=future,
    )
    assert new_job.status in {OperationalJobStatus.SUCCESSFUL, OperationalJobStatus.PARTIAL}

    session.close()
    engine.dispose()


# --- concurrent process / engine access -------------------------------------


def test_two_engines_against_the_same_database_do_not_corrupt_it(tmp_path):
    """Not necessarily a supported normal operating mode, but it must fail
    safely: two independent engines (simulating two processes) writing to
    the same SQLite file must serialize through SQLite's own locking
    (backed by this pass's 30s busy-timeout) rather than corrupt data or
    silently lose writes."""
    db_path = tmp_path / "concurrent.db"
    engine_a = get_engine(f"sqlite:///{db_path}")
    init_db(engine_a)
    engine_b = get_engine(f"sqlite:///{db_path}")

    session_a = get_sessionmaker(engine_a)()
    session_b = get_sessionmaker(engine_b)()
    for i in range(10):
        session_a.add(OperationalJobRun(
            job_type=OperationalJobType.HEALTH_CHECK, trigger_type=OperationalTriggerType.TEST,
            started_at=utcnow(), finished_at=utcnow(), status=OperationalJobStatus.SUCCESSFUL,
            owner_identity=f"engine-a-{i}",
        ))
        session_a.commit()
        session_b.add(OperationalJobRun(
            job_type=OperationalJobType.HEALTH_CHECK, trigger_type=OperationalTriggerType.TEST,
            started_at=utcnow(), finished_at=utcnow(), status=OperationalJobStatus.SUCCESSFUL,
            owner_identity=f"engine-b-{i}",
        ))
        session_b.commit()

    session_a.close()
    session_b.close()
    engine_a.dispose()
    engine_b.dispose()

    verify_engine = get_engine(f"sqlite:///{db_path}")
    verify_session = get_sessionmaker(verify_engine)()
    total = verify_session.scalar(select(func.count()).select_from(OperationalJobRun))
    assert total == 20  # every write from both engines landed, none lost or duplicated
    integrity = verify_session.execute(text("PRAGMA integrity_check")).scalar()
    assert integrity == "ok"
    verify_session.close()
    verify_engine.dispose()


def test_concurrent_singleton_settings_startup_does_not_duplicate_rows(tmp_path):
    """Two independent engines (simulating two separate processes) opening
    the same singleton settings row must never end up with two rows.
    The precise concurrent-insert race itself (both sessions believing the
    row is missing, one losing and recovering via the IntegrityError path)
    is already covered deterministically by
    tests/test_settings_singleton_concurrency.py; this checks the same
    guarantee end-to-end across genuinely independent engine objects,
    committing between accesses the way two separate short-lived requests
    actually would (an uncommitted flush held open indefinitely, which
    real request handlers never do, would just be sustained lock
    contention, not the race this pass cares about)."""
    from semi_intel.operations.scheduler import get_scheduler_settings

    db_path = tmp_path / "concurrent_singleton.db"
    engine_a = get_engine(f"sqlite:///{db_path}")
    init_db(engine_a)
    engine_b = get_engine(f"sqlite:///{db_path}")

    session_a = get_sessionmaker(engine_a)()
    settings_a = get_scheduler_settings(session_a)
    session_a.commit()

    session_b = get_sessionmaker(engine_b)()
    settings_b = get_scheduler_settings(session_b)
    session_b.commit()
    assert settings_a.id == settings_b.id == 1

    session_a.close()
    session_b.close()
    engine_a.dispose()
    engine_b.dispose()

    verify_engine = get_engine(f"sqlite:///{db_path}")
    verify_session = get_sessionmaker(verify_engine)()
    count = verify_session.execute(
        text("select count(*) from scheduler_settings")
    ).scalar()
    assert count == 1
    verify_session.close()
    verify_engine.dispose()


# --- filesystem and path handling -------------------------------------------


def test_database_and_backups_work_with_a_path_containing_spaces_and_punctuation(tmp_path, monkeypatch):
    quirky_dir = tmp_path / "Semi Intel (test) - data & backups!"
    quirky_dir.mkdir()
    db_path = quirky_dir / "semi_intel.db"

    _migrate_to_head(f"sqlite:///{db_path}", monkeypatch)
    engine = get_engine(f"sqlite:///{db_path}")
    session = get_sessionmaker(engine)()

    from semi_intel.operations.backup import BackupService
    backup_dir = quirky_dir / "backups"
    backup_dir.mkdir()
    record = BackupService(session, backup_directory=backup_dir).create()
    assert Path(record.path).exists()
    assert Path(record.path).parent == backup_dir.resolve()

    session.close()
    engine.dispose()

    # Reopen -- a fresh engine against the same spaces-containing path.
    reopened = get_engine(f"sqlite:///{db_path}")
    reopened_session = get_sessionmaker(reopened)()
    integrity = reopened_session.execute(text("PRAGMA integrity_check")).scalar()
    assert integrity == "ok"
    reopened_session.close()
    reopened.dispose()


def test_dashboard_works_launched_from_a_directory_other_than_the_project_root(tmp_path, monkeypatch):
    """The application must not depend on the current working directory
    being the project root -- create_app(mutation_authorizer=lambda _value: True) must still find alembic.ini/
    migrations/ (via _project_root(), which is frozen-exe-aware) and the
    packaged static assets regardless of cwd."""
    unrelated_cwd = tmp_path / "an unrelated folder with spaces"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{tmp_path / 'cwd_independent.db'}")

    from semi_intel.web.app import create_app
    app = create_app(mutation_authorizer=lambda _value: True)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/api/topics").status_code == 200


# --- backup / restore round trip into a separate disposable location -------


def test_backup_restore_round_trip_into_a_separate_location_starts_successfully(tmp_path, monkeypatch):
    """Never restores over the working test database -- the backup file is
    copied to an entirely separate disposable directory and the app is
    launched against THAT copy."""
    import json

    from semi_intel.domain.enums import NotificationFeedbackRating
    from semi_intel.domain.models import (
        MonitoredTopic, Notification, NotificationFeedback, SavedNotificationView,
    )
    from semi_intel.editorial.service import TopicService
    from semi_intel.notifications.service import NotificationService
    from semi_intel.operations.backup import BackupService
    from semi_intel.operations.quality import NotificationQualityService, SavedViewService

    working_dir = tmp_path / "working"
    working_dir.mkdir()
    db_path = working_dir / "semi_intel.db"
    _migrate_to_head(f"sqlite:///{db_path}", monkeypatch)
    engine = get_engine(f"sqlite:///{db_path}")
    session = get_sessionmaker(engine)()

    TopicService(session).seed()
    session.commit()
    topic = session.scalar(select(MonitoredTopic))

    notification = NotificationService(session).create_test_notification()
    session.commit()
    NotificationQualityService(session).feedback(
        notification.id, NotificationFeedbackRating.USEFUL, reason="good_timing",
    )
    SavedViewService(session).save(name="Backup round trip view", severities=["informational"])

    settings = get_scheduler_settings(session)
    settings.pipeline_interval_minutes = 77
    session.commit()

    backup_dir = working_dir / "backups"
    backup_dir.mkdir()
    backup_service = BackupService(session, backup_directory=backup_dir)
    record = backup_service.create()
    rehearsal = backup_service.rehearse(Path(record.path))
    assert rehearsal["passed"] is True
    assert rehearsal["schema_up_to_date"] is True

    # Copy the verified backup file into a completely separate disposable
    # location -- never touching `working_dir`'s own live database.
    restore_dir = tmp_path / "restored_elsewhere"
    restore_dir.mkdir()
    restored_db_path = restore_dir / "semi_intel.db"
    import shutil
    shutil.copy2(record.path, restored_db_path)

    session.close()
    engine.dispose()

    # The original working database must be untouched.
    assert db_path.exists()

    # The restored copy must be a fully usable, startable application database.
    restored_engine = get_engine(f"sqlite:///{restored_db_path}")
    restored_session = get_sessionmaker(restored_engine)()
    restored_notification = restored_session.scalar(select(Notification))
    assert restored_notification.id == notification.id

    restored_feedback = list(restored_session.scalars(select(NotificationFeedback)))
    assert len(restored_feedback) == 1
    assert restored_feedback[0].rating == NotificationFeedbackRating.USEFUL

    restored_view = restored_session.scalar(select(SavedNotificationView))
    assert restored_view.name == "Backup round trip view"
    assert json.loads(restored_view.severities) == ["informational"]

    restored_settings = get_scheduler_settings(restored_session)
    assert restored_settings.pipeline_interval_minutes == 77

    restored_candidate_count = restored_session.execute(
        text("select count(*) from signal_candidates")
    ).scalar()
    assert restored_candidate_count == 0  # none were created -- nothing spuriously appeared

    restored_session.close()
    restored_engine.dispose()
