# Semiconductor Intelligence Platform 3.3.14 — Unattended Collection Repair

**Status: current, verified, and deployed.** Field inspection
found that `SemiIntel Operational Cycle` fired every 30 minutes but exited with
result 1 before SemInt launched. The generated nested `cmd /c` quoting split a
path containing spaces, so no scheduler heartbeat or scheduler-originated job
could be written. Separately, all Radar-managed sources remained at the safe
import default `polling_enabled=False`, allowing manual GUI runs to mask the
absence of unattended collection.

## Repair

- Scheduled tasks now use native action fields: the absolute `semintel.exe` as
  Execute, `automation cycle` as Arguments, and the application directory as
  WorkingDirectory. Repair is explicit/idempotent and preserves an existing
  task's principal and settings. Status validates all three fields, identifies
  legacy cmd-wrapped tasks as stale, and explains common result codes.
- Radar Sources provides explicit selection, clear-selection, selected polling
  enable/disable, and eligible-RSS bulk enable controls. X polling requires a
  count-bearing warning, global X enablement, and a local session containing
  the required authentication-cookie names. Cookie values are never returned.
- Automation & Health exposes RSS/X polling counts and warnings for zero-polling
  configurations or missing X sessions, with a direct route to Sources.
- Disabled legacy manual RSS sources are no longer eligible for their older
  automatic direct-to-Evidence path.
- Focused scheduler/source/pipeline gate: **50 passed**. Relevant automation,
  web, lifecycle, and JavaScript gate: **122 passed** after five subprocess
  tests were rerun under the checkpoint's Python 3.13 environment because the
  sandboxed system Python 3.14 inherited an invalid Windows stdin handle.
- Final combined SemInt + OEM Radar suite, with operator webhook variables
  removed only from the disposable test process: **857 passed, 1 skipped**, 0
  failed, in 19:02. The skip and existing datetime/Starlette warnings are
  unchanged and out of scope.
- No database migration or production source-polling mutation was performed.
  Alembic remains `a0b5d7e9f314`.

## Production verification

- Both frozen executables were rebuilt with Playwright support after the full
  suite passed. Disposable frozen smoke passed 3.3.14 version, install/update,
  offline doctor, native task preview, and a disabled operational cycle.
- The root executables were replaced after backing up the previous binaries,
  database, and configuration. The dashboard was restarted from the same live
  folder. `semintel.exe` SHA-256 is
  `AC6ABB8FEE80458BA2918E0FFF0EACD0234C775841B9D6A8558CA9A52017DB81`;
  `semi-intel.exe` SHA-256 is
  `945DC79326F43027798E1DD4041A3C7B24F8A2DDFC4A0AD37B75577129E2E7CD`.
- `SemiIntel Operational Cycle` was repaired in place and read back as one
  enabled task with the expected executable, `automation cycle` arguments,
  live working directory, and 30-minute interval. Its production result is 0.
- The first bounded cycle wrote the previously absent heartbeat and completed
  scheduler job #4 successfully. It used the intended populated database. An
  immediate unchanged firing returned 0 without creating a duplicate job or
  notification; notification count remained 63.
- No Radar source was opted into polling automatically. All RSS/X choices
  remain for explicit operator review in the new provider-aware GUI controls.

---

# Semiconductor Intelligence Platform 3.3.13 — Portable Populated Checkpoint Repair

**Status: current and verified.** The 3.3.12
private archive contained the correct populated `semi_intel.db` but the wrong
configuration: a final `semintel install --data-dir <smoke>` invocation had been
run from the project root and rewrote `semintel.config.json` to the disposable
absolute path. Launching the archive therefore showed the empty smoke database
and its four X preflight errors instead of the packaged operator data.

## Repair

- Restored the portable checkpoint config to `data_dir: .` and
  `sqlite:///semi_intel.db`. Direct inspection confirms the included database
  remains populated with 5,211 signal items, 350 candidates, 106 source
  suggestions, and 2,020 provider runs.
- Added a packaging regression requiring that exact portable config.
- `/api/radar/status` now applies `safe_error` to recent provider runs instead
  of returning raw stored errors. Known Playwright spawn, missing-browser, and
  missing-session errors have short path-free messages; generic errors are
  capped at 240 characters.
- Focused source-management, provider, packaging, portable-config, and dashboard
  JavaScript gate: **38 passed**.
- Both Playwright-enabled executables were rebuilt. A release-copy smoke—not a
  source-root install—ran 3.3.13 update/status/doctor and the frozen dashboard
  against copied release artifacts. The CLI showed 80 sources; the dashboard
  returned 80 sources and all 350 candidates. Recent errors were short and
  path-free, and the relative config remained unchanged. Both server processes
  were stopped.
- No schema or data mutation was required. Alembic remains `a0b5d7e9f314`.
- `semintel.exe` SHA-256:
  `38F6D1E1D865F4B4463D37304F3AA08581E6B826D48B9ED3CF18F85F7FA4B73C`
- `semi-intel.exe` SHA-256:
  `8585F951D8F6798B55D543909E0C5AD95F5D66E547BF0EFFFE93C45C98CA46B6`

---

# Semiconductor Intelligence Platform 3.3.12 — Frozen X Runtime Packaging Repair

**Status: current and verified.** Field testing of the 3.3.11
private archive exposed a packaging-only defect: the clean PyInstaller build
environment omitted the optional Playwright package, so every frozen X source
failed before session or browser checks. The source X provider itself was sound.

## Repair

- Both PyInstaller specs conditionally collect the complete Playwright package,
  including its lazy Python modules, driver, and Node runtime.
- Both official build scripts install `.[web,x]`, so an official frozen build
  cannot silently lose X support. Source-only installations retain the optional
  dependency boundary.
- Focused packaging and provider gate: **12 passed**. A Playwright-enabled source
  preflight constructed `XProvider` and reached the expected missing-session
  guard, proving the package-level check no longer blocks X.
- Frozen preflight first proved the packaged provider reached the missing-session
  guard. A deeper smoke then found Playwright's frozen default looking for a
  package-local Chromium. `BrowserSession` now points frozen builds to the normal
  per-user Playwright cache while respecting an explicit
  `PLAYWRIGHT_BROWSERS_PATH` override.
- Final unsandboxed frozen smoke used a disposable empty session and successfully
  launched the operator's installed Chromium 1228, completing an X collection
  cycle with `status: ok` and zero items. It used no real session or token. Both
  one-file server processes were stopped afterward.
- No session, cookie, token, or Chromium browser is embedded. Frozen X uses the
  operator's separately imported session and locally installed Playwright
  Chromium. X remains opt-in and disabled by default.
- No migration or application-logic change; Alembic remains `a0b5d7e9f314`.
- `semintel.exe` SHA-256:
  `7976B2552487D79FC4794849032D8333DA6485A308F75F25B65441C8C2EA0264`
- `semi-intel.exe` SHA-256:
  `7D7CE5BE13218DBBDA401A7954E1A5E60347FCC64BC0C0D9451A2417F319C46F`

---

# Semiconductor Intelligence Platform 3.3.11 — Operator Reliability Repair

**Status: current and verified.** This bounded repair pass addresses
the field defects reported in Radar source collection, digest/external delivery,
and Automation & Health. It preserves all disabled-by-default controls and does
not change scoring, promotion, notification eligibility, scheduled collection,
backup behavior, or schema. Alembic remains `a0b5d7e9f314`.

## Delivered behavior

- Radar sources have deterministic validation and health classification,
  sanitized errors, editable identity/settings, and an explicit disabled-source
  guard. Manual checkbox batches are sequential and operator-visible: RSS runs
  first, X requires confirmation and existing opt-in, cancel stops between
  sources, and authentication/challenge/rate-limit failures halt remaining X
  requests without abandoning RSS results. Clustering/rescoring runs once after
  the batch rather than after every source.
- The redundant Suggested Sources panel was removed from Radar. Suggestions are
  reviewed only in their existing dedicated workspace; the legacy-import option
  remains available.
- Manual digest generation can refresh the current date's digest and optionally
  generate notifications or deliver. Empty digests give a factual diagnostic.
  Delivery status exposes configuration and safe last-result metadata without
  secret values; already-delivered digests are not reset or duplicated.
- Automation & Health loads each panel independently, reports actionable errors,
  refreshes while work is active, and disables duplicate manual runs. Effective
  automation state combines the setting, real Windows Task Scheduler state,
  configured executable path, heartbeat, and recent runs. Task repair is a
  previewed, explicit action. Stale RUNNING rows can be reconciled to ABANDONED
  while active leases and all history are preserved.
- The checkpoint had accidentally omitted the X provider browser-session module.
  It was recovered from the last intact fusion tree. Its Playwright import stays
  lazy and optional, and operator-imported cookies remain outside packages.

## Verification

- Source-management focused gate: **53 passed**.
- Digest/delivery focused gate: **31 passed**.
- Automation/health focused gate: **41 passed**.
- Cross-system signal, notification, operations, web, lifecycle, pipeline, and
  newsroom gate: **185 passed**.
- Dashboard JavaScript gate: **9 passed**.
- The first complete-suite process reached the final few tests without a failure
  but was terminated by an undersized 15-minute command ceiling, so it was not
  treated as a result. The authoritative rerun completed: **548 passed**, 0
  failed, in 14:46. Existing broad `datetime.utcnow()` and Starlette warnings
  remain intentionally out of scope.
- Both Windows executables were rebuilt only after that pass. Frozen smoke on a
  disposable database passed help/version, clean install and migration, doctor,
  status, verified backup and restore rehearsal, dashboard load, source
  create/edit/disable guard, digest generation/current retrieval, operational
  health/scheduler/trends/task endpoints, stale-run reconciliation, and the new
  GUI-control assertions. The smoke server was stopped afterward.
- No real feed, X account, webhook, Windows scheduled task, or operator database
  was touched during verification.
- `semintel.exe` SHA-256:
  `034325CD9266C0FA11A817CABAFAEA2D6F8A56575559DB3BF4C50703524D3BA2`
- `semi-intel.exe` SHA-256:
  `11AE6D0877568E941B930AAFD8AFB4BAC431F58026BD0664AE3B77142BB23E69`

---

# Semiconductor Intelligence Platform 3.3.10 — Signal Radar Aging

**Status: current.** This bounded pass removes stale Radar clutter from the
default operator view without deleting or mutating historical intelligence.
It does not change collection, clustering, scoring, promotion, notifications,
scheduling, backups, or disabled-by-default safety controls. Alembic remains
`a0b5d7e9f314`; no migration was required.

## Delivered behavior

- Candidate aging is a read-only service classification: Current includes
  meaningful activity inside the chosen 3-, 7-, 14-, or 30-day window; Older
  contains candidates outside it; All ages preserves the complete view. Seven
  days and Current are the defaults.
- Meaningful activity is the newest first observation across the candidate's
  existing independence groups. Within each group, the earliest member report
  owns the activity time, so delayed collection, repeated ingestion, citations,
  and syndicated copies cannot refresh an old story. A later new independent
  group can resurface it.
- Timestamp precedence is report `posted_at`, then the normalized Radar
  observation represented by that field, then `collected_at` only when no
  publication time exists. Collection fallback and missing derived independence
  groups are explicitly reported. Datetimes are normalized consistently for
  comparison.
- Candidate list and detail payloads expose classification, meaningful activity
  time, numeric age, timestamp source/fallback, group count, resurfacing status,
  and a plain-language reason. Filtering happens before limit and composes with
  state, seen/unseen, topic, minimum score, and meaningful-activity sorting.
- Signal Radar and the Editorial Inbox fallback shortlist now default to Current
  within seven days and provide Older/All ages plus window controls, badges, and
  useful empty states. Promoted editorial stories are unaffected.

## Conservative limitations

- Accurate resurfacing requires persisted independence-group rows. If a
  recovered candidate lacks them, the service treats its member reports as one
  conservative group and says so; it will not optimistically call a late copy
  new. If no member timestamps exist, stored candidate activity is the final
  disclosed fallback.
- Resurfaced is shown only when a gap longer than the selected window exists
  between distinct independence-group first observations. No new activity
  history table or event-sourcing subsystem was added.

## Verification

- Focused aging/API/GUI and JavaScript gate: **22 passed**.
- Relevant Radar/clustering and newsroom/editorial/lifecycle gates: **130 + 67
  = 197 passed**.
- Complete authoritative suite (`pytest tests`), run once after the gates:
  **519 passed**, 0 failed. Existing `datetime.utcnow()` and Starlette warnings
  remain intentionally out of scope.
- Frozen acceptance initially found a PyInstaller-only missing dynamic AnyIO
  asyncio backend. Both specs now declare it explicitly; the packaging
  regression check passed, both executables were rebuilt, and the corrected
  frozen API passed Current/Older/All ages, late-collection, dependent-copy,
  independent-resurfacing, seen composition, invalid-window, GUI-control, and
  restart-persistence checks on disposable fixtures.

---

# Semiconductor Intelligence Platform 3.3.9 — Canonical Entities and Claim Matches

**Status: current.** This bounded pass reconnects the surviving legacy
knowledge graph and deterministic claim-link suggestion engine to Signal Radar
and Claims & Evidence. It does not change collection, clustering, scoring
weights, match thresholds, automatic promotion, notifications, or safety
defaults. Alembic remains `a0b5d7e9f314`; no migration was required.

## Confirmed causes

- The populated checkpoint had 11,150 Radar mention rows but zero canonical
  entities. The Entities tab was passive, while creation was hidden in Add.
- Claims exposed only a numeric subject-entity ID, so the matcher could not use
  its dominant deterministic entity-match signal in normal GUI operation.
- Claim Suggestions correctly had zero work with no claims/evidence, but showed
  only IDs and weak feedback. It also failed to exclude pairs that already had
  a real evidence link.

## Delivered workflow

- Entities now combines deliberate canonical creation with a bounded, ranked
  unresolved-mention review queue. Exact normalized groups can be resolved to
  a new/existing entity, optionally captured as aliases, ignored, or rejected.
  Resolution synchronizes candidate associations; listing/scanning never
  creates entities.
- Entity detail exposes parsed aliases/attributes, relationships, claim and
  evidence usage, and Radar provenance. Claims use searchable entity selectors,
  and Radar claim dialogs prioritize relevant resolved entities without
  auto-selecting one.
- The visible Claim Matches workspace explains its purpose, reports readiness
  and scan diagnostics, displays full claim/evidence/source context and Radar
  provenance, and retains human stance/rejection decisions. Existing links are
  excluded both during scanning and defensively during acceptance.

## Verification

- Focused canonical-entity, match-workflow, and JavaScript gate: **20 passed**.
- Relevant entity, graph, claim-engine, Radar, newsroom, and web gate:
  **128 passed**.
- Complete suite, run exactly once after focused gates: **507 passed**, 0
  failed. Existing `datetime.utcnow()` warnings remain intentionally out of
  scope.

---

# Semiconductor Intelligence Platform 3.3.8 — Newsroom Usability Pass

**Status: current.** This bounded pass connects the already-populated Signal
Radar to the operator-facing claims/evidence and editorial workflows. It does
not change collection, scoring, automatic-promotion thresholds, notification
policy, or disabled-by-default safety settings. Alembic remains
`a0b5d7e9f314`; no migration was required.

## Confirmed causes

- Candidate detail already existed in the API, but the dashboard rendered it
  below a long candidate list without scrolling or focus, so clicking appeared
  to do nothing.
- Claim/evidence write APIs existed, but their dedicated tabs were passive
  tables; creation controls lived in the unrelated generic Add tab.
- The populated checkpoint correctly contained no editorial stories: legacy
  derived stories were intentionally skipped, all 350 reconstructed candidates
  were unpromoted, automatic promotion was disabled, and historical candidates
  commonly failed age/attention automatic rules.

## Delivered workflow

- Candidate cards are accessible buttons with a visible report-count action.
  Detail shows the complete report timeline, excerpts, source links, topics,
  labels, attach reasons, independence/citation grouping, score explanations,
  promotion warnings, and candidate actions. It opens in view with loading,
  focus, close, and error feedback.
- Claims & Evidence is now one workspace with filters, search, useful empty
  states, manual forms, linked-evidence detail, and provenance back to Radar.
  A SignalItem converts idempotently through the existing unique
  `Evidence.origin_signal_item_id`; an operator can create a human-authored
  claim and link the selected report as supports, weakens/context, or
  contradicts. Links can be edited or removed without deleting either record.
- Editorial Inbox now includes a deterministic top-20 Radar review shortlist,
  including below-threshold items explicitly labelled as suggestions. Manual
  promotion remains an operator decision, uses the canonical idempotent
  promotion service, accepts an edited headline, refreshes the inbox, and never
  enables automatic promotion.

## Verification

- Focused newsroom plus dashboard JavaScript gate: **17 passed**.
- Relevant Radar, editorial, claim/evidence, notification, persistence, and
  newsroom regression gate: **108 passed**.
- Complete suite, run exactly once after focused gates: **496 passed**, 0
  failed. The first relevant-gate attempt exposed only a missing offline
  `tzdata` path in the recovered test interpreter; the affected tests and the
  complete gate passed with the already-recovered timezone bundle restored.
- Existing `datetime.utcnow()` warnings remain intentionally out of scope.

---

# Semiconductor Intelligence Platform 3.3.7 — Optional Local Windows Desktop Notifications

**Status: current.** One bounded increment over the verified 3.3.6 newsroom-
workflow checkpoint. Native Windows desktop notifications now exist behind an
explicit, persisted opt-in. No Phase 10C, media/OCR, collection expansion,
scoring change, or unrelated cleanup was undertaken.

## Delivered behavior

- New isolated `windows_desktop` delivery adapter using the Windows PowerShell
  toast API already present on supported Windows installations. It performs no
  network activity and adds no tray process, opaque binary, GUI framework, or
  Python dependency. Non-Windows imports remain safe and report unavailable.
- Desktop notifications remain disabled by default. Alerts & Digest now shows
  disabled/available/unavailable/error state, an enable checkbox, and a
  synthetic test button with immediate success/failure feedback.
- Eligible important/urgent notifications reuse activation, mute, quiet-hour,
  hourly-cap, idempotency, and bounded-retry rules. Content is bounded to the
  application name, severity, headline, short reason, and topic name when
  present.
- Desktop and webhook attempts use separate channels. Desktop success,
  deferral, rejection, or retry does not overwrite webhook delivery state and
  never changes story seen state or notification read/dismiss state.
- Pipeline, manual notification generation, and scheduled notification/retry
  paths invoke desktop delivery only after notification state is committed.
  Adapter and OS failures are isolated and cannot fail collection or analysis.

## Schema

- Alembic head: `a0b5d7e9f314`.
- The migration adds only
  `notification_settings.windows_desktop_notifications_enabled`, non-null with
  a false server default. Existing databases upgrade with desktop delivery off.

## Verification

- New desktop-notification tests: **11 passed**.
- Relevant notification, delivery, scheduler, operations, pipeline, web,
  migration, CLI, and lifecycle regression set: **156 passed**.
- Dashboard JavaScript checks: **9 passed**.
- Complete suite, run once after focused gates: **488 passed**, 0 failed.
- Frozen 3.3.7 smoke on Windows 11, fresh disposable database and local RSS
  fixtures: initial state disabled; synthetic native toast accepted; two
  eligible transitions produced two desktop attempts; unchanged rerun stayed
  at two; opt-in survived restart; disabling followed by another pipeline run
  stayed at two; story seen state and saved view survived restart; backup
  rehearsal passed; schema reported current.

## Windows limitations

- Automated verification can prove the Windows API accepted the toast and that
  the process returned success, but cannot prove a human saw the banner.
  Windows Focus Assist/Do Not Disturb, per-app notification permission, or
  notification-center policy may suppress presentation.
- Sandboxed processes can receive `E_ACCESSDENIED` from the toast API. The
  bounded native smoke was therefore run with normal Windows notification-
  center permission; the same probe and frozen endpoint succeeded there.
- Existing `datetime.utcnow()` deprecation warnings remain intentionally out
  of scope.

---

# Semiconductor Intelligence Platform 3.3.6 — Stabilization Pass 2: Core Newsroom Workflow

**Status: current.** This was a bounded stabilization pass, not a feature
phase. It began from the recovered 3.3.5 checkpoint, exercised the primary
newsroom workflow with deterministic local fixtures and a disposable database,
repaired the one confirmed defect, and stopped. Alembic remains at
`f9a4c6d8e203`; no migration was added.

## What was verified

- Four representative monitored topics and aliases: RDNA 5, Zen 6, RTX 60
  Series, and RTX 50 Super.
- Editorial and radar RSS ingestion using local fixtures only: strong exact and
  alias matches, irrelevant common-word noise, duplicate reports, later
  independent corroboration, and a citation of an unregistered publisher.
- Automatic relevance, understandable match reasons, editorial clustering,
  radar candidate convergence, corroboration updating existing state, and
  creation of the expected source suggestion.
- Seen/unseen behavior across refresh, new coverage, and a full restart.
- Notification eligibility and exactly-once generation on unchanged reruns;
  reading/dismissing notifications does not alter story state.
- A saved notification view combining multiple severities, event types, topics,
  date range, search text, and sort order, including restart persistence.
- Operational endpoints and dashboard controls through direct API tests and
  dashboard JavaScript syntax tests. No repeated browser automation was used.
- A frozen-executable workflow from a fresh database, followed by application
  restart and a real backup rehearsal.

## Confirmed defect and repair

Relative backup paths were process-working-directory dependent. The dashboard
could create a valid backup under the database's `backups` folder, then
`semintel backups rehearse` could reject that same file if invoked from another
folder because it calculated a different managed backup root. `BackupService`
now resolves persisted relative backup settings against the active SQLite
database directory. Operator backup commands no longer inject their own
working-directory-derived path, so create/list/verify/prune/restore/rehearse and
the dashboard share one rule. An explicit relative path passed directly to the
service still remains caller-relative for test and tooling compatibility.

## Verification record

- Focused backup/newsroom tests: **13 passed**.
- Broader operator, backup, web-operations, lifecycle, and newsroom regressions:
  **78 passed**.
- Dashboard JavaScript syntax checks: **9 passed**.
- Final post-repair complete suite: **477 passed**, 0 failed.
- A pre-repair complete suite also passed 476 tests; the backup path defect was
  found only by the subsequent frozen cross-working-directory smoke, which is
  why the complete suite was correctly rerun after repair.
- Both Windows executables were rebuilt after the final suite and smoke-tested.

## Remaining limitations

- No live X access, internet-wide discovery, LLM, real webhook, or real Windows
  scheduled task was used; all external effects remained disabled by default.
- Existing `datetime.utcnow()` deprecation warnings remain intentionally out of
  scope.
- The platform remains a local single-operator application; this pass does not
  add multi-user or distributed coordination.

---

# Semiconductor Intelligence Platform 3.3.5 — Stabilization Pass 1: Application and Database Lifecycle

**Status: current.** Not a feature phase. A bounded test-and-repair pass
proving the application can be installed, started, stopped, restarted,
upgraded, and recovered without losing data or entering a broken database
state, across every supported entry point (source, both frozen
executables). Began from the verified 3.3.4 checkpoint (starting version
`3.3.4`, Alembic head `f9a4c6d8e203`, 434 tests passing, both executables
rebuilt -- see that section immediately below for the concurrency-fix
details this pass builds on).

## Scenarios tested

All eight required scenarios from the assignment, using disposable
databases and copies throughout -- the operator's real database was never
touched:

1. **Fresh installation** (source and frozen): empty disposable directory,
   directories created safely, database initialized exactly once, Alembic
   reaches head, all tables present, singleton settings created only on
   first real use, disabled-by-default settings stay disabled, dashboard
   loads, core endpoints respond, no startup traceback, missing favicon is
   a controlled 404, stop/restart works without resetting settings or
   duplicating seeded topics.
2. **Persistence across restart**: a representative dataset (editorial
   source, radar source, monitored topic, a deterministic fixture signal
   item analyzed into a candidate, a seen-state change, a notification, a
   feedback record, a saved notification view, an operational job record,
   non-default-but-disabled scheduler settings) all survive a full
   engine/session teardown and a brand-new engine reopening the same file
   -- with no duplication.
3. **Existing database upgrade**: a database built at the exact Phase 8
   Alembic head (`e8b7c2d4a901`, missing the Phase 9 operational tables),
   holding real rows, opened **directly through the dashboard** (not via
   `semintel install`/`db upgrade` first) -- see "What changed" below for
   the real defect this surfaced and fixed.
4. **Clean shutdown**: idle, mid-read, immediately after a write, scheduler
   disabled, and via two independent engines -- using a real
   `semi-intel web serve` subprocess (not just TestClient, which never
   exercises actual socket binding/release), confirming bounded exit, the
   port genuinely released (proven by an immediate successful rebind, not
   just process-exit status), and no open transaction left behind.
5. **Interrupted-operation recovery**: a real subprocess killed outright
   (`proc.kill()`, not an injected exception) mid-operation, after
   acquiring a real lease and writing a real `RUNNING` job row -- see
   "What changed" for the stale-run visibility gap this surfaced and fixed.
6. **Concurrent process protection**: two real `semi-intel web serve`
   subprocesses against the same SQLite file, plus two independent
   SQLAlchemy engines directly. See "Intended behavior" below.
7. **Filesystem and path handling**: database, backups, and dashboard all
   verified from a directory containing spaces and punctuation
   (`Stab Pass 1 - frozen smoke!`), and from a working directory other
   than the project root.
8. **Backup compatibility**: a backup created from the repaired build,
   rehearsed (schema-current, ORM counts correct), copied to a **separate**
   disposable location, and started successfully there -- saved views,
   settings, and notifications all intact; the original working database
   was never restored over.

## What changed

- **`create_app()` now reconciles the schema via the same Alembic-aware
  path `semintel install`/`update` already used**
  (`semi_intel.cli.upgrade_or_stamp_to_head()`, moved there from
  `operator.py` so the web app can share it), instead of a bare
  `Base.metadata.create_all()`. **Confirmed real defect**: the dashboard
  (`semi-intel web serve`, `semintel gui`) is a supported entry point
  someone can launch directly against an existing database without ever
  running `semintel install` first. A bare `create_all()` only adds
  missing tables -- opening a database stamped at the Phase 8 head
  directly through the dashboard left `alembic_version` at
  `e8b7c2d4a901` forever (even though the missing Phase 9 tables got
  created, since that migration happens to be purely additive), and would
  have silently masked a future non-additive migration entirely. Fixed
  and verified: the same older database opened through `create_app()` now
  correctly advances `alembic_version` to `f9a4c6d8e203`, creates every
  missing table, and preserves every existing row and singleton-row
  absence exactly as the equivalent real-Alembic-migration test already
  proved for the CLI path (`tests/test_migrations.py`).
- **`stale_run_threshold_minutes` is now actually wired up.** **Confirmed
  real defect**: this operator-configurable setting (exposed in
  settings/schemas since Phase 9, default 180 minutes) existed purely as
  dead configuration -- `grep` found zero consumers anywhere in the
  codebase. A job whose process is killed outright (not a clean exception
  -- `run_job()`'s own `try/except/finally` never gets to run) leaves its
  `OperationalJobRun` row stuck at `RUNNING` with no `finished_at`
  forever, permanently invisible to the operator. `HealthService.report()`
  now flags this as a `degraded` issue once a run has been `RUNNING`
  longer than the configured threshold, mirroring the existing
  stale-lease check immediately above it in the same method. The stuck
  row's own status is deliberately never rewritten -- it stays honestly
  `RUNNING`, never masquerading as complete; the underlying lease (if
  still present) continues to self-heal via `LeaseManager.acquire()`'s
  existing stale-lease takeover on the next real attempt, exactly as
  before this pass. Verified end-to-end with a real killed subprocess
  (`tests/test_lifecycle_operations.py::test_hard_killed_worker_process_recovers_on_next_attempt`).
- **Investigated and explicitly did not change**: `semintel install
  --data-dir`'s `semintel.config.json` placement (it writes to the
  invoking folder, not the `--data-dir` folder) looked like a bug on
  first read and was reverted after a full test run caught it --
  `tests/test_operator_cli.py::test_install_with_data_dir_flag` already
  covers and defends this exact behavior as intentional (its own comment
  explains why). Caught before shipping by running the existing test
  suite before concluding it was a defect, not after.

## Intended behavior: concurrent processes (Scenario 6)

This is a local, single-operator desktop tool (per its own README framing)
-- it is **not** designed to support multiple simultaneously-active
dashboard processes as a normal operating mode, and this pass did not
build a distributed locking system for it (out of scope: "do not build a
distributed locking system"). What was verified instead, so the failure
mode is at least safe rather than silently corrupting data:

- Concurrent startup does not duplicate singleton settings rows (each
  get-or-create helper's `IntegrityError`-recovery, fixed in 3.3.4,
  already covers this).
- Two real dashboard processes against the same file: both serve reads
  successfully and consistently; stopping one does not damage the other;
  the database remains fully usable (passes `PRAGMA integrity_check`,
  singleton rows still exactly one row each) after both exit.
- Writes serialize through SQLite's own locking, backed by the 30-second
  busy-timeout already added in 3.3.4 -- confirmed with 20 alternating
  writes across two independent engines landing correctly with zero loss
  or duplication and a clean integrity check afterward.
- No additional startup warning or refusal mechanism was added; the
  existing per-process port binding (a second `web serve` on the same
  host:port simply fails to bind, which uvicorn already reports clearly)
  is judged sufficient given the smallest-suitable-mechanism instruction,
  and different ports were used deliberately in testing to exercise the
  same-database (not same-port) case specifically.

## Testing

- Focused new lifecycle tests: **41 passed** across five new files --
  `tests/test_lifecycle_bootstrap.py` (10: fresh init, repeated-init
  idempotence, singleton-not-pre-seeded, Alembic reconciliation on fresh
  and older databases via `create_app()`, running `create_app()` twice is
  harmless, session rollback/closure, frozen-resource path resolution for
  both `_project_root()` and `STATIC_DIR`), `tests/test_lifecycle_persistence.py`
  (3: full representative dataset across a real restart, no duplication
  across repeated restarts, same logical state via the dashboard API
  before/after), `tests/test_lifecycle_operations.py` (9: stale-run
  health flagging in and out of threshold, no status-masquerading, a real
  killed subprocess recovering on the next attempt, two engines not
  corrupting the database, concurrent singleton startup, paths with
  spaces and punctuation, cwd-independent dashboard startup, backup/
  restore round trip into a separate location), `tests/test_lifecycle_shutdown.py`
  (3: real-process clean shutdown + port release + immediate restart,
  shutdown immediately after a write leaves no open transaction, two real
  processes against one database fail safely), `tests/test_lifecycle_core_endpoints.py`
  (16: every core endpoint the assignment lists, checked for status AND
  minimal response validity -- no error object behind a 200).
- Existing database/migration/scheduler/backup/web/operational suites run
  alongside the new tests: **168 passed**, 0 failed.
- Complete suite, run once after focused acceptance: **475 passed**
  (434 baseline + 41 new), 27,535 warnings, 735.69s. No failures.
- No schema change, no new Alembic revision.
- Both Windows executables rebuilt:
  - `dist/semi-intel.exe` -- 25,918,046 bytes; SHA-256
    `BD5BEE15EB99017ECB4AA2ED57663DD75C6F32D8AA4286B4D86104954C1323FC`
  - `dist/semintel.exe` -- 25,946,366 bytes; SHA-256
    `2EE9008A80906ADF20F88F124468D99C232A2DB36AE23387A0C70547403415F9`
- Frozen lifecycle smoke test, both executables, from a disposable
  directory containing spaces and punctuation
  (`Stab Pass 1 - frozen smoke!`): `semintel update` reported version
  `3.3.5`; fresh `semi-intel.exe web serve` returned HTTP 200 on `/`; all
  14 core endpoints returned 200; created a notification and a saved
  view; created and rehearsed a backup via `semintel.exe backups create`/
  `rehearse` (`passed: true`, `schema_up_to_date: true`); stopped the
  process cleanly and confirmed the port was genuinely free by
  successfully rebinding it immediately; the notification and saved view
  both survived the restart; copied the verified backup into a
  completely separate disposable directory and started `semi-intel.exe`
  against that copy -- HTTP 200, same notification and saved view present,
  and the original working database was confirmed still in place and
  untouched afterward; both server logs were free of tracebacks,
  `database is locked`, or `IntegrityError`; `scheduler_settings` and
  `notification_settings` each had exactly one row throughout.

## Known remaining limitations

- The `datetime.utcnow()` deprecation cleanup remains out of scope for
  this pass, as instructed.
- `create_all()`'s masking risk for a genuinely non-additive future
  migration is now closed for the dashboard entry point specifically;
  `semi_intel/cli.py`'s per-command `_session()` (used by small CLI
  commands like `entity add`) still falls back to a bare `create_all()`
  on a from-scratch database, matching its own long-documented,
  byte-identical-schema-tested behavior (`init-db`'s docstring already
  warns not to mix it with `db upgrade` against real data) -- left
  unchanged as it was not a demonstrated defect and touching it would be
  a broader change than this pass's scope invites.
- Scenario 6 (concurrent processes) is confirmed safe, not confirmed
  fast or contention-free under heavy simultaneous write load; this is a
  single-operator local tool by design, not a multi-writer service.

## Next recommended increment

Continue down the existing "Recommended Phase 10" list (optional local
Windows notifications, or the `datetime.utcnow()` deprecation cleanup as
its own dedicated pass). Do not begin Phase 10C, media/OCR, or broaden
collection.

---

# Semiconductor Intelligence Platform 3.3.4 — Dashboard Concurrency Fixes

**Status: superseded by 3.3.5 above.** Not a Phase 10 increment. This session delivered the
3.3.3 Phase 10B (saved-view composition) checkpoint below, then the
operator ran the rebuilt `semi-intel.exe` against their real database and
hit two live crashes from the dashboard's own concurrent page-load
requests -- both pre-existing bugs, unrelated to Phase 10B, in code this
session had not touched (`semi_intel/web/app.py`'s session dependency,
`semi_intel/operations/scheduler.py`, and five other singleton-settings
get-or-create helpers). With explicit operator sign-off to fix them as
their own bounded follow-up, this section documents that fix. The 3.3.3
section right below remains an accurate, complete record of Phase 10B
itself -- nothing in it changed.

## What changed

- **`database is locked` under concurrent requests.** `get_session()`
  (`semi_intel/web/app.py`) built a brand-new SQLAlchemy engine -- and
  re-ran full schema reflection via `Base.metadata.create_all()` -- on
  every single HTTP request. The dashboard's own tab-load JS already fires
  a dozen-plus concurrent requests per tab via `Promise.all()`; each one
  hammered the same SQLite file with a fresh connection and a reflection
  burst, so a reader colliding with a writer failed immediately instead of
  waiting. Fixed: `create_app()` builds one engine at startup (it already
  did, for initial topic seeding) and now overrides the `get_session`
  dependency via `app.dependency_overrides` so every request reuses that
  same engine/pool -- `create_all()` now runs once per server lifetime.
  Also raised the SQLite connect `timeout` to 30s (`semi_intel/db.py`) as
  headroom for genuine residual contention. Journal mode deliberately
  stays the rollback-journal default, not WAL -- `BackupService` copies
  the `.db` file directly, and WAL risks a backup missing commits still
  sitting in a separate `-wal` file.
- **`UNIQUE constraint failed: scheduler_settings.id`** (and the same bug
  in six other singleton-settings helpers: notification, collection,
  promotion, scoring, discovery settings, and webhook adapter status).
  Every one of them was `session.get(Model, 1)` -> `None` -> insert `id=1`
  with no handling for a concurrent request's session winning that same
  race on a brand-new database. Fixed with the same
  `try: flush() / except IntegrityError: rollback(); re-fetch` pattern
  already established by `OperationalScheduler.acquire()` elsewhere in
  this codebase.

## Verification (this fix)

- 10 new regression tests (`tests/test_settings_singleton_concurrency.py`,
  `tests/test_web_session_reuse.py`): 7 deterministically reproduce each
  singleton race (confirmed, before the fix, to raise the exact
  `UNIQUE constraint failed: scheduler_settings.id` seen in live use);
  1 proves `get_session` is overridden to reuse one engine across
  requests (confirmed to fail without the fix); 2 fire concurrent request
  bursts (12-way and a mixed 24-request dashboard-style burst) against the
  routes that crashed in live use.
- Complete suite, run once after focused acceptance: **434 passed**
  (424 + 10 new), 24,059 warnings, 657.84s.
- No schema change, no new migration.
- Both Windows executables rebuilt:
  - `dist/semi-intel.exe` -- 25,916,570 bytes; SHA-256
    `3F7EF0F3A94C6C24D1E032F02924F0377555BE04971765E46824D49397D7611D`
  - `dist/semintel.exe` -- 25,944,223 bytes; SHA-256
    `2204FCBFBE7A68B111D8B348E4485DFCC131BA5F36F406F444FCE75C4907920D`
- Frozen smoke against a disposable database, loopback server only:
  `semintel update` reported version `3.3.4`; dashboard returned HTTP 200;
  fired a 36-request concurrent burst (4x nine of the exact routes that
  crashed in live use: `/api/operations/scheduler`, `/api/operations/
  backups`, `/api/operations/health`, `/api/operations/jobs`,
  `/api/notifications/status`, `/api/notifications/saved-views`,
  `/api/topics`, `/api/radar/status`, `/api/operations/trends`) at a
  freshly-migrated database via `Start-ThreadJob` -- **all 36 returned
  HTTP 200**; the server log showed no `database is locked`, no
  `IntegrityError`, no 500s, and no reference to any non-loopback host.

## Boundaries preserved (this fix)

- No change to saved-view behavior, filter semantics, scheduling policy,
  collection, scoring, promotion, or delivery. Scheduler disabled by
  default, collection disabled by default, X collection disabled by
  default, automatic promotion disabled by default, external delivery
  disabled by default -- all unchanged.
- No new database table, no Alembic revision, no WAL/journal-mode change.

---

# Semiconductor Intelligence Platform 3.3.3 — Saved Notification View Composition

**Status: superseded by 3.3.4 above (Phase 10B content unchanged).** This
is a single bounded Phase 10B increment over the verified 3.3.2
operational-trends checkpoint. The older sections below (including the
former 3.3.2 top section, now demoted) remain historical context.

## What changed

Saved notification views were already persisted with a complete filter
model (state, event types, severities, topic ids, relation filters, date
window, search text, sort order) since 3.3.0, but the GUI only ever saved a
rough snapshot and applied just the first event type/severity/topic. This
increment makes composition and application actually complete:

- Added `semi_intel/notifications/query.py`: a bounded, stateless
  `NotificationQueryService` / `NotificationQueryFilters` used by **both**
  `GET /api/notifications` and saved-view application, so filter rules live
  in exactly one place. Read-only -- every code path is a `select()`, so it
  cannot mutate read/dismissed/feedback/mute state.
- Filter semantics: values within one category combine with OR (e.g.
  `important` OR `urgent`); different categories combine with AND (state
  AND severity AND event type AND topic AND date window AND search).
- Controlled sort orders: `newest`, `oldest`, `severity` (explicit rank
  urgent(0) > important(1) > notable(2) > informational(3), NOT
  alphabetical/enum order), tie-broken by `event_at` then `id` descending
  for deterministic, repeatable results.
- Controlled date windows: 1/3/7/14/30/90 days. The saved view stores only
  the day-count rule; the cutoff (`now - N days`, timezone-aware UTC) is
  computed fresh on every application from the notification's editorially
  meaningful `event_at`, so the same view naturally covers a different
  absolute range tomorrow.
- `GET /api/notifications` now accepts repeated `event_type=`, `severity=`,
  `topic_id=` query parameters (FastAPI's standard multi-value convention)
  for OR filtering. Every existing single-value call
  (`?state=unread`, `?severity=important`, `?event_type=high_attention`)
  keeps working unchanged -- a single repeated param naturally becomes a
  one-element list.
- Extended `SavedViewService` (`semi_intel/operations/quality.py`):
  `get()`, `duplicate()` (proposes `"<name> copy"`, then `"<name> copy 2"`,
  ... on collision), `describe()` (short human-readable summary, e.g.
  `"Unread · important or urgent · high attention · AMD topic · Last 7
  days"`). `save()` now validates topic ids against real `MonitoredTopic`
  rows, validates state/sort/date-window against controlled vocabularies,
  and -- critically -- only touches `relation_filters` when the caller
  explicitly passes it (a new `_UNSET` sentinel distinguishes "omitted" from
  "explicitly `{}`"), so editing unrelated fields never silently discards
  stored relation data. A missing view id now raises a dedicated
  `SavedViewNotFoundError` (was previously silently creating a new row on
  PUT with a stale id -- a real bug fixed this session).
- New/changed saved-view endpoints, all service-backed: `GET
  /api/notifications/saved-views/{id}` (get one), `POST
  .../{id}/duplicate`, `GET .../{id}/apply` (read-only, returns the view's
  complete composed notification list plus the view itself -- never
  mutates). `PUT` now uses `model_dump(exclude_unset=True)` so a client that
  omits `relation_filters` entirely preserves the stored value. 404 for a
  missing view id, 422 for invalid controlled filters or a duplicate name.
- Rebuilt the Alerts & Digest "Saved views" panel on the existing vanilla
  HTML/CSS/JS dashboard (no new frontend dependency, no build system): a
  native `<dialog>` editor with a name field, state select, event-type
  checkboxes, severity checkboxes, topic checkboxes (populated from
  `/api/topics`), date-window select, search text, and sort select: New
  view / Edit / Duplicate / Delete (with a `confirm()` gate) / Apply /
  Clear view. An active-view banner shows `Viewing: <name>` plus its
  description, and switches to `Filters changed — save as a new view or
  update this view` the moment a manual toolbar filter is touched while a
  view is active -- manual edits never silently overwrite the saved view.
  No raw JSON is ever shown to the operator.
- No schema change. `saved_notification_views` (added in the 3.3.0 Codex
  build) already had every column this increment needed.

## Filter semantics reference

```
Unread AND (important OR urgent) AND (high_attention OR independent_corroboration)
     AND (AMD topic OR NVIDIA topic) AND within last 7 days AND title/body/reason contains "leak"
```

## Backward compatibility

- `GET /api/notifications?state=unread`, `?severity=important`,
  `?event_type=high_attention` (single-value) all still work exactly as
  before -- verified in
  `tests/test_notifications_list_supports_repeated_query_params_and_stays_backward_compatible`.
- Existing 3.3.0-3.3.2 `saved_notification_views` rows remain readable and
  editable unchanged -- verified in
  `tests/test_existing_3_3_x_saved_view_rows_remain_readable`.

## Verification

- Package version: `3.3.3`
- Alembic head: `f9a4c6d8e203` -- unchanged; no migration or schema change
- Focused Phase 10B tests (`test_operations_quality.py`,
  `test_notifications_query.py`, `test_web_operations.py`,
  `test_web_notifications.py`, `test_web_dashboard_static.py`): **47
  passed** in 71.04s
- Dashboard JavaScript independent parse: passed (both a standalone `node
  --check` extraction and the pytest-embedded
  `test_dashboard_javascript_parses_with_node`)
- Complete suite, run once after focused acceptance: **424 passed, 23,616
  warnings in 699.52s** (388 baseline + 36 new focused tests). Warnings
  remain the established legacy `datetime.utcnow()`/TestClient deprecation
  noise; this increment did not broaden that cleanup.
- Both Windows executables rebuilt:
  - `dist/semi-intel.exe` -- 25,913,003 bytes; SHA-256
    `50505A8413FB09921AC43E68CD5E9F97E0EE7F6F5FA29FC6ECE3BC56F688B4FA`
  - `dist/semintel.exe` -- 25,943,947 bytes; SHA-256
    `B7B1B78891B2633C227811BB931AD0B3B98DE3F8D8D8F5DD0417CEE29B0D5751`
- Frozen smoke against a disposable database, loopback server only:
  `semintel update` reported version `3.3.3`; `semi-intel db current`
  reported Alembic head `f9a4c6d8e203`; dashboard returned HTTP 200 with
  the packaged `<dialog id="saved-view-dialog">` editor and its controls
  present in the served HTML; created a saved view through the frozen API
  with two event types, two severities, and one topic and got back
  normalized arrays plus a human-readable `description`; applying it
  correctly excluded a notification that failed the AND-composed topic
  filter, then a second view without that topic constraint confirmed the
  OR-composed severity match; updated, duplicated, and deleted the
  duplicate (confirmed 404 afterward); the seeded notification's
  `read_at`/`dismissed_at` were identical before and after all of the
  above; an invalid severity and an invalid `date_window_days` both
  returned 422, a missing view id returned 404; the server log showed no
  reference to any non-loopback host.
- Manual browser check (one bounded attempt, performed after all automated
  tests passed, per instruction not to fight unstable browser automation):
  opened the Alerts & Digest tab, opened "New view", entered a name,
  checked an event-type and a severity checkbox, set the date window to
  "Last 7 days", saved -- the panel showed "View saved.", the new view
  listed with its description, and "Apply" produced `Viewing: <name>` in
  the active-view banner with a correctly empty result list (the seeded
  test notification's `informational` severity didn't match the view's
  `important`-only filter). Duplicate proposed `"<name> copy"`. Clicking
  Delete on the duplicate triggered the browser's native `confirm()`,
  which the automation tooling could not accept -- confirmed via the
  network log that **no DELETE request was sent**, which is itself a
  correct demonstration of the required confirmation gate. "Clear view"
  correctly returned to "No saved view is active." Zero browser console
  errors throughout. (Reaching controls below the initial viewport inside
  the `<dialog>` required an extra `scroll_to` step because this session's
  seeded database has 70+ monitored topics and the accessibility-tree
  reader only captures what's currently in view -- a tooling quirk, not an
  application defect.)

## Boundaries preserved

- No new database table or Alembic revision; no scheduler, collection, X
  collection, promotion, scoring, backup/restore, or
  notification-generation behavior changed.
- Scheduler disabled by default, collection disabled by default, X
  collection disabled by default, automatic promotion disabled by default,
  external delivery disabled by default -- all unchanged.
- No LLM, charting dependency, frontend framework, build system, or new
  CSS dependency was added.

## Known limitations / deferred work

- Relation filters (`relation_filters` on `SavedNotificationView`) remain
  service/API-only, preserved on edit but with no GUI representation --
  the brief explicitly allows this ("if the application does not yet have
  a clear non-technical representation for them").
- The `datetime.utcnow()` deprecation cleanup (flagged as its own
  dedicated pass since 3.3.1) is still not started.
- Trend charts/visualization, optional local Windows notifications, and
  every other Phase 10 item not named "saved-view composition" remain
  deferred, per this increment's explicit scope boundary.

## Next increment

Continue down the existing "Recommended Phase 10" list: optional local
Windows notifications, or -- as its own dedicated, carefully-verified pass,
not bundled with anything else -- the `datetime.utcnow()` deprecation
cleanup. Do not begin media/OCR, broaden collection, or touch scoring.

---

# Semiconductor Intelligence Platform 3.3.2 — Operational Trends

**Status: superseded by 3.3.3 above.** This was a single bounded Phase 10A
increment over the verified 3.3.1 backup-rehearsal checkpoint. The older
sections below remain historical context.

## What changed

- Added `OperationalTrendService` in `semi_intel/operations/trends.py`.
- It reads existing `OperationalJobRun`, `NotificationFeedback`, and
  `Notification` rows for an explicitly supported 7-, 30-, or 90-day window.
- It returns job success/partial/failure/skipped counts, reliability rate,
  counts and average duration by job type, useful/not-useful totals and rate,
  useful rate by notification event type, and the five most common not-useful
  reasons.
- It also provides one deterministic plain-language headline. It never mutates
  jobs, feedback, notifications, settings, or thresholds.
- Added read-only `GET /api/operations/trends?days=7|30|90`. Unsupported
  windows return HTTP 422.
- Added a compact Recent trends panel to Automation & Health with a window
  selector, CSS-native bars, job reliability, alert usefulness, event-type
  rates, common reasons, and an explicit empty-data state.

## Verification

- Package version: `3.3.2`
- Alembic head: `f9a4c6d8e203` — unchanged; no migration or schema change
- Focused service/API/GUI tests: **9 passed**
- Dashboard JavaScript independent parse: passed
- Complete suite, run once after focused acceptance: **388 passed,
  23,024 warnings in 634.58 seconds**
- Warnings remain the established legacy `datetime.utcnow()` and TestClient
  deprecations; this increment did not broaden that cleanup.
- Both Windows executables rebuilt:
  - `dist/semi-intel.exe` — 25,902,372 bytes; SHA-256
    `AF1A007F9587D2DD83CE748792DE69297716CA0FB2FC3787CBD25721FC8D3402`
  - `dist/semintel.exe` — 25,931,117 bytes; SHA-256
    `89F1F08A49EF346A8B7DF2BE21AE734CD9C232421638AFD8AFED15C4335CBC36`
- Frozen smoke against a disposable database passed: version `3.3.2`,
  dashboard HTTP 200, packaged trend selector/panel present, the 7-day
  endpoint returned its valid empty state, and an unsupported 14-day window
  returned HTTP 422. Verification used a temporary loopback server without
  browser automation or external network activity.

## Boundaries preserved

- No scheduling, delivery, backup, restore, collection, promotion, scoring, or
  notification-generation behavior changed.
- No automatic tuning, LLM, network request, charting dependency, frontend
  framework, or new persistence was added.
- All existing disabled-by-default safety settings remain unchanged.

## Next increment

Keep the next pass similarly bounded. A suitable candidate is saved-view
composition improvement as its own feature. Do not combine it with Windows
notifications, deprecation cleanup, media/OCR, or collection expansion.

---

# Semiconductor Intelligence Platform 3.3.1 — Backup Restore Rehearsal

**Status: current.** Everything below this section is a historical append
log (each phase's own handoff, oldest and newest mixed — the file was never
restructured as it grew). Read this section first; it is the only part that
describes the actual current state of the repository.

## What this session did

The previous session ended at a `3.0.0` checkpoint (Phases 0–6 only, Phases
7–9 explicitly deferred, per this repository's own now-superseded top
section below). Between then and now, a separate agent (ChatGPT Codex,
working in a different local project folder) completed Phases 7, 8 and 9
end to end — the legacy Signal Radar importer, the notification/digest
subsystem, and operational automation (scheduler, backups, health,
diagnostics, a generic HTTPS webhook adapter) — reaching package version
`3.3.0` with 377 tests passing. That work is recorded in the "3.1.0" (Phase
7), "3.2 (Phase 8 complete)" and "3.3" sections further down this file, in
that append order.

This session:

1. Received the actual Codex-built `3.3.0` source tree (not just its prose
   handoff) as a ~51 MB zip (`Semi Intel 3.3 Operational Automation
   Checkpoint.zip`).
2. Backed up the prior `3.0.0` working tree
   (`checkpoint_archives/pre-3.3-swap-source-backup.tar.gz`) and swapped the
   Codex `3.3.0` source in as the new working tree (`semi_intel/`, `tests/`,
   `migrations/`, `packaging/`, docs, `pyproject.toml`, `alembic.ini`, both
   `dist/*.exe`).
3. Verified the swap is genuine, not just a matching prose claim: confirmed
   `pyproject.toml`/`semi_intel.__version__` read `3.3.0`, confirmed all 9
   migration files are present through `f9a4c6d8e203`, reinstalled the
   package (`pip install -e ".[dev]"`, picking up the new `tzdata`
   dependency), and ran the complete, unmodified test suite once before
   touching any code: **377 passed**, 0 failed — exactly matching the
   Codex handoff's own claim.
4. Implemented exactly one bounded increment, per instruction to not
   attempt too much in this session: **backup restore rehearsal**, the
   first item on this file's own "Recommended Phase 10" list (see below).
   `BackupService.rehearse()` (`semi_intel/operations/backup.py`) copies a
   verified backup to a throwaway temp file, opens it with a real
   SQLAlchemy engine, and runs counts through the actual domain models
   (`Source`, `SignalCandidate`, `Notification`, `ProviderRun`) — proving
   the backup would actually load if restored, which the existing
   `verify()`/`restore(dry_run=True)` never did (they only ran raw
   `sqlite3` `PRAGMA integrity_check` plus a table-name check against the
   file itself). It also compares the backup's stamped Alembic revision
   against the currently installed code's expected head and warns if the
   backup predates it (meaning `semintel update` would be needed right
   after a restore). It never touches the live database, session, or
   leases, and never raises for a bad backup — it reports `passed: false`
   with a redacted error instead, matching this codebase's established
   fault-isolation style.
5. Added `semintel backups rehearse <path> [--json]` (CLI only, matching
   the existing "restore is CLI-only" precedent — this is read-only against
   a temp copy, so it would be reasonable to expose via API/GUI too, but
   that's left as a follow-up rather than expanding this session's scope).
6. Added 5 new tests: 3 service-level (`tests/test_operations_backup_health.py`
   — rehearsal passes and confirms schema currency + ORM counts; flags a
   backup stamped behind the installed head; reports failure without
   raising for a corrupt file) and 2 CLI-level
   (`tests/test_operator_cli.py` — passing rehearsal via `--json`; graceful
   failure on a corrupted backup file).
7. Bumped the version to `3.3.1` (`pyproject.toml`, `semi_intel/__init__.py`)
   and added a `3.3.1` entry to `CHANGELOG.md`, plus a short mention in
   `OPERATOR_GUIDE.md`. Backfilled the `3.2.0` and `3.3.0` entries into
   `CHANGELOG.md`, which had been left at `3.1.0` despite the code, tests
   and this file's own history already being at `3.3.0` — a real
   documentation gap in the delivered Codex build, not something this
   session's own change caused.
8. A bounded release follow-up rebuilt both Windows executables from the
   exact 3.3.1 source tree, then exercised the frozen operator workflow
   against a disposable database. `semintel.exe` reported version `3.3.1`,
   created a verified backup at head `f9a4c6d8e203`, and successfully ran
   `backups rehearse` through the packaged service/ORM path with
   `passed: true`, `schema_up_to_date: true`, and all four representative
   ORM counts returned. `semi-intel.exe` also loaded the packaged timezone
   data and listed all notification presets. No source feature or schema
   change was made during this release follow-up.

## Exact versions and identifiers (this session)

- Package version: `3.3.1` (`pyproject.toml` and `semi_intel.__version__`)
- Alembic migration head: **`f9a4c6d8e203`** ("Phase 9 operational
  automation") — **unchanged this session**; no new migration, no schema
  change. 48 application tables.
- Test suite: baseline (unmodified Codex `3.3.0` source, run before any
  code was touched) — **377 passed**, 0 failed, `648.44s`. After this
  session's change (definitive full run, one pass) — **382 passed**,
  0 failed, `23024 warnings in 605.45s` (`python -m pytest -q`). Warnings
  are the same pre-existing `datetime.utcnow()`/TestClient deprecation
  noise already present in the Codex build — not new.
- Python compile check (`py_compile`) passed for every file touched this
  session: `semi_intel/operations/backup.py`, `semi_intel/operator.py`,
  `tests/test_operations_backup_health.py`, `tests/test_operator_cli.py`.
  No GUI/JavaScript file was touched this session, so no JS parse check
  was needed.
- Windows executables are now current for 3.3.1:
  - `dist/semi-intel.exe` — 25,897,508 bytes; SHA-256
    `D0F00DA3ABC9642C2E107872FC81E53C068BCD73F325A703F7D19AB489ED86DE`
  - `dist/semintel.exe` — 25,925,111 bytes; SHA-256
    `79913436056F599FFA628751991C7E2CF10E83E0EAFD41F6B99BCD58FB153768`
- Release-follow-up verification reran the five focused rehearsal tests:
  **5 passed**. The definitive complete source suite remains the immediately
  preceding **382 passed** run; it was not repeated because this follow-up
  changed only frozen artifacts and documentation.

## What was NOT done this session (deliberately, per scope instruction)

Per instruction, this was a single bounded increment, not a return to full
Phase 10 scope. Explicitly not started: trend charts/analytics, saved-view
composition improvements, optional local Windows notifications, or the
`datetime.utcnow()` deprecation cleanup (also on the Phase 10 list — see
"Next recommended increment" for why that one specifically was judged too
large/risky for a bounded session: 15 files mix naive and timezone-aware
timestamps by design already, per Phase 9's own note that "new Phase 9
operational timestamps use timezone-aware UTC" while Phases 1–6 remain
naive — converting this safely needs a dedicated pass with full-suite
verification at every step, not a fit for "don't attempt too much").

## Next recommended increment

Continue down this file's own existing "Recommended Phase 10" list (just
below, at the end of the `3.3.0` section): trend charts from feedback/job
tables, better saved-view composition, optional local Windows
notifications, and — as a dedicated, carefully-verified pass on its own,
not bundled with anything else — the `datetime.utcnow()` deprecation
cleanup. Do not begin media/OCR or broaden collection.

---

# Semiconductor Intelligence Platform 3.1.0 — Legacy Import Checkpoint

**Status: Phase 7 complete and verified; later operations remain deferred.**
This is a deliberate, recoverable stopping point. Phases 0–6 absorbed Signal
Radar's collection/candidate pipeline; Phase 7 now provides the production
legacy-database import path. Unified notifications, media/OCR operations and
the remaining full acceptance walkthrough are deferred. See "Next
recommended increment" before doing anything else.

## Exact versions and identifiers

- Package version: `3.1.0` (`pyproject.toml` and `semi_intel.__version__`)
- Alembic migration head: **`d3c8e41f9a62`** ("remove redundant
  SignalItem-to-Evidence provenance link")
- Full migration chain (base → head):
  `71747eaa2044` (initial schema) → `9f3c2a1b7d10` (editorial discovery
  inbox) → `b71d4e2c9a30` (bounded discovery ring) → `f4f0279f3459` (signal
  radar absorption: signal layer) → `4ee15190b40b` (signal topic matches) →
  `a6a1b2c73e08` (candidate promotion audit) → `d3c8e41f9a62`
  (remove redundant reverse provenance FK, **current head**)
- Test suite: **334 passed**, 0 failed (`python -m pytest -q` →
  `334 passed, 21827 warnings in 351.26s`; warnings are the pre-existing
  `datetime.utcnow()` deprecation noise already present before this merge,
  not new failures)
- Baselines this merge started from (see `PHASE0_AUDIT.md` for the full
  audit): Semi Intel 2.2 — 202 passed; Signal Radar — 90 passed (not the
  stale 73/78/80/84 figures in Signal Radar's own handoff)
- Growth across the merge: 202 (Semi Intel baseline) → 203 (Phase 1) → 230
  (Phase 2) → 246 (Phase 3) → 282 (Phase 4) → 298 (Phase 5) → **320**
  (Phase 6 checkpoint) → **326** (stabilization) → **334** (Phase 7)

## Phase 7 — legacy Signal Radar importer

`semi_intel/legacy_import.py` is the single importer service used by both
interfaces:

- CLI: `semi-intel radar import --database <path>` previews by default;
  add `--apply` to commit, `--json` for a machine-readable report, and
  `--categories` to select record types.
- GUI/API: the Signal Radar tab has a file picker, category controls,
  mandatory preview, reviewed apply and a 128 MB upload bound. Endpoints are
  `POST /api/radar/import/preview` and `/api/radar/import/apply`, accepting
  raw SQLite bytes without a multipart dependency.
- Imported: sources, raw posts, media metadata, provider-run history and
  source candidates/suggestions.
- Deliberately not imported: Radar stories, story scores, legacy evidence,
  entity graph, extracted labels/entity assignments, review queue,
  notifications, score weights and reliability history. They are listed in
  every report so the omission is explicit. Raw posts are reassessed by the
  current analyzer/candidate pipeline.
- Safety: preview writes nothing; apply is transactional and rolls back on
  failure; repeat runs reuse every row; sources always enter with polling
  disabled; cookie/session/secret fields and old local media paths are not
  transferred.
- After apply, `semi-intel radar cluster` (or **Recluster & rescore now**) now
  analyzes pending posts before clustering and scoring them.

No Alembic revision was needed: all accepted data maps to the existing 3.0
canonical tables, so the head remains `d3c8e41f9a62`.

Real-database acceptance used the supplied 58 MB Signal Radar database:
80 sources, 5,211 posts, 2,056 media rows, 2,020 provider runs and 106 source
suggestions imported (9,473 total); an immediate second apply reported all
9,473 as duplicates. Analysis processed all 5,211 posts, created 350
candidates, attached 329 posts to existing candidates and suppressed 4,532
low-signal posts. There were zero pending items afterward and no imported
local media paths.

## Architecture decision (unchanged since Phase 0)

Semiconductor Intelligence Platform 2.2 is canonical. Signal Radar's
collector/provider architecture was absorbed; its editorial/story-clustering
"brain" was **not** — the supplied Radar database's own top-ranked stories
(`United States / The Six Fi` at interest score 0.927, the single highest
score in that database; `South Korean / Galaxy Z8`; `Jensen Huang`; a
catch-all `Xeon` story) are direct evidence that single-shared-entity
clustering with unconditional TitleCase entity creation is unsafe as a
canonical editorial layer. Full reasoning and root-cause trace in
`PHASE0_AUDIT.md` section 3.

The governing pipeline (all stages persisted, idempotent, independently
retryable, each isolated so a crash between stages never loses collected
material or double-creates anything):

```
Provider collection (RSS / X / replay)
  -> SignalItem (raw, immutable) + ProviderRun telemetry
  -> Signal analysis (SignalTopicMatch / SignalEntityMention / SignalLabel)
  -> SignalCandidate clustering (conservative, explained, independence-grouped)
  -> Attention scoring (persisted, configurable weights, full explanation)
  -> Editorial promotion (manual always available; automatic OFF by default)
  -> Evidence + EditorialStory (unchanged canonical layer)
  -> existing claim-link suggestions + bounded discovery (unchanged, reused)
```

## What was preserved vs. retired from each side

See `PHASE0_AUDIT.md` section 5 for the full overlap map. Summary: Semi
Intel's `Entity`/`Relationship`/`Claim`/`Evidence`/`EditorialStory`/
`MonitoredTopic`/bounded-discovery stack is untouched. Signal Radar's
`stories`/`story_entities`/its own `evidence` table, its single-shared-
entity story engine, and its raw-distinct-source-ID "independence" were
**not** ported — see "Radar DB import" in "Next recommended increment" for
why importing that data as canonical truth is still explicitly out of
scope even for the eventual importer.

## Schema additions (Phases 1 and 5)

Migration `f4f0279f3459` (Phase 1) added the raw signal + candidate layer;
`4ee15190b40b` added per-item topic matches; `a6a1b2c73e08` (Phase 5) added
the promotion audit trail; `d3c8e41f9a62` removed the redundant reverse
SignalItem-to-Evidence FK. All new tables, in one place:

- **Extended**: `sources` (+provider/provider_key/enabled/polling_enabled/
  muted/priority/languages/expertise/signal_types/notes/cursor/
  last_success_at/last_observed_item_at/error_state/updated_at/
  provider_metadata, unique on `(provider, provider_key)`), `evidence`
  (+`origin_signal_item_id`, nullable unique — see "Known limitations" for
  the circular-FK note), `source_suggestions` (+kind/platform/provider_key/
  independent_origin_count/inferred_reliability, unique on
  `(platform, provider_key)`)
- **New — raw sensory layer**: `signal_items`, `signal_media`,
  `signal_entity_mentions`, `signal_labels`, `signal_topic_matches`,
  `provider_runs`
- **New — candidate layer**: `signal_candidates`, `candidate_signal_items`,
  `candidate_topic_matches`, `candidate_entities`, `candidate_relationships`,
  `signal_independence_groups`, `signal_independence_group_members`
- **New — settings (all conservative-default, get-or-create singleton
  rows)**: `signal_collection_settings`, `attention_scoring_settings`,
  `candidate_promotion_settings`
- **New — promotion audit**: `candidate_promotion_events`

Verified: clean upgrade from a fresh database, upgrade from the exact
pre-merge Semi Intel 2.2 schema with real seeded data (39 sources, 71
topics — all preserved, every new column lands on a safe off-by-default
value), full downgrade-to-base round trip, and schema parity between
`alembic upgrade head` and `Base.metadata.create_all()`
(`tests/test_migrations.py`, `tests/test_cli_db.py`).

## New services (`semi_intel/signals/`)

- `providers/` — the provider contract (`collect`/`normalize`/`validate`),
  adapted to this codebase's synchronous style. `rss.py` (feedparser-based,
  separate code path from the pre-existing `RSSSourcePlugin`), `replay.py`
  (fixture playback), `providers/x/` (full port: session/auth/interceptor/
  collector/html_fallback/normalizer/provider — optional, importable without
  Playwright installed, gated behind its own `x_provider_enabled` setting
  independent of the general collection toggle).
- `collection.py` — `CollectionService`: persists `SignalItem`/`SignalMedia`,
  dedups on `(provider, external_id)` with content-hash as a secondary
  guard, advances `Source.cursor` only after a successful commit, priority-
  derived poll intervals, per-provider startup staggering, gated behind
  `SignalCollectionSettings.collection_enabled` (automatic path only —
  manual `radar collect` always works, X still requires its own opt-in
  either way).
- `analysis.py` — three-tier extraction (monitored-topic match reusing
  `editorial.service.match_topic()` exactly; canonical-entity match against
  *existing* entities only, never creating one; unknown TitleCase-shaped
  phrases staying `candidate`/`rejected` forever). Ported Signal Radar's
  hardware-context gate, hard-block list, and edge-trim rules verbatim, plus
  added `architecture`/`overview` to the hard-block list. `ANALYSIS_VERSION`
  tracking + `reprocess_stale_items()`.
- `clustering.py` — conservative attachment scoring: a specific monitored-
  topic match OR an exact structured-artifact match is sufficient alone;
  quote/reply lineage short-circuits directly to the parent's candidate
  (including reactivating a STALE candidate, but never a dismissed/snoozed
  one); broad umbrella topics (GeForce/CUDA/x86/ARM/...) are excluded from
  the topic-match bonus (a real bug caught during testing: two different
  products both merged on "GeForce" alone before this fix).
- `independence.py` — union-find grouping by same canonical URL, quote/
  reply lineage, same author, or explicit citation phrase (via/according
  to/reported by/citing/source/spotted by/hat tip/originally published by).
  This is the actual fix for Signal Radar's raw-distinct-source-ID
  "independence" bug (`PHASE0_AUDIT.md` section 3).
- `scoring.py` — `AttentionScoringSettings`-driven weighted score (defaults
  0.30/0.20/0.15/0.15/0.15/0.05 for topic relevance/novelty/momentum/
  source diversity/artifact strength/source quality), real time-window
  momentum (not total evidence count), syndication/staleness penalties,
  full per-component JSON explanation persisted on the candidate.
- `candidate_state.py` — seen/unseen/dismiss/restore/snooze/wake/stale/
  reversible-and-audited merge. Nothing is ever deleted.
- `promotion.py` — the sole path into the canonical editorial layer. Reuses
  `EditorialDiscoveryService.process_evidence()` for the first Evidence row,
  then attaches every remaining candidate item directly to that same story
  (our own candidate clustering already established they belong together;
  letting editorial's independent 0.72-headline-similarity re-decide per
  row risked fragmenting one candidate across multiple stories). Idempotent
  via `Evidence.origin_signal_item_id`. Never creates a Claim. Automatic
  promotion defaults off, enforces every threshold in
  `CandidatePromotionSettings` plus an hourly budget.
- `suggestions.py` — the Signal Radar half of unified source suggestions:
  mines explicit-attribution-credited handles from `SignalItem` text (same
  detector `independence.py` uses for grouping), writing into the *same*
  `SourceSuggestion` table Semi Intel's domain-citation mining already used
  (`kind=handle` vs `kind=domain`). `accept_source_suggestion()` resolves by
  `(provider, provider_key)`, idempotent.

## API and CLI additions

**Web API** (`semi_intel/web/app.py`, all under `/api/radar/`): `status`,
`candidates` (list+detail), `candidates/seen`, `candidates/{id}/dismiss`,
`candidates/{id}/restore`, `candidates/{id}/snooze`,
`candidates/{id}/promote`, `settings` (GET/PUT), `sources` (list/add),
`sources/{id}/collect`, `cluster`, `source-suggestions` (list/refresh/
review). Request schemas in `semi_intel/web/schemas.py`.

**CLI** (`semi_intel/cli.py`, `radar` command group): `status`, `collect`,
`reprocess`, `candidates`, `candidate-seen`, `candidate-dismiss`, `cluster`,
`promote`, `promote-eligible`, `provider-health`.

**GUI** (`semi_intel/web/static/index.html`, one file, no build step, same
vanilla-JS pattern as the existing dashboard): new "Signal Radar" nav tab
with an overview panel (collection/promotion state, counts, recent
provider runs), a filterable/sortable candidate list, a candidate detail
panel (score breakdown with per-component bars, full signal timeline with
attach reasons and mentions/labels, independence groups, eligibility
explanation, seen/dismiss/restore/snooze/promote actions), a source-
management panel (paste-a-handle-or-URL add form with provider auto-
detection, source health table, suggested-sources review), and a settings
form bound to `/api/radar/settings`.

## Manual GUI acceptance performed

Live browser verification (not just automated tests) was performed in this
session against a seeded demo database exercising the brief's own worked
scenario (a VideoCardz-sourced RTX 50 Super leak, two follow-ups explicitly
crediting VideoCardz, one independent Geekbench-benchmark item, one
Jensen-Huang noise item, one generic-topic tariffs item, one Zen 6 item):

- Editorial Inbox tab loads clean, zero console errors.
- Signal Radar tab loads clean, zero console errors. Overview counts
  correct (3 unseen active, 3 high-attention, 0 stale/snoozed/dismissed/
  promoted).
- Candidate list correctly shows exactly 3 candidates: `RTX 50 Super` (4
  items, 3 sources, **2** independent groups), `Zen 6` (1 item), and
  `semiconductor tariffs` (1 item, from the tariffs post). The Jensen Huang
  item produced **zero** candidates, visually confirmed absent from the
  list.
- Candidate detail view for `RTX 50 Super` confirmed: correct score
  breakdown across all 6 components with human-readable explanations
  (`monitored topic 'RTX 50 Super' (priority 0.60)`, `2 independent
  group(s) out of 4 item(s)`, etc.); correct signal timeline with
  per-item attach reasons (`exact topic match`, `shared artifact`,
  `publication gap`); correct independence groups — **Group (citation):
  items 1, 2, 3** (origin + two explicit VideoCardz citations) and
  **Group (independent): item 4** (the Geekbench benchmark item),
  directly confirming the "twelve citations count as ~1 group, a separate
  benchmark counts independently" requirement live in the browser, not
  just in a unit test.
- Source list, source-add form, and settings form all rendered with
  correct initial values from the live API.
- One cosmetic bug found and fixed during this pass: the "shared artifact"
  attach reason displayed the internal normalized form (`10 de 2 d 04`)
  instead of the original text (`10DE:2D04`) — fixed in `clustering.py`
  (`display_text` mapping added to `_ItemSignals`).
- Not exercised in this pass: promote/dismiss/snooze button clicks (covered
  by `tests/test_web_radar.py` instead, which does exercise every one of
  these against the same API the GUI calls), the "Add source" submit flow
  end-to-end (covered by `tests/test_web_radar.py::
  test_add_x_source_via_handle` and the RSS-fixture equivalent), settings
  save round-trip (covered by `test_radar_settings_roundtrip`).

## Real bugs found and fixed during this merge (not hypothetical)

1. **Broad-topic false merge** (`clustering.py`): "GeForce"/"CUDA"/etc. are
   themselves seeded monitored topics (umbrella brand terms, not specific
   codenames), so two unrelated products both matching "GeForce" were
   merging into one candidate on that basis alone. Fixed by excluding a
   `BROAD_TOPIC_NORMALIZED_NAMES` set from the topic-match attach bonus.
2. **Citation-based independence grouping required the cited source to
   already be a registered `Source`** — realistic for "VideoCardz" (a
   tracked RSS source), but the first version of the test suite used a
   generic source name and revealed the gap.
3. **Stale-candidate reactivation was completely unreachable**: the
   attachment query filtered to `state == ACTIVE` only, so a candidate that
   went stale could never accept new evidence again, contradicting the
   `mark_stale_candidates()` docstring's own claim. Fixed by including
   `STALE` in the attachable-states set for the lineage short-circuit path
   (general topical re-matching correctly still excludes long-stale
   candidates via the existing time-window guard — verified this doesn't
   regress the "stale time window remains separate" requirement).
4. **A promotion transactional bug**: the original draft caught a
   suggestion/discovery failure with `session.rollback()`, which would have
   silently discarded the just-added Evidence/Story/audit-event rows from
   the *promotion itself* (all still uncommitted at that point), not just
   the failing sub-step. Fixed by committing the promotion first, then
   attempting suggestions/discovery each in their own isolated try/except.
5. **RSS dual-collection risk** (found during this checkpoint's explicit
   review, not by a test failure): `PipelineService._rss_sources_to_poll()`
   selected any `Source` with `type=RSS` and a `url`, with no regard to
   `provider` — so a source explicitly registered through the new Signal
   pipeline (`provider="rss"`, `polling_enabled=True`) could have been
   polled by *both* the legacy direct-to-Evidence path and the new
   SignalItem path in the same cycle. Fixed by restricting the legacy
   query to `provider == "manual"` (the default for every pre-merge and
   CLI-added source; the new pipeline always sets a concrete provider).
   Regression test: `tests/test_pipeline_service.py::
   test_legacy_and_signal_rss_paths_never_collect_the_same_source`.
6. **Circular provenance dependency** (stabilization): the checkpoint
   stored the same promotion relationship in both
   `Evidence.origin_signal_item_id` and `SignalItem.origin_evidence_id`,
   creating an unnecessary two-way FK cycle. Migration `d3c8e41f9a62`
   removes the reverse column. Candidate detail derives the Evidence ID
   from the unique canonical link. Migration parity and promotion
   idempotency remain green.
7. **Cross-form duplicate feed registration** (stabilization): one feed
   could be added once through the legacy source form and again through
   Signal Radar. `semi_intel/source_identity.py` now provides shared URL
   identity normalization used by both web paths and the legacy CLI.
   Scheme, `www`, default ports, trailing slashes, fragments and common
   tracking parameters no longer create a second source row. Distinct
   paths and meaningful query parameters remain distinct.

## Known limitations (checkpoint)

- **Deferred merge phases remain**: there are no unified notifications, no
  telemetry dashboard beyond what's in the status
  endpoint, no OCR/media-download implementation (schema exists, wiring
  does not), no live X collection verification (the ported code is
  believed correct — it is a faithful port of Signal Radar's own working
  implementation — but has not been exercised against a live X session in
  this codebase). The Windows executables include RSS/replay Signal Radar
  support; the optional Playwright/X runtime is not bundled in the default
  executables and continues to fail closed.
- Media download and OCR settings exist (`SignalCollectionSettings.
  media_download_enabled`/`ocr_enabled`) and are exposed in the settings
  GUI/API, but there is no service that actually acts on them yet —
  they are inert toggles at this checkpoint.

## X safety status

Unchanged from the design ported in Phase 2: optional (`pip install
semi-intel[x]`), importable without Playwright installed, no automated
login (only human-session cookie replay via `auth.py`), no fingerprint
spoofing or anti-detection, gated behind `SignalCollectionSettings.
x_provider_enabled` (separate from the general `collection_enabled`
toggle, and checked regardless of manual-vs-automatic collection).
**Not verified against a live X session** in this codebase — only unit-
tested against the normalizer/interceptor logic with fixture data, exactly
as Signal Radar's own test suite did.

## 3.1 checkpoint archive and builds

The delivered 3.1 archive is source plus the two rebuilt Windows executables. It
excludes virtual environments, build/test caches, temporary databases,
session/cookie material and `originals_backup/`.

The existing PyInstaller specs already trace the complete
`semi_intel.signals` import graph through the CLI entry points, so no spec
rewrite was required. Both builds completed on Windows/Python 3.14:

- `dist/semi-intel.exe`
- `dist/semintel.exe`

Packaged smoke passed against a clean temporary database:

- `semi-intel.exe --help`
- `semi-intel.exe db upgrade`
- `semi-intel.exe radar status`
- `semi-intel.exe radar import --database <real legacy DB> --categories sources --json`
- `semi-intel.exe radar candidates`
- `semi-intel.exe discovery status`
- `semintel.exe status`

The final archive audit and output path are recorded in the delivery report.

## Rollback and backup instructions

- Every original artifact is untouched: `Semi intel 2.0.zip`,
  `X Scraper.zip`, their extracted trees, and both original databases
  (copied read-only to `originals_backup/` in this project before any work
  began — see `PHASE0_AUDIT.md` section 7).
- To roll back a database from this checkpoint's migration head to the
  exact pre-merge Semi Intel 2.2 schema: `alembic downgrade b71d4e2c9a30`
  (this removes every table/column this merge added; back up the database
  file first regardless — SQLite downgrade is destructive to the removed
  columns' data).
- To roll back the source tree: this checkpoint's working directory is
  `Semi Int and X Scraper Fusion/`, built fresh from the Semi Intel 2.2
  source (see `PHASE0_AUDIT.md`); the original, unmodified Semi Intel 2.2
  tree remains at `Semi intel 2.0/semi_intel_platform` for comparison or
  a clean restart.

## Next recommended increment

In priority order:

1. **Phase 8 — operations**: notifications (candidate/story-based, deduped,
   default off), telemetry beyond the status endpoint, media download +
   OCR wiring (schema and settings already exist), scheduled-task/service
   deployment docs, and an explicit decision about whether the optional X
   runtime should ever be packaged rather than installed separately.
2. **Phase 9 — final verification**: full manual GUI acceptance walkthrough
   per the brief's 21-step scenario (this checkpoint covered a meaningful
   subset live, not all 21 steps). Windows builds and clean packaged smoke
   now pass, but the remaining acceptance scenario should be completed
   after the importer/operations work changes the final product surface.

---



## 2.2 bounded discovery-ring addendum

### Outcome

The former “no backlink/news provider” limitation is addressed with a
bounded discovery ring. Only recent stories that the existing editorial
engine has already scored as interesting are searched. Results are provider
metadata linked to a story, not fabricated evidence.

The system does not crawl the internet. It does not fetch result articles,
follow links, search from discovered results, or fetch candidate homepages.
Homepage access remains confined to the existing editor-triggered feed
discovery workflow.

### Provider

`semi_intel/discovery/providers.py` defines `DiscoveryProvider`,
`SearchRequest`, and normalized `ProviderResult`. The initial
`GoogleNewsRSSProvider` makes a bounded RSS search and reads publisher
identity from result metadata. Provider parsing is isolated because this
external format and availability can change. No credential is required.

Network tests use injected fixtures; the suite never calls the live service.
The application remains fully useful when discovery is disabled or the
provider fails.

### Defaults and persisted controls

`DiscoverySettings` is the single-user settings row:

- discovery enabled for manual use: yes;
- automatic discovery: no, until the editor enables it;
- minimum interest: 0.55;
- maximum age: 48 hours;
- cooldown: 6 hours;
- maximum completed cycles per story: 3;
- maximum queries per cycle: 3;
- results per query: 10;
- maximum results per cycle: 30;
- global cycles per rolling hour: 5;
- provider requests per rolling hour: 15;
- query cache: 6 hours;
- request timeout: 8 seconds;
- locale: `en-US`, region `US`.

Settings, run counts and request counts survive restarts. Runs left in
`running` state for over 30 minutes are failed safely on recovery.

### Eligibility

The deterministic check requires:

- discovery enabled;
- automatic mode enabled when called by the pipeline;
- score at or above the configured minimum;
- story within the configured age window;
- at least three non-generic distinctive headline tokens;
- fewer than the configured completed cycles;
- expired cooldown;
- remaining global-cycle and provider-request budgets.

Seen state is not a rejection signal. A seen story remains eligible when
other requirements hold; new direct coverage can therefore support a later
cycle. Duplicate evidence does not create coverage or invoke discovery.

### Queries

At most three normalized, deduplicated, 180-character queries are produced:

1. a distinctive normalized headline phrase;
2. primary monitored topic plus originating publication;
3. “according to [publication]” plus the primary topic.

Generic standalone terms such as AMD, NVIDIA, GPU, semiconductor and report
are removed from the distinctive phrase. Queries and their explanations are
stored on each `DiscoveryRun`.

### Relevance and relationship rules

Score, capped at 1.0:

- normalized headline similarity × 0.40;
- specific monitored-topic overlap, up to 0.25;
- explicit attribution to the registered origin, 0.25;
- publication within the bounded window, 0.08.

Acceptance requires score ≥ 0.45 and a specific monitored-topic match.
Blocked/noise domains, listing/category/search URLs, out-of-window results,
and URLs already stored as evidence are rejected explicitly.

Relationships are conservative:

- `explicitly_cites_known_source` only when available metadata contains an
  attribution phrase plus the source name;
- `likely_follow_up` for high headline similarity;
- `possible_original_report` only when metadata uses origin/exclusive
  language;
- otherwise `unknown`.

The supporting phrase is retained when an explicit citation is claimed.

### Schema and migration

Revision `b71d4e2c9a30`, after `9f3c2a1b7d10`, adds:

- `discovery_settings`;
- `discovery_runs`;
- `discovery_results`.

Indexes cover story, provider, status/time, run, canonical URL/domain,
publication time, relevance and accepted state. Results are unique by
run/canonical URL. Upgrade/downgrade and clean-schema parity are tested.

### Product surfaces

- Story detail: eligibility, reason, budgets, last run, stored queries,
  manual run button, accepted result links, classification and explanation.
- Discovery Activity: persisted settings, provider, hourly usage and recent
  completed/partial/skipped/failed activity.
- Suggested Sources: accepted unknown domains contribute bounded-discovery
  reasons; existing registered and blocked domains are excluded.
- CLI: `discovery status`, `discovery run [--story-id]`, and
  `discovery backfill`.
- Pipeline: runs eligible cycles only when automatic mode is enabled; errors
  are isolated as `targeted discovery` failures.

### New and materially changed files

Added:

- `semi_intel/discovery/__init__.py`
- `semi_intel/discovery/providers.py`
- `semi_intel/discovery/service.py`
- `migrations/versions/b71d4e2c9a30_bounded_discovery_ring.py`
- `tests/test_discovery_service.py`

Changed:

- domain enums/models, pipeline, CLI, web API/schemas/GUI;
- migration table-count tests, editorial web tests and pipeline tests;
- README, INSTALL, CHANGELOG and this handoff.

### Known limitations

- Google News RSS may expose an intermediary result URL; publisher domain
  comes from source metadata. No redirect/article fetch is performed.
- Search metadata can omit attribution even when an article contains it.
- “Possible origin” remains an inference and is labelled accordingly.
- A synchronous manual search may take up to the configured timeout per
  uncached query. A future multi-user deployment should queue runs.
- Cache reuse is query-based and retained through stored discovery results;
  no provider-wide shared cache table is required at the current volume.
- Automatic mode intentionally defaults off to prevent surprise network
  traffic after upgrade.

### Commands

```powershell
semi-intel db upgrade
semi-intel discovery status
semi-intel discovery run --story-id 123
semi-intel discovery backfill
```

Final 2.2 test, GUI and packaged-build results are recorded at the end of
this file after verification.

## Outcome

The platform now converts ingested evidence into an automated editorial
inbox. Claims remain the verification/truth-tracking layer; `EditorialStory`
is the discovery/triage layer. This separation is intentional: a keyword
match can make an article worth reading without turning its text into a
verified claim.

The GUI now supports:

- unseen/seen/all editorial-story views;
- explainable interest ranking and story filters;
- single and bulk seen actions;
- coverage timelines and citation links;
- full monitored-topic CRUD, aliases, categories, priority, and enable state;
- suggested-source review, ignore/block/restore, feed discovery, and addition.

## Architecture

`IngestionService` persists immutable evidence and immediately passes new
rows to `EditorialDiscoveryService`. `PipelineService` additionally runs the
idempotent backfill after every scheduled pass, which catches evidence
created through older/manual paths. The web API uses the same service.

Business rules are in `semi_intel/editorial/`; HTTP routes remain adapters.
The frontend remains a packaged, dependency-free single HTML file.

## Schema and migration

Alembic revision: `9f3c2a1b7d10`, after `71747eaa2044`.

New tables:

- `monitored_topics`: name, normalized name, keyword, JSON aliases, category,
  priority, enabled state, notes, and timestamps.
- `editorial_stories`: representative headline/summary, deterministic score
  and reasons, coverage count, persistent seen timestamp, new-coverage count,
  and cluster timestamps.
- `story_evidence`: unique evidence membership in a story.
- `topic_matches`: unique story/topic match with the actual matched term.
- `citations`: unique normalized outbound URL per evidence row.
- `source_suggestions`: canonical domain, inferred name/feed, score/reasons,
  aggregate counts, review status, and discovery timestamps.

All common filtering/join columns are indexed. Upgrade and clean
`create_all()` schemas have parity. Downgrade removes the six new tables.

Seed topics are inserted idempotently by `TopicService.seed()` when the web
application or editorial discovery starts. Duplicate `Intel 18A` in the
product brief resolves to one row because normalized names are unique.

## Topic matching

`normalize_phrase()` applies Unicode NFKC normalization, case folding,
punctuation/whitespace normalization, and letter/number boundary
normalization. Thus `RDNA5`, `RDNA-5`, `RDNA—5`, and `RDNA 5` normalize to
the same tokens.

Matching searches for complete normalized token sequences with surrounding
spaces, preventing `ARM` from matching `alarming`. Each match stores the
topic and the exact configured keyword/alias that matched. Topic creation
checks names, primary keywords, and aliases against every existing topic and
returns an explicit conflict instead of silently duplicating it.

## Story clustering

Clustering is deliberately conservative:

1. candidate stories must have activity within three days of the evidence;
2. they must share at least one monitored topic; and
3. normalized headline similarity must be at least 0.72.

Otherwise a new story is created. False separation is preferred to combining
unrelated AMD/NVIDIA articles. Canonical evidence dedup remains unchanged.

## Interest score

The persisted score is capped at 1.0:

- topic signal: highest priority × 0.35 plus 0.04 per matched topic (up to
  three topics), total capped at 0.45;
- recency: up to 0.25, linearly decaying to zero over seven days;
- coverage: 0.05 per article after the first, capped at 0.15;
- source quality: best contributing source trust × 0.10;
- detected editorial citations: 0.01 each, capped at 0.05.

Stored reasons include matched topic names, coverage count, recency,
high-priority status, and citation count. The GUI never shows a bare score.

## Seen-state policy

`EditorialStory.seen_at` is persisted. The default inbox filters to null
`seen_at`. Marking seen clears the current new-coverage counter. New evidence
clustered into a seen story leaves it seen and increments
`new_coverage_count`; it does not force the editor to revisit the item.
Duplicate evidence creates neither a new story nor new coverage.

## Citation and source discovery

Citation extraction reads HTML anchor `href` values and plain HTTP(S) URLs
available in stored evidence content. It:

- strips common tracking parameters;
- normalizes HTTP/HTTPS, `www`, mobile prefixes, AMP suffixes, and domains;
- preserves the canonical destination URL;
- ignores self-links and an extensible set of analytics, advertising, CDN,
  commerce, social, sharing, and media domains;
- aggregates unknown editorial domains into source suggestions.

Suggestion score is capped at 1.0:

- up to five references × 0.10;
- up to four distinct relevant stories × 0.12;
- up to four monitored topics × 0.08;
- 0.10 when a feed is known.

Feed discovery uses HTML `rel=alternate` links plus `/feed`, `/rss`,
`/rss.xml`, `/feed.xml`, and `/atom.xml`; candidates must parse with at least
one entry and no parser error. Requests use an eight-second timeout, a
1 MB read cap, and `SemiIntel/2.1` user agent. Discovery runs only when the
editor presses Find feed.

## Important limitations

- Citation discovery is limited to URLs present in ingested feed content.
  Many RSS feeds provide summaries without outbound links.
- There is no web-wide search or backlink provider yet. Consequently the
  system can identify unknown origins cited by ingested sites, but it cannot
  independently find every site on the web that cited VideoCardz. A future
  news-search or full-article discovery adapter is needed for that exact
  workflow.
- The application does not fetch full article bodies during normal
  ingestion. That is intentional for this increment: it avoids unbounded
  crawling, robots/terms issues, and new rate-limit complexity.
- Clustering uses headline similarity rather than embeddings. This is
  explainable and safe but may leave close coverage in separate clusters.
- Feed autodiscovery performs synchronous requests after an explicit GUI
  action; a production multi-user deployment should move it to a job queue.
- Seen state is currently single-editor/global, matching the local
  single-user application. Multi-user state would need an account key.

## Files added

- `semi_intel/editorial/__init__.py`
- `semi_intel/editorial/service.py`
- `semi_intel/editorial/feed_discovery.py`
- `migrations/versions/9f3c2a1b7d10_editorial_discovery.py`
- `tests/test_editorial_discovery.py`
- `tests/test_editorial_web.py`
- `CHANGELOG.md`
- `HANDOFF.md`

## Files materially changed

- `semi_intel/domain/enums.py`
- `semi_intel/domain/models.py`
- `semi_intel/ingestion/service.py`
- `semi_intel/pipeline/service.py`
- `semi_intel/web/app.py`
- `semi_intel/web/schemas.py`
- `semi_intel/web/static/index.html`
- `semi_intel/cli.py`
- `tests/test_cli_db.py`
- `tests/test_migrations.py`
- `README.md`
- `INSTALL.md`

Packaging specs already collect the complete `semi_intel` package,
`migrations/`, and `web/static/`, so the new Python modules, revision, and
HTML are included without new data declarations.

## Verification

Baseline after installing declared development dependencies:

- 173 passed;
- 2 migration subprocess failures caused by the test replacing the complete
  Windows environment with only PATH and `SEMI_INTEL_DB_URL`.

The harness now preserves the environment, which fixes the `_overlapped`
startup failure and tests the actual migration.

Focused editorial tests cover normalization, boundaries, seeding,
duplicates, matching/explanations, ranking, conservative clustering,
backfill idempotency, seen persistence, new-coverage policy, citations,
domain/noise filtering, source review/addition, and feed validation.

Final verification on 2026-07-26:

- `.\.venv\Scripts\python.exe -m pytest -q`
  — **190 passed** in 69.86 seconds.
- Migration parity, clean upgrade, and downgrade-to-base are included in
  that passing suite.
- JavaScript was parsed independently with Node (`new Function(script)`) —
  syntax valid.
- Live browser GUI:
  - confirmed all seeded topics and aliases render;
  - created `Rubin Ultra` with aliases and priority 0.9 through the GUI;
  - created a source and matching evidence through the GUI;
  - confirmed the story appeared automatically with Rubin/Rubin Ultra
    reasons and a 0.70 score;
  - opened its coverage timeline;
  - marked it seen and confirmed it left the unseen inbox and remained in
    the Seen view;
  - confirmed `origin-lab.example` appeared under Suggested Sources with
    reference/story/topic counts.
- The live pass revealed a concurrent first-load seeding race. Seeding was
  moved from per-request setup to single application startup, then the full
  suite passed.
- Both PyInstaller specs built successfully on Windows/Python 3.14:
  `dist/semi-intel.exe` and `dist/semintel.exe`.
- Packaged smoke:
  - `semi-intel.exe --help` loaded;
  - `semi-intel.exe db upgrade` upgraded a clean database to head;
  - `semi-intel.exe editorial backfill` completed;
  - `semintel.exe status` reported schema up to date.
- Expected non-blocking warnings remain: Python 3.14 deprecates naive
  `datetime.utcnow()`, and PyInstaller reports unused optional database
  drivers (`pysqlite2`, `MySQLdb`) and optional `tzdata` as absent.

## Commands for the next developer

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m semi_intel.cli editorial backfill
.\.venv\Scripts\python.exe -m semi_intel.cli web serve --port 8000
```

For an existing operator database:

```powershell
semintel backup
semi-intel db upgrade
semi-intel editorial backfill
semintel gui
```

## Recommended next increment

Add a rate-limited full-article fetcher and a news/backlink discovery adapter.
Store fetch status and robots/terms decisions explicitly. This is the missing
piece for discovering *citing publishers* that are not already present in
any registered feed. Keep it separate from the deterministic source
suggestion scorer implemented here.

## 2.2 final verification addendum

Final verification for the bounded discovery ring on 2026-07-26:

- Baseline before this increment: **190 passed**.
- Final full suite:
  `.\.venv\Scripts\python.exe -m pytest -q`
  — **202 passed** in 91.41 seconds.
- The added tests cover query construction, fixture-backed Google News RSS
  parsing, the VideoCardz/RTX 50 Super citation scenario, relevance rejection,
  suggested sources, seen-story eligibility, cooldowns, persisted hourly
  budgets, blocked domains, provider timeouts, stale-run recovery, settings
  persistence, API behavior, and pipeline failure isolation.
- The static GUI script was independently parsed by Node with
  `new Function(script)` — syntax valid.
- API-backed GUI behaviors (settings persistence, disabled-provider manual
  run, status, domain blocking, story detail data) pass automated tests.
- A new live browser pass was attempted, but the desktop browser harness
  repeatedly interrupted while handing off to its temporary local server.
  No claim of a completed 2.2 visual browser pass is made. The previously
  completed 2.1 browser acceptance remains recorded above.
- Both Windows PyInstaller builds completed:
  `dist/semi-intel.exe` and `dist/semintel.exe`.
- Clean packaged smoke passed:
  `semi-intel.exe --help`, `semi-intel.exe db upgrade`,
  `semi-intel.exe discovery status`, `semi-intel.exe discovery backfill`,
  and `semintel.exe status`.
- Discovery defaults to automatic mode **off**. Operators can enable it in
  Discovery Activity after reviewing the persisted limits.
- The provider intentionally consumes search/RSS metadata only. It does not
  crawl full articles, recurse through discovered links, or guarantee that
  every intermediary citation can be reconstructed from provider metadata.
# Handoff — Semiconductor Intelligence Platform 3.2 (Phase 8 complete)

Phase 8 is implemented as a deterministic notification subsystem, not as GUI
polling logic. The authoritative services are in `semi_intel/notifications/`;
the CLI, FastAPI routes, GUI, and pipeline delegate to them.

## What shipped

- Six new persisted tables: notifications, notification settings, transition
  watermarks, delivery attempts, digests, and provider incidents.
- Alembic head `e8b7c2d4a901` (41 application tables).
- Transition alerts covering high attention, score increases, new independent
  corroboration, promotion readiness/completion, topic activity, source
  suggestions, provider failure/recovery, and operator tests.
- Historical flood protection via `NotificationSettings.activation_at`.
- Stable timezone-aware daily digest windows and deduplication.
- Read/unread, dismiss/restore, retention, muting, provider incident tracking,
  quiet hours, hourly caps, adapter idempotency, and bounded exponential retry.
- `semi-intel notifications ...` CLI, `/api/notifications...` API, and the
  **Alerts & Digest** GUI.
- Pipeline generation is last and fault-isolated from collection, clustering,
  scoring, promotion, ingestion, and discovery.
- No real external messaging adapter. `external_delivery_enabled` defaults
  false; the in-app adapter and injectable interface are the only shipped
  delivery mechanisms.

## Verification

- Baseline before Phase 8: 334 tests passed.
- Final Phase 8 acceptance/migration/API/CLI/delivery/pipeline group: 39 passed.
- Final complete regression suite: **357 passed, 22,595 warnings in 512.56s**.
- The warnings are known pre-existing `datetime.utcnow()` deprecations and a
  TestClient deprecation. New Phase 8 timestamps use timezone-aware UTC.
- Python compile and dashboard JavaScript parse checks passed.
- Both Windows executables rebuilt; frozen smoke checks passed for migration,
  notification status, test alert, digest, listing, and operator status.
- Frozen build sizes: `semi-intel.exe` 25,828,198 bytes; `semintel.exe`
  25,847,253 bytes. `semintel update` reports version 3.2.0, and the frozen
  migration reports Alembic head `e8b7c2d4a901`.

The deterministic acceptance test records the complete “from now forward”
workflow: an old candidate stays quiet; a fresh candidate starts below the
threshold; independent corroboration and high attention alert exactly once;
unchanged generation deduplicates; read state survives session expiration;
dismiss/restore persists; promotion readiness and completion alert once; two
provider failures remain quiet and the third opens one incident; success creates
one recovery; the same digest window reuses its digest; quiet hours defer a fake
external adapter; the post-quiet-hours retry delivers once; candidate/story seen
state remains unchanged.

Manual browser automation was not performed because browser control in this
environment has been unreliable and the brief makes it a bounded optional
check. GUI behavior is covered by FastAPI integration tests, dashboard asset
tests, required-control assertions, and direct JavaScript syntax validation.

## Design invariants for the next phase

1. Never derive alerts solely in the GUI; generation belongs in
   `NotificationService`.
2. Every recurring alert type needs both a transition watermark and a stable
   dedup key.
3. Imported/pre-activation history seeds state but does not alert.
4. Digest generation must not mark candidates or alerts as seen.
5. Network delivery must remain behind `DeliveryAdapter`, disabled by default,
   secret-safe, retry-bounded, and testable with a fake.
6. Notification failure must never roll back intelligence pipeline work.

## Sensible next work

- Phase 9 can add one explicitly configured external adapter (for example email
  or Slack), plus encrypted credential handling and an operator-visible
  connection test.
- Add digest scheduling to the Windows service layer rather than relying only
  on pipeline cadence.
- Add notification preference presets after real-world threshold tuning.
# Handoff — Semiconductor Intelligence Platform 3.3

## Phase 9: Operational Automation, Delivery, and Signal-Quality Tuning

Phase 9 is complete as a bounded local operational layer over the existing
application. It does not create a second scheduler application, database,
notification system, web server, or pipeline. Scheduled work invokes the
existing canonical services.

### Verified release state

- Package version: `3.3.0`
- Alembic head: `f9a4c6d8e203`
- Application tables: 48
- Starting Phase 8 baseline reproduced: 357 tests passed
- Phase 9 focused migration/service/API/operator group: 59 tests passed
- Webhook/pipeline/API regression group: 23 tests passed
- Final complete suite: **377 passed, 23,022 warnings in 628.80 seconds**
- Python compilation: passed
- Dashboard JavaScript syntax: passed
- `semi-intel.exe`: rebuilt and smoke-tested, 25,893,606 bytes
- `semintel.exe`: rebuilt and smoke-tested, 25,924,438 bytes
- Manual browser walkthrough: not performed. Automated dashboard/API coverage
  and JavaScript parsing were used, avoiding the previously unstable browser
  verification loop.

Warnings are the established legacy `datetime.utcnow()` deprecations plus the
FastAPI/Starlette TestClient deprecation. New Phase 9 operational timestamps
use timezone-aware UTC.

### Schema and migration

Additive migration `f9a4c6d8e203`, based on Phase 8 head `e8b7c2d4a901`, adds:

1. `scheduler_settings`
2. `operational_job_runs`
3. `operational_job_leases`
4. `notification_feedback`
5. `saved_notification_views`
6. `delivery_adapter_status`
7. `backup_records`

Migration verification passed for fresh upgrade, schema parity, downgrade to
base, exact upgrade from Phase 8, notification/settings preservation, and safe
disabled defaults. The migration performs no network request and creates no
enabled settings row.

### Scheduling and leases

`OperationalScheduler` runs one bounded cycle and exits. This is designed for
Windows Task Scheduler rather than a permanently running daemon. Supported job
types are:

- Intelligence pipeline
- Notification generation
- Daily digest
- Delivery retry
- Backup
- Database maintenance
- Retention cleanup
- Health check

Every run records its trigger, schedule/start/finish times, status, attempt,
parent retry, safe owner identity, summary, structured counts, safe error and
next retry time.

`OperationalJobLease` provides one unique database-backed lease per job type.
Acquisition is atomic under SQLite. A second session records a safe skipped
run instead of overlapping. Leases include an opaque token, owner and expiry;
long work can refresh them. Expired leases recover automatically and produce
an `abandoned` audit record. No silent force-unlock path exists.

Scheduler defaults are conservative:

- Scheduler disabled
- Digest scheduling disabled
- Backup scheduling disabled
- Maintenance scheduling disabled
- Timezone `Asia/Kolkata`
- Pipeline interval 30 minutes
- Startup catch-up enabled

The operator CLI includes status, enable, disable, run-now, single-cycle,
history, retry, Task Scheduler install preview/application, and removal
preview/application. OS task creation/removal requires `--apply` and explicit
confirmation. The frozen smoke test exercised only the dry-run command; no real
Windows task was created.

### Notification quality

Deterministic presets are constants:

- Quiet: threshold 0.82, increase 0.22, three independent groups, two immediate
  deliveries/hour, digest favored.
- Balanced: threshold 0.70, increase 0.15, two groups, five/hour.
- Breaking news: threshold 0.58, increase 0.10, one group, ten/hour.
- Custom is recorded when alert settings are edited manually.

Preset preview is non-mutating. Applying a preset is idempotent and preserves
muted event types, muted topics and external-delivery state. It never changes
automatic-promotion settings.

Each notification can hold one current useful/not-useful rating with a
controlled reason, optional note, created time and auditable updated time.
Summaries report counts/rates by event type, severity, topic and source, common
not-useful reasons, the active preset and deterministic advisory observations.
Feedback never changes thresholds automatically.

Saved notification views support validated CRUD for state, event types,
severities, topic IDs, bounded date windows, search text, relation metadata and
controlled sort order. Duplicate names are refused. Applying a view does not
change notification state.

### Generic HTTPS webhook

Exactly one real network adapter exists: `generic_https_webhook`.

Configuration is read only from:

- `SEMI_INTEL_WEBHOOK_URL`
- `SEMI_INTEL_WEBHOOK_TOKEN` (optional)
- `SEMI_INTEL_WEBHOOK_TIMEOUT` (optional, clamped to 1–30 seconds)

The URL and token are never stored in ordinary database columns. Only
non-secret adapter state and the redacted host are persisted. Delivery is
disabled by default and requires a configured endpoint, successful synthetic
test (or explicit override), and explicit enable action.

Safety behavior:

- HTTPS required except loopback development
- Embedded URL credentials rejected
- Redirects refused
- Preview is pure and never opens a socket
- Synthetic test contains no candidate intelligence
- Payload fields are bounded and explicitly allow-listed
- Response body limited to 64 KiB
- Query strings, bearer values and endpoint URLs are redacted from errors
- Stable idempotency key/header
- Permanent 4xx failures are not retried
- Transient failures use bounded retries
- Quiet hours, hourly caps and maximum attempts remain authoritative
- Successful delivery is not duplicated
- Old pre-activation notifications are not externally delivered
- Only important/urgent new alerts and generated digests enter the bounded
  external-delivery coordinator
- Adapter failure cannot roll back local generation or prior pipeline work

All HTTP tests used mocked openers. No real webhook was contacted.

### Backups and restore

Backups use SQLite's backup API, never a naïve live-file copy. Each backup has
a unique microsecond timestamp, application-managed filename and adjacent JSON
manifest containing version, schema revision, creation time, non-sensitive
database identity, size, SHA-256, integrity result, table count and important
record counts.

Creation verifies that the database opens, `PRAGMA integrity_check` returns
`ok`, the Alembic revision exists, and core tables are present. A verification
failure preserves the artifact with a `.failed.sqlite3` designation and records
a safe failed result.

Pruning supports age/count retention, previews by default, resolves every
target inside the configured backup directory, and touches only files with the
application-managed prefix. Restore is CLI-only. It:

1. Validates the selected managed backup.
2. Refuses while any operational lease is active.
3. Performs a no-change dry run by default.
4. Requires explicit confirmation for application.
5. Creates a verified safety backup first.
6. Copies to a temporary file and validates it.
7. Uses atomic replacement where supported.

No restore was performed against operator data. Frozen smoke testing created and
verified a backup against a disposable database at head `f9a4c6d8e203`.

### Health and diagnostics

`HealthService` returns controlled `healthy`, `attention_needed`, `degraded`,
`disabled`, or `unknown` component states with a plain-language explanation and
recommended action for every problem. It consolidates scheduler heartbeat and
missed runs, leases, job results, provider incidents, unread/old important
alerts, delivery failures/deferments, adapter state, schema revision, SQLite
integrity, file/WAL sizes, latest verified backup, and safety configuration.

Diagnostics produce a ZIP containing only version/platform metadata,
non-secret settings, recent safe job/provider/incident summaries, health and
table counts. It excludes the database, candidate/article bodies, browser
sessions, cookies, tokens, webhook URLs, authorization headers and environment
secrets. Tests seed secret-like strings and inspect the archive for leakage.

### Interfaces

The operator-friendly `semintel` CLI now exposes:

- `automation status|enable|disable|run-now|cycle|jobs|retry`
- `automation install-task|remove-task`
- `health`
- `backups create|list|verify|prune|restore`
- `diagnostics create`

The detailed `semi-intel notifications` CLI exposes preset list/preview/apply,
feedback, feedback summary, delivery status/preview/test/retry, and saved-view
list/save/delete.

The service-backed FastAPI surface includes scheduler settings/toggle, run-now,
job history/detail/retry, health, presets, feedback, saved views, webhook
status/preview/test/enable/retry, backup list/create/verify/prune, and
diagnostics creation. Restore intentionally remains CLI-only.

The dashboard adds a top-level **Automation & Health** area with health,
automation control, run-now actions, job history, retry, backup visibility,
prune preview, diagnostics and Windows scheduling guidance. **Alerts & Digest**
adds preset preview/application, useful/not-useful actions, saved views, and
clear webhook configuration/test state.

### Frozen smoke results

Both rebuilt executables ran from a disposable local database:

- Reported version `3.3.0`
- Installed/stamped schema head `f9a4c6d8e203`
- Loaded `Asia/Kolkata` timezone data
- Reported scheduler disabled
- Reported collection, X and automatic promotion disabled
- Reported external delivery unconfigured and disabled
- Returned database integrity `ok`
- Listed all three notification presets
- Created and verified a SQLite backup
- Created a diagnostics archive and SHA-256
- Produced a network-free webhook preview
- Produced an absolute Windows Task Scheduler dry-run command
- Created no real OS task and contacted no endpoint

### Known limitations and deferred work

- Windows Task Scheduler command construction and frozen dry run are verified;
  a real task was not created because it was not authorized.
- Manual browser verification was not performed. API, static-control and
  JavaScript tests are the acceptance evidence.
- Backups and restore support local file-backed SQLite only.
- Restore is intentionally not exposed through GUI/API.
- Feedback remains advisory; no automatic threshold adjustment exists.
- There is no daemon, cloud control plane, multi-user authentication or remote
  database support.
- Media downloading, OCR, video transcription, live-X validation, browser
  session changes, internet-wide crawling, LLM summaries, AI-written articles,
  email/vendor-specific delivery, multiple webhook variants, automatic source
  approval, automatic restore, and a new frontend framework remain deferred.

### Recommended Phase 10

Keep Phase 10 narrow: newsroom review analytics and operational polish. Good
candidates are trend charts derived from the existing feedback/job tables,
better saved-view composition, backup restore rehearsal tooling, optional local
Windows notifications, and reduction of legacy timezone/deprecation warnings.
Do not begin media/OCR or broaden collection until this operational checkpoint
has accumulated real-world feedback.
