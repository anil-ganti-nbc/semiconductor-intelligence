from __future__ import annotations

from typer.testing import CliRunner

from semi_intel.cli import app

runner = CliRunner()


def test_full_claim_workflow(cli_env):
    r = runner.invoke(app, ["init-db"])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["source", "add", "Golden Pig", "--type", "social", "--trust-weight", "0.7"])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["entity", "add", "Nova Lake", "--type", "product"])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["entity", "add", "Intel", "--type", "company"])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["entity", "relate", "1", "2", "--type", "manufactured_by"])
    assert r.exit_code == 0, r.output

    r = runner.invoke(
        app,
        ["evidence", "add", "1", "--title", "leak post", "--content", "Nova Lake uses 18A-P", "--entity-id", "1"],
    )
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["claim", "create", "Nova Lake uses Intel 18A-P", "--subject-entity-id", "1"])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["claim", "link-evidence", "1", "1", "--stance", "supports"])
    assert r.exit_code == 0, r.output
    assert "New confidence" in r.output

    r = runner.invoke(app, ["claim", "show", "1"])
    assert r.exit_code == 0, r.output
    assert "Nova Lake uses Intel 18A-P" in r.output

    r = runner.invoke(app, ["claim", "timeline", "1"])
    assert r.exit_code == 0, r.output
    assert "evidence_linked" in r.output

    r = runner.invoke(app, ["entity", "show", "1"])
    assert r.exit_code == 0, r.output
    assert "manufactured_by" in r.output

    r = runner.invoke(app, ["claim", "resolve", "1", "--status", "confirmed", "--note", "launched with 24GB"])
    assert r.exit_code == 0, r.output


def test_duplicate_entity_rejected(cli_env):
    runner.invoke(app, ["init-db"])
    runner.invoke(app, ["entity", "add", "Nova Lake", "--type", "product"])
    r = runner.invoke(app, ["entity", "add", "Nova Lake", "--type", "product"])
    assert r.exit_code != 0


def test_duplicate_evidence_rejected(cli_env):
    runner.invoke(app, ["init-db"])
    runner.invoke(app, ["source", "add", "Chiphell", "--type", "forum"])
    runner.invoke(app, ["evidence", "add", "1", "--title", "t", "--content", "same text"])
    r = runner.invoke(app, ["evidence", "add", "1", "--title", "t2", "--content", "same text"])
    assert r.exit_code != 0


def test_duplicate_rss_url_rejected_across_normalized_forms(cli_env):
    runner.invoke(app, ["init-db"])
    first = runner.invoke(
        app,
        [
            "source", "add", "Original Feed", "--type", "rss",
            "--url", "http://www.example.com/feed/?utm_source=first",
        ],
    )
    assert first.exit_code == 0, first.output

    duplicate = runner.invoke(
        app,
        [
            "source", "add", "Duplicate Feed", "--type", "rss",
            "--url", "https://example.com/feed",
        ],
    )
    assert duplicate.exit_code != 0
    assert "already registered" in duplicate.output
