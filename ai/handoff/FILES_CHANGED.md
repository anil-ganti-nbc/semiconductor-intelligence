# Files changed — cloud/semi-intel-soak, baseline ff25b6e

## Added

- `Dockerfile` — python:3.13-slim-bookworm, non-root (uid 10001), one-shot/CLI
  image (no in-process scheduler), `HEALTHCHECK` via `semi-intel health`
- `.dockerignore`
- `docker-compose.staging.yml` — the only compose file this clank has this
  phase (no production compose exists or was added). Named volume
  `semintel_staging_data`, `restart: "no"`, no ports, no Docker socket.
- `scripts/entrypoint.sh` — routes CLI subcommands to `semi-intel` /
  `semintel`; `backup` delegates to the pre-existing `semintel backup`
- `semi_intel/runtime_bridge.py` — version/identity/health payload
  construction, modeled on the Free Game Tracker clank's pattern (adapted,
  not copied — different app, different data model, different health
  signals)
- `ai/handoff/` (this directory)

## Modified

- `semi_intel/cli.py` (the `semi-intel` console script):
  - Added `version`, `identity`, `health` as new top-level commands
    (additive only — every existing command's behavior/output format is
    unchanged; `semintel health` in `operator.py`, a separate and more
    detailed operational report, is untouched)
  - Fixed a portability defect in `_project_root()`: added a
    `SEMINTEL_PROJECT_ROOT` env override (defaults to the exact prior
    `parent.parent` behavior when unset) so `alembic.ini`/`migrations/` are
    still found after a real, non-editable `pip install .` — see
    KNOWN_ISSUES.md. No migration files, schema, or migration *behavior*
    were changed; this only fixes where the CLI looks for `alembic.ini`.

## Explicitly left unchanged

- All collector/domain/detection logic (`semi_intel/ingestion/*`,
  `semi_intel/signals/*`, `semi_intel/claim_engine/*`,
  `semi_intel/contradiction_engine/*`, `semi_intel/discovery/*`,
  `semi_intel/story_scoring/*`, `semi_intel/graph/*`, etc.)
- Database schema and all 13 Alembic migrations under `migrations/versions/`
  (no revision added, removed, or edited)
- The existing backup/restore mechanism (`semi_intel/operations/backup.py`,
  the `semintel backup` / `semintel backups *` commands) — reused as-is,
  not rewritten
- `semintel` (operator.py) CLI's own `health`/`status`/`doctor`/`update`
  commands and their existing output formats
- The embedded `src/oem_radar` subproject — dead/abandoned fork, not
  touched, not containerized, excluded from the Docker build context
- The optional `x` (Playwright/X-Twitter) extra — not installed in the
  image, see KNOWN_ISSUES.md
