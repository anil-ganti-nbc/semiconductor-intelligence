# Qualification provenance and reset evidence

Semiconductor's qualification projection is target-local and separate from
editorial signal promotion. The authoritative execution boundary is
`OperationalScheduler.run_job`, whose `OperationalTriggerType` is persisted on
`OperationalJobRun` for scheduler, manual CLI/GUI, startup-catchup, retry, and
test paths. The qualification projection maps those real paths to
`SCHEDULED`, `MANUAL_CLI`, `MANUAL_GUI`, `STARTUP_CATCHUP`, `RETRY`, `TEST`, or
`UNKNOWN`; there is no trusted deploy or recovery authority in this repository.

`QualificationMaterial` computes a deterministic `siq1-` digest from the
qualification-relevant job type, application version, implementation revision,
configuration fingerprint, policy version, and execution scope. It excludes
run IDs, timestamps, host/process details, telemetry, and content hashes.
Unknown material components remain visible and make the gate fail closed.

Before an execution can read qualification evidence, `QualificationService`
binds the job to the current `QualificationEpoch`. A material change creates a
new epoch and an append-only `RESET` event with prior/new identities and a
reason. A first epoch has an `EPOCH_STARTED` event. Terminal results are stored
as independent, idempotent `TERMINAL` events, so reset and terminal facts can
coexist for one execution. Legacy rows keep nullable provenance, identity, and
epoch fields; they are not backfilled.

The qualification gate reads only the current epoch, requires a trustworthy
material identity and a healthy terminal event with qualifying scheduled
provenance, and rejects missing, stale, divergent, unknown, or untrusted
evidence. Editorial candidate promotion remains governed by its existing
thresholds and audit model; it is not treated as qualification evidence.
