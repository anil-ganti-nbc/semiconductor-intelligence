# Release Notes

## v0.9.0 — CI Stabilization (2026-08-06)

### Summary

This milestone closes out the CI stabilization effort described in
[HANDOFF_CI_STABILIZATION.md](HANDOFF_CI_STABILIZATION.md). The full test
suite now runs clean under Python 3.13, and the last known intermittent
failure (Signal Candidate Review returning 404 during full-directory runs)
has been isolated and fixed with a test-only change. No production code was
modified to reach this state.

### Major completed work

- **Runtime abstraction** — canonical per-application data directories
  (`%LOCALAPPDATA%\SemiIntel\`, `%LOCALAPPDATA%\OEMRadar\`) with explicit
  precedence for CLI args, environment variables, canonical paths, and
  legacy working-directory fallback. See [docs/RUNTIME_LAYOUT.md](docs/RUNTIME_LAYOUT.md).
- **OEM Radar isolation** — `src/oem_radar/` shares no imports, database, or
  runtime state with `semi_intel/`. Verified independently during this
  milestone (see Task 6 in the reconciliation below).
- **Signal Candidate Review integration** — the FastAPI candidate review
  workflow (mention resolution, promotion, dismissal, snoozing) is covered
  by [tests/test_signal_candidate_review_workflow.py](tests/test_signal_candidate_review_workflow.py)
  and now passes reliably in isolation, in combination with
  `test_lifecycle_bootstrap.py`, and inside the full suite.
- **Canonical runtime paths** — `semi_intel/paths.py` resolves
  `SEMINTEL_DB`, config, and data locations consistently across CLI, web,
  and packaged `.exe` entry points.

### CI stabilization

- **Python 3.13 control environment** — Python 3.14 was found to cause
  late-suite hangs and teardown instability (`_pytest.stash.StashKey`
  errors) and is no longer used for test runs. Python 3.13 (`.venv313`)
  completes full-suite runs without hanging.
- **Signal Review test isolation fix** — root cause: `tests/test_lifecycle_bootstrap.py`
  reloads `semi_intel.web.app` via `importlib.reload()`. `tests/test_signal_candidate_review_workflow.py`
  previously imported `create_app` and `get_session` at module collection
  time, so after a reload elsewhere in the suite its `client` fixture
  registered a FastAPI dependency override against a stale `get_session`
  function object. Requests then fell through to the app's normal (empty)
  database and returned 404 for seeded candidates. Fix: `create_app` and
  `get_session` are now imported lazily inside the `client` fixture, so the
  override always targets the live dependency object. Only
  `tests/test_signal_candidate_review_workflow.py` was changed; no
  production files were touched.

### Current test status

```
754 collected
753 passed
1 skipped
0 failed
```

Full suite: `python -X faulthandler -m pytest tests -q --tb=short --timeout=600 --timeout-method=thread`
Exit code: 0. Duration: ~958s (0:15:57) on Python 3.13.

### Known limitations

- Automatic candidate promotion (as opposed to manual/operator-triggered
  promotion, which is tested and working) is described in
  [docs/ARCHITECTURE_RECONCILIATION.md](docs/ARCHITECTURE_RECONCILIATION.md)
  as not yet bridging entity mentions into fully registered entities at
  scale (5,713 collected signals → 402 clusters → 0 automatic promotions
  as of that snapshot). This has not been re-verified as part of this
  milestone and should not be assumed fixed.
- Several root-level artifacts remain outside the intended layout and are
  flagged as known cleanup debt rather than fixed in this milestone: two
  ~60MB built `.exe` files, a ~57MB `semi_intel.db`, and a `temp_ui.db`
  sit in the repository root rather than `dist/` and `data/` respectively.
  See the Recommendations section of the CI stabilization reconciliation
  for detail.
- The repository has no git history (`git init` has not been run). All
  file moves in this milestone are plain filesystem moves, not tracked
  renames.

### Future roadmap

Not part of this milestone. The longer-term plan is to extract Semi Intel,
OEM Radar, and future collectors (Smartphone Radar, Free Games Tracker,
Chinese Tech Wire) into independent repositories/containers under a larger
automation platform. See the "Long-term context" note in
[docs/ARCHITECTURE_2026-08.md](docs/ARCHITECTURE_2026-08.md) for the
extraction-readiness assessment as of this snapshot.
