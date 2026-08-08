from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from semi_intel.domain.enums import (
    OperationalJobStatus, OperationalJobType, OperationalTriggerType,
)
from semi_intel.domain.models import OperationalJobLease, OperationalJobRun
from semi_intel.operations.scheduler import (
    LeaseManager, OperationalScheduler, get_scheduler_settings, next_runs,
)
from semi_intel.notifications.service import aware


BASE = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)


def test_scheduler_defaults_and_next_runs(db_session):
    settings = get_scheduler_settings(db_session)
    assert settings.scheduler_enabled is False
    assert settings.backup_enabled is False
    assert settings.maintenance_enabled is False
    assert settings.timezone == "Asia/Kolkata"
    runs = next_runs(settings, now=BASE)
    assert runs["pipeline"] == BASE + dt.timedelta(minutes=30)
    assert all(value.tzinfo == dt.UTC for value in runs.values())


def test_atomic_lease_refuses_overlap_and_recovers_stale(db_session):
    first = LeaseManager(db_session).acquire(
        OperationalJobType.PIPELINE, duration_minutes=10, now=BASE, owner="first"
    )
    assert first.acquired
    db_session.commit()

    Other = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    other = Other()
    try:
        blocked = LeaseManager(other).acquire(
            OperationalJobType.PIPELINE, duration_minutes=10,
            now=BASE + dt.timedelta(minutes=1), owner="second",
        )
        assert blocked.acquired is False
        assert blocked.lease.owner_identity == "first"

        lease = other.scalar(select(OperationalJobLease))
        lease.expires_at = BASE + dt.timedelta(minutes=2)
        other.commit()
        recovered = LeaseManager(other).acquire(
            OperationalJobType.PIPELINE, duration_minutes=10,
            now=BASE + dt.timedelta(minutes=3), owner="second",
        )
        assert recovered.acquired
        assert recovered.recovered_owner == "first"
        assert other.scalar(select(func.count()).select_from(OperationalJobRun).where(
            OperationalJobRun.status == OperationalJobStatus.ABANDONED
        )) == 1
    finally:
        other.close()


def test_job_run_audits_success_and_releases_lease(db_session):
    job = OperationalScheduler(db_session).run_job(
        OperationalJobType.NOTIFICATION_GENERATION,
        trigger=OperationalTriggerType.TEST, now=BASE,
    )
    assert job.status == OperationalJobStatus.SUCCESSFUL
    assert job.trigger_type == OperationalTriggerType.TEST
    assert db_session.scalar(select(func.count()).select_from(OperationalJobLease)) == 0
    assert "Generated" in job.summary


def test_disabled_cycle_is_safe_noop(db_session):
    scheduler = OperationalScheduler(db_session)
    assert scheduler.cycle(now=BASE) == []
    assert aware(scheduler.settings().last_scheduler_heartbeat) == BASE
