# Unattended collection (service model)

OEM Radar uses **stateless one-shot runs**, not an in-process daemon
(see **ADR-1** in `ARCHITECTURE.md`).

## What automation already exists

| Layer | Mechanism | Role |
|-------|-----------|------|
| CLI | `oem-radar run` | One catch-up cycle: due sources → snapshot → diff → notify |
| Per-source gating | `min_interval` on each source | Skip sources crawled too recently inside a cycle |
| Single-instance lock | `data/oem-radar.lock` | Refuse a second concurrent `run` against the same DB |
| Windows | Task Scheduler via `install-hourly-task.cmd` | Invokes `crawl-silent.vbs` → `crawl-hourly.cmd` → `oem-radar run` hourly |
| Linux | systemd timer or cron (samples in `deploy/`) | Same one-shot CLI on a schedule |

There is **no** long-running `while True` service process in the Python package.
Cadence comes from **how often the OS invokes** `oem-radar run`.

### Why not an in-process daemon?

- A desktop machine sleeps; a daemon that is not running detects nothing.
- Catch-up semantics mean a missed hour is recovered on the next invocation.
- OS schedulers already provide restart, logging, and “do not start a new instance.”
- ADR-1 keeps the door open: the same CLI is what a VPS cron would call.

## Manual one-shot

```bash
oem-radar run
oem-radar run --force                  # ignore min_interval
oem-radar run --source gmktec-shopify
oem-radar run --dry-run                # no lock, in-memory store
```

## Windows (existing)

1. Double-click **`install-hourly-task.cmd`** once.
2. Task name: `OEM Radar Hourly Crawl`
3. Action: `wscript.exe …\crawl-silent.vbs` (hidden window) → `crawl-hourly.cmd` → `python -m oem_radar.cli run`
4. Log: `data\crawl-runs.log`
5. Remove: `uninstall-hourly-task.cmd`
6. Force now: `schtasks /run /tn "OEM Radar Hourly Crawl"`

Task Scheduler’s own “do not start a new instance if running” plus
`run_lock_path` both protect against overlap.

## Linux

### systemd timer (preferred)

```bash
# Edit paths in the example files first
sudo cp deploy/oem-radar-run.service.example /etc/systemd/system/oem-radar-run.service
sudo cp deploy/oem-radar-run.timer.example  /etc/systemd/system/oem-radar-run.timer
sudo systemctl daemon-reload
sudo systemctl enable --now oem-radar-run.timer
systemctl list-timers | grep oem-radar
journalctl -u oem-radar-run.service -n 50
```

### cron

See `deploy/crontab.example`.

## Single-instance lock

- Config: `run_lock_path: data/oem-radar.lock` in `radar.yaml`
- Acquired at the start of a non-dry-run `oem-radar run`
- Contents: JSON `{pid, started_at, started_at_iso}`
- If PID is alive → exit code 2, clear error message
- If PID is dead → stale lock removed and reclaimed
- If liveness is unknown → refuse (never steal blindly)
- Released in a `finally` block
- Escape hatch: `oem-radar run --no-lock` (not recommended)

### Recovery after unclean shutdown

If a crawl is killed hard, the lock file may remain. On the next start the
stale-PID check removes it automatically. If a message says liveness could not
be determined, confirm no crawl is running, then delete the lock file manually.

## Scheduling semantics

OS schedules the **invocation**. Inside each invocation:

1. Load config
2. Acquire lock
3. For each enabled source: skip if within `min_interval`, else crawl
4. Drain notification outbox
5. Release lock

This is **fixed-delay at the OS level** (hourly task) combined with
**per-source due-ness** inside the process. A long crawl does not start a
second concurrent crawl when the lock is held (or when Task Scheduler is set
to not start a new instance).

## What was deliberately not added

- No `oem-radar service` long-running loop (would duplicate OS scheduling and
  contradict ADR-1)
- No APScheduler / Celery / Redis
- No dashboard start/stop controls for processes
- No Docker requirement

## Troubleshooting

| Symptom | Check |
|---------|--------|
| “another oem-radar run is active” | `data/oem-radar.lock` PID; wait or stop the other process |
| Hourly task never runs | `schtasks /query /tn "OEM Radar Hourly Crawl" /v` |
| No Discord alerts from scheduled runs | `config/discord_webhook.txt` or env; `data/crawl-runs.log` |
| Sources always skipped | `min_interval` not elapsed; use `--force` to test |
| Linux timer silent | `journalctl -u oem-radar-run.service` |
