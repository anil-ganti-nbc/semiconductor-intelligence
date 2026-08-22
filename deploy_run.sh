#!/bin/sh
# Hetzner staging entrypoint (Phase 2C): the hourly lane goes through
# OperationalScheduler so invocation != committed-success is evidenced.
set -eu
cd "$(dirname "$0")"
export IMAGE_TAG
IMAGE_TAG="$(cat .deployed-id)"
exec docker compose -f docker-compose.staging.yml run --rm semi-intel semintel automation cycle
