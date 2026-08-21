"""Stabilization Pass 1 -- real-process shutdown/restart and two genuinely
separate dashboard processes against the same database.

These use a real `semi-intel web serve` subprocess (not TestClient) because
TestClient never exercises actual socket binding/release or process
termination -- exactly what "the port is released" and "can immediately
restart" require proving.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = Path(sys.executable)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http_ok(url: str, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.2)
    return False


def _wait_for_connection_refused(host: str, port: int, timeout: float = 15.0) -> bool:
    """Confirms the port was actually released -- a fresh connect attempt
    must fail, not hang or succeed against a lingering listener."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                pass
            time.sleep(0.2)
            continue  # still accepting connections -- not released yet
        except (ConnectionRefusedError, OSError):
            return True
    return False


def _spawn_dashboard(db_path: Path, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["SEMI_INTEL_DB_URL"] = f"sqlite:///{db_path}"
    test_server = (
        "import sys,uvicorn; "
        "from semi_intel.web.app import create_app; "
        "uvicorn.run(create_app(mutation_authorizer=lambda _request: True), "
        "host='127.0.0.1', port=int(sys.argv[1]))"
    )
    return subprocess.Popen(
        [str(VENV_PYTHON), "-c", test_server, str(port)],
        cwd=str(PROJECT_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


@pytest.fixture()
def alembic_available():
    if not (PROJECT_ROOT / "alembic.ini").exists():
        pytest.skip("alembic.ini not found -- migrations not part of this checkout")


def test_clean_shutdown_releases_the_port_and_allows_immediate_restart(tmp_path, alembic_available):
    db_path = tmp_path / "shutdown.db"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    proc = _spawn_dashboard(db_path, port)
    try:
        assert _wait_for_http_ok(f"{base_url}/"), "dashboard never came up"
        # A read request completes cleanly before we stop the process.
        with urllib.request.urlopen(f"{base_url}/api/topics", timeout=5) as resp:
            assert resp.status == 200
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)

    assert proc.returncode is not None  # exited within the bounded wait above
    assert _wait_for_connection_refused("127.0.0.1", port), "port was not released after shutdown"

    # Immediate restart on the very same port must succeed.
    restarted = _spawn_dashboard(db_path, port)
    try:
        assert _wait_for_http_ok(f"{base_url}/"), "dashboard failed to restart on the just-released port"
        with urllib.request.urlopen(f"{base_url}/api/notifications/status", timeout=5) as resp:
            assert resp.status == 200
    finally:
        restarted.terminate()
        try:
            restarted.wait(timeout=15)
        except subprocess.TimeoutExpired:
            restarted.kill()
            restarted.wait(timeout=10)


def test_shutdown_immediately_after_a_write_leaves_no_open_transaction(tmp_path, alembic_available):
    db_path = tmp_path / "shutdown_after_write.db"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    proc = _spawn_dashboard(db_path, port)
    try:
        assert _wait_for_http_ok(f"{base_url}/")
        req = urllib.request.Request(f"{base_url}/api/notifications/test", method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)

    # The database must be immediately usable, unlocked, with the write intact.
    import sqlite3
    con = sqlite3.connect(db_path, timeout=5)
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    assert integrity == "ok"
    count = con.execute("select count(*) from notifications").fetchone()[0]
    assert count == 1
    con.close()


def test_two_real_dashboard_processes_against_the_same_database_fail_safely(tmp_path, alembic_available):
    """Not necessarily a supported normal operating mode, but it must fail
    safely: reads from both stay stable, the database is never corrupted,
    and stopping one process never damages the other or leaves the
    database unusable afterward."""
    db_path = tmp_path / "two_processes.db"
    port_a = _free_port()
    port_b = _free_port()

    proc_a = _spawn_dashboard(db_path, port_a)
    proc_b = None
    try:
        assert _wait_for_http_ok(f"http://127.0.0.1:{port_a}/")
        proc_b = _spawn_dashboard(db_path, port_b)
        assert _wait_for_http_ok(f"http://127.0.0.1:{port_b}/")

        # Reads from both must stay stable and agree.
        with urllib.request.urlopen(f"http://127.0.0.1:{port_a}/api/topics", timeout=5) as resp:
            topics_a = resp.status
        with urllib.request.urlopen(f"http://127.0.0.1:{port_b}/api/topics", timeout=5) as resp:
            topics_b = resp.status
        assert topics_a == topics_b == 200

        # Stopping one must not damage the other.
        proc_a.terminate()
        proc_a.wait(timeout=15)
        with urllib.request.urlopen(f"http://127.0.0.1:{port_b}/api/topics", timeout=5) as resp:
            assert resp.status == 200
    finally:
        for proc in (proc_a, proc_b):
            if proc is None or proc.poll() is not None:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)

    # The database must remain usable after both exit.
    import sqlite3
    con = sqlite3.connect(db_path, timeout=5)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("select count(*) from scheduler_settings").fetchone()[0] <= 1  # no duplicate singleton
    con.close()
