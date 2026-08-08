# CI Stabilization Handoff

## 1. Repository and Environment

- **Repository Path:** c:\Users\anil\Desktop\SemInt Plus X Scrap fork 1
- **Git Available:** NO
- **Backup Path:** c:\Users\anil\Desktop\SemInt Plus X Scrap fork 1
- **Python Executable:** c:\Users\anil\Desktop\SemInt Plus X Scrap fork 1\.venv313\Scripts\python.exe
- **Python Version:** Python 3.13
- **Venv Path:** .venv313
- **Pytest Version:** 8.4.2
- **Loaded Plugins:** None
- **Known Unsupported Environment:** Python 3.14

Python 3.13 completes large test groups and full-suite runs without hanging.
Python 3.14 produced late-suite hangs, teardown instability, and _pytest.stash.StashKey errors after interruption.
Python 3.14 should not be used for further diagnosis.

## 2. Original Problem

- full suite previously hung or failed to terminate under Python 3.14
- multiple Python processes and background resources remained
- individual modules often passed
- interrupted runs produced misleading internal pytest errors
- speculative SQLite lock fixes were attempted and later reverted

## 3. Proven Findings

- Python 3.13 clean control environment works
- 240 interacting CLI/web/lifecycle tests passed cleanly
- one full run collected 754 tests, with 744 passing and 10 ordinary failures
- no hang or deadlock occurred under Python 3.13
- HTTP server fixtures lacked complete shutdown/close handling
- multiple naked TestClient instances did not guarantee lifespan teardown
- speculative .scalars().all() production changes were unsupported and reverted
- shared in-memory SQLite via StaticPool was rejected because it changed production-relevant semantics

## 4. Retained Changes

- `tests/test_fetch.py`: HTTP server lifecycle fix, independently validated
- `tests/test_feedback_review_api.py`: TestClient lifecycle fix, independently validated
- `tests/conftest.py`: HTTP server and engine disposal lifecycle fix, independently validated
- `tests/test_signal_providers.py`: deterministic optional-dependency test, independently validated
- `tests/test_entity_match_workflow.py`: Semantic HTML assertion, independently validated
- `tests/test_source_management_repair.py`: Semantic HTML assertion, independently validated
- `tests/test_signal_candidate_review_workflow.py`: TestClient lifecycle fix, independently validated
- `tests/test_web_session_reuse.py`: HTTP server lifecycle fix, independently validated
- `tests/test_web_radar.py`: Semantic HTML assertion and lifecycle fix, independently validated
- `tests/test_web_operations.py`: Semantic HTML assertion, independently validated
- `tests/test_web_notifications.py`: HTTP server lifecycle fix, independently validated
- `tests/test_automation_health_repair.py`: Semantic HTML assertion and accidental syntax/format repair, independently validated
- `tests/test_lifecycle_core_endpoints.py`: TestClient lifecycle fix, independently validated
- `tests/test_digest_delivery_repair.py`: Accidental syntax/format repair, independently validated
- `tests/test_editorial_web.py`: Semantic HTML assertion, independently validated

## 5. Reverted or Rejected Changes

- .scalars(stmt).all() changes in production modules were reverted
- StaticPool shared in-memory DB change was rejected and reverted
- mass global Python process termination must not be used again
- temporary debug prints were removed
- temporary scripts/logs were deleted

## 6. Current Test State

- latest full-suite collected count: 754
- latest pass count: 744
- latest fail count: 10
- whether the run exited cleanly: YES
- whether the true pytest exit code was captured: YES
- exact remaining failing tests if known: Nine API-oriented Signal Candidate Review tests return 404 only during a full directory-based suite run. 

## 7. Signal Candidate Review Failure

- tests/test_signal_candidate_review_workflow.py passes alone
- it passes with test_cli_radar_promote.py
- it passes with test_signal_analysis.py
- the full test_signal_* grouping passes
- nine API-oriented tests previously returned 404 only during a full directory-based suite run
- the exact predecessor or global-state leak has not been isolated
- module bisection attempts produced contradictory or invalid results
- some bisection scripts incorrectly inferred failure without testing the second half
- one subprocess used the wrong Python interpreter and lacked pytest
- explicit predecessor collection produced 534 tests matching the prefix of the 754-test directory collection
- the final decisive explicit predecessor run was cancelled before producing a result

The Signal Review leak is not isolated.

## 8. Invalidated Hypotheses

- test_cli_radar_promote.py causes the leak: ruled out
- test_signal_analysis.py causes the leak: ruled out
- the full test_signal_* group causes the leak: ruled out
- modules 50–54 individually or together cause the leak: ruled out
- directory discovery necessarily collects in a different prefix order: ruled out for the first 534 items
- SQLite streaming queries are the confirmed cause: unproven
- naked TestClient alone explains all 404 failures: unproven
- ThreadingHTTPServer alone explains all failures: unproven

## 9. Known Diagnostic Mistakes

- using Get-Process python* | Stop-Process -Force
- modifying production code before a minimal reproduction
- changing many fixtures at once
- using Python 3.14 for diagnosis
- interrupting pytest manually and treating resulting StashKey errors as primary
- capturing the wrong exit code through PowerShell pipelines
- writing bisection scripts that infer the untested half failed
- invoking subprocess python instead of sys.executable
- redirecting long runs without preserving observability or exit status
- starting another full-suite run before reviewing the previous result
- leaving temporary debug files in repository root

## 10. Recommended Next Task

Reproduce the Signal Candidate Review 404 failure using a fresh Python 3.13 process while capturing all environment variables, FastAPI dependency overrides, engine URLs, and app identities immediately before the first failing request.

Do not recommend more module bisection until a clean full-suite control run confirms the failure still exists.

Suggested sequence:
1. Run one clean full suite under Python 3.13.
2. Capture the true pytest exit code.
3. If Signal Review still fails, add session-safe diagnostic instrumentation through a temporary pytest plugin or command-line tracing, not by rewriting test files.
4. Compare:
   - engine URL used by the seeding fixture
   - engine URL used by the FastAPI request dependency
   - app object identity
   - dependency override contents
   - relevant environment variables
5. Stop once the first mismatch is observed.

## 11. Commands for the Next Engineer

- `.\.venv313\Scripts\Activate.ps1`
- `pytest tests/test_signal_candidate_review_workflow.py`
- `pytest tests/test_signal_*.py`
- `pytest tests --timeout=600`
- `echo $LASTEXITCODE`
- `python -m py_compile tests/test_signal_candidate_review_workflow.py`
- `Get-ChildItem -Path . -File`
- `Get-ChildItem -Path src, tests -Recurse -File | Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-1) }`

## 12. Safety Boundaries

- no production behavior changes without a minimal reproduction
- no authoritative database access from tests
- no use of Python 3.14
- no shared in-memory SQLite substitution
- no new features
- no dashboard redesign
- no scoring or promotion changes
- no global process termination
- no full-suite reruns in a loop

## 13. Final Status

CI hang: RESOLVED UNDER PYTHON 3.13
Green suite: NOT ACHIEVED
Signal Review full-suite leak: NOT ISOLATED
Production code modified: NO
Safe for a fresh engineer to take over: YES

## 14. Resolution (2026-08-06)

The Signal Review full-suite leak described in §7 was isolated and fixed.

Root cause: `tests/test_lifecycle_bootstrap.py` reloads `semi_intel.web.app`
via `importlib.reload()`. `tests/test_signal_candidate_review_workflow.py`
imported `create_app` and `get_session` at module collection time, so after
a reload elsewhere in the suite its `client` fixture registered a FastAPI
dependency override against the now-stale `get_session` function object.
Requests then used the app's normal (empty) database and returned 404 for
seeded candidates.

Fix: `create_app` and `get_session` are imported lazily inside the `client`
fixture in `tests/test_signal_candidate_review_workflow.py` instead of at
module level. No production files were changed.

Verified:
- `tests/test_lifecycle_bootstrap.py` + `tests/test_signal_candidate_review_workflow.py`: 22 passed, exit code 0
- `tests/test_signal_candidate_review_workflow.py` alone: 12 passed, exit code 0
- Full suite (`pytest tests -q --timeout=600 --timeout-method=thread`): 753 passed, 1 skipped, 0 failed, exit code 0

Updated Final Status: Green suite ACHIEVED. Signal Review full-suite leak
RESOLVED. See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the milestone
writeup.
