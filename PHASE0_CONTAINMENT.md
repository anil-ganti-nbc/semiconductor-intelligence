# Phase 0 containment status

Classification: **UNVERIFIED_PRODUCTION**  
Promotion eligible: **NO**

This change is source-level remediation only. It has not been deployed, merged,
or used to repair a live scheduled task. A production claim is not verified
until the canonical fleet ledger records the deployed artifact digest and a
real Windows task completes two unattended runs from the installed path.

The scheduler action now uses native Task Scheduler `Execute`, `Arguments`, and
`WorkingDirectory` fields, including paths containing spaces. Status reports
the configured action and last Task Scheduler result separately. The success
heartbeat advances only after at least one scheduled job commits with a
successful or partial status; invocation alone does not create a false healthy
signal.

The dashboard has no remote authenticated profile, so non-loopback binds fail.
State-changing HTTP methods require
`Authorization: Bearer $SEMINTEL_DASHBOARD_AUTH_TOKEN`; without a configured
token the dashboard is read-only. CLI mutations remain local operator actions.

## Required field verification before the Phase 0 gate can close

1. Install or repair the task on a disposable Windows host using an installed
   path containing spaces.
2. Read back Execute, Arguments, WorkingDirectory, principal, and settings.
3. Observe two natural unattended triggers with Task Scheduler result `0`.
4. Confirm each trigger creates the expected job audit and that the heartbeat
   advances only after the job transaction commits.
5. Break the executable path deliberately and confirm health reports an invalid
   path or failed task result without advancing the heartbeat.

Record host evidence in `diagnostic-clank/clank-fleet/inventories/fleet.yaml`.
