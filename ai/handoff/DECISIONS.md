# Decisions — Semiconductor Intelligence cloud migration (soak phase)

1. **This is Tier B: staging/soak only. No production artifact was built.**
   Only `docker-compose.staging.yml` exists; there is no production compose
   and none should be added until Semiconductor Intelligence is explicitly
   promoted past Tier B by someone with authority to do so. Nothing in this
   phase is labeled "production-ready" or bare "staging" (promoted) — every
   surface says "soaking".

2. **`release_channel` defaults to `"soaking"`, never `"staging"` or
   `"production"`.** Read from `SEMINTEL_RELEASE_CHANNEL` in
   `semi_intel/runtime_bridge.py`, matching the sibling Free Game Tracker
   clank's pattern of defaulting to the least-trusted channel so a freshly
   built, never-tested image can't self-report a maturity it hasn't earned.
   An operator would raise this to `"staging"` explicitly only once a real
   promotion happens — not part of this phase.

3. **`identity`/`health`/`version` are new, additive commands on the
   `semi-intel` CLI, not replacements.** `semi_intel/cli.py`'s existing
   commands (entity/source/evidence/claim/ingest/... and everything else)
   keep their exact behavior and output format. `semintel health`
   (`operator.py`'s separate, richer operational report) is untouched —
   the new `semi-intel health` is a distinct, thinner, container-oriented
   contract meant for `docker`'s `HEALTHCHECK` and external drivers, not a
   replacement for the operator tool's own health command.

4. **Reused the existing backup mechanism; wrote no new backup script.**
   Unlike Free Game Tracker (which had no backup tooling and needed one
   built from scratch), this repo already has a verified,
   SQLite-online-backup-API-based, integrity-checked backup/restore/
   rehearse system (`semi_intel/operations/backup.py`, exposed via
   `semintel backup` / `semintel backups {create,list,verify,prune,restore,
   rehearse}`). This phase only wires it up: `scripts/entrypoint.sh`'s
   `backup` verb delegates straight to `semintel backup`, and
   `docker-compose.staging.yml`'s doc comment gives the exact
   `docker compose run --rm semi-intel backup` invocation.

5. **Fixed the Alembic path-resolution portability defect, but touched no
   migration files.** `_project_root()` in `semi_intel/cli.py` needed a
   `SEMINTEL_PROJECT_ROOT` override to find `alembic.ini`/`migrations/`
   after a real `pip install .` — see KNOWN_ISSUES.md. This is the same
   category of fix the sibling Free Game Tracker migration made
   (`alembic_home`), and stays strictly within "path resolution", never
   touching `migrations/versions/*.py`, model definitions, or the schema
   itself, per the brief's explicit constraint on this repo.

6. **No Playwright/Chromium in the image; the `x` extra is left out.** The
   brief was explicit that this is optional and not required for RSS/core
   functionality. Installing Playwright + a browser download would roughly
   triple the image's footprint for a capability this soak-only image
   doesn't need to exercise. Documented as a known limitation, not a
   defect, in KNOWN_ISSUES.md.

7. **Collector/domain/detection logic, database schema, and Alembic
   migrations were not touched anywhere in this phase**, per the brief's
   explicit constraint for this Tier B repo. All changes are packaging/
   portability/runtime-bridge only.

8. **Did not provision any cloud host, did not push any git history.**
   Everything lives on `cloud/semi-intel-soak`, verified only against local
   Docker Desktop with disposable named volumes — never the repo's
   root-level `semi_intel.db`/`semintel.config.json`/`config/` files. No
   `git push` was run (no remote exists for this repo regardless).
