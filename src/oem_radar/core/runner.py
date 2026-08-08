"""run_all: iterate configured OEMs/sources, honoring due-ness, recording
telemetry, then drain the outbox. Separated from the CLI so tests can inject
fake fetchers/stores/notifiers."""

from __future__ import annotations

import logging

from .config import OemConfig, RadarConfig
from .interfaces import Fetcher, Notifier
from .pipeline import SourceRunStats, run_source
from .registry import engines
from .story import detect as detect_stories

log = logging.getLogger("oem_radar.runner")


def run_all(
    radar_cfg: RadarConfig,
    oems: dict[str, OemConfig],
    store,
    notifier: Notifier,
    fetcher: Fetcher,
    *,
    force: bool = False,
    only_source: str | None = None,
) -> list[SourceRunStats]:
    rules = radar_cfg.severity_rules or None
    all_stats: list[SourceRunStats] = []

    for oem in oems.values():
        man = oem.manufacturer
        man_id = store.ensure_manufacturer(man.name, man.country, man.aliases)
        for src in oem.sources:
            if not src.enabled or (only_source and src.id != only_source):
                continue
            if not force and not store.source_due(src.id, src.min_interval_s):
                log.info("skip %s: crawled within min_interval", src.id)
                continue
            store.ensure_source(src.id, man_id, src.engine, src.base_url, src.model_dump())
            baseline = radar_cfg.baseline_quiet and not store.has_completed_run(src.id)
            if baseline:
                log.info("%s: first crawl — baseline mode, notifications suppressed", src.id)
            run_id = store.run_started(src.id)
            try:
                engine = engines.get(src.engine)(src, man.name)
                stats = run_source(
                    src, engine, fetcher, store, notifier, rules,
                    baseline=baseline,
                    health_cfg=radar_cfg.collector_health,
                )
                # Failed catalog health must not become the last-good baseline.
                run_status = "failed" if stats.health == "failed" else "ok"
                store.run_finished(
                    run_id, run_status,
                    vars(stats) | {
                        "errors": len(stats.errors),
                        "health": stats.health,
                        "health_reason": getattr(stats, "health_reason", None),
                    },
                    stats.errors,
                )
                all_stats.append(stats)
                log.info(
                    "%s: %d discovered, %d new snapshots, %d unchanged, %d skipped, "
                    "%d events, %d errors",
                    src.id, stats.discovered, stats.snapshots_written,
                    stats.unchanged, stats.skipped, stats.events, len(stats.errors),
                )
                sent_now = notifier.drain()  # per-source: embeds arrive promptly
                if sent_now:
                    log.info("sent %d notification(s)", sent_now)
            except Exception as exc:
                store.run_finished(run_id, "failed", {}, [repr(exc)])
                log.exception("source %s failed entirely: %r", src.id, exc)

    # Story detection (DESIGN_REVIEW §7): correlate this and recent events
    # across OEMs, BEFORE the final drain, so a story can demote its
    # constituent product pings. Deterministic; runs only if rules exist.
    story_rules = getattr(radar_cfg, "story_rules", None) or []
    if story_rules and hasattr(store, "event_rows_for_stories"):
        max_window = max(r.window_s for r in story_rules)
        rows = store.event_rows_for_stories(max_window)
        for story in detect_stories(rows, story_rules):
            if store.story_exists(story.dedup_key()):
                continue  # already alerted this OEM-set for this key
            store.save_story(story)
            keys = [e.product_key for e in story.evidence]
            demoted = store.demote_events_for_products(keys)
            if hasattr(notifier, "enqueue_story"):
                notifier.enqueue_story(story)
            log.info("STORY: %s (score %d, demoted %d ping(s))",
                     story.title, story.score, demoted)

    sent = notifier.drain()
    if sent:
        log.info("drained outbox: %d notification(s) sent", sent)
    return all_stats
