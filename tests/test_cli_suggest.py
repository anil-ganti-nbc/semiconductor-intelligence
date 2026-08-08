from __future__ import annotations

from typer.testing import CliRunner

from semi_intel.cli import app

runner = CliRunner()


def _seed(cli_env):
    runner.invoke(app, ["init-db"])
    runner.invoke(app, ["source", "add", "VideoCardz", "--type", "rss", "--trust-weight", "0.8"])
    runner.invoke(app, ["entity", "add", "Nova Lake", "--type", "product"])
    runner.invoke(app, ["claim", "create", "Nova Lake uses Intel 18A-P", "--subject-entity-id", "1"])
    runner.invoke(
        app,
        [
            "evidence",
            "add",
            "1",
            "--title",
            "Leak: Nova Lake spotted",
            "--content",
            "New leak shows Nova Lake using Intel's 18A-P node in samples",
        ],
    )


def test_evidence_entities_command(cli_env):
    _seed(cli_env)
    r = runner.invoke(app, ["evidence", "entities", "1"])
    assert r.exit_code == 0, r.output
    assert "Nova Lake" in r.output


def test_suggest_run_list_show_accept_workflow(cli_env):
    _seed(cli_env)

    r = runner.invoke(app, ["suggest", "run"])
    assert r.exit_code == 0, r.output
    assert "1 new suggestion" in r.output

    r = runner.invoke(app, ["suggest", "list"])
    assert r.exit_code == 0, r.output
    assert "pending" in r.output

    r = runner.invoke(app, ["suggest", "show", "1"])
    assert r.exit_code == 0, r.output
    assert "Nova Lake uses Intel 18A-P" in r.output
    assert "reasons:" in r.output

    r = runner.invoke(app, ["suggest", "accept", "1", "--stance", "supports"])
    assert r.exit_code == 0, r.output
    assert "New confidence" in r.output

    r = runner.invoke(app, ["claim", "show", "1"])
    assert "supports" in r.output

    # re-running suggest run should not re-propose an already-resolved pair
    r = runner.invoke(app, ["suggest", "run"])
    assert "0 new suggestion" in r.output


def test_suggest_reject_workflow(cli_env):
    _seed(cli_env)
    runner.invoke(app, ["suggest", "run"])

    r = runner.invoke(app, ["suggest", "reject", "1", "--note", "false positive"])
    assert r.exit_code == 0, r.output
    assert "rejected" in r.output

    r = runner.invoke(app, ["suggest", "list", "--status", "rejected"])
    assert "#1" in r.output

    # a rejected suggestion cannot be accepted afterwards
    r = runner.invoke(app, ["suggest", "accept", "1", "--stance", "supports"])
    assert r.exit_code != 0
