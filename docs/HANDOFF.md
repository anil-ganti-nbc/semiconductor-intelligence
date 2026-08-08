# HANDOFF.md — pickup document for OEM Radar

**If you are an AI assistant or developer taking over this project, read this
file first, then `docs/DESIGN_REVIEW.md`, then `docs/ARCHITECTURE.md`.** This
file is the living state of the project; the others are the design and its
decision records (ADRs). When you finish a chunk of work, update THIS file.

Owner: X8, a technology journalist (contact@x8.design). Runs on Windows /
PowerShell. The platform's mission: spot newly launched boutique-PC-OEM
hardware before mainstream tech media notices. It is NOT a scraper — it
reasons about products over immutable snapshots. Honor that framing.

## How to start a session on this project

1. Read this file + DESIGN_REVIEW.md.
2. `pip install -e ".[dev]"` (or just run `start-radar.cmd`, which
   self-bootstraps). Then `pytest` — must be green (59 tests as of
   2026-07-19) before you touch anything.
3. Ask the owner for their "oddities list" from recent live runs
   (misparses, phantom pings, wrong specs). That list drives priority 1.
4. Do the work. Keep tests green. Update this file. Never break an ADR
   without amending the ADR.

## Current state (2026-07-19)

Milestones M0–M7 done, M8 partially, **M12 (dashboard) done**. **63 pytest
tests, all green.** Running live daily on the owner's Windows machine; Discord
notifications confirmed working end-to-end.

**Dashboard (M12):** `oem-radar dashboard` (or `dashboard.cmd`) serves a
stdlib-only web UI at http://127.0.0.1:8787 — Signals feed, filterable change
log, unseen-hardware feed, per-OEM counts, run telemetry, clickable store
links. Reads DB read-only per GET (safe during crawls, WAL). Code:
`src/oem_radar/dashboard/`. Tests: `tests/test_dashboard.py`.
It has exactly ONE write action (owner request 2026-07-19): `POST
/api/mark-seen` marks discovered components 'seen' so they leave the
unseen-hardware feed (they stay known, never re-alert). Opens a brief RW
connection only for that. Deliberate departure from read-only; keep it the
only writer. The review-queue confirm/split UI still belongs with the
`oem-radar review` command (item 3), not here.

**Known-hardware seed fix (2026-07-19):** the seed list was hand-written
slugs that DIDN'T match `canonicalize()` output, so ~45 well-known chips got
flagged 'discovered' on first crawl. Fixed two ways: (1) `SEED_COMPONENTS`
is now RAW vendor strings, canonicalized through the same function at seed
time — slugs can never drift again; (2) `canonicalize()` collapses the
"Ryzen9"/"Ryzen 9" spelling split so variants converge. NOTE: this doesn't
retroactively clean the owner's EXISTING db (those rows are already
'discovered') — that's what the dashboard "Mark all as seen" button is for.
On a FRESH db the unseen feed correctly shows only genuinely-novel silicon.

The DESIGN_REVIEW "now" list is COMPLETE:
- Variant-level product model: `configurations[]` (per-variant memory/
  storage/price/SKU/region/availability). Variants no longer flattened.
- Snapshot compression: zlib in `snapshots.normalized_zjson` (schema v2;
  read path falls back to plaintext `normalized_json` for v1 rows).
- Image-URL canonicalization: `?v=` params stripped in engine AND diff, so
  Shopify theme republishes don't fake `images_changed` events.
- `vendor_sku` + `region` first-class on listings and the model.
- Severity rules support comparison operators (`">10"`, `">=5"`, `"<3"`) and
  `direction: up|down`, unit-aware (1 TB > 512 GB).
- ADR-7 amended to match reality (discovery strategies are still private
  methods in the Shopify engine, registry unused until engine #2).
- `tests/engine_harness.py` is real; goldens in `tests/goldens/`. Regenerate
  goldens deliberately with `UPDATE_GOLDENS=1 pytest`.

Schema is at v2. Live v1 databases auto-migrate on open (adds columns,
records migration rows). The migration test is
`tests/test_review_now_list.py::test_v1_database_migrates_and_old_snapshots_load`.

## OEM coverage

Engines: **shopify** (boutique brands) and **dell** (first big-brand, static
HTML + JSON-LD). Enabled Shopify: **GMKtec, Minisforum, Beelink, AOOSTAR**.
Enabled Dell: **dell-us-laptops** (laptops/gaming/desktops catalog).
Stubbed `enabled: false` pending local `oem-radar probe`: Trigkey, GEEKOM,
AYANEO. Adding a Shopify OEM = one YAML file. Big brands need an engine.

**Big brands — read `docs/BIG_BRANDS.md`.** Feasibility probe 2026-07-22:
Dell is static (built); **ASUS and Lenovo are JS-rendered and need a
Playwright fetcher** (owner deferred this — "Dell now, Playwright later").
The Fetcher is an injected interface, so a PlaywrightFetcher is a drop-in with
zero core/engine changes. Do NOT write ASUS/Lenovo parsers against assumed
HTML — probe+fixture first (that rule is why we didn't fake them). Dell's
catalog gives new-model-code signal; exact silicon comes from the Dell
engine's `deep_crawl` flag (BUILT 2026-07-22, still static): per-model spec
pages for exact CPU/GPU/RAM/storage/display, off by default (one extra request
per model). Seed now includes RTX 50-series + Core Ultra HX chips.

## Owner tuning decisions already applied (don't undo without asking)

- `baseline_quiet: true` — first-ever crawl of a source records history but
  sends no notifications (prevents the ~100-ping baseline flood; happened
  once on the first GMKtec/Minisforum run, now prevented).
- Price changes under 15% are silenced (severity 1, below the notify
  threshold of 3); only `magnitude_pct > 15` pings. This was the owner's
  most recent feedback: routine price wobble is noise for a hardware-news
  tool. Rules live in `config/radar.yaml`.

## Oddities from the live dashboard 2026-07-19 — ALL FIXED 2026-07-22

All three resolved with regression tests in `tests/test_parser_fixes.py`
(fixtures mirror the real Beelink shapes). Summary of fixes:
1. Memory/storage swap → `_split_mem_storage()` in the Shopify engine assigns
   by UNIT + keyword, not position ("1TB SSD + 32GB RAM" now parses correctly;
   TB is never RAM; untagged GB: smaller=memory, larger=storage).
2. Non-product listings → config-driven denylist (`_DEFAULT_NON_PRODUCT` +
   `non_product_terms` in ShopifySourceConfig); validate() returns a FATAL
   issue; the PIPELINE now SKIPS any product with a fatal validation (no
   snapshot, no diff, no notification) and counts it in `stats.skipped`.
   NB: this changed pipeline semantics — fatal validation = skip (was: keep
   at confidence 0). Non-fatal issues still keep+lower-confidence.
3. Resolution collision → `resolve_prior` prefers vendor_sku match, and only
   merges on a coarse model-key when `_same_product()` agrees (models
   differing by a TIER WORD — pro/max/plus/ultra… — are distinct products,
   not renames). Stops the phantom "SER9 → SER9 PRO" component diff.

NOTE for the owner's EXISTING db: these fix FUTURE crawls. Rows already
mis-parsed (a SER9 PRO wrongly merged, an accessories "product") persist as
history. They'll stop generating new bad events; to purge old bad component
rows from the components feed use the dashboard "Mark seen" button, and the
bad product rows simply go stale. A fresh db is fully clean.

### (historical, for reference) the original oddity notes

1. **Memory/storage swap (Beelink SER9 PRO):** dashboard showed
   `memory 1 TB → 32 GB`. Memory must never be a TB value. The variant-
   option extractor (`_SIZE_RE.findall(option1)`, "first size = memory,
   second = storage") mis-orders on some Beelink option phrasings. Capture
   the exact option1 string; the fix likely needs unit-aware assignment
   (GB→memory, TB→storage) rather than positional.
2. **Suspected resolution collision (Beelink SER9):** a SER9 PRO showed
   `cpu ryzen-7-h-255 → ryzen-ai-9-hx-370`. A PRO should never have been an
   H 255 — two different SER9 models are probably colliding under the coarse
   key `beelink::ser9` and overwriting each other's snapshots (produces fake
   component-change events every crawl). Fix: use `vendor_sku` (now captured)
   as a resolution tiebreaker in `SqliteStore.resolve_prior` / `model_key`,
   or lengthen the key. Verify against the DB before changing the key — a key
   change is a hash-affecting migration, so gate it like the v2 change.
3. **Non-product listings scored as ★★★★★ new products:** "【Contact US】
   Accessories" appeared as a 5-star NEW_PRODUCT. Accessories/contact/
   bundle pages aren't products. Add an engine-level product filter (config-
   driven title/handle/product_type denylist, e.g. drop "contact", "gift
   card", "accessor", "cable") — validate as fatal so they store with
   confidence 0 and never notify. Keep it config, not hardcoded.

## Story detection BUILT 2026-07-23 (the marquee feature)

Rule-driven cross-OEM story engine, read docs/STORY_ENGINE.md. Pure layer
(`core/story.py`) after diff / before final drain in run_all. Patterns are
config (`story_rules` in radar.yaml) like severity rules. Fires when N distinct
OEMs share a grouped event key within a window; demotes constituent product
pings; one purple story embed with explainable additive score + evidence
links. `stories` table, dashboard "Stories" tab (now the first tab).
Tests: tests/test_story.py (94 total tests green). Refused (per DESIGN_REVIEW):
fabricated "traffic/article value" scores. Next natural extensions: BIOS/
driver-referenced-unseen-CPU stories once those source engines exist; weekly
story digest.

**Automation (Windows) BUILT 2026-07-23:** `install-hourly-task.cmd`
registers a Scheduled Task running `crawl-silent.vbs` → `crawl-hourly.cmd`
(headless, no window, no dashboard) every hour; logs to data\crawl-runs.log;
uninstall via `uninstall-hourly-task.cmd`. Crawl and dashboard share
data/radar.db so scheduled results show in the GUI on demand. Per-source
min_interval still gates actual fetches. Non-Windows: just cron the same
`oem-radar run`.

**More OEMs added 2026-07-23:** Chuwi ENABLED (store confirmed Shopify at
us.chuwi.com — note the marketing site www.chuwi.com is a separate CMS).
Ready-to-enable stubs (enabled:false, probe on the owner's machine then flip):
GEEKOM, GPD, Morefine, Bosgame, NiPoGi, Peladn, Firebat, Kingnovy — sandbox
egress was too limited to confirm these, but each descriptor has the likely
store URL + a probe reminder. 16 OEM descriptors total now. Also hardened the
Shopify CPU regex with bare-family fallbacks ("Intel Core Ultra 7", "AMD
Ryzen 5" — family only, no model number) and seeded those families so they
don't false-flag as unseen silicon. Precise matches still win first.
To enable a stub: `oem-radar probe <url>`; if shopify, set enabled:true.

## Next steps, in priority order (agreed with owner)

1. **Parser hardening from the owner's oddities list** (the three above,
   plus whatever else accrues). Every real-world misparse becomes a fixture
   + regression test BEFORE the fix (standing rule). This is priority 1.
2. **UX: silent run start.** `run_all` logs nothing before `engine.discover`
   completes, so runs open with minutes of blank console (owner hit this).
   Log "crawling <source_id>..." before discovery; per-fetch line at DEBUG.
3. **Core gaps, one sitting:** (a) removal detection — `product_removed`
   after `removal_grace` consecutive FULL SUCCESSFUL passes without the
   listing (never on partial/failed runs); (b) daily digest rollup for
   events below `digest_below` (collect suppressed outbox rows into one
   embed); (c) `oem-radar review confirm|split <listing-id>` writing aliases.
4. **Story detection** (DESIGN_REVIEW §7 — the highest-value unbuilt
   feature). Pure rule-driven layer over the change_events stream, after
   diff, before notify. "N OEMs list the same previously-unseen component
   within a window" is a SQL query, not a new engine. Stories DEMOTE their
   constituent events (one story embed, not N pings). Deterministic
   detection; AI only narrates. Build alongside explainable additive scoring
   (base + named modifiers, every contribution stored/shown). REFUSE the
   fabricated bits: "traffic potential", "expected article value".
5. **Per-domain-concurrent fetcher** (DESIGN_REVIEW §1) — needed before the
   OEM count passes ~20; currently serial across domains, one hung site
   stalls the rest. Small worker pool, per-domain queues preserving
   per-domain serialism. This is the ONE place added concurrency is worth it.
6. **Discovery quorum + `source_degraded`** (DESIGN_REVIEW §2): trust the
   hidden-listing flag only when a bulk pass completed cleanly with a
   plausible product count vs. history. Same telemetry powers both.
7. Later, evidence-driven: `generic_html` + oddball engines (Topton, CWWK);
   Anthropic summarizer (needs owner's API key); JD/BIOS/GitHub sources
   (expect JD to resist — their global Shopify stores may be the better
   source); archive partitioning past ~10 GB; local dashboard (M12).

Explicitly REJECTED in the review, do not build: event sourcing as primary
store; a YAML parsing DSL; traffic/article-value prediction; auto-resolved
product merges; any second process/microservice/queue for a single-user
desktop tool.

## Environment gotchas (don't re-learn these)

- **Windows / PowerShell.** Commands need `.\` prefix. `start-radar.cmd`
  self-bootstraps (finds python/py, pip-installs deps first run) and holds
  the window open on double-click. It CONTAINS THE DISCORD WEBHOOK — it's a
  secret; keep it out of any zip you share publicly; rotate in Discord
  channel settings if leaked.
- **`data/` is the owner's live database and history — never delete or
  regenerate it.** It's the accumulating asset. When shipping updates, zip
  the repo EXCLUDING `data/` and have the owner extract over their copy.
- **Windows MAX_PATH (260):** cache/raw filenames truncated to 24 hex chars;
  all cache/raw writes are non-fatal by design. Keep new file writes short
  and non-fatal.
- **SQLite WAL** falls back silently on filesystems that reject it. Fine.
- **`requests` raises bare FileNotFoundError** (not RequestException) when
  certifi's CA bundle is broken — fetcher handles it with a clear message.
- **Cloud/sandbox sessions often have NO direct network egress** for the
  crawler or Discord; use the platform's sanctioned fetch tool only for
  one-off probes/fixture capture. The crawler runs on the owner's machine.
  You can (and should) still run the full pytest suite offline.
- **First run after any model change is slow and writes a snapshot per
  product** (hash epoch) but must send ZERO notifications (migration
  boundary in diff). If it pings during that wave, it's a bug.

## Live-probe quirks baked into the Shopify engine

- GMKtec: vendor flips "GMKtec"/"GMKtec US"; unicode ™ in handles; region in
  variant option2.
- Minisforum (store.minisforum.com): CPU in VARIANT OPTIONS not title;
  placeholder variants priced "0.00".
- Beelink (bee-link.com): Intel "Wildcat Lake Core 3 304" naming — pattern +
  test added. Expect more new naming schemes; extend `_CPU_PATTERNS` WITH a
  test each time.
- AOOSTAR (aoostar.com): discrete GPUs in titles ("RX 6600M"); "barebone"
  variants have no RAM/storage (empty sizes list is correct, not a bug).

## Standing rules (enforce on yourself and any contributor)

- Verify against live data before coding; captured fixtures over assumptions.
- Every real-world misparse/mis-diff becomes a test case BEFORE the fix.
- No engine work without harness fixtures + goldens.
- No policy in code — severity, thresholds, cadence live in
  `config/radar.yaml`.
- Core imports nothing vendor-specific; engines do I/O only via the injected
  Fetcher; the diff engine stays PURE (no network, no clock). Replay and
  testability depend on that purity.
- A doc claim without a test backing it is a bug (that's how the three
  original doc-drifts happened — see DESIGN_REVIEW §0).
- `pytest` green before and after every change.
