# Architecture Reconciliation

## Authoritative Runtime

### Semi Intel Platform
- **Python executable**: `python` or `py -3` (discovered by wrappers like `dashboard.cmd`)
- **Working directory**: Repository root (e.g., `cd /d "%~dp0"`)
- **Entry points**: 
  - `semi_intel.cli:app` (`semi-intel`) - Legacy/alternate
  - `semi_intel.operator:app` (`semintel`) - Primary Operator CLI
- **Database**: `semi_intel.db` (SQLite)
- **Configuration**: `semintel.config.json`
- **Migration head**: `a0b5d7e9f314`
- **Scheduler**: `semi_intel.operations.scheduler.OperationalScheduler` using `scheduler_job_leases` table.
- **Dashboard**: `semi_intel/web/app.py` (FastAPI/Uvicorn)
- **Notification path**: `semi_intel/notifications/service.py` -> Discord Adapter.

### OEM Radar
- **Entry points**: `src/oem_radar/cli.py` (`python -m oem_radar.cli`)
- **Database**: `data/radar.db` (does not exist by default, but targeted)
- **Configuration**: `config/radar.yaml` and `config/oems/*.yaml`
- **Dashboard**: `oem_radar.cli dashboard`

## Application Boundaries
Semi Intel (`semi_intel/`) and OEM Radar (`src/oem_radar/`) span identical directories but operate **independently**.
- **Cross-imports**: Zero imports or runtime boundary calls exist between `semi_intel/` and `src/oem_radar/`. 
- **Dependencies**: Disjoint packaging rules (`semi_intel` built exclusively by `pyproject.toml`).
- **Future Extraction Readiness**: Trivial. `src/oem_radar/` shares no state or code, allowing a clean git subtree extract and relocation. OEM Radar should be an independent standalone package.

## Signal Radar Lifecycle
- **RSS -> Signal -> Normalization**: Completed synchronously per feed.
- **Entity detection & Clustering**: Creates `source_candidates` successfully. Accumulates thousands of signals.
- **Candidate -> Promotion**: Broken logic. 5,713 collected items -> 402 clusters -> 0 promotions. The editorial workflow lacks the deterministic bridge to transition candidate mentions into fully registered `Entities`. The system functionally accumulates posts with high false-negative risks because there's no working editorial promotion gateway. 

## X Lifecycle
Fully robust, external, optional integration.
- **Session Handling**: BrowserSession drives Playwright asynchronously.
- **TimelineInterceptor**: Captures GraphQL timelines on scroll.
- **HTML Fallback**: Explicit fallback to low-fidelity DOM reading if GraphQL schema changes, explicitly labeling confidence drops.
- **Failure Handling**: Handles expiration by safely suppressing failures via `ProviderUnavailable`, skipping unauthenticated sources instead of crashing.

## Database & Configuration Ownership
- **Semi Intel**: Owns Alembic schemas, SQLite operations on `semi_intel.db`, and `semintel.config.json`. 
- **OEM Radar**: Relies entirely on external generic SQLite/JSON storage (`data/radar.db`) driven by YAML configurations.

## Secret Ownership & Release Ownership
Currently heavily entangled in the working directory. Secrets, operator data, development assets, and distributable `.exe` files sit redundantly in root.
