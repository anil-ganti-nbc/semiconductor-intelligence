#!/usr/bin/env bash
# Double-click to launch the Semi Intel dashboard locally, against the
# isolated local staging database. Delegates entirely to mac/dashboard —
# no logic lives here. This is Tier B staging/soak, not production.
cd "$(dirname "${BASH_SOURCE[0]}")"
exec mac/dashboard
