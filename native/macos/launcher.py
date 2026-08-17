"""Finder launcher for the isolated Semiconductor Intelligence dashboard."""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path


APP_NAME = "Semiconductor Intelligence"


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parents[1] / "Resources"
    return Path(__file__).resolve().parents[2]


def default_state_root() -> Path:
    return Path.home() / "Library" / "Application Support" / "SemiIntel"


def configure_field_test_runtime() -> Path:
    state = Path(os.environ.get("SEMINTEL_FIELD_TEST_HOME", default_state_root())).expanduser().resolve()
    os.environ["SEMINTEL_HOME"] = str(state)
    os.environ["SEMINTEL_ALLOW_LEGACY_PATHS"] = "0"
    os.environ["SEMI_INTEL_DB_URL"] = f"sqlite:///{state / 'data' / 'semi_intel.db'}"
    os.environ["SEMI_INTEL_X_SESSION_PATH"] = str(state / "browser" / "field-test-no-session.json")
    os.environ["SEMI_INTEL_X_PROFILE_DIR"] = str(state / "browser" / "field-test-profile")
    os.environ["SEMINTEL_DISCORD_WEBHOOK_FILE"] = str(state / "config" / "field-test-no-webhook.txt")
    os.environ["SEMINTEL_FIELD_TEST_READ_ONLY"] = "1"
    for key in (
        "SEMI_INTEL_WEBHOOK_URL", "SEMI_INTEL_WEBHOOK_TOKEN",
        "OEM_RADAR_DISCORD_WEBHOOK", "DISCORD_WEBHOOK_URL",
        "SEMINTEL_X_SESSION", "SEMINTEL_X_PROFILE", "PLAYWRIGHT_BROWSERS_PATH",
    ):
        os.environ.pop(key, None)
    from semi_intel.paths import ensure_runtime_dirs

    ensure_runtime_dirs()
    return state


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_ready(port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runtime/identity", timeout=0.5) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.1)
    return False


def main() -> int:
    state = configure_field_test_runtime()
    revision_file = resource_root() / "metadata" / "revision.txt"
    os.environ["SEMINTEL_SOURCE_REVISION"] = (
        revision_file.read_text(encoding="utf-8").strip()
        if revision_file.exists() else "local-development"
    )
    log_path = state / "logs" / "dashboard-launcher.log"
    logging.basicConfig(filename=log_path, level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    import uvicorn
    from semi_intel.web.app import create_app

    port = available_port()
    runtime_path = state / "runtime" / "dashboard.json"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(json.dumps({"pid": os.getpid(), "host": "127.0.0.1", "port": port}))
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="info"))
    server_thread = threading.Thread(target=server.run, name="semintel-loopback", daemon=False)

    def stop(_signum: int, _frame: object) -> None:
        server.should_exit = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server_thread.start()
    if wait_for_ready(port) and os.environ.get("SEMINTEL_NO_BROWSER") != "1":
        subprocess.Popen(["open", f"http://127.0.0.1:{port}/"])
    try:
        server_thread.join()
        return 0 if server.started else 1
    finally:
        server.should_exit = True
        if server_thread.is_alive():
            server_thread.join(timeout=10)
        runtime_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
