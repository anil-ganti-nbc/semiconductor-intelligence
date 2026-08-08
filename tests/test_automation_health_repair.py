from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from semi_intel.domain.enums import OperationalJobStatus, OperationalJobType, OperationalTriggerType
from semi_intel.domain.models import OperationalJobLease, OperationalJobRun
from semi_intel.notifications.service import aware
from semi_intel.operations.scheduler import (
    OperationalScheduler, effective_automation_state, get_scheduler_settings,
)
from semi_intel.operations.windows_task import WindowsTaskStatusService


BASE = dt.datetime(2026, 8, 3, 8, 0, tzinfo=dt.UTC)


def _task(**changes):
    value = {
        "supported": True, "installed": True, "path_exists": True,
        "path_matches_current": True, "state": "ready",
    }
    value.update(changes)
    return value


@pytest.mark.parametrize(
    ("task", "heartbeat", "expected"),
    [
        (_task(installed=False), None, "task_not_installed"),
        (_task(path_exists=False), None, "task_path_invalid"),
        (_task(path_matches_current=False), None, "task_path_mismatch"),
        (_task(), None, "task_never_ran"),
        (_task(), BASE - dt.timedelta(hours=3), "heartbeat_stale"),
        (_task(), BASE - dt.timedelta(minutes=5), "running_normally"),
    ],
)
def test_effective_automation_states(db_session, task, heartbeat, expected):
    settings = get_scheduler_settings(db_session)
    settings.scheduler_enabled = True
    settings.last_scheduler_heartbeat = heartbeat
    settings.pipeline_interval_minutes = 30
    settings.missed_run_warning_minutes = 20
    assert effective_automation_state(settings, task, now=BASE)["state"] == expected


def test_disabled_state_wins_over_task_status(db_session):
    settings = get_scheduler_settings(db_session)
    assert effective_automation_state(settings, _task(), now=BASE)["state"] == "disabled"


def test_windows_task_status_parses_path_and_runtime(tmp_path):
    executable = tmp_path / "semintel.exe"
    executable.write_bytes(b"test")
    payload = json.dumps({
        "state": "Ready", "execute": "cmd.exe",
        "arguments": f'/c ""{executable}" automation cycle"',
        "working_directory": str(tmp_path), "last_run": "2026-08-03T08:00:00",
        "next_run": "2026-08-03T08:30:00", "last_result": 0,
    })
    runner = lambda _command: subprocess.CompletedProcess([], 0, payload, "")
    status = WindowsTaskStatusService(runner).status(expected_executable=executable)
    assert status["installed"] is True
    assert status["path_exists"] is True
    assert status["path_matches_current"] is True
    assert status["last_result"] == 0


def test_reconcile_stale_runs_preserves_active_lease_and_is_idempotent(db_session):
    settings = get_scheduler_settings(db_session)
    settings.stale_run_threshold_minutes = 60
    stale = OperationalJobRun(
        job_type=OperationalJobType.PIPELINE,
        trigger_type=OperationalTriggerType.MANUAL_GUI,
        started_at=BASE - dt.timedelta(hours=3), status=OperationalJobStatus.RUNNING,
        owner_identity="crashed", lock_token="stale-token",
    )
    protected = OperationalJobRun(
        job_type=OperationalJobType.BACKUP,
        trigger_type=OperationalTriggerType.MANUAL_GUI,
        started_at=BASE - dt.timedelta(hours=3), status=OperationalJobStatus.RUNNING,
        owner_identity="live", lock_token="live-token",
    )
    db_session.add_all([stale, protected, OperationalJobLease(
        job_type=OperationalJobType.BACKUP, owner_identity="live", lock_token="live-token",
        acquired_at=BASE - dt.timedelta(minutes=5), refreshed_at=BASE,
        expires_at=BASE + dt.timedelta(minutes=30),
    )])
    db_session.commit()
    scheduler = OperationalScheduler(db_session)
    assert scheduler.reconcile_stale_runs(now=BASE) == [stale.id]
    assert stale.status == OperationalJobStatus.ABANDONED
    assert aware(stale.finished_at) == BASE
    assert protected.status == OperationalJobStatus.RUNNING
    assert scheduler.reconcile_stale_runs(now=BASE) == []


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{tmp_path / 'automation_repair.db'}")
    from semi_intel.operations.windows_task import WindowsTaskStatusService as Service
    monkeypatch.setattr(Service, "status", lambda self, **kwargs: {
        "supported": True, "installed": False, "state": "not_installed",
        "expected_executable": "C:\\checkpoint\\semintel.exe", "error": None,
    })
    from semi_intel.web.app import create_app
    with TestClient(create_app()) as client:
        yield client


def test_web_status_never_promises_next_run_without_task_or_heartbeat(client):
    client.post("/api/operations/scheduler/true", json={})
    status = client.get("/api/operations/scheduler").json()
    assert status["effective"]["state"] == "task_not_installed"
    assert status["next_runs"]["pipeline"] is None
    health = client.get("/api/operations/health").json()
    assert health["overall"] == "degraded"
    assert "not installed" in health["scheduler"]["effective"]["explanation"]


def test_task_install_requires_confirmation_and_packaged_runtime(client):
    assert client.post(
        "/api/operations/windows-task/install", json={"confirmed": False}
    ).status_code == 409
    assert client.post(
        "/api/operations/windows-task/install", json={"confirmed": True}
    ).status_code == 409


def test_operations_dashboard_loads_panels_independently_and_has_recovery_controls():
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    start = html.index("async function loadOperations")
    end = html.index("async function loadOperationalTrends", start)
    body = html[start:end]
    assert "Promise.allSettled" in body
    assert "Promise.all([" not in body
    for expected in (
        'id="operations-reconcile-stale"',
        'id="operations-windows-task"',
        'id="operations-task-install"',
        "installWindowsTask()",
        "reconcileStaleJobs()",
        "Last heartbeat",
        "not scheduled",
    ):
        assert expected in html


def test_run_health_check_button_calls_the_health_check_job_not_a_bare_reload():
    """Regression test: the 'Run health check' button was previously wired
    to onclick="loadOperations()" -- a no-op re-render of already-loaded
    data -- instead of runOperationalJob('health_check') like its sibling
    buttons (Run pipeline now / Generate digest now / Create backup now).
    Clicking it silently did nothing: no job was created, no request left
    the browser. Assert it now matches the same pattern as its siblings."""
    html = Path("semi_intel/web/static/index.html").read_text(encoding="utf-8")
    assert 'onclick="runOperationalJob(\'health_check\')">Run health check' in html
    assert 'onclick="loadOperations()">Run health check' not in html
