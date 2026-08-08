"""oem-radar CLI: validate | run | status | probe."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .core.config import (
    ConfigError,
    RadarConfig,
    load_oem_configs,
    load_radar_config,
    parse_interval,
)
from .core.knownhw import SEED_COMPONENTS
from .core.registry import engines, notifiers, stores

# Imports for side effect: registry registration.
from . import providers  # noqa: F401
from .engines import dell  # noqa: F401
from .engines import shopify  # noqa: F401
from .providers import discord as _discord  # noqa: F401
from .providers import sqlite as _sqlite  # noqa: F401


def _load(config_dir: Path) -> tuple[RadarConfig, dict]:
    radar = load_radar_config(config_dir / "radar.yaml")
    oems = load_oem_configs(config_dir / "oems")
    return radar, oems


def _setup_logging(cfg: RadarConfig) -> None:
    level = getattr(logging, str(cfg.logging.get("level", "INFO")).upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if cfg.logging.get("file"):
        Path(cfg.logging["file"]).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(cfg.logging["file"], encoding="utf-8"))
    logging.basicConfig(
        level=level, handlers=handlers,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        radar, oems = _load(Path(args.config))
    except (ConfigError, FileNotFoundError) as e:
        problems = e.problems if isinstance(e, ConfigError) else [str(e)]
        for p in problems:
            print(f"ERROR: {p}", file=sys.stderr)
        return 1
    problems = []
    for oem in oems.values():
        for src in oem.sources:
            if src.engine not in engines:
                problems.append(f"source {src.id}: unknown engine {src.engine!r} "
                                f"(registered: {engines.names()})")
                continue
            schema = engines.get(src.engine).config_schema
            try:
                schema.model_validate(src.model_dump())
            except Exception as exc:
                problems.append(f"source {src.id}: engine config invalid: {exc}")
    if problems:
        for p in problems:
            print(f"ERROR: {p}", file=sys.stderr)
        return 1
    n_sources = sum(len(o.sources) for o in oems.values())
    print(f"OK: {len(oems)} OEM(s), {n_sources} source(s), engines: {engines.names()}")
    return 0


def _resolve_webhook(radar: RadarConfig, config_dir: Path) -> tuple[str | None, int, str]:
    """Find the Discord webhook from, in order: the env var, or a
    `discord_webhook.txt` file in the config dir. The file means crawls run
    from ANY terminal notify correctly — not just via start-radar.cmd.
    Returns (webhook_or_None, min_severity, source_description)."""
    discord_cfg = radar.notify.get("discord")
    min_sev = discord_cfg.min_severity if discord_cfg else 3
    env_name = "OEM_RADAR_DISCORD_WEBHOOK"
    if discord_cfg:
        env_name = getattr(discord_cfg, "webhook_url_env", None) or env_name
    from .core.paths import get_discord_webhook_path
    
    wh = os.environ.get(env_name)
    if wh:
        return wh, min_sev, f"env {env_name}"
    wf = get_discord_webhook_path(config_dir)
    if wf.exists():
        txt = wf.read_text(encoding="utf-8").strip()
        if txt and txt.startswith("http"):
            return txt, min_sev, str(wf.name)
    return None, min_sev, "none"


def _build_fetcher(cfg: RadarConfig):
    from .core.fetch import HttpFetcher
    rl = cfg.rate_limit
    delay = rl.get("per_domain_delay", ["3s", "9s"])
    return HttpFetcher(
        cache_dir=Path(cfg.db_path).parent / "http_cache" if cfg.db_path != ":memory:" else None,
        delay_range=(float(parse_interval(delay[0])), float(parse_interval(delay[1]))),
        backoff_base=float(rl.get("backoff_base", 2)),
        backoff_max=float(parse_interval(rl.get("backoff_max", "300s"))),
        max_retries=int(rl.get("max_retries", 4)),
    )


def cmd_run(args: argparse.Namespace) -> int:
    from .core.runner import run_all
    from .core.run_lock import LockError, RunLock

    radar, oems = _load(Path(args.config))
    _setup_logging(radar)

    lock = None
    if not getattr(args, "dry_run", False) and not getattr(args, "no_lock", False):
        from .core.paths import get_lock_path
        try:
            lock_path = get_lock_path(radar.run_lock_path)
            lock = RunLock.acquire(lock_path)
        except LockError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

    if args.dry_run:
        store = stores.get(radar.store)(":memory:", radar.raw_dir)
        notifier = notifiers.get("console")()
        print("dry run: in-memory store, console notifications, nothing persisted")
    else:
        from .core.paths import get_db_path, get_raw_dir
        db_path = get_db_path(radar.db_path)
        raw_dir = get_raw_dir(radar.raw_dir)
        store = stores.get(radar.store)(db_path, raw_dir)
        webhook, min_sev, wh_src = _resolve_webhook(radar, Path(args.config))
        if webhook is None:
            print("WARNING: no Discord webhook found — notifications will queue "
                  "as pending and NOT send.\n  Fix: run via start-radar.cmd, or put "
                  "your webhook URL in config/discord_webhook.txt", file=sys.stderr)
        notifier_cls = notifiers.get(radar.notifier)
        fb = radar.feedback
        notifier = notifier_cls(
            store, webhook, min_sev,
            review_base_url=fb.dashboard_base_url,
            feedback_enabled=fb.enabled,
        )

    try:
        store.seed_components(SEED_COMPONENTS)
        stats = run_all(radar, oems, store, notifier, _build_fetcher(radar),
                        force=args.force, only_source=args.source)
        total_events = sum(s.events for s in stats)
        total_snaps = sum(s.snapshots_written for s in stats)
        print(f"done: {len(stats)} source(s) crawled, {total_snaps} snapshot(s), "
              f"{total_events} event(s)")
        if not args.dry_run:
            pending = store.db.execute(
                "SELECT COUNT(*) c FROM notifications WHERE status='pending'").fetchone()["c"]
            if pending:
                print(f"note: {pending} notification(s) still pending in the outbox "
                      f"(webhook source: {wh_src})")
        return 0
    finally:
        if lock is not None:
            lock.release()


def cmd_status(args: argparse.Namespace) -> int:
    from .core.paths import get_db_path, get_raw_dir
    radar, _ = _load(Path(args.config))
    db_path = get_db_path(radar.db_path)
    raw_dir = get_raw_dir(radar.raw_dir)
    store = stores.get(radar.store)(db_path, raw_dir)
    rows = store.recent_runs(20)
    if not rows:
        print("no runs recorded yet")
        return 0
    print(f"{'source':<24} {'started':<21} {'status':<7} stats")
    for r in rows:
        print(f"{r['source_key']:<24} {r['started_at'][:19]:<21} {r['status']:<7} "
              f"{r['stats_json']}")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    radar, _ = _load(Path(args.config)) if Path(args.config, "radar.yaml").exists() \
        else (RadarConfig(), {})
    fetcher = _build_fetcher(radar)
    base = args.url.rstrip("/")
    verdict = "unknown"
    try:
        doc = fetcher.get(f"{base}/products.json?limit=1")
        if doc.body.lstrip().startswith("{") and "products" in doc.body[:200]:
            verdict = "shopify"
    except Exception:
        pass
    if verdict == "unknown":
        try:
            doc = fetcher.get(base)
            body = doc.body.lower()
            if "woocommerce" in body or "/wp-content/" in body:
                verdict = "woocommerce"
            elif "cdn.shopify.com" in body:
                verdict = "shopify"
        except Exception as exc:
            print(f"probe failed: {exc}", file=sys.stderr)
            return 1
    print(f"{base}: {verdict}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from pathlib import Path as _P
    from .core.paths import get_db_path, get_raw_dir
    from .dashboard import serve

    radar, _ = _load(_P(args.config))
    db_path = get_db_path(radar.db_path)
    if not _P(db_path).exists():
        print(f"no database at {db_path} yet — run a crawl first "
              "(oem-radar run)", file=sys.stderr)
        return 1
    fb = radar.feedback
    db_path = get_db_path(radar.db_path)
    raw_dir = get_raw_dir(radar.raw_dir)
    serve(db_path, host=args.host, port=args.port,
          open_browser=not args.no_browser,
          max_body=fb.max_review_request_bytes,
          raw_dir=raw_dir)
    return 0


def cmd_outbox(args: argparse.Namespace) -> int:
    """Inspect the notification outbox; optionally suppress everything pending."""
    from .core.paths import get_db_path, get_raw_dir
    radar, _ = _load(Path(args.config))
    db_path = get_db_path(radar.db_path)
    raw_dir = get_raw_dir(radar.raw_dir)
    store = stores.get(radar.store)(db_path, raw_dir)
    if args.suppress_pending:
        n = store.db.execute(
            "UPDATE notifications SET status='suppressed' WHERE status='pending'"
        ).rowcount
        store.db.commit()
        print(f"suppressed {n} pending notification(s) — they stay in history, won't send")
        return 0
    for row in store.db.execute(
        "SELECT status, COUNT(*) c FROM notifications GROUP BY status"
    ).fetchall():
        print(f"{row['status']:<12} {row['c']}")
    return 0


def cmd_test_notify(args: argparse.Namespace) -> int:
    """Send a sample embed through the real webhook path to verify wiring."""
    from datetime import datetime, timezone

    from .core.models import ChangeEvent, ChangeType, Component, NormalizedProduct, Price, Severity
    from .providers.discord import _post_webhook, build_embed

    radar, _ = _load(Path(args.config))
    webhook = args.webhook or _resolve_webhook(radar, Path(args.config))[0]
    if not webhook:
        print("no webhook found. Set it one of three ways:\n"
              "  1. run via start-radar.cmd (sets the env var)\n"
              "  2. put the URL in config/discord_webhook.txt\n"
              "  3. pass --webhook <url>", file=sys.stderr)
        return 1

    product = NormalizedProduct(
        manufacturer="GMKtec", model="K12 (sample)",
        cpu=Component(raw="AMD Ryzen AI MAX+ 396", canonical="ryzen-ai-max+-396", known=False),
        gpu=Component(raw="Radeon 8060S"), memory="128 GB", storage="2 TB",
        prices=[Price(amount=999.99, currency="USD", region="US")],
        source_url="https://www.gmktec.com/products/example",
    )
    event = ChangeEvent(
        product_key="test:k12", change_type=ChangeType.NEW_PRODUCT,
        new_value="K12", severity=Severity.BREAKING,
        detected_at=datetime.now(timezone.utc),
        meta={"hidden": True, "unseen_component": True},
    )
    payload = build_embed(event, product)
    payload["embeds"][0]["footer"] = {"text": "OEM Radar — test notification, not a real product"}
    ok, err = _post_webhook(webhook, payload)
    print("sent — check your Discord channel" if ok else f"failed: {err}")
    return 0 if ok else 1



def cmd_feedback_analyze(args: argparse.Namespace) -> int:
    """Deterministic offline analysis of reviewed alerts → suggestions."""
    from .core.feedback_analyze import analyze_reviews, persist_candidates
    from .core.paths import get_db_path, get_raw_dir
    radar, _ = _load(Path(args.config))
    db_path = get_db_path(radar.db_path)
    raw_dir = get_raw_dir(radar.raw_dir)
    store = stores.get(radar.store)(db_path, raw_dir)
    fb = radar.feedback
    min_samples = args.minimum_samples or fb.minimum_samples_for_suggestion
    min_noise = args.minimum_noise_ratio if args.minimum_noise_ratio is not None else fb.minimum_noise_ratio
    max_sig = args.maximum_signal_loss_ratio if args.maximum_signal_loss_ratio is not None else getattr(fb, "maximum_signal_loss_ratio", fb.maximum_hit_loss_ratio)
    try:
        cands = analyze_reviews(
            store.db,
            start=args.start,
            end=args.end,
            collector=args.collector,
            alert_type=args.alert_type,
            min_samples=min_samples,
            min_noise_ratio=min_noise,
            max_signal_loss_ratio=max_sig,
        )
        if args.dry_run:
            rows = [
                {
                    "collector": c.collector,
                    "alert_type": c.alert_type,
                    "reason_code": c.reason_code,
                    "rule_type": c.rule_type,
                    "explanation": c.explanation,
                    "supporting_alert_count": c.supporting_alert_count,
                    "estimated_noise_reduction": c.estimated_noise_reduction,
                    "estimated_signal_loss": c.estimated_signal_loss,
                    "fingerprint": c.fingerprint(),
                    "status": "PROPOSED",
                }
                for c in cands
            ]
        else:
            rows = persist_candidates(store, cands, max_signal_loss_ratio=max_sig)
    finally:
        store.close()

    if args.json:
        import json as _json
        print(_json.dumps({"suggestions": rows, "count": len(rows)}, indent=2, default=str))
        return 0
    if not rows:
        print("No suggestions (insufficient evidence or no matching patterns).")
        return 0
    for r in rows:
        print(f"Collector: {r.get('collector')}")
        print(f"Alert type: {r.get('alert_type')}")
        print(f"Pattern: {r.get('explanation') or r.get('suggested_rule')}")
        print(f"Estimated noise reduction: {(r.get('estimated_noise_reduction') or 0)*100:.1f}%")
        print(f"Estimated signal loss: {(r.get('estimated_signal_loss') or r.get('estimated_hit_loss') or 0)*100:.1f}%")
        print(f"Status: {r.get('status')}")
        if r.get("id"):
            print(f"ID: {r['id']}")
        print("---")
    return 0


def cmd_feedback_simulate(args: argparse.Namespace) -> int:
    from .core.feedback_simulate import simulate_rule
    from .core.feedback import FeedbackError
    from .core.paths import get_db_path, get_raw_dir
    radar, _ = _load(Path(args.config))
    db_path = get_db_path(radar.db_path)
    raw_dir = get_raw_dir(radar.raw_dir)
    store = stores.get(radar.store)(db_path, raw_dir)
    fb = radar.feedback
    try:
        result = simulate_rule(
            store.db,
            rule_id=args.rule_id,
            start=args.start,
            end=args.end,
            min_samples=fb.minimum_samples_for_suggestion,
            max_signal_loss_ratio=getattr(fb, "maximum_signal_loss_ratio", fb.maximum_hit_loss_ratio),
        )
    except FeedbackError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        store.close()
        return 1
    finally:
        store.close()
    if args.json:
        import json as _json
        print(_json.dumps(result, indent=2, default=str))
        return 0
    print(f"Suggestion {result.get('rule_id')}")
    rule = result.get("rule") or {}
    print(f"Rule: {rule.get('rule_type')}")
    print(f"Historical alerts matched: {result['total_matched']}")
    print(f"Reviewed matches: {result['reviewed_matched']}")
    print(f"NOISE affected: {result['noise_affected']}")
    print(f"BUG affected: {result['bug_affected']}")
    print(f"HIT affected: {result['hit_affected']}")
    print(f"INTERESTING affected: {result['interesting_affected']}")
    nr = result.get("estimated_noise_reduction")
    sl = result.get("estimated_signal_loss")
    print(f"Estimated noise reduction: {nr*100:.1f}%" if nr is not None else "Estimated noise reduction: n/a")
    print(f"Estimated signal loss: {sl*100:.1f}%" if sl is not None else "Estimated signal loss: n/a")
    print(f"Assessment: {result['assessment']}")
    if result.get("warnings"):
        print(f"Warnings: {', '.join(result['warnings'])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oem-radar", description="OEM product intelligence")
    parser.add_argument("--config", default="config", help="config directory (default: config)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="validate configuration offline").set_defaults(func=cmd_validate)

    p_run = sub.add_parser("run", help="one-shot crawl of all due sources")
    p_run.add_argument("--dry-run", action="store_true", help="no persistence, console output")
    p_run.add_argument("--force", action="store_true", help="ignore min_interval")
    p_run.add_argument("--source", help="crawl only this source id")
    p_run.add_argument("--no-lock", action="store_true",
                      help="skip single-instance lock (not recommended)")
    p_run.set_defaults(func=cmd_run)

    sub.add_parser("status", help="recent run telemetry").set_defaults(func=cmd_status)

    
    p_fb = sub.add_parser("feedback", help="feedback analyze | simulate")
    fb_sub = p_fb.add_subparsers(dest="feedback_cmd", required=True)
    p_an = fb_sub.add_parser("analyze", help="deterministic noise-pattern analysis")
    p_an.add_argument("--start")
    p_an.add_argument("--end")
    p_an.add_argument("--collector")
    p_an.add_argument("--alert-type")
    p_an.add_argument("--minimum-samples", type=int, default=None)
    p_an.add_argument("--minimum-noise-ratio", type=float, default=None)
    p_an.add_argument("--maximum-signal-loss-ratio", type=float, default=None)
    p_an.add_argument("--dry-run", action="store_true")
    p_an.add_argument("--json", action="store_true")
    p_an.set_defaults(func=cmd_feedback_analyze)
    p_sim = fb_sub.add_parser("simulate", help="counterfactual simulation of a suggestion")
    p_sim.add_argument("--rule-id", type=int, required=True)
    p_sim.add_argument("--start")
    p_sim.add_argument("--end")
    p_sim.add_argument("--json", action="store_true")
    p_sim.set_defaults(func=cmd_feedback_simulate)

    p_dash = sub.add_parser("dashboard", help="launch the local web dashboard")
    p_dash.add_argument("--port", type=int, default=8787)
    p_dash.add_argument("--host", default="127.0.0.1")
    p_dash.add_argument("--no-browser", action="store_true",
                        help="don't auto-open a browser window")
    p_dash.set_defaults(func=cmd_dashboard)

    p_outbox = sub.add_parser("outbox", help="notification outbox status")
    p_outbox.add_argument("--suppress-pending", action="store_true",
                          help="mark all pending notifications suppressed (won't send)")
    p_outbox.set_defaults(func=cmd_outbox)

    p_test = sub.add_parser("test-notify", help="send a sample embed to the Discord webhook")
    p_test.add_argument("--webhook", help="override webhook URL (else env var)")
    p_test.set_defaults(func=cmd_test_notify)

    p_probe = sub.add_parser("probe", help="fingerprint a storefront platform")
    p_probe.add_argument("url")
    p_probe.set_defaults(func=cmd_probe)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
