"""Single-instance run lock tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from oem_radar.core.run_lock import LockError, RunLock, _pid_alive


def test_acquire_and_release(tmp_path):
    path = tmp_path / "oem-radar.lock"
    lock = RunLock.acquire(path)
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["pid"] == os.getpid()
    lock.release()
    assert not path.exists()


def test_context_manager(tmp_path):
    path = tmp_path / "lock"
    with RunLock.acquire(path) as lock:
        assert lock._held
        assert path.exists()
    assert not path.exists()


def test_duplicate_instance_rejected(tmp_path):
    path = tmp_path / "lock"
    lock1 = RunLock.acquire(path)
    with pytest.raises(LockError, match="another oem-radar run is active"):
        RunLock.acquire(path)
    lock1.release()
    # after release, acquire works
    lock2 = RunLock.acquire(path)
    lock2.release()


def test_stale_lock_reclaimed(tmp_path):
    path = tmp_path / "lock"
    # Write a lock for a definitely-dead PID
    path.write_text(json.dumps({
        "pid": 999999,  # almost certainly not alive
        "started_at": 0,
        "started_at_iso": "1970-01-01T00:00:00Z",
    }))
    # Confirm dead
    assert _pid_alive(999999) is False
    lock = RunLock.acquire(path)
    assert lock.pid == os.getpid()
    data = json.loads(path.read_text())
    assert data["pid"] == os.getpid()
    lock.release()


def test_release_does_not_delete_foreign_lock(tmp_path):
    path = tmp_path / "lock"
    lock = RunLock.acquire(path)
    # Simulate another process overwriting (should not happen, but be safe)
    path.write_text(json.dumps({"pid": os.getpid() + 1, "started_at": 1}))
    lock.release()
    # Foreign lock left in place
    assert path.exists()
    assert json.loads(path.read_text())["pid"] == os.getpid() + 1


def test_pid_alive_self():
    assert _pid_alive(os.getpid()) is True


def test_radar_config_has_lock_path():
    from oem_radar.core.config import RadarConfig
    cfg = RadarConfig()
    assert cfg.run_lock_path == "data/oem-radar.lock"
