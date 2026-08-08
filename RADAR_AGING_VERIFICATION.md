# Semiconductor Intelligence Platform 3.3.10 — Radar Aging Verification

## Scope and safety

This pass adds only read-only Signal Radar age classification, API filters, and
GUI controls. It adds no table or migration and does not change collection,
clustering, scoring, promotion, notifications, scheduling, backup, restore, or
disabled-by-default settings. Alembic remains `a0b5d7e9f314`.

The supplied populated operator database is treated as read-only. Frozen-app
acceptance uses a disposable database and deterministic local fixtures.

## Implemented policy

- Current is inclusive at the exact boundary: a candidate becomes Older only
  after more than the selected number of full days without meaningful activity.
- For each independence group, its earliest member publication/observation is
  its first meaningful activity. The candidate activity clock uses the newest
  of those group-first timestamps.
- `posted_at` takes precedence over `collected_at`. Collection time is a
  disclosed last-resort fallback.
- A later member of the same independence group cannot refresh activity. A new
  independent group can. Resurfacing is reported only when distinct group-first
  timestamps contain a gap longer than the selected window.
- Missing derived independence groups trigger a disclosed conservative fallback
  that treats all candidate members as one group.

## Automated verification

- Focused age service, API, GUI, and JavaScript checks: **22 passed**.
- Radar/clustering/source-independence regressions: **130 passed**.
- Newsroom/editorial/lifecycle regressions: **67 passed**.
- Complete authoritative suite: **519 passed, 0 failed**.
- Existing `datetime.utcnow()` deprecation and Starlette warnings remain out of
  scope.

## Frozen verification

Both executable entry points returned clean help output. The first frozen web
smoke exposed a packaging-only omission: AnyIO's dynamically imported asyncio
backend was absent from both PyInstaller specs. Both specs now include the
backend explicitly, a focused packaging regression check passed, and both
executables were rebuilt before acceptance continued.

The corrected `semi-intel.exe` was exercised against a disposable database with
five deterministic candidates:

- A one-day-old report appeared in Current.
- An eight-day-old report appeared only in Older/All ages.
- A fourteen-day-old report collected today remained Older.
- A current duplicate placed in the old report's existing independence group
  did not refresh that candidate.
- A current report in a new independent group resurfaced a twenty-day-old
  candidate and produced the accurate Resurfaced indicator.
- Current/Older/All ages, seven-day selection, invalid-window validation, and
  both Radar and Editorial Inbox GUI control sets were present and correct.
- Marking the recent candidate seen removed it from Current + Unseen without
  changing its age classification.
- After stopping and restarting the frozen executable, the Current/Older split,
  Resurfaced result, and seen state remained intact.

No browser-automation loop was used; direct frozen API checks and the already
passed independent JavaScript syntax check were the primary verification.
