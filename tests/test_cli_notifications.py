from __future__ import annotations

import json

from typer.testing import CliRunner

from semi_intel.cli import app


runner = CliRunner()


def test_notification_cli_lifecycle(cli_env):
    runner.invoke(app, ["init-db"])
    status = runner.invoke(app, ["notifications", "status", "--json"])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["external_delivery_enabled"] is False

    created = runner.invoke(app, ["notifications", "test"])
    assert created.exit_code == 0, created.output
    listing = runner.invoke(app, ["notifications", "list", "--json"])
    row = json.loads(listing.output)[0]

    assert runner.invoke(app, ["notifications", "read", str(row["id"])]).exit_code == 0
    assert runner.invoke(app, ["notifications", "dismiss", str(row["id"])]).exit_code == 0
    assert runner.invoke(app, ["notifications", "restore", str(row["id"])]).exit_code == 0


def test_notification_cli_digest_and_settings(cli_env):
    runner.invoke(app, ["init-db"])
    settings = runner.invoke(
        app, ["notifications", "settings", "--minimum-score", "0.8",
              "--timezone", "Asia/Kolkata", "--enable-digest", "--json"],
    )
    assert settings.exit_code == 0, settings.output
    assert json.loads(settings.output)["daily_digest_enabled"] is True
    digest = runner.invoke(app, ["notifications", "digest", "--json"])
    assert digest.exit_code == 0, digest.output
    assert "Nothing material" in json.loads(digest.output)["text"]


def test_notification_cli_event_mute(cli_env):
    runner.invoke(app, ["init-db"])
    muted = runner.invoke(
        app, ["notifications", "settings", "--mute-event", "provider_failure", "--json"],
    )
    assert muted.exit_code == 0, muted.output
    assert "provider_failure" in json.loads(muted.output)["muted_event_types"]
