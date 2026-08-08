# Staging release runbook — Semiconductor Intelligence on the Synology NAS

Tier B: staging/soak only, no production tier exists for this clank in this phase.
This is the repeatable procedure for every future candidate build, not just the first
one. Nothing here has been executed against the real NAS yet (no access until
2026-08-15).

## Per-release identity (fill in every time)

| Field | Value |
|---|---|
| development branch | `cloud/semi-intel-soak` (or a future feature branch merged into it) |
| candidate tag | the reviewed commit SHA |
| candidate image | `semi-intel:<sha>` |
| staging state path | `/volume1/docker-data/semi-intel-staging/` — never the operator's real `semi_intel.db` |
| staging schedule | disabled by default; enabled only for a deliberate soak run, per the brief's "a newly deployed candidate must not begin running merely because its container was created" |
| staging notification target | none, or a distinct test webhook |
| rollback image | previous candidate's image, kept loaded |

## Procedure

1. **Build off-NAS**: `docker build --platform linux/amd64 -t semi-intel:<sha> .`
2. **Local validation** (repeat what this session already proved): non-root,
   `identity`/`health`/`version` in-container, `db upgrade` succeeds, persistence
   across recreation on a throwaway volume, backup/restore drill.
3. **Transfer**: `docker save | gzip` → copy to NAS → `docker load`.
4. **Staging run, isolated volume only**:
   `IMAGE_TAG=<sha> docker compose -f docker-compose.staging.yml run --rm semi-intel <command>`
5. **Soak**: per the brief, elapsed soak time from a previous build does **not**
   transfer to a materially different build. If this candidate changes collector
   logic, scoring, or anything beyond pure packaging, its soak clock restarts at zero
   regardless of how long the last build had been running.
6. **Never promote to "production"** — no such tier exists for this clank in the
   current maturity policy. If real-world evidence eventually supports promoting it,
   that's a maturity-classification decision for the user to make explicitly, not
   something this runbook authorizes.
7. **Record the release** — even for staging, keep a short log of which candidate SHA
   is currently soaking, when it started, and what changed, so "how long has this
   actually been soaking" is never a guess.

## Rollback

Same pattern as Free Game Tracker: previous image stays loaded, swapping back is a
one-line config change, not a rebuild.
