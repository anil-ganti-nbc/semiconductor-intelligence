from __future__ import annotations

from typer.testing import CliRunner

from semi_intel.cli import app

runner = CliRunner()


def _seed(cli_env):
    runner.invoke(app, ["init-db"])
    runner.invoke(app, ["entity", "add", "Nova Lake", "--type", "product"])
    runner.invoke(app, ["entity", "add", "Intel", "--type", "company"])
    runner.invoke(app, ["entity", "add", "18A-P", "--type", "foundry_node"])
    runner.invoke(app, ["entity", "relate", "1", "2", "--type", "manufactured_by"])
    runner.invoke(app, ["entity", "relate", "1", "3", "--type", "uses_node"])


def test_graph_related_command(cli_env):
    _seed(cli_env)
    r = runner.invoke(app, ["graph", "related", "1"])
    assert r.exit_code == 0, r.output
    assert "Intel" in r.output
    assert "18A-P" in r.output


def test_graph_related_not_found(cli_env):
    runner.invoke(app, ["init-db"])
    r = runner.invoke(app, ["graph", "related", "999"])
    assert r.exit_code != 0


def test_graph_find_command(cli_env):
    _seed(cli_env)
    r = runner.invoke(app, ["graph", "find", "--relation-type", "uses_node", "--target", "18A-P"])
    assert r.exit_code == 0, r.output
    assert "Nova Lake" in r.output
    assert "18A-P" in r.output


def test_graph_find_no_matches(cli_env):
    _seed(cli_env)
    r = runner.invoke(app, ["graph", "find", "--relation-type", "competes_with"])
    assert r.exit_code == 0, r.output
    assert "No matches" in r.output
