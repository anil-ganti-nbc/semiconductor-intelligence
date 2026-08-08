#!/bin/sh
# Container entrypoint for the Semiconductor Intelligence STAGING/SOAK image.
#
# Tier B: soaking only. No in-process scheduler, no browser launch, no GUI.
# An external operator/compose invocation drives this via
# `docker compose -f docker-compose.staging.yml run --rm semi-intel <verb>`.
#
# Two console scripts ship in this package with different audiences:
#   semi-intel  -- the detailed CLI (entities/sources/claims/... plus the
#                  identity/health/version runtime-bridge commands added in
#                  this cloud-migration phase).
#   semintel    -- the operator-friendly CLI (install/run/status/backups/...).
#                  The existing, already-verified backup mechanism
#                  (`semintel backup` / `semintel backups create`) lives
#                  here; this phase reuses it as-is rather than writing a
#                  new backup script.
set -eu
cd /app

case "${1:-}" in
  "")
    exec semi-intel identity
    ;;
  semi-intel)
    shift
    exec semi-intel "$@"
    ;;
  semintel)
    shift
    exec semintel "$@"
    ;;
  backup)
    # Documented invocation: `docker compose run --rm semi-intel backup`.
    # Delegates to the pre-existing, already-verified `semintel backup`
    # command -- no new backup logic was written for this phase.
    exec semintel backup
    ;;
  version|identity|health|init-db|db)
    exec semi-intel "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
