# Architecture Snapshot — 2026-08

This is a snapshot of the repository as it exists today, written at the
CI Stabilization milestone (see [RELEASE_NOTES.md](../RELEASE_NOTES.md)).
It is **not** a roadmap or a design proposal — it describes what is
actually running and where things live, so a future engineer (including a
future you) can orient in one sitting. Where prior documents already cover
a topic in depth, this snapshot points to them rather than repeating them.

Related documents:
- [ARCHITECTURE_RECONCILIATION.md](ARCHITECTURE_RECONCILIATION.md) — the
  most recent prior reconciliation pass; this snapshot builds on it.
- [RUNTIME_LAYOUT.md](RUNTIME_LAYOUT.md) — canonical filesystem paths.
- [ARCHITECTURE.md](ARCHITECTURE.md) — OEM Radar's own design record (ADRs).
- [../HANDOFF_CI_STABILIZATION.md](../HANDOFF_CI_STABILIZATION.md) — the
  CI investigation this milestone closes out.

## 1. Current subsystems

Two independent applications share this one repository:

| Application | Package | Purpose |
|---|---|---|
| Semi Intel | `semi_intel/` (root) | Semiconductor industry signal intelligence: RSS/X ingestion, entity resolution, editorial review, story scoring, notifications, operator dashboard. |
| OEM Radar | `src/oem_radar/` | Independent product-change tracker for mini-PC/SBC OEM storefronts (Shopify/WooCommerce/generic HTML). |

Semi Intel subsystems (each a top-level package under `semi_intel/`):

- **Signal Radar** (`signals/`) — collection (`collection.py`), clustering
  (`clustering.py`), scoring (`scoring.py`), independence checks
  (`independence.py`), aging (`aging.py`), candidate lifecycle state
  (`candidate_state.py`), promotion (`promotion.py`), source suggestion
  (`suggestions.py`), source management (`source_management.py`).
- **Signal providers** (`signals/providers/`) — `rss.py` (feed ingestion),
  `x/` (X/Twitter browser-driven collection: `session.py`, `collector.py`,
  `interceptor.py`, `normalizer.py`, `html_fallback.py`, `auth.py`),
  `replay.py` (fixture replay for tests/dev).
- **Canonical Entity Resolution** (`entities/service.py`,
  `claim_engine/entity_matcher.py`) — resolves free-text entity mentions to
  canonical `Entity` rows; the claim engine additionally does scoring
  (`claim_engine/scoring.py`) and suggestion (`claim_engine/suggestion_service.py`).
- **Editorial Workflow** (`editorial/service.py`,
  `editorial/feed_discovery.py`) — turns evidence into ranked stories,
  manages topics, discovery settings, and feed suggestions.
- **Editorial Inbox** — surfaced through `web/app.py` endpoints backed by
  `editorial/service.py`; not a separate package.
- **Evidence Graph** (`graph/queries.py`) — read-side graph queries over
  entities/evidence/claims for the dashboard.
- **Contradiction Engine** (`contradiction_engine/`) — `memory_rules.py`
  and `service.py`, flags conflicting claims.
- **Story Scoring** (`story_scoring/service.py`) — ranks stories by
  momentum/source-count for the `story rank` CLI command and dashboard.
- **Source Intelligence** (`source_intelligence/`) — scoring and service
  logic for source quality/trust.
- **Scheduler** (`operations/scheduler.py`) — `OperationalScheduler` using
  a `scheduler_job_leases` DB table for lease-based job coordination;
  also `operations/backup.py`, `health.py`, `quality.py`, `trends.py`,
  `webhook.py`, `windows_task.py`.
- **Dashboard** (`web/app.py`, `web/static/`) — FastAPI/Uvicorn app,
  served both by `semintel dashboard`-style entry points and inside the
  packaged `.exe` builds.
- **Notifications** (`notifications/`) — `service.py` (orchestration),
  `delivery.py`, `digest.py`, `query.py`, `windows_desktop.py` (native
  Windows toast notifications).
- **OEM Radar** — see `docs/ARCHITECTURE.md` for its own ADRs; summarized
  in §7 below for boundary purposes only.

## 2. Current runtime ownership

Per [RUNTIME_LAYOUT.md](RUNTIME_LAYOUT.md), each application owns a
separate canonical directory tree under `%LOCALAPPDATA%`
(`SemiIntel\` and `OEMRadar\` respectively), each with its own `config/`,
`data/`, `diagnostics/`, `logs/`, `backups/` subfolders. Precedence for
resolving any given path: explicit CLI args → environment variables
(e.g. `SEMINTEL_HOME`) → canonical platform path → legacy
working-directory path (deprecated, warns) → hardcoded default.

`semi_intel/paths.py` is the single resolver Semi Intel code calls into;
OEM Radar has its own equivalent under `src/oem_radar/`. Neither imports
the other's path-resolution code.

Entry points as currently packaged:
- `semi-intel` (`semi_intel.cli:app`) — legacy/alternate CLI.
- `semintel` (`semi_intel.operator:app`) — primary Operator CLI, also
  wraps the dashboard.
- `python -m oem_radar.cli` — OEM Radar CLI (`oem-radar run`,
  `oem-radar status`, `oem-radar dashboard`).

Two prebuilt Windows executables (`semi-intel.exe`, `semintel.exe`, ~60MB
each) currently sit in the repository root rather than `dist/`. This is
flagged as cleanup debt, not fixed in this milestone (see
[RELEASE_NOTES.md](../RELEASE_NOTES.md) known limitations).

## 3. Database ownership

- **Semi Intel** owns `semi_intel.db` (SQLite), migrated via Alembic
  (`alembic.ini`, `migrations/`). Current migration head per the last
  reconciliation pass: `a0b5d7e9f314` — re-verify with `alembic current`
  before relying on this, it is not re-checked in this snapshot.
- **OEM Radar** owns `data/radar.db` (SQLite, created on first run, not
  present by default), driven by `config/radar.yaml` and
  `config/oems/*.yaml`. No ORM/Alembic — generic SQLite/JSON storage per
  OEM Radar's own ADR-4 (content-hash dedup, no migration framework).
- No cross-database queries or shared connections exist between the two
  applications.
- `semi_intel.db` (~57MB) and `temp_ui.db` (~1MB) currently live in the
  repository root rather than under a `data/` subdirectory — same cleanup
  debt noted in §2.

## 4. Application boundaries

Confirmed by direct inspection during this milestone (Task 6 of the
reconciliation): zero imports or runtime calls exist between `semi_intel/`
and `src/oem_radar/`. They are packaged separately (`semi_intel` builds
exclusively via `pyproject.toml`; `src/oem_radar.egg-info` is a distinct
package). This matches the prior reconciliation's assessment in
[ARCHITECTURE_RECONCILIATION.md](ARCHITECTURE_RECONCILIATION.md) and has
not regressed.

## 5. Current Signal lifecycle

1. **Collection** — RSS (`signals/providers/rss.py`) or X
   (`signals/providers/x/`) providers ingest raw items into
   `SignalItem` rows.
2. **Clustering** — `signals/clustering.py` groups related items into
   `SignalCandidate` rows; `signals/independence.py` checks source
   independence for confidence.
3. **Entity mention extraction** — mentions are attached to candidates as
   `SignalEntityMention` rows (state: `CANDIDATE` → `RESOLVED`/`REJECTED`).
4. **Scoring** — `signals/scoring.py` computes `attention_score` and
   explanation JSON per candidate.
5. **Review** — the FastAPI endpoints under `web/app.py`
   (`/api/radar/candidates/*`, `/api/entities/mention-proposals/*`) let an
   operator resolve/reject mentions, dismiss, snooze, or manually promote
   a candidate. This surface is covered end-to-end by
   [tests/test_signal_candidate_review_workflow.py](../tests/test_signal_candidate_review_workflow.py),
   confirmed passing as of this milestone.
6. **Promotion** — `signals/promotion.py` handles both manual
   (`human_operator`-triggered, tested and working) and automatic
   eligibility checks. The prior reconciliation pass recorded automatic
   promotion as not yet bridging the full candidate volume into
   registered entities (0 automatic promotions against 402 clusters in
   that snapshot); this has **not** been re-verified in this milestone
   and should not be assumed resolved.

## 6. Current Editorial lifecycle

1. Evidence (citations, discovered links) feeds `editorial/service.py`,
   which clusters it into ranked `Story` records with an explanation.
2. `editorial/feed_discovery.py` runs bounded discovery (RSS/search-based)
   to find candidate new sources, subject to per-hour budgets and
   cooldowns, gated by a persisted automatic-mode setting (off by
   default).
3. Stories, topics, and discovery settings/activity are exposed through
   `web/app.py` endpoints for the operator dashboard.
4. `contradiction_engine/` and `claim_engine/` operate downstream of
   editorial evidence to flag conflicting claims and suggest
   entity/source matches respectively.

## 7. Current OEM lifecycle (summary)

Full detail lives in [ARCHITECTURE.md](ARCHITECTURE.md) (OEM Radar's own
ADRs). In one line: a stateless, catch-up-semantics pipeline
(`discover → fetch → parse → normalize → validate → resolve → snapshot →
diff → score → outbox`) invoked as `oem-radar run`, with per-source
`min_interval` config standing in for a scheduler, content-hash dedup
instead of storing every crawl, and a deterministic diff engine that
treats AI purely as a prose renderer over validated facts. OEM Radar
remains fully isolated from Semi Intel per §4 — this snapshot does not
change that.

## 8. Current notification flow

`notifications/service.py` orchestrates delivery; `delivery.py` and
`digest.py` handle immediate vs. digest-style batching; `query.py` reads
notification history for the dashboard; `windows_desktop.py` sends native
Windows toast notifications. OEM Radar has a separate Discord-webhook
notifier with its own outbox table (per its ADR-1) — the two notification
paths do not share code or delivery state.

## 9. Current deployment assumptions

- Single-machine, Windows desktop deployment. No Docker, no NAS, no
  multi-service orchestration exist today — these are explicitly future
  work (see §10 and `RELEASE_NOTES.md`).
- Both applications are invoked ad hoc or via Windows Task Scheduler
  (`install-hourly-task.cmd`, `crawl-hourly.cmd`, `crawl-silent.vbs`,
  `uninstall-hourly-task.cmd`, `start-radar.cmd`, `dashboard.cmd` at the
  repository root) rather than running as a persistent daemon, consistent
  with OEM Radar's ADR-1 stateless-pipeline decision, which Semi Intel's
  scheduler mirrors via lease-based job coordination rather than an
  in-process daemon loop.
- Packaged distribution is via PyInstaller-built `.exe` files for Semi
  Intel; OEM Radar is run from source (`python -m oem_radar.cli`).
- The repository has no git history in its current location
  (`git init` has not been run here). All version control assumptions in
  other documents (e.g. references to commits or branches) describe a
  different checkout, not this one.

## 10. Known future extraction boundaries

Per the "long-term context" already recorded in the CI stabilization task:
this repository is intended to become one component of a larger
automation platform ("Clankmaster 9000"), eventually splitting into
independent repositories/containers per collector (Semi Intel, OEM Radar,
Smartphone Radar, Free Games Tracker, Chinese Tech Wire, future
collectors) deployed on a Synology NAS. As of this snapshot:

- **OEM Radar** (`src/oem_radar/`) is the most extraction-ready piece:
  zero cross-imports with Semi Intel, its own package metadata
  (`src/oem_radar.egg-info`), own database, own config directory. The
  prior reconciliation called this "trivial" to extract via a clean
  subtree move; nothing in this milestone changes that assessment.
- **Semi Intel** is not extraction-ready in the same sense — it owns the
  repository root (`pyproject.toml`, `alembic.ini`, packaging metadata)
  and several root-level artifacts (`.exe` builds, `.db` files, ops
  `.cmd`/`.vbs` scripts) are entangled with the repository root rather
  than scoped to a subdirectory. Extracting Semi Intel cleanly would
  first need those root-level artifacts relocated under a
  Semi-Intel-specific subtree (documentation-only recommendation; not
  undertaken in this milestone — see §11).
- No Docker, no NAS-specific configuration, and no shared inter-service
  contract exist yet. This snapshot does not propose one.

## 11. Recommendations arising from this snapshot (documentation only)

These are observations, not action items taken during this milestone:

- Root-level `.exe` builds and `.db` files could move to `dist/` and
  `data/` respectively to match the intended root layout in
  `RUNTIME_LAYOUT.md`'s own `data/` convention; this was left alone
  because it touches release/runtime paths, not test-only cleanup, and
  was out of scope for a CI reconciliation pass.
- The six `.cmd`/`.vbs` operator scripts (`crawl-hourly.cmd`,
  `crawl-silent.vbs`, `dashboard.cmd`, `install-hourly-task.cmd`,
  `uninstall-hourly-task.cmd`, `start-radar.cmd`) are legitimate
  deployment tooling analogous to `deploy/*.example` (the Linux
  equivalents) and were kept in the root untouched — they are not CI
  debugging artifacts.
- Automatic candidate promotion's real-world throughput (the 0-promotion
  observation in `ARCHITECTURE_RECONCILIATION.md`) should be re-verified
  independently of this CI milestone before being reported as fixed or
  broken in any future handoff.
