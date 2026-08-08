from __future__ import annotations

from typer.testing import CliRunner

from semi_intel.cli import app

runner = CliRunner()


def test_check_memory_config_flags_the_briefs_own_example(cli_env):
    r = runner.invoke(
        app,
        ["check", "memory-config", "--bus-width", "384", "--chip-density-gbit", "16", "--total-gb", "16"],
    )
    assert r.exit_code == 0, r.output
    assert "CONTRADICTION" in r.output
    assert "24" in r.output


def test_check_memory_config_passes_a_real_configuration(cli_env):
    r = runner.invoke(
        app,
        ["check", "memory-config", "--bus-width", "384", "--chip-density-gbit", "16", "--total-gb", "24"],
    )
    assert r.exit_code == 0, r.output
    assert "CONSISTENT" in r.output


def test_claim_create_memory_spec_and_review(cli_env):
    runner.invoke(app, ["init-db"])

    r = runner.invoke(
        app,
        [
            "claim",
            "create-memory-spec",
            "Leaked slides: 384-bit, 16GB, 16Gbit chips",
            "--bus-width",
            "384",
            "--chip-density-gbit",
            "16",
            "--total-gb",
            "16",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "CONTRADICTION" in r.output

    r = runner.invoke(app, ["claim", "memory-spec", "1"])
    assert r.exit_code == 0, r.output
    assert "384-bit" in r.output
    assert "CONTRADICTION" in r.output

    r = runner.invoke(app, ["claim", "timeline", "1"])
    assert r.exit_code == 0, r.output
    assert "contradiction_detected" in r.output

    # the claim itself stays open and untouched -- the engine only surfaces,
    # a human decides what a contradiction means for the claim
    r = runner.invoke(app, ["claim", "show", "1"])
    assert "[open]" in r.output


def test_claim_memory_spec_not_found_for_plain_claim(cli_env):
    runner.invoke(app, ["init-db"])
    runner.invoke(app, ["claim", "create", "A plain claim with no memory spec"])

    r = runner.invoke(app, ["claim", "memory-spec", "1"])
    assert r.exit_code != 0
