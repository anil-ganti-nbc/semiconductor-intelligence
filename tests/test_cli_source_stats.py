from __future__ import annotations

from typer.testing import CliRunner

from semi_intel.cli import app

runner = CliRunner()


def test_source_stats_no_track_record(cli_env):
    runner.invoke(app, ["init-db"])
    runner.invoke(app, ["source", "add", "Golden Pig", "--type", "social"])

    r = runner.invoke(app, ["source", "stats", "1"])
    assert r.exit_code == 0, r.output
    assert "no track record yet" in r.output


def test_source_stats_and_rank_after_resolution(cli_env):
    runner.invoke(app, ["init-db"])
    runner.invoke(app, ["source", "add", "Golden Pig", "--type", "social"])
    runner.invoke(app, ["entity", "add", "Nova Lake", "--type", "product"])
    runner.invoke(app, ["claim", "create", "Nova Lake uses 18A-P", "--subject-entity-id", "1"])
    runner.invoke(app, ["evidence", "add", "1", "--title", "t", "--content", "c"])
    runner.invoke(app, ["claim", "link-evidence", "1", "1", "--stance", "supports"])
    runner.invoke(app, ["claim", "resolve", "1", "--status", "confirmed"])

    r = runner.invoke(app, ["source", "stats", "1"])
    assert r.exit_code == 0, r.output
    assert "1/1 correct (100%)" in r.output

    r = runner.invoke(app, ["source", "rank"])
    assert r.exit_code == 0, r.output
    assert "Golden Pig" in r.output
    assert "100%" in r.output
