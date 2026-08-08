"""CLI-level tests for `semi-intel pipeline ...`. Network calls are patched
out the same way test_cli_ingest.py does it -- this suite never touches the
internet."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import semi_intel.ingestion.plugins.pci_ids_plugin as pci_ids_plugin
import semi_intel.ingestion.plugins.rss_plugin as rss_plugin
from semi_intel.cli import app

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"


def _patch_fetchers(monkeypatch):
    xml = (FIXTURES / "sample_feed.xml").read_text()
    pci_text = (FIXTURES / "sample_pci_ids.txt").read_text()
    monkeypatch.setattr(rss_plugin, "_default_fetch", lambda url: __import__("feedparser").parse(xml))
    monkeypatch.setattr(pci_ids_plugin, "_default_fetch", lambda url: pci_text)


def test_pipeline_run_polls_registered_sources_and_pci_ids(cli_env, monkeypatch):
    _patch_fetchers(monkeypatch)
    runner.invoke(app, ["init-db"])
    runner.invoke(app, ["source", "add", "Fake Hardware News", "--type", "rss", "--url", "https://example.com/rss"])

    r = runner.invoke(app, ["pipeline", "run"])

    assert r.exit_code == 0, r.output
    assert "Fake Hardware News" in r.output
    assert "PCI ID Repository" in r.output
    assert "suggestions:" in r.output

    r = runner.invoke(app, ["evidence", "list"])
    assert len(r.output.strip().splitlines()) == 5  # 2 rss + 3 pci-ids


def test_pipeline_run_is_idempotent(cli_env, monkeypatch):
    _patch_fetchers(monkeypatch)
    runner.invoke(app, ["init-db"])
    runner.invoke(app, ["source", "add", "Fake Hardware News", "--type", "rss", "--url", "https://example.com/rss"])

    runner.invoke(app, ["pipeline", "run"])
    r = runner.invoke(app, ["pipeline", "run"])

    assert r.exit_code == 0, r.output
    assert "2 duplicate(s)" in r.output
    assert "3 duplicate(s)" in r.output


def test_pipeline_run_skip_pci_ids_flag(cli_env, monkeypatch):
    _patch_fetchers(monkeypatch)
    runner.invoke(app, ["init-db"])
    runner.invoke(app, ["source", "add", "Fake Hardware News", "--type", "rss", "--url", "https://example.com/rss"])

    r = runner.invoke(app, ["pipeline", "run", "--skip-pci-ids"])

    assert r.exit_code == 0, r.output
    assert "PCI ID Repository" not in r.output

    r = runner.invoke(app, ["evidence", "list"])
    assert len(r.output.strip().splitlines()) == 2  # rss only


def test_pipeline_run_with_no_registered_sources(cli_env, monkeypatch):
    _patch_fetchers(monkeypatch)
    runner.invoke(app, ["init-db"])

    r = runner.invoke(app, ["pipeline", "run"])

    assert r.exit_code == 0, r.output
    assert "PCI ID Repository" in r.output  # pci-ids still runs even with zero rss sources registered
