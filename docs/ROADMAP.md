# ROADMAP.md

Each milestone ends in a working, tested state; nothing starts before its predecessor's exit criteria pass. Your M-numbering is kept, with two re-orderings, argued inline.

**Re-ordering 1:** your list had the plugin system (M2) before SQLite/snapshots (M3–M4) and the first real OEM at M7. But an engine can't prove itself without storage to snapshot into, and the platform can't prove itself without a real OEM early. So storage lands right after the crawler, and the Shopify engine + GMKtec config land immediately after — GMKtec at M4, not M7, because per ADR-2 an OEM is config, not a milestone-sized effort.

**Re-ordering 2:** entity resolution (ADR-3) wasn't in your list at all but gates trustworthy diffing, so it's explicit at M5.

| M | Deliverable | Exit criteria (all testable offline unless noted) |
|---|---|---|
| **M0** ✅ | Skeleton: package, models, protocols, config loader, registry, CLI stub | `pytest` green; `oem-radar validate` accepts sample config, rejects broken ones; fake-engine pipeline composition test passes |
| **M1** ✅ | Crawler framework: `Fetcher` with per-domain budgets, jittered delays, exponential backoff, ETag/Last-Modified conditional GETs, on-disk response cache; structured logging; `crawler_runs` telemetry | Verified against a local mock server: backoff timing, 304 cache accounting, politeness intervals, no-retry on 4xx (`tests/test_fetch.py`) |
| **M2** ✅ | Engine system live: Shopify engine (products.json inline-bulk + sitemap discovery, hidden-listing flag), `probe` fingerprinting | Passes on captured **live GMKtec fixtures** incl. vendor quirks; CPU extraction from titles, bodies, and variant options (`tests/test_shopify.py`) |
| **M3** ✅ | SQLite provider: full DATABASE.md schema, raw-payload store, outbox, telemetry | Round-trips, UNIQUE-hash dedup, price observation stream, raw refs on disk (`tests/test_sqlite_store.py`) |
| **M4** ✅ | End-to-end `run`/`status` CLI: due-ness, force, dry-run, per-source telemetry | Simulated two-run test: run 2 on unchanged catalog stores 0 snapshots, sends 0 (`tests/test_runner.py`). Live web run pending your machine (sandbox had no direct egress) |
| **M5** ✅ | Known-hardware DB (seeded ~50 CPUs/GPUs) + canonicalizer + resolution v1 (URL → model-key → new) | Unseen "Ryzen AI MAX+ 396" fires ★★★★★ and is auto-learned; rename resolves to existing product (`test_runner.py`, `test_sqlite_store.py`) |
| **M6** ✅ | Discord notifier: outbox, drain, dedup keys, rich embeds, severity gating, retry cap | Embed snapshot tests; retry-across-runs test with Discord mocked down; suppressed events audited. Live webhook send: pending your webhook URL |
| **M7** ✅/◐ | Minisforum (verified Shopify, live config) + **Beelink** (pending) | Minisforum descriptor shipped; Beelink probe + descriptor next |
| **M8** | AOOSTAR + GEEKOM + remaining Shopify/Woo OEMs from your list | Descriptor-per-OEM; WooCommerce engine lands here if any target needs it |
| **M9** | Oddball engines: Topton/CWWK-class custom storefronts, `generic_html` fallback engine | Harness green on fixtures; graceful low-confidence normalization proven |
| **M10** | AI summaries: Anthropic provider, event→prose rendering, grounding validator, template fallback | Validator rejects a deliberately hallucinated fixture; AI-off mode fully functional |
| **M11** | New source classes: JD.com engine, support/BIOS/driver page engines, GitHub releases, RSS | One OEM with 2+ source types feeding one product identity; `support_artifact_added` events fire |
| **M12** ✅ | Dashboard: local read-only web UI over the DB (Signals feed, filterable change log, unseen-hardware feed, per-OEM counts, run telemetry, clickable store links) | Runs offline against the existing DB, read-only (safe during crawls); `tests/test_dashboard.py` covers data + render + link presence. Review-queue confirm/split UI deferred to the review-command work (HANDOFF item 3) |

Deferred beyond M12, deliberately: multi-user anything, Postgres, daemon mode (ADR-1 keeps the door open — a VPS cron of the same CLI is daemon mode), translation of CN-only listings (candidate for M10.5 via the same AI provider), price-history analytics.

Standing rules across all milestones: every real-world parsing or diffing mistake becomes a fixture/case before it's fixed; no engine merges without harness fixtures; `radar.yaml` is the only place policy lives.
