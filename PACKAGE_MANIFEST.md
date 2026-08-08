# Semiconductor Intelligence Platform 3.3.13 — Package Manifest

- Version: `3.3.13`
- Alembic head: `a0b5d7e9f314`
- Focused source-management gate: `53 passed, 0 failed`
- Focused digest/delivery gate: `31 passed, 0 failed`
- Focused automation/health gate: `41 passed, 0 failed`
- Cross-system regression gate: `185 passed, 0 failed`
- Dashboard JavaScript gate: `9 passed, 0 failed`
- Complete authoritative test suite: `548 passed, 0 failed`
- 3.3.12 focused X provider/packaging gate: `12 passed, 0 failed`
- 3.3.13 focused config/error-sanitization/frozen gate: `38 passed, 0 failed`
- Frozen walkthrough: clean install/migration, doctor, verified backup/rehearsal,
  dashboard and repaired APIs/controls passed on a disposable database. The
  3.3.12 frozen X smoke additionally imported Playwright, reused installed
  Chromium 1228, launched it, and completed an empty-session collection cycle.
- Release-copy smoke loaded 80 sources and 350 candidates from the packaged
  relative database, and recent Radar errors contained no command lines or
  machine paths.
- `semintel.exe` SHA-256: `38F6D1E1D865F4B4463D37304F3AA08581E6B826D48B9ED3CF18F85F7FA4B73C`
- `semi-intel.exe` SHA-256: `8585F951D8F6798B55D543909E0C5AD95F5D66E547BF0EFFFE93C45C98CA46B6`
- Private populated `semi_intel.db` SHA-256: `CC939019B5EA2B24BB425AE00B14391DA7E55BBBCC7009316DDF72BD35F60F65`

The sanitized package intentionally omits `semi_intel.db` and
`semintel.config.json`. Archive hashes are supplied alongside the finished ZIP
files because a ZIP cannot contain its own stable hash.
