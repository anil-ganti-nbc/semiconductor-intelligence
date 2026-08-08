# Phase 0 Audit — Semiconductor Intelligence Platform 2.2 + Signal Radar Merge

Date: 2026-07-26

## 1. Sources

- `Semi intel 2.0.zip` → extracted into a disposable audit workspace
- `X Scraper.zip` → extracted into a disposable audit workspace (package `signal_radar`)
- Both archives were already extracted on disk prior to this session; original zips and extracted
  trees were **not modified**. Archived virtualenvs (`.build_venv`, `.venv`) were **not used** —
  fresh venvs were created under the scratch/temp directory for baseline verification only.

## 2. Baseline test results (measured, not taken from handoff claims)

Fresh venvs created at
`%TEMP%\claude\...\scratchpad\baseline_venvs\{semi_intel_venv,signal_radar_venv}`,
each `pip install -e ".[dev]"` from the original source trees, then `pytest -q`.

| Project | Command | Result |
|---|---|---|
| Semi Intel Platform 2.2 | `pytest -q` in `semi_intel_platform` | **202 passed** in 92.52s |
| Signal Radar | `pytest -q` in `X Scraper` | **90 passed** in 44.20s |

Both match the expected baselines given in the brief. Signal Radar's own `HANDOFF.md` contains
stale totals (73/78/80/84 recorded incrementally across sessions); **90 is the actual current
total** and is treated as ground truth going forward. Do not cite 73/78/80/84 again.

## 3. Database inspection (read-only, against backup copies)

Originals backed up untouched to `originals_backup/` in this project (never opened read-write):

- `originals_backup/semi_intel/semi_intel.db` (344 KB) — copy of
  `Semi intel 2.0/semi_intel_platform/dist/semi_intel.db`, the packaged-smoke-test database.
  Contains schema + 71 seeded monitored topics + 39 seed sources, but **no evidence/claims/
  stories** (all operational tables empty — this is a clean post-`db upgrade` demo DB, not a
  populated production one).
- `originals_backup/signal_radar/signal_radar.db` (+ `-wal`/`-shm`, 58 MB total) — copy of
  `X Scraper/signal_radar.db`.

Signal Radar row counts (measured):

| Table | Count | Brief's diagnostic figure |
|---|---|---|
| sources | 80 (47 `x`, 33 `rss`) | ~80 (47 X / 33 RSS) ✓ |
| posts | 5,211 | ~5,211 ✓ |
| entities | 3,763 | ~3,763 ✓ |
| stories | 1,409 | ~1,409 ✓ |
| evidence | 2,490 | ~2,490 ✓ |
| relationships | 6,092 | ~6,092 ✓ |
| source_candidates | 106 | ~106 ✓ |
| review_queue | 3,168 (100% reason=`unknown_codename`, all `provisional` status) | ~3,168 ✓ |
| provider_runs | 2,020 | ~2,020 ✓ |
| notifications | 3,130 (2,048 sent / 1,082 digested) | ~3,130 ✓ |

All supplied diagnostic numbers confirmed against the live data — treated as reliable context.

**Confirmed false/overbroad top stories** (by `editorial_score`, sqlite query against the
Radar DB):

```
196  United States / The Six Fi          confirmed / confirmed   0.927   <- HIGHEST SCORE IN DB
296  South Korean / Galaxy Z8             open / very_strong      0.89
26   Xeon                                 confirmed / confirmed   0.872
54   Jensen Huang                         confirmed / confirmed   0.865
```

`United States / The Six Fi` is literally the single highest-`editorial_score` story in the
entire Radar database. This is decisive evidence for the brief's core architectural ruling:
**do not import Radar's editorial layer as canonical truth.**

Root cause traced in code (`signal_radar/core/entities/__init__.py`): a `CODENAME_HARD_BLOCK`
set (containing `jensen`, `huang`, `united`, `states`, `korean`, `south`, etc.) and a
`HW_CONTEXT` gate **do already exist** in the current extraction code — but per Signal Radar's
own `HANDOFF.md`, this filter was added late in development and is **additive-only**: it
prevents *new* junk but does not retroactively purge existing junk stories from a running
database. The supplied DB predates full application of the filter, so these four stories are
partly a stale-data artifact. That does not weaken the architectural conclusion: even with the
filter live, single shared-entity matching (`core/story_engine`) still creates/joins a story
from **one** matching entity with no clustering conservatism, no multi-source requirement, and
no independence discounting — a broad company/family match (`Xeon`, `NVIDIA`) is structurally
sufficient today. The Signal Candidate + conservative clustering + independence-grouping layer
specified in the brief is a real fix, not a redundant one.

## 4. Baseline schemas

### Semi Intel Platform 2.2 (canonical, SQLAlchemy 2.x + Alembic)

19 tables, 3 migrations (`71747eaa2044` initial → `9f3c2a1b7d10` editorial/discovery →
`b71d4e2c9a30` bounded discovery ring). Full model definitions read from
`semi_intel/domain/models.py` / `enums.py`:

`Entity`, `Relationship`, `Source` (minimal: id/name/type/url/description/trust_weight/
created_at — **no** provider identity, cursor, polling flags, priority, etc. — must be
extended), `Evidence` (immutable, content-hash deduped), `Claim`, `ClaimEvidenceLink`,
`ClaimEvent` (append-only), `ClaimLinkSuggestion`, `MemorySpecClaim`, `MonitoredTopic`,
`EditorialStory`, `StoryEvidence`, `TopicMatch`, `Citation`, `SourceSuggestion`,
`DiscoverySettings`, `DiscoveryRun`, `DiscoveryResult`.

### Signal Radar (raw SQL migrations, hand-rolled repository layer, no ORM)

40 tables incl. 5 FTS5 shadow-table groups (`entities_fts*`, `posts_fts*`, `stories_fts*`).
Core tables: `sources` (platform/handle/priority/reliability/languages/expertise/signal_types/
enabled/muted/notes/cursor/meta — richer operational shape than Semi Intel's `Source`),
`posts` (platform-neutral post shape: external_id, quoted/reply lineage, raw JSON, fidelity,
processed_at), `media`, `entities`/`post_entities` (mention-level, span+extractor+confidence),
`post_labels`, `provider_runs` (cursor_before/after, collection_path, status/error),
`relationships` (generic from_type/to_type graph edges), `reliability_history`,
`review_queue`, `score_weights` (DB-tunable), `source_candidates`, `stories`/`story_entities`/
`story_scores`/`evidence` (Radar's own, non-canonical "evidence"), `notifications`,
`categories`/`tags`/`source_tags`, `metrics`.

Provider contract (`signal_radar/providers/base.py`): `Provider.collect(source, cursor) ->
CollectResult`, `.normalize(raw) -> NormalizedPost`, `.validate(handle_or_url) ->
SourceCandidate | ValidationError`, plus a `ProviderRegistry`. Implementations present:
`x/` (Playwright + human-session cookie import), `rss/`, `replay/` (fixture playback),
`bluesky/`, `discord/` (notifications, not a collection provider), stub `openai/`/
`anthropic/` AI summarizer backends, `sqlite/` (storage-adjacent).

## 5. Overlap map — which project currently owns each concept

| Concept | Semi Intel 2.2 | Signal Radar | Canonical decision |
|---|---|---|---|
| Source registry | `Source` (name/type/url/trust_weight) — thin | `sources` (platform/handle/priority/reliability/languages/expertise/cursor/muted/meta) — rich, operational | **Extend Semi Intel `Source`** with Radar's operational fields + `(provider, provider_key)` identity. One table. |
| Raw collected item | *(none — Semi Intel has no raw-post layer, `Evidence` is the first-class immutable row)* | `posts` (platform-neutral, quote/reply lineage, raw JSON, fidelity, processed_at) | **New `SignalItem` in Semi Intel**, modeled on Radar's `posts` shape, sitting *before* `Evidence`. |
| Media | *(none)* | `media` (download state, OCR fields, from_quoted) | **New `SignalMedia`**, ported from Radar concept. |
| Entity mention (pre-canonical) | *(none — Semi Intel `Entity` is already canonical)* | `post_entities` (span/extractor/confidence, resolved directly into canonical `entities`) | **New `SignalEntityMention`** — proposal layer, not auto-canonical (this is the fix for the Jensen Huang / Xeon problem). |
| Canonical knowledge-graph entity | `Entity` + `Relationship`, typed, immutable-ish | `entities` + `relationships`, generic from_type/to_type, `status` unknown/provisional/confirmed baked into the *canonical* table itself | **Keep Semi Intel `Entity`/`Relationship` canonical.** Radar's "provisional" status concept becomes `SignalEntityMention.status`, not a canonical-entity field — canonical entities are confirmed only. |
| Post classification/labels | *(none)* | `post_labels` (label/confidence/rule) | **New `SignalLabel`**, ported. |
| Immutable evidence | `Evidence` (source_id/entity_id/content_hash dedup, one row per observation) | `evidence` (story_id/post_id link table — really a "story membership" concept, not an immutable observation) | **Keep Semi Intel `Evidence` as sole canonical evidence.** Radar's `evidence` table is actually closer to Semi Intel's `StoryEvidence`; do not port it under that name — it would collide semantically. Add `origin_signal_item_id` to Semi Intel `Evidence` for promotion traceability. |
| Claims / provenance / contradiction | `Claim`, `ClaimEvidenceLink`, `ClaimEvent`, `ClaimLinkSuggestion`, `MemorySpecClaim`, contradiction engine | *(none — Radar has no claim/provenance layer, only story confidence label)* | **Canonical: Semi Intel only.** Nothing to merge. |
| Editorial "what deserves attention" layer | `EditorialStory` + `TopicMatch` + `StoryEvidence`, conservative clustering (topic overlap + 3-day window + 0.72 headline similarity), explainable `interest_score` | `stories` + `story_entities` + `story_scores`, single-shared-entity clustering (see §3), DB-tunable `score_weights`, `confidence` enum (rumor→confirmed) | **Keep Semi Intel `EditorialStory` as sole canonical editorial layer.** Radar's `stories` are **not** ported as canonical — reprocessed from raw `SignalItem`s through the new Signal Candidate pipeline instead (per brief §2/§18). Radar's DB-tunable score-weights pattern (`score_weights` table) is a good implementation pattern to reuse for the new `AttentionScoringWeights`. |
| Missing layer (both) | *(none)* | *(none — Radar conflates "cluster of posts" and "editorial story" into one `stories` table, which is the root cause of the noise problem)* | **New `SignalCandidate`** — the layer neither project has today. Bounded cluster, attention-scored, promotes into `EditorialStory`. |
| Monitored topics | `MonitoredTopic` (name/normalized_name/keyword/aliases/category/priority/enabled) | *(none — Radar has no monitored-topic concept, entities ARE the subjects)* | **Canonical: Semi Intel only.** Seed RDNA 5 / Zen 6 / RTX 60 Series / RTX 50 Super per brief §8. |
| Provider ingestion protocol | *(none — Semi Intel `ingestion/base.py` is plugin-based for structured sources like PCI IDs, not a social/RSS collector contract)* | `Provider` protocol (`collect`/`normalize`/`validate`) + registry, `x`/`rss`/`replay`/`bluesky` implementations | **Adopt Radar's `Provider` protocol conceptually into Semi Intel's `semi_intel/ingestion/` package.** Port RSS + replay first-class; X isolated as optional extra; Bluesky/Discord out of scope for this merge (not requested by brief) unless trivial to keep behind the same interface. |
| Discovery (bounded, post-promotion) | `semi_intel/discovery/` — Google News RSS, eligibility rules, budgets, 6h cache, cooldown, per-story cycle limits | *(none — Radar has no outbound discovery; `graph.py` "related_stories" is intra-DB only)* | **Canonical: Semi Intel only, unchanged.** Do not let it become a general crawler. |
| Source suggestions | `SourceSuggestion` — citation-mined from ingested evidence HTML | `source_candidates` — mined from quotes/credits in collected posts, `est_reliability`, accept/dismiss | **Merge into one workflow.** Extend Semi Intel `SourceSuggestion` (or a light superset) to also carry Radar's platform/handle-suggestion shape; unify accept path into the one `Source` registry. |
| Notifications | *(none)* | `notifications` (story-based, dedup, digest, mute-aware, Discord webhook) | **Port concept, retarget at `SignalCandidate`/`EditorialStory` events, not raw posts.** Default off/dry-run per brief §21. |
| Scheduler | `semi_intel/pipeline/service.py` (single backfill-style pass, `semintel.exe` operator) | `signal_radar/core/collector` + `Scheduler._seed_stagger` (per-source priority, per-platform startup stagger, jitter) | **Fold Radar's staggering/backoff behavior into Semi Intel's `PipelineService`/`operator.py`. One scheduler.** |
| Telemetry/observability | Discovery runs/results only | `core/telemetry/` (provider health, pipeline health, DB counts, notification stats) | **Port concept into Semi Intel, extended to cover the new provider/candidate stages.** |
| Web GUI | FastAPI + single packaged static HTML (`web/static/index.html`), unseen/seen editorial inbox, topic CRUD, discovery activity, source suggestions | FastAPI + Jinja2 server-rendered templates, dashboard/sources/stories/review/search/discovery/telemetry/weights/graph | **Canonical shell: Semi Intel's**, per brief §3 ("current GUI as canonical unless a carefully justified migration preserves every workflow"). Add a new top-level Radar area following Semi Intel's existing static-HTML/FastAPI-JSON pattern rather than pulling in Jinja2 as a second templating system. |
| CLI | Typer (`semi_intel.cli:app`) + separate operator app (`semi_intel.operator:app`, packaged as `semintel.exe`) | Typer-like (`signal_radar.__main__:main`) — `web`, `run`, `collect-once`, `reprocess`, `maintain`, `seed-rss`, `import-sources`, `add-source`, `fetch-media`, `notify`, `digest`, `test-discord`, `import-x-session`, `debug-fetch`, `connect-x` | **One CLI: extend `semi_intel.cli`** with a `radar` subcommand group (`radar status/collect/reprocess/candidates/promote/import/maintenance/provider-health`) per brief §20. Operator CLI (`semintel.exe`) keeps its packaged/service role. |
| Packaging | PyInstaller specs → `semi-intel.exe` / `semintel.exe`, both build clean today | `Install Service.bat`/`Uninstall Service.bat` (Windows Scheduled Task registration, not PyInstaller) | **Canonical: Semi Intel's PyInstaller specs**, extended with new deps/fixtures/static assets. Radar's Windows Scheduled Task registration pattern is worth keeping as documented deployment guidance (`deploy/`), not a second packaging system. |

## 6. What is explicitly NOT being ported as-is

- Signal Radar's `stories`/`story_entities`/`story_scores`/`evidence` tables — reprocessed
  through the new pipeline, never imported as canonical editorial truth (brief §2, §18).
- Signal Radar's single-shared-entity clustering (`core/story_engine._find_story`) — replaced
  by conservative multi-signal `SignalCandidate` clustering (brief §9).
- Signal Radar's raw distinct-source-ID independence counting — replaced by an explicit
  independence-grouping model (brief §10).
- Signal Radar's "Potential Traffic" experimental proxy — kept out of the primary attention
  score if ported at all, per brief §11.
- Signal Radar's `.venv`/`.pytest_cache`/`debug/*.json` (raw captured API payloads, useful only
  as replay fixtures, screened for anything sensitive before any reuse) — not packaged.

## 7. Preservation confirmation

- Original zip archives: untouched (`Semi intel 2.0.zip`, `X Scraper.zip`).
- Original extracted trees: untouched (`Semi intel 2.0/`, `X Scraper/`), read-only inspection
  throughout.
- Both databases backed up to `originals_backup/` in this project before any further work.
- Archived virtualenvs: never invoked; fresh scratch venvs used for baseline verification only
  and are not part of this project.

Phase 0 complete. Proceeding to Phase 1 (canonical schema + Alembic migration) in the merged
project tree, which will be built fresh under this directory from the Semi Intel 2.2 source as
its base.
