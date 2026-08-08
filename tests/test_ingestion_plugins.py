"""Plugin-level unit tests: pure fetch-and-parse, no database, no network.

Each plugin gets an injected fetch_fn that reads a local fixture instead of
hitting the real feed / pci.ids URL, so these tests run offline and fast.
"""

from __future__ import annotations

from pathlib import Path

import feedparser

from semi_intel.ingestion.plugins.pci_ids_plugin import PciIdsSourcePlugin
from semi_intel.ingestion.plugins.rss_plugin import RSSSourcePlugin

FIXTURES = Path(__file__).parent / "fixtures"


def test_rss_plugin_parses_fixture_feed_into_raw_items():
    xml = (FIXTURES / "sample_feed.xml").read_text()
    plugin = RSSSourcePlugin(
        name="Fake Hardware News",
        feed_url="https://example.com/rss",
        fetch_fn=lambda _url: feedparser.parse(xml),
    )
    items = list(plugin.fetch())

    assert len(items) == 2
    assert items[0].title == "Nova Lake spotted with 18A-P process node"
    assert "18A-P" in items[0].content
    assert items[0].external_id == "https://example.com/nova-lake-18a-p"
    assert items[0].observed_at is not None
    assert items[1].title.startswith("RTX 5080 Super")


def test_pci_ids_plugin_parses_vendor_device_pairs_and_stops_at_class_section():
    text = (FIXTURES / "sample_pci_ids.txt").read_text()
    plugin = PciIdsSourcePlugin(fetch_fn=lambda _url: text)
    items = list(plugin.fetch())

    external_ids = {item.external_id for item in items}
    assert external_ids == {"10de:2782", "10de:2704", "8086:a780"}

    rtx4090 = next(i for i in items if i.external_id == "10de:2704")
    assert "AD102" in rtx4090.content
    assert "NVIDIA Corporation" in rtx4090.content

    # subvendor/subdevice lines and the device-class section must not leak through
    assert not any("Motherboard model" in i.content for i in items)
    assert not any(i.external_id and i.external_id.startswith("00:") for i in items)


def test_pci_ids_plugin_ignores_malformed_lines_gracefully():
    text = "not a vendor line at all\n\tstray device with no vendor\n"
    plugin = PciIdsSourcePlugin(fetch_fn=lambda _url: text)
    assert list(plugin.fetch()) == []
