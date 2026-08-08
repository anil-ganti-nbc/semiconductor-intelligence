"""Run orchestration: discover → fetch → parse → normalize → validate →
resolve → snapshot → diff → score → outbox.

Core knows engines/stores/notifiers only through protocols. This module is
deliberately boring — all intelligence lives in the stages it wires together.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from .config import SeverityRule, SourceConfig
from .diff import diff
from .interfaces import Fetcher, Notifier, SnapshotStore, SourceEngine
from .knownhw import canonicalize
from .models import ChangeEvent, ChangeType, FetchedDocument, NormalizedProduct, Severity

log = logging.getLogger("oem_radar.pipeline")


@dataclass
class SourceRunStats:
    source_id: str
    discovered: int = 0
    fetched: int = 0
    snapshots_written: int = 0
    unchanged: int = 0
    skipped: int = 0        # non-product listings dropped by fatal validation
    invalid: int = 0
    events: int = 0
    errors: list[str] = field(default_factory=list)
    health: str = "ok"      # ok | degraded | failed
    previous_discovered: int | None = None
    catalog_fraction: float | None = None
    health_reason: str = "HEALTHY_CATALOG"
    health_min_fraction: float | None = None
    health_warn_fraction: float | None = None
    unexpected_zero_is_failure: bool | None = None


def _stamp_components(product: NormalizedProduct, store: SnapshotStore) -> None:
    """Canonicalize cpu/gpu/npu and mark against the known-hardware DB.
    Unseen components are learned immediately so the *second* sighting is
    no longer breaking news (DIFF_ENGINE.md §4)."""
    for kind in ("cpu", "gpu", "npu"):
        comp = getattr(product, kind)
        if comp is None:
            continue
        comp.canonical = canonicalize(comp.raw)
        if comp.canonical is None:
            comp.known = None  # can't judge; renderers must caveat, not guess
        else:
            comp.known = store.known_component(comp.canonical)


def _learn_components(product: NormalizedProduct, store: SnapshotStore) -> None:
    for kind in ("cpu", "gpu", "npu"):
        comp = getattr(product, kind)
        if comp is not None and comp.canonical and comp.known is False:
            store.learn_component(kind, comp.canonical, comp.raw)


def run_source(
    source: SourceConfig,
    engine: SourceEngine,
    fetcher: Fetcher,
    store: SnapshotStore,
    notifier: Notifier,
    rules: list[SeverityRule] | None = None,
    baseline: bool = False,
    health_cfg: "CollectorHealthConfig | None" = None,
) -> SourceRunStats:
    """Crawl one source. Degrades per-product, never aborts the source;
    the caller wraps this in one storage transaction (ADR-1).

    health_cfg: when None, uses CollectorHealthConfig defaults (backward-compatible
    for direct unit tests). Production paths must pass radar_cfg.collector_health.
    """
    from .config import CollectorHealthConfig
    if health_cfg is None:
        health_cfg = CollectorHealthConfig()

    stats = SourceRunStats(source_id=source.id)
    stats.health_min_fraction = health_cfg.minimum_fraction_of_previous_catalog
    stats.health_warn_fraction = health_cfg.warn_fraction_of_previous_catalog
    stats.unexpected_zero_is_failure = health_cfg.unexpected_zero_is_failure

    refs = list(engine.discover(fetcher))
    log.info("%s: %d product(s) discovered, processing...", source.id, len(refs))
    stats.discovered = len(refs)

    # Catalog health: unexpected zero / abrupt collapse vs previous successful run.
    # Never convert collapse into mass product_removed — that is a separate path.
    # Reference baseline = last crawler_runs row with status='ok' for this source.
    # Failed/degraded runs do NOT become the new baseline.
    prev = None
    try:
        if hasattr(store, "last_successful_discovered"):
            prev = store.last_successful_discovered(source.id)
        elif hasattr(store, "db"):
            row = store.db.execute(
                "SELECT stats_json FROM crawler_runs WHERE source_key=? AND status='ok' "
                "ORDER BY id DESC LIMIT 1",
                (source.id,),
            ).fetchone()
            if row:
                import json as _json
                prev = _json.loads(row["stats_json"] or "{}").get("discovered")
    except Exception:
        prev = None
    stats.previous_discovered = prev
    zero_is_fail = health_cfg.unexpected_zero_is_failure
    min_frac = health_cfg.minimum_fraction_of_previous_catalog
    warn_frac = health_cfg.warn_fraction_of_previous_catalog

    if len(refs) == 0:
        if zero_is_fail and (prev is None or prev > 0):
            stats.health = "failed"
            stats.health_reason = "UNEXPECTED_ZERO" if prev else "NO_PREVIOUS_BASELINE"
            stats.errors.append(
                f"{stats.health_reason}: discovered=0 "
                + (f"(previous successful discovered={prev})" if prev is not None else
                   "(no prior successful baseline; treat as failure for enabled sources)")
            )
            log.error("%s: %s", source.id, stats.errors[-1])
            return stats
        # intentional zero allowed
        stats.health = "ok"
        stats.health_reason = "HEALTHY_CATALOG"
        return stats
    if prev and prev > 0:
        frac = len(refs) / prev
        stats.catalog_fraction = frac
        if frac < min_frac:
            stats.health = "failed"
            stats.health_reason = "CATALOG_FAILURE_THRESHOLD"
            stats.errors.append(
                f"catalog_collapse: discovered={len(refs)} is {frac:.0%} of previous {prev} "
                f"(minimum_fraction={min_frac})"
            )
            log.error("%s: %s", source.id, stats.errors[-1])
            return stats
        if frac < warn_frac:
            stats.health = "degraded"
            stats.health_reason = "CATALOG_WARN_THRESHOLD"
            stats.errors.append(
                f"catalog_shrink_warning: discovered={len(refs)} is {frac:.0%} of previous {prev}"
            )
            log.warning("%s: %s", source.id, stats.errors[-1])
        else:
            stats.health = "ok"
            stats.health_reason = "RECOVERED" if prev != len(refs) else "HEALTHY_CATALOG"
    else:
        stats.health = "ok"
        stats.health_reason = "HEALTHY_CATALOG" if prev is not None else "NO_PREVIOUS_BASELINE"
    processed = 0
    for ref in refs:
        processed += 1
        if processed % 25 == 0:
            log.info("%s: %d/%d processed (%d changed so far)",
                     source.id, processed, len(refs), stats.snapshots_written)
        try:
            if ref.inline_payload is not None:
                doc = FetchedDocument(
                    url=ref.url, status=200,
                    body=json.dumps(ref.inline_payload),
                    content_type="application/json",
                )
            else:
                doc = fetcher.get(ref.url)
                stats.fetched += 1
            raw = engine.parse(doc)
            product = engine.normalize(raw)
            issues = engine.validate(product)
            if any(i.fatal for i in issues):
                # Fatal = not a trackable product (non-product listing, empty
                # model). Skip entirely: no snapshot, no diff, no notification.
                # This is the accessories/"Contact US" filter (DESIGN_REVIEW).
                stats.skipped += 1
                continue
            if issues:  # non-fatal: keep, but lower confidence (parse gaps are
                product.confidence = min(product.confidence, 0.5)  # still signal
            _stamp_components(product, store)

            product_key = f"{source.id}:{ref.handle or ref.url}"
            before, relation = store.resolve_prior(product_key, product)

            if before is not None and before.content_hash() == product.content_hash():
                store.touch(product_key)
                stats.unchanged += 1
                continue

            store.append(product_key, product)
            stats.snapshots_written += 1

            events = diff(before, product, product_key, rules)
            unseen = any(
                getattr(product, k) is not None and getattr(product, k).known is False
                for k in ("cpu", "gpu", "npu")
            )
            for event in events:
                if baseline:
                    # First-ever crawl of this source: everything is "new" by
                    # definition. Record events for history, don't ping.
                    event.meta["baseline"] = True
                if event.change_type == ChangeType.NEW_PRODUCT:
                    if relation == "existing_product":
                        # A different listing already carries this identity:
                        # variant/duplicate, not a launch (ADR-3).
                        event.change_type = ChangeType.DUPLICATE_LISTING
                        event.severity = Severity.MINOR
                    if ref.hidden:
                        event.meta["hidden"] = True
                    if unseen:
                        event.meta["unseen_component"] = True
                notifier.enqueue(event, product)
                stats.events += 1
            _learn_components(product, store)
        except Exception as exc:  # degrade, log, continue (ARCHITECTURE.md §3)
            log.warning("source %s: %s failed: %r", source.id, ref.url, exc)
            stats.errors.append(f"{ref.url}: {exc!r}")

    return stats
