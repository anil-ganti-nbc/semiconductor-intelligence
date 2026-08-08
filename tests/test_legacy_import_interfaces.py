from __future__ import annotations

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from semi_intel.cli import app
from semi_intel.web.app import create_app
from tests.test_legacy_import import make_legacy_db


runner = CliRunner()


def test_cli_preview_then_apply(tmp_path, cli_env):
    legacy = make_legacy_db(tmp_path / "legacy.db")

    preview = runner.invoke(app, ["radar", "import", "--database", str(legacy)])
    assert preview.exit_code == 0, preview.output
    assert "Legacy import preview" in preview.output
    assert "posts: detected=1, importable=1" in preview.output

    applied = runner.invoke(app, ["radar", "import", "--database", str(legacy), "--apply"])
    assert applied.exit_code == 0, applied.output
    assert "Legacy import apply" in applied.output
    assert "imported=1" in applied.output
    assert "radar cluster" in applied.output

    repeated = runner.invoke(app, ["radar", "import", "--database", str(legacy), "--apply"])
    assert repeated.exit_code == 0, repeated.output
    assert "duplicate=1" in repeated.output


def test_cli_rejects_wrong_database(tmp_path, cli_env):
    wrong = tmp_path / "wrong.db"
    wrong.write_bytes(b"not sqlite")

    result = runner.invoke(app, ["radar", "import", "--database", str(wrong)])
    assert result.exit_code == 1
    assert "Import failed" in result.output


def test_web_preview_apply_and_repeat(tmp_path, monkeypatch):
    destination = tmp_path / "destination.db"
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{destination}")
    client = TestClient(create_app())
    legacy = make_legacy_db(tmp_path / "legacy.db")
    payload = legacy.read_bytes()
    headers = {"Content-Type": "application/vnd.sqlite3"}

    preview = client.post("/api/radar/import/preview", content=payload, headers=headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["mode"] == "preview"
    assert preview.json()["categories"]["posts"]["importable"] == 1
    assert client.get("/api/radar/sources").json() == []

    applied = client.post("/api/radar/import/apply", content=payload, headers=headers)
    assert applied.status_code == 200, applied.text
    assert applied.json()["categories"]["posts"]["imported"] == 1
    sources = client.get("/api/radar/sources").json()
    assert len(sources) == 1
    assert sources[0]["polling_enabled"] is False

    processed = client.post("/api/radar/cluster", json={})
    assert processed.status_code == 200, processed.text
    assert processed.json()["analyzed"] == 1

    repeated = client.post("/api/radar/import/apply", content=payload, headers=headers)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["categories"]["posts"]["duplicate"] == 1
    assert len(client.get("/api/radar/sources").json()) == 1


def test_web_rejects_non_sqlite_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{tmp_path / 'destination.db'}")
    client = TestClient(create_app())

    response = client.post("/api/radar/import/preview", content=b"definitely not sqlite")

    assert response.status_code == 400
    assert "not a SQLite database" in response.json()["detail"]
