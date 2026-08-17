"""PipelineService.run_once() ties together ingestion (every registered RSS
source + pci.ids) and the suggestion scanner in one idempotent pass. These
tests hit a real (temp-file sqlite) database and fixture-backed fetch
functions -- no live network, same pattern as test_ingestion_service.py."""

from __future__ import annotations

from pathlib import Path

import feedparser

from semi_intel.domain.enums import EntityType, SourceType
from semi_intel.domain.models import Entity, Source, SignalItem
from semi_intel.ingestion.plugins.rss_plugin import RSSSourcePlugin
from semi_intel.ingestion.plugins import pci_ids_plugin as pci_ids_plugin_module
from semi_intel.ingestion.plugins import rss_plugin as rss_plugin_module
from semi_intel.pipeline.service import PipelineService
from semi_intel.repository.repositories import (
    ClaimRepository,
    EvidenceRepository,
    SourceRepository,
    SuggestionRepository,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _patch_fetchers(monkeypatch):
    xml = (FIXTURES / "sample_feed.xml").read_text()
    pci_text = (FIXTURES / "sample_pci_ids.txt").read_text()
    monkeypatch.setattr(rss_plugin_module, "_default_fetch", lambda url: feedparser.parse(xml))
    monkeypatch.setattr(pci_ids_plugin_module, "_default_fetch", lambda url: pci_text)


def test_run_once_polls_every_registered_rss_source_and_pci_ids(db_session, monkeypatch):
    _patch_fetchers(monkeypatch)
    db_session.add(
        Source(name="Fake Hardware News", type=SourceType.RSS, url="https://example.com/rss", trust_weight=0.6)
    )
    db_session.commit()

    result = PipelineService(db_session).run_once()

    # one result for the registered rss source, one for pci-ids
    assert len(result.ingestion_results) == 2
    names = {r.source_name for r in result.ingestion_results}
    assert names == {"Fake Hardware News", "PCI ID Repository"}
    assert sum(r.created for r in result.ingestion_results) == 5  # 2 rss + 3 pci-ids

    assert len(EvidenceRepository(db_session).list()) == 5
    assert result.suggestion_result is not None


def test_run_once_mines_handle_suggestions_from_signal_text(db_session, monkeypatch):
    """Regression test for the v0.9.3 diversity investigation: before this,
    refresh_handle_suggestions() was only reachable via an explicit POST to
    /api/radar/source-suggestions/refresh that no automated job or UI
    control ever called, so the Suggested Sources queue could only ever
    grow via the one-time legacy import. run_once() must mine handle
    suggestions from already-collected SignalItem text every cycle, the
    same way it already runs clustering/scoring every cycle."""
    _patch_fetchers(monkeypatch)
    social_source = Source(name="Aggregator", type=SourceType.SOCIAL, provider="rss")
    db_session.add(social_source)
    db_session.commit()
    for i in range(4):
        db_session.add(SignalItem(
            source_id=social_source.id, provider="rss", external_id=str(i),
            raw_payload="{}", content_hash=f"h-{i}",
            normalized_text="According to VideoCardz, RTX 50 Super ships with 24GB VRAM.",
        ))
    db_session.commit()

    result = PipelineService(db_session).run_once()

    assert result.handle_suggestions_created_or_updated == 1


def test_legacy_and_signal_rss_paths_never_collect_the_same_source(db_session, monkeypatch):
    """A source explicitly registered through the new Signal Radar pipeline
    (provider="rss") must never also be polled by the legacy direct-to-
    Evidence RSS plugin path, even though both are type=RSS with a url --
    otherwise the same feed would be collected twice per cycle, once
    straight into Evidence and once into SignalItem. A legacy source
    (provider="manual", the default for every pre-merge/CLI-added source)
    must still be polled by the old path exactly as before -- this is not
    a behavior change for existing installations."""
    _patch_fetchers(monkeypatch)
    legacy_source = Source(
        name="Legacy Feed", type=SourceType.RSS, url="https://example.com/rss", trust_weight=0.6,
        provider="manual",
    )
    signal_source = Source(
        name="New Signal Feed", type=SourceType.RSS, url="https://example.com/rss2", trust_weight=0.6,
        provider="rss", provider_key="https://example.com/rss2", polling_enabled=True, enabled=True,
    )
    db_session.add_all([legacy_source, signal_source])
    db_session.commit()

    polled = PipelineService(db_session)._rss_sources_to_poll()

    assert [s.id for s in polled] == [legacy_source.id]  # only the legacy source, never the signal one


def test_disabled_legacy_rss_source_is_never_polled_automatically(db_session):
    source = Source(
        name="Disabled Legacy Feed", type=SourceType.RSS,
        url="https://example.com/disabled", provider="manual", enabled=False,
    )
    db_session.add(source)
    db_session.commit()
    assert PipelineService(db_session)._rss_sources_to_poll() == []


def test_run_once_skips_pci_ids_when_requested(db_session, monkeypatch):
    _patch_fetchers(monkeypatch)

    result = PipelineService(db_session).run_once(include_pci_ids=False)

    assert result.ingestion_results == []  # no rss sources registered, pci-ids skipped
    assert result.suggestion_result is not None


def test_run_once_ignores_non_rss_sources(db_session, monkeypatch):
    _patch_fetchers(monkeypatch)
    db_session.add(Source(name="Golden Pig", type=SourceType.SOCIAL, trust_weight=0.7))
    db_session.commit()

    result = PipelineService(db_session).run_once(include_pci_ids=False)

    assert result.ingestion_results == []


def test_run_once_is_idempotent(db_session, monkeypatch):
    _patch_fetchers(monkeypatch)
    db_session.add(
        Source(name="Fake Hardware News", type=SourceType.RSS, url="https://example.com/rss", trust_weight=0.6)
    )
    db_session.commit()
    service = PipelineService(db_session)

    first = service.run_once()
    second = service.run_once()

    assert sum(r.created for r in first.ingestion_results) == 5
    assert sum(r.created for r in second.ingestion_results) == 0
    assert sum(r.skipped_duplicate for r in second.ingestion_results) == 5
    assert len(SourceRepository(db_session).list()) == 2  # rss source + pci-ids source, neither re-created


def test_run_once_creates_suggestions_for_matching_evidence(db_session, monkeypatch):
    _patch_fetchers(monkeypatch)
    db_session.add(
        Source(name="Fake Hardware News", type=SourceType.RSS, url="https://example.com/rss", trust_weight=0.6)
    )
    entity = Entity(type=EntityType.PRODUCT, name="RTX 4090", aliases="[]", attributes="{}")
    db_session.add(entity)
    db_session.commit()

    claim = ClaimRepository(db_session).create(statement="RTX 4090 rumor claim", subject_entity_id=entity.id)

    PipelineService(db_session).run_once(include_pci_ids=False)

    # whether a suggestion actually fires depends on the fixture evidence's
    # wording matching the claim/entity -- what matters here is that the
    # suggestion scanner ran across the newly ingested evidence without error
    # and that pending suggestions (if any) are reviewable through the normal
    # human-in-the-loop path, not auto-applied.
    suggestions = SuggestionRepository(db_session).list_by_status(None)
    for s in suggestions:
        assert s.status.name == "PENDING"
    links = ClaimRepository(db_session).links_for(claim)
    assert links == []  # nothing auto-linked -- suggestions require accept


def test_a_failing_source_does_not_abort_the_rest_of_the_run(db_session, monkeypatch):
    """One dead RSS feed (e.g. a timeout) must not prevent other registered
    sources, pci.ids, or the suggestion scan from running -- this is the
    scenario that broke before PipelineService isolated each source's
    ingestion call."""
    _patch_fetchers(monkeypatch)
    db_session.add(
        Source(name="Dead Feed", type=SourceType.RSS, url="https://example.com/dead", trust_weight=0.5)
    )
    db_session.add(
        Source(name="Fake Hardware News", type=SourceType.RSS, url="https://example.com/rss", trust_weight=0.6)
    )
    db_session.commit()

    def _boom(url):
        raise TimeoutError("simulated network timeout")

    original_run = PipelineService._run_source

    def patched_run_source(self, plugin, result):
        if plugin.name == "Dead Feed":
            plugin._fetch_fn = _boom
        return original_run(self, plugin, result)

    monkeypatch.setattr(PipelineService, "_run_source", patched_run_source)

    result = PipelineService(db_session).run_once()

    # the dead feed is recorded as a failure, not a crash
    assert len(result.failures) == 1
    assert result.failures[0].source_name == "Dead Feed"
    assert "simulated network timeout" in result.failures[0].error

    # the healthy rss source and pci-ids still ran
    names = {r.source_name for r in result.ingestion_results}
    assert names == {"Fake Hardware News", "PCI ID Repository"}
    assert sum(r.created for r in result.ingestion_results) == 5

    # the suggestion scan still ran despite the earlier failure
    assert result.suggestion_result is not None

    # str(result) surfaces the failure alongside the successful results
    assert "Dead Feed: FAILED" in str(result)


def test_a_failing_suggestion_scan_does_not_lose_ingestion_results(db_session, monkeypatch):
    _patch_fetchers(monkeypatch)
    db_session.add(
        Source(name="Fake Hardware News", type=SourceType.RSS, url="https://example.com/rss", trust_weight=0.6)
    )
    db_session.commit()

    service = PipelineService(db_session)

    def _boom():
        raise RuntimeError("simulated suggestion engine bug")

    monkeypatch.setattr(service.suggestion_service, "run", _boom)

    result = service.run_once(include_pci_ids=False)

    assert result.suggestion_result is None
    assert len(result.failures) == 1
    assert result.failures[0].source_name == "suggestion scan"
    assert sum(r.created for r in result.ingestion_results) == 2  # ingestion still succeeded


def test_a_failing_targeted_discovery_does_not_abort_pipeline(db_session, monkeypatch):
    from semi_intel.discovery.service import DiscoveryService

    def _boom(self):
        raise TimeoutError("discovery provider unavailable")

    monkeypatch.setattr(DiscoveryService, "run_eligible", _boom)
    result = PipelineService(db_session).run_once(include_pci_ids=False)
    assert any(f.source_name == "targeted discovery" for f in result.failures)
