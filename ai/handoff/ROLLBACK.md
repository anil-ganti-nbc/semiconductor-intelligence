# Rollback — Semiconductor Intelligence cloud migration (soak phase)

## Code rollback

Everything in this phase lives on `cloud/semi-intel-soak`, three commits
ahead of the `master` baseline:

```
ff25b6e  Baseline commit: current state of Semiconductor Intelligence Platform before cloud migration
c355fab  Add cloud-migration runtime bridge (version/identity/health)
6b34977  CLI: additive identity/health/version commands + alembic path fix
c560e07  Add Docker packaging for staging/soak (Tier B) verification
```

To fully roll back the code: `git checkout master` (or reset the branch to
`ff25b6e`). Every native entry point (`semi-intel`, `semintel`,
`semi-intel.exe`, `semintel.exe`, the `.cmd`/`.vbs` launchers) was left
untouched by this phase except for the two additive changes described
below, so native Windows operation is unaffected regardless of which branch
is checked out.

To roll back only the riskier of the two `semi_intel/cli.py` changes (the
`SEMINTEL_PROJECT_ROOT` override in `_project_root()`) while keeping the
new `identity`/`health`/`version` commands: revert commit `6b34977`
specifically and re-apply just the command additions — but note the
override is inert (falls back to prior behavior) whenever
`SEMINTEL_PROJECT_ROOT` is unset, so there is normally no reason to.

## Image rollback (once a real deploy exists)

No image was pushed to any registry — the only tag built and verified this
phase is the local, disposable `semi-intel:soak-local`. Any future deploy
should tag every image with an immutable commit SHA before it's ever run
anywhere beyond a local soak check.

## State rollback

The database is untouched by any code rollback — `semintel_staging_data`
(or whatever named volume an eventual soak deployment uses) is independent
of the image. No schema changes were made this phase, so there is no
schema-incompatibility scenario to plan around yet. If one is ever
introduced later:
1. Stop whatever is driving the container (there is no in-process scheduler
   to disable in this image).
2. Run `docker compose -f docker-compose.staging.yml run --rm semi-intel backup`
   if a current backup doesn't already exist.
3. Roll back to the previous image tag.
4. Verify `semi-intel health` / `semintel status` against the existing
   volume before resuming.

## Backup / restore drill (proven this phase)

Verified end-to-end against disposable named volumes (never the repo's
root-level `semi_intel.db`):

1. `docker compose run --rm semi-intel backup` (delegates to
   `semintel backup`) produced a verified, integrity-checked
   `.sqlite3` + `.manifest.json` pair under `/app/data/backups/` on the
   volume.
2. `semintel backups rehearse <path>` — copies the backup to a throwaway
   temp file, opens it with a real SQLAlchemy engine, and queries it
   through the actual ORM models, all without ever touching the live
   database or session. Passed: reported the correct stamped Alembic
   revision and ORM record counts.
3. A full restore drill: wrote state (`source add`), took a backup, wrote
   more state, then ran `semintel backups restore <path> --apply --yes`
   (which creates its own safety backup first). The post-backup state was
   gone and the pre-backup state was back — confirming the restore
   actually replaces live data with the backup's contents, not a no-op.
4. `semi-intel health` against the restored volume reported `"healthy"`
   with the correct record count, not a fabricated status.
