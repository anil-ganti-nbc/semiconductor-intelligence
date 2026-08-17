# Semiconductor Intelligence Platform 3.3.14 — Package Manifest

- Version: `3.3.14`
- Alembic head: `a0b5d7e9f314`
- Focused source-management gate: `53 passed, 0 failed`
- Focused digest/delivery gate: `31 passed, 0 failed`
- Focused automation/health gate: `41 passed, 0 failed`
- Cross-system regression gate: `185 passed, 0 failed`
- Dashboard JavaScript gate: `9 passed, 0 failed`
- Complete authoritative test suite: `548 passed, 0 failed`
- 3.3.12 focused X provider/packaging gate: `12 passed, 0 failed`
- 3.3.13 focused config/error-sanitization/frozen gate: `38 passed, 0 failed`
- 3.3.14 focused scheduler/source/pipeline gate: `50 passed, 0 failed`
- 3.3.14 relevant automation/web/lifecycle gate: `122 passed, 0 failed`
- 3.3.14 final combined-tree suite: `857 passed, 1 skipped, 0 failed`
- Frozen walkthrough: clean install/migration, doctor, verified backup/rehearsal,
  dashboard and repaired APIs/controls passed on a disposable database. The
  3.3.12 frozen X smoke additionally imported Playwright, reused installed
  Chromium 1228, launched it, and completed an empty-session collection cycle.
- Release-copy smoke loaded 80 sources and 350 candidates from the packaged
  relative database, and recent Radar errors contained no command lines or
  machine paths.
- 3.3.14 frozen smoke: clean install/update, offline doctor, native task
  preview, disabled cycle, and production task read-back/result 0 passed.
- `semintel.exe` SHA-256: `AC6ABB8FEE80458BA2918E0FFF0EACD0234C775841B9D6A8558CA9A52017DB81`
- `semi-intel.exe` SHA-256: `945DC79326F43027798E1DD4041A3C7B24F8A2DDFC4A0AD37B75577129E2E7CD`

The sanitized package intentionally omits `semi_intel.db` and
`semintel.config.json`. Archive hashes are supplied alongside the finished ZIP
files because a ZIP cannot contain its own stable hash.
