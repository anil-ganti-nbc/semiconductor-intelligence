from __future__ import annotations

from typer.testing import CliRunner

from semi_intel.cli import app

runner = CliRunner()


def test_story_rank_command(cli_env):
    runner.invoke(app, ["init-db"])
    runner.invoke(app, ["claim", "create", "A freshly created rumor"])

    r = runner.invoke(app, ["story", "rank"])
    assert r.exit_code == 0, r.output
    assert "A freshly created rumor" in r.output
    assert "score=" in r.output


def test_story_rank_no_open_claims(cli_env):
    runner.invoke(app, ["init-db"])
    r = runner.invoke(app, ["story", "rank"])
    assert r.exit_code == 0, r.output
    assert "No open claims" in r.output


def test_story_rank_respects_limit(cli_env):
    runner.invoke(app, ["init-db"])
    for i in range(3):
        runner.invoke(app, ["claim", "create", f"Rumor {i}"])

    r = runner.invoke(app, ["story", "rank", "--limit", "1"])
    assert r.exit_code == 0, r.output
    assert r.output.count("#") == 1
