# ARCHITECTURE.md

This document records the final architecture and, importantly, **where and why it deviates from the original brief**. Each deviation is a numbered decision record (ADR). The brief was strong; the deviations exist because a few of its assumptions would have cost you maintenance effort for years.

---

## 1. The critique — where the brief was challenged

### ADR-1: Stateless one-shot runs, not a daemon

**Brief said:** "continuously crawl … immediately notify."
**Reality:** the platform runs on your desktop, ad hoc.

A daemon that isn't running detects nothing, and a design built around "immediate" creates false expectations and complexity (in-process schedulers, liveness, restart handling) that buys nothing on a machine that sleeps.

**Decision:** the entire system is a stateless pipeline invoked as `oem-radar run`. Every run has *catch-up semantics*: it crawls everything due, diffs against the last stored snapshot regardless of how old it is, and drains the notification outbox. Consequences, all deliberate:

- No scheduler component in-process. "Scheduling" is per-source `min_interval` config: a run skips sources crawled too recently. Cadence comes from *how often you invoke it* (manually, Windows Task Scheduler, or later a VPS cron — the code is identical, which keeps the daemon door open).
- Notifications report **detection time**, never pretend to be launch time.
- A run must be safely interruptible: each source's crawl→snapshot→diff→notify is its own transaction. Killing the process mid-run loses at most the in-flight source.
- The Discord notifier writes to an **outbox table** first; sending is a separate drain step. If Discord is down or the run dies, the next run retries. Nothing is lost, nothing duplicates (outbox rows carry a dedup key).

This is the single biggest simplification versus the brief, and it makes the system *more* reliable, not less.

### ADR-2: The extension unit is the Source, not the OEM

**Brief said:** every OEM is a code plugin; core knows nothing about GMKtec.
**Problem:** ~70% of the target OEMs (GMKtec, Minisforum, AOOSTAR, Beelink, Trigkey, Bosgame, AYANEO's store, …) run Shopify or WooCommerce. Twenty hand-written plugins would be twenty near-identical copies of the same Shopify JSON parser, each drifting independently. That is the maintenance trap the brief was trying to avoid, reintroduced through the plugin boundary.

**Decision (confirmed with you):** platform-first, hybrid.

- An **engine** is code implementing the `SourceEngine` protocol for a *storefront platform*: `shopify`, `woocommerce`, `generic_html`, later `jd`, `sitemap_rss`, `github_releases`.
- An **OEM descriptor** is YAML: identity (name, aliases, region) plus a list of **sources**, each naming an engine and its config (base URL, collection paths, currency, field-mapping hints).
- A **hand-written engine** is still possible for oddballs (Topton, CWWK, PDD storefronts) — it implements the same protocol, so the core can't tell the difference.

Adding a Shopify OEM = one YAML file, zero code. The brief's real requirement — "adding a new OEM changes nothing else" — is *better* satisfied than by per-OEM code.

A second consequence the brief hinted at but didn't model: **one OEM, many sources.** GMKtec's official store, its JD.com storefront, and its support/BIOS pages are three sources feeding one manufacturer identity. Modeling sources explicitly is exactly what makes the "future sources" list (JD, AliExpress, BIOS pages, GitHub releases) a config change instead of a redesign.

### ADR-3: Entity resolution is a first-class component

The brief's hardest problems — renamed products, regional variants, duplicate listings, "same product, new listing" — are not diffing problems. They are **identity** problems: *which canonical product does this listing belong to?* If identity is wrong, the diff engine confidently reports garbage ("new product!" for a renamed URL).

**Decision:** an explicit `resolve` stage sits between normalization and diffing. It matches an incoming normalized listing to a canonical product via a cascade: exact source-URL/handle match → vendor SKU/model match → alias table → fuzzy match on (manufacturer, normalized model string, spec fingerprint). Every match records a method and confidence; low-confidence matches create a *candidate link* that gets notified as "possible rename/variant" rather than silently merged. Unmatched listings become new products. This is where most long-term intelligence accrues (the alias table grows forever), so it deserves its own module and tests.

### ADR-4: Immutable ≠ store every crawl

**Brief said:** every crawl stores a complete snapshot; nothing overwritten.
**Problem:** crawling 20 OEMs × ~100 products daily stores ~700k identical snapshots/year. Immutability is the right principle; blind append is the wrong mechanism.

**Decision:** content-hash dedup. Each normalized snapshot is canonically serialized and hashed. If the hash equals the product's latest snapshot, no new row — only `last_seen` on the listing advances (a mutable *observation* timestamp, not product data). If the hash differs, a new immutable snapshot row is appended. History is complete *in information terms*: you can reconstruct exactly what was live at any time from (snapshots + observation intervals), at ~1% of the storage.

### ADR-5: AI is a renderer, not an analyst

**Brief said:** AI receives yesterday/today JSON, must never hallucinate.
**Problem:** handing an LLM two JSON blobs and asking "what changed?" makes the LLM do the diffing — the one place hallucination would be catastrophic.

**Decision:** the deterministic diff engine produces the facts (typed `ChangeEvent`s). The AI (Claude Haiku per-event, Sonnet for weekly digests) receives *only the event list plus the two snapshots as reference* and its job is prose rendering and context ("the 396 has never appeared in any product we track"). A post-validator checks that every model number, spec token, and price in the AI output appears verbatim in the input; violations fall back to the rule-based template renderer. The system is fully functional with AI disabled — summaries are just drier. This is what "never hallucinate" looks like when enforced by construction rather than by prompt.

### ADR-6: Severity is data, not code

The brief's star-rating table is policy, and policy changes weekly once you start using the tool ("stop pinging me about photos"). Severity rules live in `radar.yaml` as ordered match rules over `ChangeEvent` fields (change type, field, magnitude, known-hardware flag), first match wins, with a default. The diff engine ships a sane built-in table; your config overrides it. Notification thresholds (per-channel minimum severity, quiet rules) are likewise config.

### ADR-7: Discovery strategies compose per source

Sitemaps, `/products.json`, category pages, hidden search endpoints — the brief is right that these must be open-ended. Each engine declares which `DiscoveryStrategy` implementations it supports; a source's YAML lists which to use, in order, with union-of-results semantics. New strategy = new class registered under a name; no existing code changes. Crucially, discovery finding a URL that navigation doesn't link to is itself a signal (`hidden_listing`) that flows into severity.

*Implementation status (honesty note, per DESIGN_REVIEW §0): strategies currently live as private methods inside the Shopify engine; the strategy registry exists but is unused. The YAML contract (`discovery: [products_json, sitemap]`) already matches this ADR, so descriptors won't change. Extract strategies into the registry when the second engine lands and actually needs to share them — not before.*

### What the brief got right (kept as-is)

Normalized product model as the universal contract; snapshot-diffing never HTML-diffing; SQLite first (single-writer, one machine — correct; the storage protocol keeps Postgres open); Discord webhooks first; known-hardware database with "previously unseen component" as the highest-severity signal; YAML everything; politeness (conditional GETs, per-domain budgets, jittered delays, exponential backoff); milestone-gated development.

---

## 2. The pipeline

One run, per due source, in one transaction:

```
discover ─→ fetch ─→ parse ─→ normalize ─→ validate ─→ resolve ─→ snapshot ─→ diff ─→ score ─→ outbox
   │          │        │          │            │           │           │         │        │        │
 strategy   Fetcher  engine     engine       engine     resolve.py   store    diff.py  rules   notifier
 (engine)   (core)   (code)     (code)       (code)     (core)      (core)    (core)  (config) (drain)
```

- **Core owns:** fetching (politeness, caching, conditional requests), resolution, storage, diffing, scoring, events, outbox, logging. Core imports nothing vendor- or platform-specific.
- **Engines own:** discovery strategies, parsing, normalization, validation. Engines never touch the DB or Discord.
- **Providers own:** concrete I/O backends (sqlite store, discord notifier, anthropic summarizer), each behind a core protocol, each swappable in config.

Contracts are Python `Protocol`s in `core/interfaces.py` — structural typing, so engines/providers need no imports from core internals, only the models module. The registry maps string names (used in YAML) to implementations via decorators now, entry points later if you ever want third-party engine packages.

## 3. Error and confidence philosophy

Every stage degrades, never aborts the run: a source that 404s logs an error row and is skipped; a product that fails validation is stored with `confidence` lowered and issues attached, not dropped (a half-parsed listing of an unknown CPU is *exactly* the story you want). Confidence is carried on the product (parse quality) and on the resolution link (identity certainty), and both feed the notification: a ★★★★★ change on a ★★☆☆☆ parse renders with an explicit caveat.

## 4. Telemetry

Each run writes a `crawler_runs` row (per source: pages fetched, cache hits, products seen, snapshots written, events emitted, errors). `oem-radar status` renders the recent history. This is also your early-warning system for silent breakage — a Shopify source that suddenly yields 0 products is itself an alertable event (`source_degraded`).
