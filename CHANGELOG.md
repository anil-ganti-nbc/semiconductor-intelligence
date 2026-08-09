# Changelog

## 3.3.14 (2026-08-09) — Unattended collection repair

- Replaced the malformed `cmd /c` Windows Task Scheduler action with native,
  separately configured executable, argument, and working-directory fields.
  Installation/repair remains explicit and idempotent, preserves an existing
  task's principal and settings, and safely handles special-character paths.
- Task health now validates the complete action rather than only guessing an
  executable from command text. Automation reports stale arguments/working
  directories and explains common nonzero Task Scheduler results.
- Added explicit provider-aware polling controls to Radar Sources. Operators
  can enable/disable selected sources or enable eligible RSS feeds in bulk;
  X activation requires a count-bearing confirmation, global X opt-in, and a
  structurally usable local session. No production source was enabled silently.
- Automation & Health now reports zero-polling RSS/X configurations and missing
  X sessions with direct navigation to Sources. Disabled legacy RSS sources are
  excluded from the older automatic collection path.
- No schema migration, scoring, clustering, notification, digest, delivery,
  backup, cloud-migration, or X scraping behavior was changed. Alembic remains
  `a0b5d7e9f314`.

## 3.3.13 (2026-08-03) — Portable populated checkpoint repair

- Fixed the private 3.3.12 archive's `semintel.config.json`, which had been
  accidentally rewritten by a disposable frozen smoke to an absolute smoke
  database path. The archive's included 52 MB populated database was intact;
  the bad config merely redirected the application to the empty smoke database.
- Restored the portable relative configuration (`data_dir: .` and
  `sqlite:///semi_intel.db`) and added a packaging regression that fails if the
  operator checkpoint is ever staged with a machine-specific database path.
- Sanitized Radar's recent provider-run errors through the existing safe-error
  boundary. Playwright launch commands, temporary extraction paths, profile
  paths, and session paths are no longer rendered in the GUI. Recognized browser
  launch, missing-browser, and missing-session failures receive short actionable
  summaries; all other first-line errors are capped at 240 characters.
- No database migration, data rewrite, X token/session operation, scoring,
  collection-policy, scheduling, delivery, or safety-default change was made.
  Alembic remains `a0b5d7e9f314`.

## 3.3.12 (2026-08-03) — Frozen X runtime packaging repair

- Fixed the official Windows executables omitting Playwright even though the
  populated operator platform includes X sources. Frozen builds now include
  Playwright's Python modules, driver, and Node runtime; X collection no longer
  fails immediately with the inapplicable `pip install semi-intel[x]` message.
- Updated both official build scripts to install the `web` and `x` extras and
  added packaging regressions for both executable specs. Source installations
  may still omit Playwright and continue to degrade cleanly.
- Browser sessions, cookies, tokens, and Chromium itself are not embedded in the
  executable or archives. The operator's existing local Playwright Chromium and
  separately imported X session remain required. X access remains opt-in and
  disabled by default.
- No application workflow, schema, scoring, collection policy, scheduling,
  delivery, or safety-default behavior changed. Alembic remains `a0b5d7e9f314`.

## 3.3.11 (2026-08-03) — Operator reliability repair

- Repaired Radar source operations. Sources now expose truthful health states
  and safe errors, can be edited in place, and support checkbox selection plus
  sequential Collect selected/Collect all runs. RSS is processed before X; an
  X-inclusive batch requires confirmation, honors the existing opt-in, can be
  cancelled between sources, and stops further X work on authentication,
  challenge, or rate-limit failures. Disabled sources remain disabled.
- Removed the duplicate source-suggestion card from Signal Radar; the dedicated
  Suggested Sources workspace remains authoritative. Legacy-import selection is
  unchanged.
- Repaired manual digest refresh and external delivery. Manual generation can
  refresh an existing daily digest after new notifications, empty results now
  explain the applicable data/threshold state, and delivery exposes configured,
  pending, success, and sanitized failure state without revealing secrets.
  Delivered digests remain idempotent and are not re-sent accidentally.
- Repaired Automation & Health so panels fail independently, job controls show
  running/error state, and status reflects the actual Windows task, executable
  path, heartbeat, and stale run records. Task installation/repair requires an
  explicit confirmation, and stale reconciliation preserves audit history and
  active leases.
- Restored the X provider's missing persistent browser-session component from
  the last intact fusion source. Playwright and X remain optional and disabled
  by default; no credentials or session data are packaged.
- No migration, scoring, collection-expansion, promotion, notification-policy,
  backup-policy, or safety-default change was made. Alembic remains
  `a0b5d7e9f314`.

## 3.3.10 (2026-08-02) — Signal Radar aging

- Added deterministic, read-only Current/Older classification for Radar
  candidates, with a seven-day default and selectable 3-, 7-, 14-, or 30-day
  windows. Old candidates remain available under Older and All ages and are
  never deleted, dismissed, or rescored by aging.
- Age is based on the first published/observed report in each existing
  independence group. A late duplicate or dependent citation in the same group
  cannot refresh the clock; a genuinely new independent group can resurface the
  candidate. Collection time is used only when publication time is unavailable
  and is explicitly disclosed.
- Added age metadata and validated filters to candidate list/detail APIs. Age
  filtering composes before limit with seen/state, topic, score, and sort
  controls; meaningful activity now drives newest/oldest Radar sorting.
- Added Current/Older/All ages controls, age-window selectors, subdued Older and
  accurate Resurfaced badges, activity reasons, and actionable empty states to
  Signal Radar and the Editorial Inbox fallback shortlist.
- No migration, scoring, clustering, collection, promotion, notification,
  scheduling, backup, or safety-default change was made. Alembic remains
  `a0b5d7e9f314`.
- Verification: **22 focused**, **197 relevant**, and the complete **519-test**
  suite passed with zero failures, including dashboard JavaScript syntax.
- Frozen verification found and repaired a packaging-only missing dynamic AnyIO
  asyncio backend. A focused spec regression passed, both executables were
  rebuilt, and corrected frozen Current/Older/All ages plus restart persistence
  passed on deterministic disposable fixtures.

## 3.3.9 (2026-08-01) — Canonical entities and claim matches

- Turned the empty Entities tab into an actionable canonical-entity workspace
  with summary counts, search/type filtering, explicit creation, parsed aliases
  and attributes, entity detail, and bounded review of unresolved Radar mention
  groups.
- Added deterministic, operator-only mention resolution to a new or existing
  canonical entity, optional alias capture, exact normalized grouping,
  reject/ignore actions, and synchronization of affected candidate-entity
  associations. Unknown extracted text is still never promoted automatically.
- Replaced numeric subject-entity entry with searchable canonical selectors in
  manual and Radar claim creation, with relevant resolved candidate entities
  shown first but never silently selected.
- Renamed the legacy Suggestions surface to Claim Matches and added readiness
  diagnostics, enriched claim/evidence/source/Radar context, history filters,
  actionable empty states, and detailed deterministic scan results.
- Fixed the matcher so evidence already linked to a claim is never proposed,
  and stale historical proposals return a clear conflict instead of an
  integrity error. Scoring weights and the minimum threshold are unchanged.
- No migration or new table was required; Alembic remains `a0b5d7e9f314`.
- Verification: **20 focused**, **128 relevant**, and the complete **507-test**
  suite passed with zero failures, including dashboard JavaScript syntax.

## 3.3.8 (2026-08-01) — Newsroom usability pass

- Made Signal Radar candidate rows keyboard- and mouse-accessible and added an
  explicit “View reports” affordance. Candidate detail now scrolls/focuses into
  view and exposes report excerpts, safe source links, topics, labels,
  attachment reasons, independence groups, score components, and clear loading
  and error states.
- Replaced the passive Claims and Evidence tabs with one actionable Claims &
  Evidence workspace. Operators can create claims, convert a Radar report into
  idempotent canonical Evidence, create-and-link in one flow, attach existing
  evidence, change stance, unlink without deleting evidence, and return to the
  originating Radar candidate.
- Added a ranked “Radar candidates awaiting editorial review” shortlist to the
  Editorial Inbox. It includes below-threshold candidates for human review
  without changing scores, age limits, or disabled automatic-promotion policy.
- Added an explicit manual-promotion confirmation with editable headline,
  eligibility warnings, idempotent promotion, and immediate inbox refresh.
- No migration, scoring change, collection expansion, LLM, OCR, or safety-
  default change was made. Alembic remains at `a0b5d7e9f314`.
- Added eight focused newsroom-usability acceptance tests; the focused gate is
  17 tests including dashboard JavaScript checks, and 108 relevant regressions
  passed before the complete **496-test** suite completed with zero failures.

## 3.3.7 (2026-08-01) — Optional local Windows desktop notifications

- Added an opt-in, disabled-by-default `windows_desktop` delivery channel using
  the native Windows PowerShell toast API. No network service, tray process,
  opaque executable, or new Python runtime dependency is involved.
- Added compact Alerts & Digest controls for enablement, support/status,
  immediate feedback, and a clearly labelled synthetic test notification.
- Desktop attempts reuse existing activation, severity, mute, quiet-hours,
  hourly-cap, idempotency, and bounded-retry rules while remaining independent
  of webhook state. Desktop success, deferral, or failure never changes story
  seen state, notification read/dismiss state, or webhook delivery state.
- Added migration `a0b5d7e9f314`, which adds only the persisted desktop opt-in
  boolean with a safe false default. Existing installations remain disabled.
- Added 11 focused tests covering platform support, persistence, success,
  exactly-once delivery, quiet hours, muting, retryable/permanent failures,
  webhook independence, pipeline failure isolation, API controls, GUI controls,
  and state preservation.
- Verification: 156 relevant regressions, 9 dashboard JavaScript checks, and
  the complete 488-test suite passed before either executable was rebuilt.

## 3.3.6 (2026-08-01) — Stabilization Pass 2: core newsroom workflow

A bounded end-to-end validation and repair pass over the primary operator
workflow. No feature phase, schema change, scoring-policy change, or safety-
default change.

- Added a deterministic acceptance test covering monitored topics and aliases,
  editorial and radar RSS fixtures, exact/alias/noise matching, duplicate
  editorial clustering, independent radar corroboration, unregistered-publisher
  suggestions, seen-state persistence, notification idempotence, saved views,
  restart persistence, and backup rehearsal.
- Fixed a confirmed lifecycle defect in backup path resolution. A relative
  configured backup directory was resolved against each process's current
  working directory, so a backup created by the dashboard could be rejected by
  `semintel backups rehearse` when the CLI was launched elsewhere. Persisted
  relative backup paths now resolve from the active SQLite database directory,
  and all operator backup commands use the same service-owned rule.
- Added a regression test that creates a backup from one working directory and
  successfully rehearses it from another.
- Verified 477 tests and the dashboard JavaScript syntax checks. Both frozen
  executables were rebuilt only after the complete suite passed, then exercised
  against local fixtures with restart persistence and backup rehearsal.

## 3.3.5 (2026-07-27) — Stabilization Pass 1: application and database lifecycle

A bounded test-and-repair pass proving install/start/stop/restart/upgrade/
recover all behave correctly across every supported entry point, using
disposable databases and copies throughout. Not a feature phase.

- **`create_app()` now reconciles the schema via the same Alembic-aware
  path `semintel install`/`update` already used**, instead of a bare
  `Base.metadata.create_all()`. The dashboard (`semi-intel web serve`,
  `semintel gui`) is a supported entry point someone can launch directly
  against an existing database without ever running `semintel install`
  first; a bare `create_all()` only adds missing tables, so it silently
  left `alembic_version` stale on an older-but-compatible database and
  would have silently masked a future non-additive migration. Moved the
  shared logic to `semi_intel.cli.upgrade_or_stamp_to_head()` (previously
  private to `operator.py`) so both the CLI and the web app call the exact
  same reconciliation. Confirmed: opening a database stamped at the Phase
  8 head directly through the dashboard now correctly advances
  `alembic_version` to head, creates the missing Phase 9 tables, and
  preserves every existing row -- see
  `tests/test_lifecycle_bootstrap.py`.
- **`stale_run_threshold_minutes` is now actually wired up.** This
  operator-configurable setting (exposed in settings/schemas since Phase
  9) existed purely as dead configuration -- nothing ever consulted it.
  A job whose process is killed outright (not a clean exception -- 
  `run_job()`'s own `try/except/finally` never gets to run) leaves its
  `OperationalJobRun` row stuck at `RUNNING` with no `finished_at`
  forever, invisible to the operator. `HealthService.report()` now flags
  this as a `degraded` issue once a run has been `RUNNING` longer than
  the configured threshold, mirroring the existing stale-lease check
  immediately above it. The stuck row's own status is never rewritten --
  it stays honestly `RUNNING`, never masquerading as complete; the
  underlying lease (if still present) continues to self-heal via
  `LeaseManager.acquire()`'s existing stale-lease takeover on the next
  real attempt, exactly as before.
- Investigated and explicitly did **not** change: `semintel install
  --data-dir`'s `semintel.config.json` placement (writes to the invoking
  folder, not the data folder) looked like a bug on first read, but it is
  documented, intentional, and already covered by
  `tests/test_operator_cli.py::test_install_with_data_dir_flag` -- caught
  before shipping by checking existing tests first.
- 41 new focused lifecycle tests across five new files (`
  tests/test_lifecycle_bootstrap.py`, `tests/test_lifecycle_persistence.py`,
  `tests/test_lifecycle_operations.py`, `tests/test_lifecycle_shutdown.py`,
  `tests/test_lifecycle_core_endpoints.py`) covering fresh initialization,
  repeated-init idempotence, older-database upgrade via the dashboard,
  full-dataset restart persistence, session rollback/closure,
  frozen-resource path resolution, a real subprocess hard-killed mid-job
  (recovered on the next attempt), two real dashboard processes against
  one database, real-process clean shutdown/port-release/immediate
  restart, paths containing spaces and punctuation, a backup/restore
  round trip into a separate disposable location, and direct loopback
  HTTP checks against every core endpoint.
- No schema change, no new migration. Scheduler, collection, X
  collection, automatic promotion, and external delivery all remain
  disabled by default; no scoring, promotion, or notification-
  classification behavior changed.

## 3.3.4 (2026-07-27) — Dashboard concurrency fixes

Two reliability bugs surfaced from running the packaged 3.3.3 build against
a real database: the dashboard's own concurrent page-load requests could
crash several routes. Both are fixed; neither touches saved-view behavior,
scheduling policy, or any disabled-by-default safety setting.

- **`database is locked` on many routes.** `get_session()`
  (`semi_intel/web/app.py`) built a brand-new SQLAlchemy engine -- and
  re-ran full schema reflection (`Base.metadata.create_all()`, a
  `PRAGMA table_info` per table) -- on *every single HTTP request*. The
  dashboard's own tab-load JS fires a dozen-plus concurrent requests via
  `Promise.all()`; each one independently hammered the same SQLite file
  with a fresh connection and a reflection burst, and with no configured
  wait margin a concurrent reader colliding with a writer failed
  immediately instead of retrying. Fixed: `create_app()` now builds one
  engine at startup (as it already did for initial seeding) and overrides
  the `get_session` dependency via `app.dependency_overrides` so every
  request reuses that same engine/connection pool -- `create_all()` now
  runs once per server lifetime, not once per request. Also raised the
  SQLite connection `timeout` from the driver default to 30s
  (`semi_intel/db.py`) as headroom for genuine residual contention.
  Journal mode is deliberately left as the rollback-journal default, not
  WAL -- `BackupService` copies the `.db` file directly
  (`semi_intel/operations/backup.py`), and WAL would risk a backup missing
  recent commits still sitting in a separate `-wal` file.
- **`UNIQUE constraint failed` on singleton settings tables.** Seven
  get-or-create singleton-row helpers (`get_scheduler_settings`,
  notification/collection/promotion/scoring settings,
  `DiscoverySettingsService.get()`, `WebhookConfigurationService.status_row()`)
  all did `session.get(Model, 1)` -> `None` -> insert `id=1` with no
  handling for a concurrent request's session winning that same race on a
  brand-new database -- the loser raised
  `sqlite3.IntegrityError: UNIQUE constraint failed`. Fixed with the same
  `try: flush() / except IntegrityError: rollback(); re-fetch` pattern
  already used by `OperationalScheduler.acquire()`'s lease logic elsewhere
  in this codebase.
- 10 new regression tests: 7 deterministically reproduce each singleton
  race (a real committed "winner" row plus a forced single missed read on
  the "loser" session -- confirmed to reproduce the exact
  `UNIQUE constraint failed: scheduler_settings.id` from live use before
  the fix), plus 3 covering the session/engine-reuse behavior and a
  12-way concurrent burst against the routes that crashed in live use.
- No schema change, no new migration, no behavior change to saved views,
  scheduling, collection, scoring, promotion, or delivery -- purely a
  concurrency/reliability fix to how the web layer opens database
  connections and initializes singleton settings rows.

## 3.3.3 (2026-07-27) — Saved notification view composition

- Saved notification views now compose and apply their complete stored
  filter set instead of only the first selected event type, severity, and
  topic. Semantics: multiple values within one category combine with OR
  (e.g. important OR urgent); different categories combine with AND (state
  AND severity AND event type AND topic AND date window AND search).
- Added a bounded `NotificationQueryService`
  (`semi_intel/notifications/query.py`) shared by `GET /api/notifications`
  and saved-view application, so the API and GUI never reimplement filter
  rules separately. Read-only -- never mutates read/dismissed/feedback/mute
  state.
- Added controlled sort orders: newest, oldest, and severity (explicit
  urgent > important > notable > informational rank, tied-broken by newest
  timestamp then id -- not alphabetical/enum order), with deterministic,
  repeatable ordering.
- Added controlled date windows (1/3/7/14/30/90 days), computed from
  timezone-aware UTC "now" at request time from the notification's
  `event_at` -- the saved view stores only the day-count rule, so the
  cutoff naturally advances on every re-application.
- Extended `GET /api/notifications` to accept repeated `event_type`,
  `severity`, and `topic_id` query parameters for multi-value OR filtering,
  while every existing single-value call keeps working unchanged.
- Extended `SavedViewService` (`semi_intel/operations/quality.py`) with
  `get()`, `duplicate()` (proposes `"<name> copy"`, `"<name> copy 2"`, ...
  on collision), and `describe()` (a short human-readable summary, e.g.
  "Unread · important or urgent · high attention · Last 7 days"). `save()`
  now validates topic ids against real monitored topics, validates the
  controlled date-window/sort/state vocabularies, and -- when
  `relation_filters` is not explicitly passed -- preserves whatever is
  already stored instead of silently clearing it, so editing unrelated
  fields never discards existing relation data.
- Added `GET/POST/PUT/DELETE /api/notifications/saved-views[/...]`
  completions: get-one, duplicate, and a read-only
  `GET .../saved-views/{id}/apply` that returns the view's complete,
  composed notification list without mutating anything. Missing views
  return 404; invalid controlled filters and duplicate names return 422.
- Rebuilt the Alerts & Digest saved-views panel on the existing vanilla
  HTML/CSS/JS dashboard (no new frontend dependency): a native `<dialog>`
  editor with full state/event-type/severity/topic checkboxes, date-window
  and sort selects, and search text; Apply/Edit/Duplicate/Delete/Clear
  actions; a "Viewing: <name>" active-view banner with its description; a
  "Filters changed -- save as a new view or update this view" indicator
  when manual toolbar filters diverge from the active view; and a
  confirmation prompt before delete. No raw JSON is shown to the operator.
- No schema change -- `saved_notification_views` already supported the full
  filter model since 3.3.0; this increment only completes how it's read,
  composed, and edited. Existing 3.3.0-3.3.2 saved-view rows remain
  readable and editable unchanged.
- Scheduler, collection, X collection, automatic promotion, and external
  delivery all remain disabled by default; no scoring, promotion,
  scheduling, backup/restore, or notification-generation behavior changed.

## 3.3.2 (2026-07-27) — Operational trend summaries

- Added a read-only deterministic trend service over existing operational-job
  and notification-feedback records, with validated 7-, 30-, and 90-day
  windows.
- Added job status counts, reliability rate, counts and average duration by
  job type, alert useful/not-useful counts and rates, event-type useful rates,
  and the five most common not-useful reasons.
- Added `GET /api/operations/trends?days=...`; invalid windows return a
  controlled 422 response.
- Added a compact Recent trends panel to Automation & Health using only
  existing HTML/CSS/JavaScript, including useful empty states.
- No migration or data mutation. Scheduling, delivery, backup, restore,
  collection, promotion, scoring, and all disabled defaults are unchanged.

## 3.3.1 (2026-07-27) — Backup restore rehearsal

- Added `BackupService.rehearse()` (`semi_intel/operations/backup.py`):
  copies a verified backup to a throwaway temp file, opens it through a real
  SQLAlchemy engine, and runs representative counts through the actual
  domain models (`Source`, `SignalCandidate`, `Notification`, `ProviderRun`)
  before deleting the copy. The existing `verify()`/`restore(dry_run=True)`
  only ran raw `sqlite3` checks (integrity + table presence) against the
  backup file itself -- that misses (1) a backup stamped behind the
  currently installed Alembic head, which would need `db upgrade`
  immediately after a restore, and (2) a file that passes integrity_check
  but fails to load through the ORM due to schema/model drift. Never
  touches the live database, session or leases; never raises for a bad
  backup -- reports `passed: false` with a redacted error instead.
- Added `semintel backups rehearse <path> [--json]`, surfacing schema
  currency and per-table ORM counts, with a clear "run `semintel update`"
  warning when the backup predates the installed app.
- Picked from `HANDOFF.md`'s own "Recommended Phase 10" list ("backup
  restore rehearsal tooling") as a single, narrow, additive increment --
  both Windows executables were subsequently rebuilt and frozen-smoke-tested
  with the new command against a disposable database.
  no new migration, no changed behavior for any existing command. 5 new
  tests (2 CLI, 3 service-level) -- 382 passing total (377 baseline + 5).

## 3.3.0 — Phase 9: operational automation

- Added a bounded, run-once `OperationalScheduler` (Windows Task Scheduler
  oriented, not a daemon) covering the intelligence pipeline, notification
  generation, daily digest, delivery retry, backup, database maintenance,
  retention cleanup and health check job types, with database-backed
  atomic leases (`OperationalJobLease`) so overlapping runs record a safe
  skip instead of double-running.
- Added `BackupService` (SQLite backup API, SHA-256 + integrity-verified
  manifests, age/count-bounded pruning, transactional CLI-only restore with
  a mandatory safety backup first) and `HealthService` (plain-language
  `healthy`/`attention_needed`/`degraded`/`disabled`/`unknown` component
  states) and `DiagnosticsService` (secret-safe ZIP export).
- Added the one real network adapter, `generic_https_webhook`: HTTPS-only,
  credentials read only from environment variables (never stored), no
  redirects, redacted errors, bounded retries, disabled until a synthetic
  test passes and the operator explicitly enables it.
- Added deterministic notification presets (Quiet/Balanced/Breaking News),
  useful/not-useful feedback with summaries, and saved notification views.
- Added migration `f9a4c6d8e203` (scheduler settings, job runs/leases,
  notification feedback, saved views, delivery adapter status, backup
  records -- 48 application tables total). Added the `semintel automation
  .../health/backups .../diagnostics create` CLI surface and the
  **Automation & Health** GUI area. 377 passing total.

## 3.2.0 — Phase 8: notifications and digests

- Added a deterministic notification subsystem (`semi_intel/notifications/`)
  generating alerts for high attention, score increases, new independent
  corroboration, promotion readiness/completion, topic activity, source
  suggestions and provider failure/recovery -- generation lives in
  `NotificationService`, never derived ad hoc in the GUI.
- Added six tables (notifications, settings, transition watermarks,
  delivery attempts, digests, provider incidents) via migration
  `e8b7c2d4a901`. Historical/imported data seeds state via
  `NotificationSettings.activation_at` without alerting retroactively.
- Added stable timezone-aware daily digest windows and deduplication,
  read/unread and dismiss/restore state, retention, muting, quiet hours,
  hourly caps and bounded exponential retry.
- Added `semi-intel notifications ...` CLI, `/api/notifications...`, and
  the **Alerts & Digest** GUI tab. No real external messaging adapter yet
  (`external_delivery_enabled` defaults false; added in 3.3.0). Pipeline
  notification generation runs last and is fault-isolated from every other
  stage. 357 passing total (334 baseline + 23).

## 3.1.0 — Legacy Signal Radar importer

- Added `LegacyRadarImporter`, a preview-first and transactionally applied
  importer for the pre-merge Signal Radar SQLite schema.
- Imports the trustworthy raw layer: sources, posts, media metadata, provider
  run history and source candidates. Imported sources never start polling
  automatically; local media paths and secret/session fields are discarded.
- Deliberately reports but does not import Radar's derived stories, scores,
  evidence, entity graph, labels, review queue or notifications. Raw posts
  enter the 3.1 analysis/candidate pipeline instead.
- Added `semi-intel radar import --database ... [--apply] [--json]` with
  category selection, human/machine-readable reports, rollback on failure and
  repeat-run idempotence.
- Added a GUI file-picker workflow with mandatory preview, reviewed apply, a
  128 MB upload bound and clear skipped-table explanations.
- Updated the manual Radar cluster action to analyze pending items before
  clustering and rescoring, making it the one post-import action.
- Validated against the supplied 58 MB Radar database: all 9,473 safe rows
  imported and the second run identified all 9,473 as duplicates. Current
  analysis produced 350 candidates and suppressed 4,532 low-signal posts.

## 3.0.0 (in progress) — Signal Radar absorption

- **Checkpoint stabilization.** Removed the redundant
  `SignalItem.origin_evidence_id` reverse link; the unique
  `Evidence.origin_signal_item_id` is now the single provenance path, so
  SQLAlchemy no longer has a circular table dependency. Added migration
  `d3c8e41f9a62`.
- Added shared feed-URL identity normalization across the legacy Source and
  Signal Radar source-add paths. Scheme, `www`, default ports, trailing
  slashes, fragments and tracking parameters no longer allow one RSS feed
  to be registered and polled twice.
- Rebuilt and smoke-tested both Windows executables. The complete stabilized
  suite is **326 passing tests**.

- **Phase 1 — canonical schema.** Extended `Source` with operational
  collection fields (`provider`, `provider_key`, `enabled`, `polling_enabled`,
  `muted`, `priority`, `languages`, `expertise`, `signal_types`, `notes`,
  `cursor`, `last_success_at`, `last_observed_item_at`, `error_state`,
  `updated_at`, `provider_metadata`) and a `(provider, provider_key)` unique
  identity, replacing name-only dedup for the new collection path. Added the
  raw signal layer: `SignalItem`, `SignalMedia`, `SignalEntityMention`
  (proposal-only, never auto-canonical), `SignalLabel`, `ProviderRun`. Added
  the missing candidate layer: `SignalCandidate` plus
  `CandidateSignalItem`/`CandidateTopicMatch`/`CandidateEntity`/
  `CandidateRelationship` joins and `SignalIndependenceGroup`/
  `SignalIndependenceGroupMember` for echo-chamber discounting. Added
  persisted, conservative-default settings: `SignalCollectionSettings`,
  `AttentionScoringSettings`, `CandidatePromotionSettings`. Extended
  `Evidence` with an optional unique `origin_signal_item_id` for idempotent
  promotion traceability. Extended `SourceSuggestion` for unified
  domain+handle suggestions (`kind`, `platform`, `provider_key`,
  `independent_origin_count`, `inferred_reliability`).
- Added migration `f4f0279f3459` (after `b71d4e2c9a30`), verified against a
  fresh database, the real pre-merge packaged database (39 sources, 71
  topics — all preserved, all new columns land on safe off-by-default
  values), and a full downgrade/upgrade round trip. See `PHASE0_AUDIT.md`
  for the audit this is built on and `HANDOFF.md` for the full merge record.
- None of Signal Radar's `stories`/`story_entities`/`evidence` tables were
  imported as canonical; see `PHASE0_AUDIT.md` section 6 for why.
- **Phase 2 — provider ingestion.** Ported Signal Radar's `Provider`
  contract (`semi_intel/signals/providers/`) adapted to this codebase's
  synchronous style: `RSSProvider` (feedparser-based, a separate path from
  the pre-existing direct-to-Evidence `RSSSourcePlugin`), `ReplayProvider`
  (fixture playback for tests), and an optional `XProvider` ported in full
  (session/auth/interceptor/collector/html_fallback/normalizer) but gated
  so importing it never requires Playwright and constructing it without the
  `x` extra or an imported session raises `ProviderUnavailable` cleanly.
  Added `CollectionService` (`semi_intel/signals/collection.py`): persists
  `SignalItem`/`SignalMedia`, dedups on `(provider, external_id)`, advances
  `Source.cursor` only after a successful commit, isolates one bad
  source/provider from the rest of a cycle, applies priority-derived poll
  intervals and per-provider startup staggering, and is gated off by
  default via `SignalCollectionSettings` (collection and X collection both
  default to disabled; a manual `radar collect` bypasses the *automatic*
  gate but X still requires its own explicit opt-in either way). Wired into
  `PipelineService.run_once()` as a new isolated stage. Added `radar
  status`/`radar collect`/`radar provider-health` CLI commands. 27 new
  tests (providers, collection service, CLI) -- 230 passing total.
- **Phase 3 — signal analysis.** Added `semi_intel/signals/analysis.py`: a
  three-tier extractor (monitored-topic match reusing
  `editorial.service.match_topic()` exactly; canonical-entity match against
  *existing* entities only, never creating one; unknown TitleCase-shaped
  phrases staying `SignalEntityMention(status=candidate|rejected)` forever).
  Ported Signal Radar's hardware-context gate, hard-block list, and
  edge-stopword trimming verbatim, plus added `architecture`/`overview` to
  the hard-block list per the audit's "Architecture Overview" fixture. Added
  a new `SignalTopicMatch` table + migration `4ee15190b40b` for per-item
  topic hits. Ported the label classifier (`SignalLabel`, deterministic
  lexical + entity-type rules) unchanged. Added `ANALYSIS_VERSION` tracking
  and `reprocess_stale_items()`/`analyze_unprocessed()` (idempotent: deletes
  prior topic-match/mention/label rows before re-analyzing, `raw_payload`
  itself is never touched). Wired analysis into `PipelineService.run_once()`
  (network-free, so it runs every cycle regardless of the collection
  toggle) and added `radar reprocess`. Regression-tested directly against
  PHASE0_AUDIT.md's four highest-ranked false-positive stories (`United
  States / The Six Fi`, `South Korean / Galaxy Z8`, `Jensen Huang`, `Xeon`)
  plus the `Architecture Overview` fixture -- none can become a canonical
  Entity or a resolved mention through this extractor, including the
  stronger case of `Jensen Huang` appearing *with* hardware context present.
  16 new tests -- 246 passing total.
- **Phase 4 — Signal Candidate engine.** Added `semi_intel/signals/
  clustering.py`: conservative, explained attachment scoring (specific
  monitored-topic match or exact structured-artifact match sufficient
  alone; quote/reply lineage short-circuits directly to the parent's
  candidate; entity overlap and text similarity alone cannot force a
  merge). Excludes broad umbrella topics (GeForce, CUDA, x86, ARM, ...)
  from the topic-match attach bonus -- a real bug caught by testing: two
  different products both mentioning "GeForce" were merging on that alone.
  Added `semi_intel/signals/independence.py`: union-find grouping by same
  canonical URL, quote/reply lineage, same author, or explicit citation
  (via/according to/reported by/...), fixing Signal Radar's raw-distinct-
  source-ID "independence" (PHASE0_AUDIT.md section 3) -- twelve articles
  citing one VideoCardz report now group with the origin instead of
  counting as twelve confirmations (also caught and fixed by testing: the
  first version required the citing text to name an already-known source,
  which only works when the origin is itself a registered/named Source).
  Added `semi_intel/signals/scoring.py`: persisted, configurable
  `AttentionScoringSettings` weights (0.30/0.20/0.15/0.15/0.15/0.05
  defaults), real time-window momentum (not total evidence count), novelty
  as independent-group ratio, artifact-strength ranking, syndication/
  staleness penalties, full per-component JSON explanation. Added
  `semi_intel/signals/candidate_state.py`: seen/unseen/dismiss/restore/
  snooze/wake/stale/reversible-audited-merge, nothing ever deleted. Wired
  clustering/scoring/state-maintenance into `PipelineService.run_once()`
  (network-free, runs every cycle). Added `radar candidates/candidate-seen/
  candidate-dismiss/cluster` CLI commands. A stale candidate reactivates on
  a direct quote/reply (causal link) but not on a merely topically-similar
  item arriving long afterward (stays a separate candidate, per "stale time
  window remains separate"); a dismissed candidate never silently regrows.
  36 new tests, including direct reproductions of the brief's own worked
  examples (NVIDIA/Xeon alone never merge, RTX 50 Series vs RTX 50 Super
  stay separate, a quoted follow-up inherits lineage, twelve VideoCardz
  citations count as ~1 group) -- 282 passing total.
- **Phase 5 — editorial promotion.** Added `semi_intel/signals/
  promotion.py`: the sole path from a `SignalCandidate` into the canonical
  editorial layer. Reuses `EditorialDiscoveryService.process_evidence()`
  for the first Evidence row (so it interoperates with pre-existing
  non-candidate stories), then attaches every remaining candidate item
  directly to that same story rather than letting editorial's own
  independent 0.72-headline-similarity clustering decide per-row -- our own
  richer candidate clustering already established these items belong
  together. Idempotent via `Evidence.origin_signal_item_id` (unique) and an
  early return on `candidate.promoted_story_id`. Preserves original URL/
  external_id/timestamps exactly; creates topic matches with reasons; never
  creates a Claim (only runs the existing suggestion scanner); triggers
  bounded discovery only through its own unchanged eligibility rules. Added
  `CandidatePromotionEvent` (append-only audit trail, migration
  `a6a1b2c73e08`) and `merge_candidate_into_story()` for the operator-chosen
  merge path. Automatic promotion (`run_automatic_promotion`) defaults off,
  requires every threshold in `CandidatePromotionSettings`, and enforces an
  hourly budget; caught and fixed a real transactional bug during testing
  where a discovery/suggestion failure's rollback would have silently
  discarded the just-committed promotion itself (fixed by committing the
  promotion before attempting either). Wired into `PipelineService` and
  added `radar promote`/`radar promote-eligible` CLI commands. 16 new
  tests -- 298 passing total.
- **Phase 6 (checkpoint) — source suggestions, Radar GUI, API+CLI.** Added
  `semi_intel/signals/suggestions.py`: mines explicitly-attribution-credited
  handles from SignalItem text into the same `SourceSuggestion` table
  Semi Intel's domain-citation mining already used (`kind=handle` vs
  `kind=domain`), one unified review/accept workflow. Added 17 new
  `/api/radar/*` JSON endpoints (status, candidates list/detail/seen/
  dismiss/restore/snooze/promote, settings get/update, sources list/add/
  collect, cluster, source-suggestions list/refresh/review) and a new
  "Signal Radar" GUI tab (overview, filterable candidate list, candidate
  detail with score breakdown/timeline/independence groups, source
  management with provider auto-detection, settings form) in the existing
  single-file vanilla-JS dashboard. Added `radar candidates/candidate-seen/
  candidate-dismiss/cluster` CLI commands. Live browser-verified against a
  seeded demo scenario mirroring the brief's own worked example (see
  HANDOFF.md "Manual GUI acceptance performed" for the full walkthrough and
  the one cosmetic bug found and fixed live: a "shared artifact" reason
  string was showing internal normalized text instead of the original).
  Also found and fixed, during this checkpoint's explicit review (not by a
  failing test): a real RSS dual-collection risk where a source registered
  through the new pipeline could have been polled by both the legacy
  direct-to-Evidence path and the new SignalItem path in the same cycle
  (`PipelineService._rss_sources_to_poll()` now excludes non-`manual`
  providers). 22 new tests -- 320 passing total. See HANDOFF.md for the
  full checkpoint record, including what's explicitly deferred (Phases 7-9)
  and the documented-not-fixed circular-FK SAWarning.

## 2.2.0 — 2026-07-26

- Added a provider-neutral, bounded targeted-discovery ring.
- Added an initial Google News RSS search adapter with no article crawling.
- Added deterministic query construction, eligibility, relevance and
  relationship classification.
- Added persistent settings, run/result history, budgets, cache metadata,
  cooldowns, cycle limits and stale-run recovery.
- Integrated accepted coverage with stories and Suggested Sources.
- Added Discovery Activity GUI, story-detail controls and discovery CLI.
- Added migration `b71d4e2c9a30` and isolated provider/pipeline/API tests.

## 2.1.0 — 2026-07-26

- Added an automated, explainable editorial-story inbox.
- Added seeded, GUI-editable monitored topics and aliases.
- Added persistent seen/unseen state and new-coverage indicators.
- Added conservative cross-source story clustering.
- Added citation extraction, domain normalization, source suggestions,
  feed autodiscovery, and one-click source addition.
- Added an idempotent editorial backfill command and pipeline integration.
- Added migration `9f3c2a1b7d10` and focused automated tests.
# 3.2.0 — Phase 8: Alerts & Digest

- Added persisted, transition-based in-app alerts for high-attention candidates,
  material score increases, independent corroboration, promotion readiness and
  completion, tracked-topic activity, source suggestions, and provider health.
- Added an activation watermark so upgrading or importing old material does not
  produce a historical alert flood.
- Added deterministic daily digests with operator timezone support. Digest
  generation is idempotent and never marks candidates as seen.
- Added read/unread, dismiss/restore, retention, event/topic muting, quiet hours,
  delivery caps, bounded retry, and provider incident/recovery records.
- Added CLI, local API, and an **Alerts & Digest** dashboard. External delivery
  remains deliberately unconfigured; Phase 8 ships a tested adapter boundary
  and local in-app adapter, with no network messaging side effects.
- Added Alembic revision `e8b7c2d4a901` and six tables (41 total).
- Windows builds now bundle `tzdata` for reliable IANA timezone handling.
# 3.3.0 — Phase 9: Operational Automation

- Added disabled-by-default, single-cycle local scheduling for the canonical
  pipeline, digests, backups, maintenance, cleanup and health checks.
- Added auditable job history and atomic SQLite leases with overlap refusal,
  expiry, refresh and recorded stale-lease recovery.
- Added Quiet, Balanced and Breaking-news alert presets; useful/not-useful
  feedback with deterministic summaries; and validated saved notification views.
- Added one environment-configured generic HTTPS webhook adapter. Preview never
  contacts the network, tests are synthetic, redirects are refused, secrets and
  endpoint URLs are not stored in the database, and delivery stays disabled
  until explicitly tested and enabled.
- Added SQLite backup-API snapshots, manifests, SHA-256/integrity verification,
  bounded retention pruning, restore dry-run, active-lease refusal and mandatory
  safety backup before replacement.
- Added consolidated health, privacy-bounded diagnostics ZIPs, operator CLI
  commands, service-backed API routes, and an Automation & Health dashboard.
- Added additive Alembic revision `f9a4c6d8e203` (48 application tables).
