"""CLI-level tests for `semi-intel ingest ...`. The underlying network calls
are patched out so this suite never touches the internet -- feedparser.parse
and urllib.request.urlopen are swapped for fixture-backed fakes."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import semi_intel.ingestion.plugins.pci_ids_plugin as pci_ids_plugin
import semi_intel.ingestion.plugins.rss_plugin as rss_plugin
from semi_intel.cli import app

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"


def test_ingest_rss_command(cli_env, monkeypatch):
    xml = (FIXTURES / "sample_feed.xml").read_text()
    monkeypatch.setattr(rss_plugin, "_default_fetch", lambda url: __import__("feedparser").parse(xml))

    runner.invoke(app, ["init-db"])
    r = runner.invoke(app, ["ingest", "rss", "Fake Hardware News", "https://example.com/rss"])

    assert r.exit_code == 0, r.output
    assert "2 new" in r.output

    r = runner.invoke(app, ["source", "list"])
    assert "Fake Hardware News" in r.output

    r = runner.invoke(app, ["evidence", "list"])
    assert len(r.output.strip().splitlines()) == 2


def test_ingest_pci_ids_command(cli_env, monkeypatch):
    text = (FIXTURES / "sample_pci_ids.txt").read_text()
    monkeypatch.setattr(pci_ids_plugin, "_default_fetch", lambda url: text)

    runner.invoke(app, ["init-db"])
    r = runner.invoke(app, ["ingest", "pci-ids"])

    assert r.exit_code == 0, r.output
    assert "3 new" in r.output

    # re-running should be a no-op
    r = runner.invoke(app, ["ingest", "pci-ids"])
    assert "0 new" in r.output
    assert "3 duplicate" in r.output
