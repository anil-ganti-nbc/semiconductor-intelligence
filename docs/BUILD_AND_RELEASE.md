# Build and Release Procedure

This document is the standard, repeatable procedure for turning a validated
source-mode fix into an updated `semintel.exe` / `semi-intel.exe` in the
live project folder. It exists because on 2026-08-06 the project's root-level
executables were found to be three days stale relative to source — every fix
applied that day silently had zero effect for anyone running the `.exe`
directly, since nobody had rebuilt it. **A source fix is not
operator-delivered until the executable is rebuilt and validated per this
procedure.**

## Authoritative environment

- Python 3.13, tested against `.venv313` for the dev/test loop.
- Executable builds use a **separate, throwaway venv** (`.build_venv`),
  installed fresh each time from `pyproject.toml`'s `web` and `x` extras
  plus `pyinstaller`. This keeps `.venv313` (used for `pytest`) free of
  PyInstaller/Playwright bloat and guarantees the build reflects exactly
  what `pyproject.toml` currently declares, not whatever happens to be
  installed in a long-lived dev venv.
- PyInstaller does not cross-compile — builds must run on Windows to produce
  `.exe` files.

## Authoritative executables

Two executables are built from the same source tree via two `.spec` files:

| Executable | Spec | Entry point | Purpose |
|---|---|---|---|
| `semintel.exe` | `packaging/semintel.spec` | `semi_intel.operator:app` | Operator CLI (`install`/`gui`/`status`/`backup`/...) — the one `START_HERE.md` tells operators to double-click |
| `semi-intel.exe` | `packaging/semi_intel.spec` | `semi_intel.cli:app` | Full detailed CLI + web dashboard (`web serve`, entity/claim CRUD, etc.) |

Both are authoritative — neither is legacy. Both bundle:
- `semi_intel/web/static/*.html` (the dashboard UI — this is what goes stale
  if only the `.py` files are checked before a rebuild)
- `alembic.ini` and `migrations/` (so `install`/`upgrade` work standalone)
- `tzdata` data files
- Playwright's driver/browser bundle and hidden imports, if the `x` extra
  was installed in the build venv (official builds should always include
  it — building without it silently drops X/Twitter collection support)

## Exact build command

```powershell
# From the repository root, on Windows:
python -m venv .build_venv
.build_venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[web,x]" pyinstaller

pyinstaller packaging\semintel.spec   --distpath dist --workpath build --noconfirm
pyinstaller packaging\semi_intel.spec --distpath dist --workpath build --noconfirm
```

`packaging/build_exe.bat` (or `.sh` on Linux/Mac, for building non-Windows
targets of the same source) wraps exactly this sequence with `pause`
statements for interactive double-click use — the commands above are the
same steps run non-interactively, which is preferable when driving the
build from a script or agent.

Output lands in `dist\semintel.exe` and `dist\semi-intel.exe` — **not** the
project root. The root-level `semintel.exe`/`semi-intel.exe` (the files
operators actually double-click, per `START_HERE.md`) are separate copies
and are never touched by the build itself.

## Staging validation (required before replacing the live executable)

1. Do **not** touch the root-level `.exe` files yet.
2. Launch the freshly built `dist\semintel.exe` against a disposable
   database, on a fresh port:
   ```powershell
   $env:SEMI_INTEL_DB_URL = "sqlite:///C:/temp/exe_rebuild_verify.db"
   dist\semintel.exe gui --no-browser --port 8600
   ```
3. In a real browser, hard-reload and verify — at minimum — the specific
   cases that motivated the rebuild (e.g. after the 2026-08-06 batch: Add
   Source classifies `phoronix.com` as `rss` not `x`; Find Feed detects a
   real feed despite a non-fatal `bozo`; Automation's "Run health check"
   creates a real job). Use the browser's network tab, not just an API
   curl, to prove the *served static assets* (not just the backend) are
   current.
4. Kill the staged process, delete the disposable DB.
5. Only after parity passes: copy the two files from `dist\` over the
   root-level executables — no backup step is required beyond this
   document's git-free rollback note below, since PyInstaller output is
   fully reproducible from source.

```powershell
Copy-Item dist\semintel.exe    .\semintel.exe    -Force
Copy-Item dist\semi-intel.exe  .\semi-intel.exe  -Force
```

6. Clean up: `Remove-Item -Recurse -Force .build_venv, build`

## Hash/timestamp verification

Record before and after every rebuild:

```powershell
Get-Item semintel.exe, semi-intel.exe | Select Name, LastWriteTime, Length
```

A rebuild that produces an executable with the same or older timestamp than
the source files it's supposed to include indicates the build did not
actually pick up the intended change — investigate before shipping it.

## Rollback procedure

This repository has no git history in its current checkout (see
`ARCHITECTURE_2026-08.md` §9), so there is no `git revert` safety net for
executables. If a freshly built executable fails staging validation, simply
do not copy it over the root files — the old root-level `.exe` remains
untouched and fully functional (just still carrying whatever defects
motivated the rebuild). There is currently no automated backup of prior
`.exe` builds; if you need to preserve a known-good build before replacing
it, copy it aside manually first (e.g. `Copy-Item semintel.exe
semintel.exe.bak`).

## Disposable-DB parity test (mandatory, every rebuild)

Never validate a build against `semi_intel.db` (the real operator database
in the project root). Every validation pass in this procedure uses
`SEMI_INTEL_DB_URL` pointed at a throwaway SQLite file. Confirm isolation
before any mutating click by checking the effective engine URL and
confirming the disposable file starts empty — see
`docs/UI_ACCEPTANCE_2026-08.md`'s environment section for the exact method,
including the `semintel.config.json` `db_url`-pinning gotcha that overrides
`SEMINTEL_DB`/`SEMINTEL_HOME` unless `SEMI_INTEL_DB_URL` is set explicitly.

## Standard release sequence

For every future accepted production/UI fix batch:

```
source fix
  -> source-mode browser validation (docs/UI_ACCEPTANCE_2026-08.md)
  -> focused tests + full suite green
  -> staged executable rebuild (dist\, this document)
  -> EXE parity validation (staged, disposable DB, fresh port)
  -> replace live project-folder executable
  -> record new executable timestamp/hash
```

Do not consider a fix complete, and do not tell the operator it's fixed,
until the live-folder executable has been rebuilt and validated — a fix
that only exists in source is not yet a fix the operator can use.
