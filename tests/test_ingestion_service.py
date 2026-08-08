"""IngestionService owns find-or-create-source and dedup -- these tests hit
a real (temp-file sqlite) database to prove that plumbing end to end."""

from __future__ import annotations

from pathlib import Path

import feedparser

from semi_intel.domain.enums import SourceType
from semi_intel.ingestion.plugins.pci_ids_plugin import PciIdsSourcePlugin
from semi_intel.ingestion.plugins.rss_plugin import RSSSourcePlugin
from semi_intel.ingestion.service import IngestionService
from semi_intel.repository.repositories import EvidenceRepository, SourceRepository

FIXTURES = Path(__file__).parent / "fixtures"


def test_run_creates_source_and_evidence(db_session):
    xml = (FIXTURES / "sample_feed.xml").read_text()
    plugin = RSSSourcePlugin(
        name="Fake Hardware News",
        feed_url="https://example.com/rss",
        default_trust_weight=0.6,
        fetch_fn=lambda _url: feedparser.parse(xml),
    )

    result = IngestionService(db_session).run(plugin)

    assert result.created == 2
    assert result.skipped_duplicate == 0
    assert result.errors == 0

    source = SourceRepository(db_session).find_by_name("Fake Hardware News")
    assert source is not None
    assert source.type == SourceType.RSS
    assert source.trust_weight == 0.6
    assert len(EvidenceRepository(db_session).for_entity(0)) == 0  # no entity linked yet
    assert len(EvidenceRepository(db_session).list()) == 2


def test_rerun_is_idempotent_and_reuses_the_source(db_session):
    xml = (FIXTURES / "sample_feed.xml").read_text()
    plugin = RSSSourcePlugin(
        name="Fake Hardware News",
        feed_url="https://example.com/rss",
        fetch_fn=lambda _url: feedparser.parse(xml),
    )
    service = IngestionService(db_session)

    first = service.run(plugin)
    second = service.run(plugin)

    assert first.created == 2
    assert second.created == 0
    assert second.skipped_duplicate == 2
    assert len(SourceRepository(db_session).list()) == 1


def test_pci_ids_ingestion_only_creates_new_devices_on_second_run(db_session):
    initial_text = (FIXTURES / "sample_pci_ids.txt").read_text()
    plugin = PciIdsSourcePlugin(fetch_fn=lambda _url: initial_text)
    service = IngestionService(db_session)

    first = service.run(plugin)
    assert first.created == 3

    updated_text = initial_text + "\t2783  AD103 [GeForce RTX 4080 SUPER]\n"
    # Rebuild the vendor context: appending after the class-section break
    # means the new line wouldn't be reached, so simulate a fresh fetch that
    # still has 10de active by inserting before the break instead.
    updated_text = initial_text.replace(
        "\t2704  AD102 [GeForce RTX 4090]",
        "\t2704  AD102 [GeForce RTX 4090]\n\t2783  AD103 [GeForce RTX 4080 SUPER]",
    )
    plugin2 = PciIdsSourcePlugin(fetch_fn=lambda _url: updated_text)
    second = service.run(plugin2)

    assert second.created == 1
    assert second.skipped_duplicate == 3

    evidence = EvidenceRepository(db_session).list()
    assert any(e.external_id == "10de:2783" for e in evidence)
