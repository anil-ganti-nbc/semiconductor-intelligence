"""CLI wiring for `web serve` -- not the actual server startup (uvicorn.run
blocks forever, which isn't something to exercise via CliRunner). Just
confirms the command is registered and its help text renders, which is
enough to prove the lazy-import guard in cli.py doesn't break command
registration for users without the `web` extra installed.
"""

from __future__ import annotations

from typer.testing import CliRunner

from semi_intel.cli import app

runner = CliRunner()


def test_web_serve_is_registered():
    r = runner.invoke(app, ["web", "--help"])
    assert r.exit_code == 0, r.output
    assert "serve" in r.output


def test_web_serve_help():
    r = runner.invoke(app, ["web", "serve", "--help"])
    assert r.exit_code == 0, r.output
    assert "--host" in r.output
    assert "--port" in r.output
    assert ".[web]" in r.output  # regression check: rich markup must not eat the brackets


def test_web_serve_rejects_non_loopback_host():
    r = runner.invoke(app, ["web", "serve", "--host", "0.0.0.0"])
    assert r.exit_code == 2
    assert "must be loopback" in r.output
