# Phase 0 Windows verification gate

> **Current status: Windows support is UNVERIFIED.** No usable Windows
> environment is currently available, so the `platform / windows` Actions job
> is skipped unless the repository variable `SEMINT_WINDOWS_CI_AVAILABLE` is
> explicitly set to `true` by an operator. A skipped job is not Windows test
> evidence and is not evidence of production readiness. Native Windows
> operator verification remains a separate human gate.

The gate is open until a Windows operator supplies reviewed evidence. Unit
tests and dry-run output are not deployment proof.

Use `scripts/phase0_windows_verify.py --emit-template ABSOLUTE_PATH` to create
the evidence record. Capture the task export digest and native Execute,
Arguments, and WorkingDirectory fields; at least one approved staging path must
contain spaces. Let two consecutive native triggers run without “Run now” or an
interactive session. Each must show Task Scheduler result 0, application start,
a unique committed `SUCCESSFUL` job, success heartbeat advancement after job
finish, source freshness, and delivery to an explicit test notification target
without duplicates. Prove `PARTIAL`, `FAILED`, and no-job-due cycles leave the
success heartbeat unchanged.

For the broken executable path, use
`--emit-broken-path-plan ABSOLUTE_PATH`. The harness never changes Task
Scheduler. On an approved staging host, the operator clones the task under an
isolated name, uses a unique nonexistent executable, waits for one native
trigger, and proves no app/job/heartbeat activity occurred. An independent
monitor—not SemInt—must produce and deliver a missed-run alert. Preserve the
task failure result, monitor rule/version, alert ID, delivery UTC, and redacted
logs. Scheduler cleanup requires separate authority.

Finally run `--evidence COMPLETED.json`; a `PASS` means the record is complete,
not that its claims were independently verified. Attach the task export,
database query evidence, event-log excerpts, notification receipts, and monitor
receipt for reviewer validation.
