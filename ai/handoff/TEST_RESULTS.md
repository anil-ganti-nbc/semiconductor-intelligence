# Test results — Semiconductor Intelligence cloud migration (soak phase)

## Native (Windows, `.venv313`, Python 3.13), before any change

Command: `PYTHONPATH=src .venv313/Scripts/python.exe -m pytest -q`

Without `PYTHONPATH=src` set at all, collection aborts with 21 collection
errors (missing `oem_radar` imports from the embedded, dead `src/oem_radar`
subproject that several test files still reference). With `PYTHONPATH=src`
set, that drops to 5 remaining collection errors, all pre-existing and
unrelated to this phase's changes — see KNOWN_ISSUES.md for the root cause
(a bare `from test_models import ...` / `from engine_harness import ...` in
five test files that needs `tests/` itself on `sys.path`, not just the repo
root, given `tests/__init__.py` makes `tests/` a package).

Using `--continue-on-collection-errors` to get real pass/fail counts past
those 5 pre-existing errors, run **before any code change on this branch**
(kicked off immediately after `git checkout -b cloud/semi-intel-soak`, prior
to writing `runtime_bridge.py` or touching `cli.py`):

```
4 failed, 791 passed, 1 skipped, 36416 warnings, 5 errors in 1385.75s (0:23:05)
```

- Collection errors: 5 — `tests/test_diff.py`, `tests/test_discord.py`,
  `tests/test_feedback_review_api.py`, `tests/test_review_now_list.py`,
  `tests/test_sqlite_store.py`. Pre-existing (see KNOWN_ISSUES.md); not
  fixed per this phase's scope — "record pass/fail counts, do not fix
  failing tests".
- Failed: 4, all pre-existing and unrelated to this phase's changes:
  - `tests/test_parser_fixes.py::test_ser9_and_ser9_pro_do_not_collide` and
    `::test_sku_match_resolves_rename` — same root cause as the 5 collection
    errors above (`ModuleNotFoundError: No module named 'test_models'`,
    called lazily inside the test body rather than at module import time,
    so the module collects but these two tests fail at run time).
  - `tests/test_web_notifications.py::test_clean_status_and_dashboard` and
    `::test_notification_lifecycle` — both fail on
    `assert body["delivery"]["external_adapter_available"] is False`
    (actual: `True`). Root cause: `config/discord_webhook.txt` exists as an
    untracked local file at the repo root (not part of any commit — see
    `.gitignore` and DECISIONS.md/the brief's note that the webhook hygiene
    issue was already fixed in git history) and `semi_intel/paths.py`'s
    legacy-path fallback picks it up from the current working directory
    when tests run from the repo root, making the webhook adapter appear
    "configured" when the test expects a clean/unconfigured environment.
    This is local test-environment leakage from an untracked file, not a
    code defect this phase touched or should fix.
- Passed: 791
- Skipped: 1
- Duration: 1385.75s (~23 minutes)

## Native (Windows, `.venv313`), after this phase's code changes

Same command, re-run after `semi_intel/runtime_bridge.py`,
`semi_intel/cli.py`'s additive `version`/`identity`/`health` commands, and
the `_project_root()` `SEMINTEL_PROJECT_ROOT` portability fix were committed.

```
4 failed, 791 passed, 1 skipped, 36416 warnings, 5 errors in 1196.22s (0:19:56)
```

Identical outcome to the baseline: same 5 collection errors, the same 4
failures (same two root causes as documented above), same 791 passed, same
1 skipped. **No regressions** — expected, since no product/collector/domain
code was touched, only a new module (`runtime_bridge.py`) and additive CLI
commands plus one env-var-gated path override (`SEMINTEL_PROJECT_ROOT`)
that defaults to the exact prior behavior when unset.

## In-container (Linux AMD64, Docker Desktop)

Not run via pytest inside the image (no dev/test dependencies installed in
the image by design — keeps it lightweight, matching the brief's
lean-image intent). Verified instead via direct CLI invocation and
`docker compose run`, all against disposable named volumes, never the
repo's root-level `semi_intel.db`:

- `id` inside the container → `uid=10001(clank) gid=10001(clank)
  groups=10001(clank)` — confirmed non-root
- `semi-intel version` → `semi-intel 3.3.13`
- `semi-intel identity` → valid JSON, `"release_channel": "soaking"`
  (not hard-coded, not "production")
- `semi-intel health` on a container with no volume mounted → `"operational_state":
  "degraded"`, truthful `status_reasons: ["database file missing: ...", "no
  operational job runs recorded yet"]` — not fabricated "healthy", exit 0
  (degraded is not a failure exit)
- `semi-intel db upgrade` against a fresh isolated named volume → `Upgraded
  to head.` — confirms the `SEMINTEL_PROJECT_ROOT` portability fix works;
  this failed before the fix would have been added (site-packages install
  can't find `alembic.ini` via the old `parent.parent` calculation)
- `semi-intel db current` → correctly reports head `c2a7f1e9b453` (see
  KNOWN_ISSUES.md re: this vs. the prior audit's stated head)
- `semi-intel health` after `db upgrade` → `"operational_state": "healthy"`,
  truthful `"status_reasons": ["no operational job runs recorded yet"]`
  (an empty-but-schema-correct DB is legitimately healthy, not "no data =
  failure")
- `semi-intel source add` + `semi-intel source list` (same container) →
  round-trips correctly
- **Persistence across recreation**: `docker run --rm` a fresh container
  instance against the same named volume → `semi-intel source list` shows
  the same source added by the previous (now-removed) container instance;
  `semi-intel health` reports `"source_count": 1` — proves state survives
  container recreation, not just within one running container
- `docker compose -f docker-compose.staging.yml config` → validates; no
  `ports`, no Docker socket mount, `restart: 'no'` present, named volume
  `semintel_staging_data`
- `docker compose run --rm semi-intel identity` / `health` / `db upgrade` /
  `backup` → all work identically through compose as through plain
  `docker run`
- **Backup drill**: `docker compose run --rm semi-intel backup` (delegates
  to the pre-existing `semintel backup`) → `Verified backup created:
  /app/data/backups/semi-intel-backup-<timestamp>.sqlite3` with a SHA-256
  and an accompanying `.manifest.json`
- **Rehearsal**: `semintel backups rehearse <path>` → `Rehearsal passed`,
  correct stamped Alembic revision and ORM record counts, all against a
  throwaway temp copy — never touches the live database
- **Full restore drill**: wrote state, took a backup, wrote more state,
  ran `semintel backups restore <path> --apply --yes` (creates its own
  safety backup first) → post-backup state gone, pre-backup state restored;
  `semi-intel health` against the restored volume → `"healthy"` with the
  correct record count

## Not run

- Load/soak testing over real elapsed time — this phase is a portability +
  local-verification pass, not a live soak; `scheduler_verified: false` and
  `notifications_verified: false` in the frontmatter reflect that
  accurately, not an oversight
- Any test against a real external network target (Discord webhook,
  Windows desktop notifications, live X/Twitter session) — out of scope;
  the `x` extra isn't even installed in this image (see KNOWN_ISSUES.md)
