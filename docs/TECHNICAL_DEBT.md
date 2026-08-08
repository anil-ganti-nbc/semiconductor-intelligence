# Technical Debt Register

## Critical
**1. Dead-End Signal Radar Promotion (Operational)**
- **Description**: The pipeline effectively clusters 5,713 items but registers 0 promotions. Missing candidate-to-entity review bridge prevents editorial utility.
- **Action**: Next milestone.

**2. Secret Hygiene (Security)**
- **Description**: Operator-specific runtime secrets (`x_session.json`, `config/discord_webhook.txt`) reside alongside source and within `.x_browser_profile/` in the root tree, risking inclusion in Git or release distributions.
- **Action**: Immediate. Relocate to an explicit operator user-data directory or strictly `.gitignore`.

**3. Application Co-location (Architectural)**
- **Description**: `src/oem_radar` and `semi_intel` share a repository without integration or coupled packaging. OEM radar is not defined in `pyproject.toml`.
- **Action**: Future. Extract OEM Radar into a canonical separate repository.

## High
**1. Duplicate Release Artifacts (Release engineering)**
- **Description**: Multiple large distributable EXEs (`semi-intel.exe`, `semintel.exe`) and overlapping `.cmd`/`.vbs` launchers live actively inside source. 
- **Action**: Immediate. Establish distinct `/dist` isolation boundaries.

**2. Asyncio Test Suite Hang (CI)**
- **Description**: Running `pytest tests` causes hanging event loops/worker threads (suspected Playwright loop lock or background daemon thread blocking teardown). Test counts display 735 collected items but CI is essentially blocked.
- **Action**: Immediate. Standardize loop scopes via pytest-asyncio and verify fixture teardowns for `TestClient`/`asyncio.run()`.

## Medium
**1. Diagnostic/Operator Leakage (Security)**
- **Description**: Operator reports in `diagnostics/semi-intel-diagnostics*.zip` sit redundantly in root.
- **Action**: Next milestone. Move export routines outside of project root.

**2. Duplicate Entry Points (Architectural)**
- **Description**: `semi-intel` vs `semintel` command-line interfaces fragment operator usage pathways.
- **Action**: Next milestone. Consolidate to `semintel` and document `semi-intel` as fully legacy.

## Low
**1. Obsolete Documentation (Documentation)**
- **Description**: Documentation mentions legacy 70+ tests passing correctly (e.g. `HANDOFF.md`, `PHASE0_AUDIT.md`), but tests hang without executing correctly under current dependencies. 
- **Action**: Future.
