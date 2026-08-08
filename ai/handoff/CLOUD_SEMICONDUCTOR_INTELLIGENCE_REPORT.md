```yaml
project: semiconductor-intelligence
stage: cloud-migration-tier-b-soak (portability + local verification)
baseline_commit: ff25b6e
branch: cloud/semi-intel-soak
target_environment: Linux AMD64 Docker (host TBD — no cloud host provisioned yet; Tier B is staging/soak only, never production)
image_digest: not pushed anywhere; local verification used disposable tag semi-intel:soak-local, never pushed
release_channel: soaking (or staging when explicitly promoted)
operational_state: healthy (verified locally against isolated named volumes)
docker_build_verified: true
container_contracts_verified: true
persistent_state_verified: true
scheduler_verified: false
notifications_verified: false
backup_verified: true
restore_verified: true
tests_passed: 791 (before and after this phase's changes — identical, no regressions; see TEST_RESULTS.md)
tests_failed: 4 (before and after — all 4 pre-existing/environmental, not caused by this phase; see TEST_RESULTS.md and KNOWN_ISSUES.md)
contracts_changed: false
schema_changed: false
architecture_deviations: none
known_product_defects: none found or introduced this phase (see KNOWN_ISSUES.md for pre-existing test-collection issue, not a product defect)
known_portability_defects: one found and fixed — Alembic path resolution under a real `pip install .` (see KNOWN_ISSUES.md and DECISIONS.md)
review_required: true
```

## What this phase covered

Portability and local Docker verification only, for Semiconductor Intelligence
as a **Tier B: staging/soak-only** clank. No cloud host has been provisioned,
no production compose file exists or was added, and nothing here is labeled
"production-ready" — every surface says "soaking". Collector/domain/detection
logic, the database schema, and all Alembic migrations were explicitly out of
scope and were not touched.

## What was verified, and how

All verification ran locally against Docker Desktop (Windows host, linux/amd64
target), using disposable named volumes and a locally-tagged image
(`semi-intel:soak-local`, never pushed anywhere) — never the repository's own
root-level `semi_intel.db`, `semintel.config.json`, or `config/` files.

| Check | Result |
|---|---|
| `docker build --platform linux/amd64` | succeeds reproducibly; final image 278MB (no Playwright/Chromium — the `x` extra is deliberately excluded, see KNOWN_ISSUES.md) |
| Non-root execution | `id` inside container: `uid=10001(clank) gid=10001(clank)` |
| `semi-intel version` in container | `semi-intel 3.3.13` |
| `semi-intel identity` in container | valid JSON; `release_channel: "soaking"` (not hard-coded, not "production") |
| `semi-intel health` on a fresh/empty volume | `operational_state: "degraded"`, honest `status_reasons: ["database file missing...", "no operational job runs recorded yet"]` — not fabricated "healthy" |
| `semi-intel db upgrade` against an isolated volume | `Upgraded to head.` — proves the Alembic path-resolution portability fix works |
| `semi-intel health` after `db upgrade` | `operational_state: "healthy"`, truthful (empty-but-schema-correct DB is legitimately healthy) |
| Write state (`source add`) + persistence across container recreation | a fresh container instance against the same named volume reads back the identical source row; `health`'s `source_count` matches |
| `docker compose -f docker-compose.staging.yml config` | validates; no ports published, no Docker socket mounted, `restart: "no"` present |
| `docker compose run --rm semi-intel backup` | delegates to the pre-existing, already-verified `semintel backup`; produced a verified `.sqlite3` + `.manifest.json` with a SHA-256 |
| `semintel backups rehearse <path>` | passed — correct stamped Alembic revision and ORM record counts, against a throwaway copy, never touching the live database |
| Full restore drill | wrote state → backup → wrote more state → `semintel backups restore --apply --yes` → post-backup state gone, pre-backup state restored, `health` reports the restored volume as `healthy` |
| Native test suite (`PYTHONPATH=src`, `.venv313`, Python 3.13), before | `791 passed, 4 failed, 1 skipped, 5 collection errors` in 1385.75s |
| Native test suite, after this phase's changes | `791 passed, 4 failed, 1 skipped, 5 collection errors` in 1196.22s — **identical, no regressions** |

## Notable observation, not a defect

`semi-intel db current` inside the built container reports head revision
`c2a7f1e9b453_candidate_intelligence`, one revision past
`a0b5d7e9f314_windows_desktop_notifications`, which the prior read-only audit
had stated as head. This phase did not add, remove, or alter any migration —
`db upgrade` simply walks whatever chain is actually present in
`migrations/versions/`. Documented in KNOWN_ISSUES.md as a discrepancy from
the prior audit's snapshot, not something this phase caused.

## What still blocks any future promotion past Tier B

1. **Cloud host decision** — no provider/size/cost has been approved. Nothing
   in this phase provisions one; Tier B soak verification is entirely local.
2. **Scheduler / notifications verification over real elapsed time** —
   deliberately not attempted (`scheduler_verified: false`,
   `notifications_verified: false`). This image has no in-process scheduler;
   `semi-intel pipeline loop` / `semintel automation cycle` exist but running
   them unattended against a real clock was not part of this phase's scope.
3. **The optional `x` (Playwright/X-Twitter) extra is not installed** — a
   deliberate scope decision, not a defect (see KNOWN_ISSUES.md). X-ingestion
   will not function in this image.
4. **Explicit promotion approval** — per the brief, passing checks never
   self-authorizes anything past `soaking`. `SEMINTEL_RELEASE_CHANNEL` stays
   `soaking` until an operator with authority says otherwise.
