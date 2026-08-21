from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from fastapi.testclient import TestClient


LAUNCHER = Path(__file__).resolve().parents[1] / "native" / "macos" / "launcher.py"


def _launcher():
    spec = importlib.util.spec_from_file_location("semintel_macos_launcher", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_field_test_paths_and_secrets_are_isolated(tmp_path, monkeypatch):
    launcher = _launcher()
    state = tmp_path / "SemiIntel"
    for key in (
        "SEMINTEL_FIELD_TEST_HOME", "SEMINTEL_HOME", "SEMI_INTEL_DB_URL", "SEMI_INTEL_X_SESSION_PATH",
        "SEMI_INTEL_X_PROFILE_DIR", "SEMINTEL_DISCORD_WEBHOOK_FILE",
        "SEMINTEL_FIELD_TEST_READ_ONLY", "SEMINTEL_ALLOW_LEGACY_PATHS",
    ):
        monkeypatch.setenv(key, str(state) if key == "SEMINTEL_FIELD_TEST_HOME" else "placeholder")
    monkeypatch.setenv("SEMI_INTEL_WEBHOOK_URL", "https://production.invalid/secret")
    monkeypatch.setenv("SEMI_INTEL_WEBHOOK_TOKEN", "secret")

    resolved = launcher.configure_field_test_runtime()

    assert resolved == state.resolve()
    assert os.environ["SEMI_INTEL_DB_URL"] == f"sqlite:///{resolved / 'data' / 'semi_intel.db'}"
    assert os.environ["SEMI_INTEL_X_SESSION_PATH"].startswith(str(resolved / "browser"))
    assert os.environ["SEMINTEL_DISCORD_WEBHOOK_FILE"].startswith(str(resolved / "config"))
    assert os.environ["SEMINTEL_FIELD_TEST_READ_ONLY"] == "1"
    assert "SEMI_INTEL_WEBHOOK_URL" not in os.environ
    assert "SEMI_INTEL_WEBHOOK_TOKEN" not in os.environ
    for directory in ("config", "secrets", "browser", "data", "diagnostics", "logs", "exports", "backups"):
        assert (resolved / directory).is_dir()


def test_default_state_root_has_no_hard_coded_username(monkeypatch):
    launcher = _launcher()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users/example")))
    assert launcher.default_state_root() == Path("/Users/example/Library/Application Support/SemiIntel")


def test_field_test_dashboard_is_read_only(tmp_path, monkeypatch):
    db_path = tmp_path / "semi_intel.db"
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("SEMINTEL_FIELD_TEST_READ_ONLY", "1")
    monkeypatch.setenv("SEMINTEL_SOURCE_REVISION", "abc123")
    from semi_intel.web.app import create_app

    client = TestClient(create_app(mutation_authorizer=lambda _value: True))
    assert client.get("/").status_code == 200
    identity = client.get("/api/runtime/identity")
    assert identity.status_code == 200
    assert identity.json()["source_revision"] == "abc123"
    assert identity.json()["field_test_read_only"] is True
    assert client.post("/api/operations/run/pipeline").status_code == 403
    assert client.post("/api/radar/cluster").status_code == 403
