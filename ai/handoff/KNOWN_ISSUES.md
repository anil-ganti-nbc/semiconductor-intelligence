# Known issues — Semiconductor Intelligence cloud migration (soak phase)

## Found and fixed this phase

**Portability defect: Alembic path resolution broke under a real `pip install`.**
`semi_intel/cli.py`'s `_project_root()` computed
`Path(__file__).resolve().parent.parent`, which assumed the package always
runs from an editable/source checkout, where `semi_intel/` sits directly
under `alembic.ini`/`migrations/`. A non-editable `pip install .` (what the
container does) moves the *package* into `site-packages` while
`alembic.ini`/`migrations/` — project files, not part of the package — stay
wherever they were copied. `semi-intel db upgrade` then looked for
`alembic.ini` inside `site-packages` and would have failed. Fixed with a new
`SEMINTEL_PROJECT_ROOT` env override (default unchanged, so native/Windows/
dev/PyInstaller behavior is identical), set to `/app` in the Dockerfile.
Verified in-container: `semi-intel db upgrade` correctly finds and applies
all migrations to head. Classified as a packaging/portability defect per the
brief's framework, not a schema or migration behavior change — no migration
files were added, removed, or altered.

## Observed, not a defect

**Alembic head differs from the prior audit's stated head.** The read-only
audit that produced this task's brief stated the head revision as
`a0b5d7e9f314_windows_desktop_notifications`. The actual head in this
checkout (confirmed via `semi-intel db current` inside the built container)
is `c2a7f1e9b453_candidate_intelligence`, which revises
`a0b5d7e9f314`. This is a one-migration discrepancy from the prior audit's
snapshot, not something this phase caused or needs to resolve — no
migration was touched, and `db upgrade` correctly walks the full chain to
whatever head is actually present.

## Pre-existing, documented, intentionally not touched

- The optional `x` extra (Playwright-based X/Twitter signal collection,
  `semi_intel/signals/providers/x/*`) is **not installed** in the container
  image. This is a deliberate scope decision per the brief, not a defect:
  installing Playwright + a Chromium download would make this a much
  heavier image for a Tier B soak-only container, and RSS/pci.ids ingestion,
  the claim/evidence/graph engine, notifications, and backups all work
  without it. **Known limitation: X-ingestion will not function in this
  container.** `radar collect --provider x` and anything depending on
  `x_provider_enabled` will fail cleanly with the existing "extra not
  installed" style error — that error path is pre-existing, not new.
- The embedded `src/oem_radar` subproject is a dead/abandoned fork (per the
  prior audit). Not copied into the image, not containerized, excluded via
  `.dockerignore`. `PYTHONPATH=src` is still needed for the *native* test
  suite to import it, but that is a test-collection concern only, unrelated
  to the container.
- `dist/`, `*.exe` (PyInstaller-built `semintel.exe`/`semi-intel.exe`),
  `packaging/` — Windows standalone-executable packaging path, untouched
  and not part of the container image.
- Two console scripts with overlapping-sounding but distinct `health`
  commands now exist: `semi-intel health` (new, thin runtime-bridge
  contract, this phase) and `semintel health` (pre-existing, richer
  operator-facing report from `semi_intel/operations/health.py`). Both are
  intentional and neither replaces the other.

## Operational note (not a defect)

On a brand-new, empty volume, `docker compose run --rm semi-intel backup`
will fail verification (`Backup is missing core tables: alembic_version`)
if the schema was only ever created via a bare `Base.metadata.create_all()`
path (e.g. `semi-intel health`'s own lazy table-creation, or `init-db`)
rather than through Alembic (`semi-intel db upgrade`, or `semintel
install`). This is pre-existing `BackupService` behavior (it requires
`alembic_version` to be present, by design, so a restored backup always
carries a known schema revision) and is not something this phase changed.
Operationally: always run `semi-intel db upgrade` (or `semintel install`)
against a fresh volume before the first backup — confirmed working
end-to-end in that order (see ROLLBACK.md's backup/restore drill).

## Test suite (pre-existing, not fixed here — see TEST_RESULTS.md)

- Five test modules (`tests/test_diff.py`, `tests/test_discord.py`,
  `tests/test_feedback_review_api.py`, `tests/test_review_now_list.py`,
  `tests/test_sqlite_store.py`) fail to *collect* even with
  `PYTHONPATH=src` set, with `ModuleNotFoundError: No module named
  'test_models'` (or `'engine_harness'`). Root cause: `tests/__init__.py`
  exists (making `tests/` a package), so pytest's default "prepend" import
  mode inserts the *repository root* onto `sys.path`, not `tests/` itself —
  but these five files do a bare `from test_models import make_product` /
  `from engine_harness import EngineHarness`, which requires `tests/` itself
  to be on `sys.path`. This is a pre-existing test-infrastructure issue
  unrelated to Docker portability; not touched per this phase's scope (no
  collector/domain/test logic was to be fixed, only recorded). See
  TEST_RESULTS.md for exact before/after counts using
  `--continue-on-collection-errors`.

## Explicitly deferred (Tier B: soak only, not applicable / not authorized)

- No cloud host has been provisioned — this phase is local Docker
  Desktop verification only, matching the brief's staged gates.
- Scheduler verification (`pipeline loop` / `automation cycle` running
  unattended over real elapsed time) — out of scope; `scheduler_verified:
  false` in the frontmatter is intentional, not an oversight.
- Notification delivery from a real network (Windows desktop notifications,
  webhook delivery) — `notifications_verified: false` is intentional;
  nothing here exercised a real external notification target.
- Any production compose file, promotion past `soaking`, or public exposure
  — explicitly out of scope per Tier B policy; not built.
