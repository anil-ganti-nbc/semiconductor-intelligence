"""PipelineService.run_once(): the single entry point a scheduler calls.

Steps, all idempotent -- running this every 15 minutes forever is safe:

  1. For every existing `Source` row with type=RSS and a `url` set, run the
     RSS ingestion plugin against it. Registering a feed for auto-polling is
     just `source add <name> --type rss --url <feed-url>` -- no separate
     config file, because Source already has a url column and IngestionService
     already knows how to find-or-create by name.
  2. Run the pci.ids ingestion plugin once (fixed source, no registration
     needed).
  3. Run the claim-suggestion scanner across all evidence.

Each step's result is collected into PipelineResult and returned; nothing
about pipeline orchestration lives anywhere else, so the CLI's `pipeline
run`/`pipeline loop` commands and any future scheduler integration share
this one implementation.

Fault isolation: this is the one command an external scheduler calls
unattended, forever. A single dead/unreachable/misbehaving source (a feed
that times out, a DNS failure, a malformed response) must not take down the
rest of the run. IngestionService already isolates per-item errors within
one source's fetch, but a failure raised by the fetch itself (e.g.
urllib.request.urlopen timing out before yielding anything) propagates past
that -- so PipelineService wraps each source's ingestion call, and the
suggestion scan, in their own try/except. A failed source is recorded in
`PipelineResult.failures` and reported, but every other source (and the
suggestion scan) still runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.claim_engine.suggestion_service import SuggestionRunResult, SuggestionService
from semi_intel.domain.enums import SourceType
from semi_intel.domain.models import Source
from semi_intel.editorial.service import EditorialDiscoveryService
from semi_intel.discovery.service import DiscoveryService
from semi_intel.ingestion.base import SourcePlugin
from semi_intel.ingestion.plugins.pci_ids_plugin import PciIdsSourcePlugin
from semi_intel.ingestion.plugins.rss_plugin import RSSSourcePlugin
from semi_intel.ingestion.service import IngestionResult, IngestionService
from semi_intel.signals.analysis import analyze_unprocessed
from semi_intel.signals.candidate_state import mark_stale_candidates, wake_snoozed
from semi_intel.signals.clustering import ClusteringSummary, cluster_unclustered_items
from semi_intel.signals.collection import CollectionService, CollectionSummary
from semi_intel.signals.promotion import AutoPromotionSummary, run_automatic_promotion
from semi_intel.signals.scoring import get_scoring_settings, rescore_active_candidates
from semi_intel.signals.suggestions import refresh_handle_suggestions
from semi_intel.notifications.service import NotificationService
from semi_intel.notifications.digest import DigestService


@dataclass
class SourceFailure:
    source_name: str
    error: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.source_name}: FAILED -- {self.error}"


@dataclass
class PipelineResult:
    ingestion_results: List[IngestionResult] = field(default_factory=list)
    failures: List[SourceFailure] = field(default_factory=list)
    suggestion_result: Optional[SuggestionRunResult] = None
    discovery_runs: int = 0
    signal_collection: Optional[CollectionSummary] = None
    signal_items_analyzed: int = 0
    signal_clustering: Optional[ClusteringSummary] = None
    signal_candidates_scored: int = 0
    handle_suggestions_created_or_updated: int = 0
    signal_promotion: Optional[AutoPromotionSummary] = None
    notifications_created: int = 0
    digest_id: Optional[int] = None

    def __str__(self) -> str:  # pragma: no cover - display only
        lines = [str(r) for r in self.ingestion_results]
        lines.extend(str(f) for f in self.failures)
        if self.suggestion_result:
            lines.append(
                f"suggestions: scanned {self.suggestion_result.evidence_scanned} evidence item(s), "
                f"{self.suggestion_result.suggestions_created} new suggestion(s)"
            )
        if self.signal_collection is not None and self.signal_collection.runs:
            lines.append(
                f"signal collection: {self.signal_collection.items_collected} item(s), "
                f"{len(self.signal_collection.failures)} source failure(s)"
            )
        if self.signal_items_analyzed:
            lines.append(f"signal analysis: {self.signal_items_analyzed} item(s) analyzed")
        if self.signal_clustering is not None and self.signal_clustering.items_processed:
            lines.append(
                f"signal clustering: {self.signal_clustering.attached_to_existing} attached, "
                f"{self.signal_clustering.new_candidates} new candidate(s), "
                f"{self.signal_clustering.suppressed_no_topic_or_artifact} suppressed"
            )
        if self.signal_candidates_scored:
            lines.append(f"signal scoring: {self.signal_candidates_scored} candidate(s) rescored")
        if self.handle_suggestions_created_or_updated:
            lines.append(
                f"handle suggestions: {self.handle_suggestions_created_or_updated} "
                "created/updated"
            )
        if self.signal_promotion is not None and (self.signal_promotion.promoted or self.signal_promotion.skipped):
            lines.append(
                f"signal promotion: {len(self.signal_promotion.promoted)} promoted, "
                f"{len(self.signal_promotion.skipped)} skipped"
                + (" (budget exhausted)" if self.signal_promotion.budget_exhausted else "")
            )
        if self.notifications_created:
            lines.append(f"notifications: {self.notifications_created} new in-app alert(s)")
        if self.digest_id:
            lines.append(f"daily digest: #{self.digest_id}")
        return "\n".join(lines)


class PipelineService:
    def __init__(self, session: Session):
        self.session = session
        self.ingestion_service = IngestionService(session)
        self.suggestion_service = SuggestionService(session)
        self.collection_service = CollectionService(session)

    def run_once(self, include_pci_ids: bool = True, loop_started_at=None) -> PipelineResult:
        result = PipelineResult()

        for source in self._rss_sources_to_poll():
            plugin = RSSSourcePlugin(
                name=source.name,
                feed_url=source.url,
                default_trust_weight=source.trust_weight,
            )
            self._run_source(plugin, result)

        if include_pci_ids:
            self._run_source(PciIdsSourcePlugin(), result)

        # New Signal Radar-absorption collection path (brief section 16):
        # separate from the RSS/pci_ids plugins above, gated behind
        # SignalCollectionSettings.collection_enabled (default off), and
        # isolated exactly like every other stage -- a collection failure
        # must not prevent suggestions/editorial backfill/discovery from
        # still running this cycle.
        try:
            result.signal_collection = self.collection_service.collect_due_sources(
                automatic=True, loop_started_at=loop_started_at,
            )
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            result.failures.append(SourceFailure(source_name="signal collection", error=str(exc)))

        # Analysis has no settings gate of its own -- it never touches the
        # network (pure text over already-collected SignalItems) and is safe
        # to run every cycle even while collection itself stays disabled, so
        # any item collected through another path (e.g. a future importer)
        # still gets analyzed.
        try:
            result.signal_items_analyzed = analyze_unprocessed(self.session)
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            result.failures.append(SourceFailure(source_name="signal analysis", error=str(exc)))

        # Clustering/scoring/state-maintenance likewise never touch the
        # network -- pure DB reasoning over already-collected/analyzed
        # material -- so they run every cycle regardless of the collection
        # toggle.
        try:
            result.signal_clustering = cluster_unclustered_items(self.session)
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            result.failures.append(SourceFailure(source_name="signal clustering", error=str(exc)))

        try:
            result.signal_candidates_scored = rescore_active_candidates(self.session)
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            result.failures.append(SourceFailure(source_name="signal scoring", error=str(exc)))

        # Handle-suggestion mining (brief section 14 absorption): pure DB
        # reasoning over already-collected SignalItem text, same as
        # clustering/scoring above, and previously only reachable via an
        # explicit POST to /api/radar/source-suggestions/refresh that no
        # automated job or UI control ever called -- this was the main
        # reason the Suggested Sources queue was never populated with
        # anything beyond the one-time legacy import (see
        # docs/UI_ACCEPTANCE_2026-08.md's v0.9.3 diversity investigation).
        try:
            result.handle_suggestions_created_or_updated = refresh_handle_suggestions(self.session)
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            result.failures.append(SourceFailure(source_name="handle suggestion mining", error=str(exc)))

        try:
            wake_snoozed(self.session)
            staleness_days = get_scoring_settings(self.session).staleness_days
            mark_stale_candidates(self.session, staleness_days=staleness_days)
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            result.failures.append(SourceFailure(source_name="signal candidate maintenance", error=str(exc)))

        # promote_candidate() manages its own commits/rollbacks internally
        # per sub-step (see semi_intel/signals/promotion.py) -- this outer
        # try/except only catches a failure in the eligibility scan itself.
        try:
            result.signal_promotion = run_automatic_promotion(self.session)
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            result.failures.append(SourceFailure(source_name="signal promotion", error=str(exc)))

        try:
            result.suggestion_result = self.suggestion_service.run()
        except Exception as exc:  # noqa: BLE001 - a scan failure shouldn't crash the scheduler either
            self.session.rollback()
            result.failures.append(SourceFailure(source_name="suggestion scan", error=str(exc)))

        # Also processes evidence that predates the editorial inbox. This is
        # idempotent, so a scheduler can safely run it on every pass.
        try:
            EditorialDiscoveryService(self.session).backfill()
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            result.failures.append(SourceFailure(source_name="editorial discovery", error=str(exc)))

        try:
            result.discovery_runs = len(DiscoveryService(self.session).run_eligible())
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            result.failures.append(SourceFailure(source_name="targeted discovery", error=str(exc)))

        # Phase 8 is deliberately last and fault-isolated: alerts observe the
        # transitions persisted by every prior stage, but an alert/digest bug
        # can never roll back collection, scoring, promotion or discovery.
        try:
            notification_service = NotificationService(self.session)
            generated = notification_service.generate()
            result.notifications_created = generated.created_count
            notification_settings = notification_service.settings()
            if notification_settings.daily_digest_enabled:
                result.digest_id = DigestService(self.session).generate().id
            notification_service.cleanup_retention()
            self.session.commit()
            # Optional local desktop delivery is independently fault-isolated:
            # a shell/OS notification problem never downgrades the pipeline.
            try:
                from semi_intel.notifications.windows_desktop import WindowsDesktopDeliveryService
                WindowsDesktopDeliveryService(self.session).deliver_pending()
            except Exception:  # noqa: BLE001
                self.session.rollback()
            # Phase 9: this remains a no-op unless the operator has both
            # configured and explicitly enabled the single webhook adapter.
            from semi_intel.operations.webhook import ExternalDeliveryService
            ExternalDeliveryService(self.session).deliver_pending()
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            result.failures.append(SourceFailure(source_name="notifications", error=str(exc)))

        return result

    def _run_source(self, plugin: SourcePlugin, result: PipelineResult) -> None:
        """Run one plugin's ingestion in isolation: on any failure (network
        error, timeout, parser blowing up before yielding anything), roll
        back whatever that attempt left pending on the session and record it
        as a failure rather than letting it propagate and abort every other
        source still queued up in this pass."""
        try:
            result.ingestion_results.append(self.ingestion_service.run(plugin))
        except Exception as exc:  # noqa: BLE001 - isolate one bad source from the rest of the run
            self.session.rollback()
            result.failures.append(SourceFailure(source_name=plugin.name, error=str(exc)))

    def _rss_sources_to_poll(self) -> List[Source]:
        """Legacy direct-to-Evidence path (brief §5/§16): must never overlap
        with the new signal-collection path (semi_intel.signals.collection.
        CollectionService), which selects on `polling_enabled` with no
        `type` filter at all. A Source explicitly registered through the
        new pipeline always has `provider` set to a concrete collection
        mechanism ("rss", "x", ...); every pre-merge/legacy source and
        every source created through the unchanged `source add` CLI/API
        defaults to `provider="manual"` and was never touched by this
        distinction before the merge. Restricting this query to
        provider="manual" is what keeps the two collection paths mutually
        exclusive -- without it, a source with type=RSS, provider="rss",
        and polling_enabled=True would be collected twice per cycle, once
        straight into Evidence here and once into SignalItem there. See
        tests/test_pipeline_service.py::test_legacy_and_signal_rss_paths_never_collect_the_same_source."""
        stmt = select(Source).where(
            Source.type == SourceType.RSS, Source.url.is_not(None), Source.provider == "manual",
        )
        return list(self.session.scalars(stmt))
