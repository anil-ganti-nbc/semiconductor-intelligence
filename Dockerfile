# Semiconductor Intelligence -- Tier B: STAGING/SOAK ONLY.
#
# This image is never a production deployment. It exists to prove the
# `semi_intel` package is portable to a Linux container and to soak-test it
# against an isolated volume -- see ai/handoff/ for what has and has not
# been verified. release_channel defaults to "soaking" (see
# semi_intel/runtime_bridge.py) and is only ever raised by an operator who
# has explicitly promoted this deployment.
#
# The optional `x` extra (Playwright-based X/Twitter collection) is
# deliberately NOT installed here -- see ai/handoff/KNOWN_ISSUES.md. RSS and
# pci.ids ingestion, the claim/evidence/graph engine, notifications, and
# backups all work without it.
#
# The embedded `src/oem_radar` subproject is a dead/abandoned fork and is
# not copied into this image at all.
FROM python:3.13-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1

LABEL clank.id="semi-intel" \
      clank.tier="B" \
      clank.release_channel="soaking"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SEMI_INTEL_DB_URL=sqlite:////app/data/semi_intel.db \
    SEMINTEL_PROJECT_ROOT=/app \
    SEMINTEL_RELEASE_CHANNEL=soaking

WORKDIR /app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin clank

# Only what semi_intel actually needs to install and run: the package
# itself, its Alembic migrations (required by `semi-intel db upgrade`, never
# edited by this change), and the entrypoint script. No src/oem_radar, no
# .venv*, no test fixtures, no packaging/ (PyInstaller-only), no dashboard
# .exe launchers.
COPY pyproject.toml requirements.container.lock ./
COPY semi_intel ./semi_intel
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY scripts ./scripts

RUN pip install --require-hashes -r requirements.container.lock \
    && pip install --no-deps . \
    && mkdir -p /app/data \
    && chmod +x /app/scripts/*.sh \
    && chown -R clank:clank /app

USER clank

# No ports: this is a CLI/batch image, not a service. The web dashboard
# extra ('web') is not installed, so `semi-intel web serve` / `semintel gui`
# fail with the existing friendly "extra not installed" error if invoked --
# that is pre-existing behavior, unchanged here.
HEALTHCHECK --interval=60s --timeout=20s --start-period=15s --retries=3 \
    CMD ["semi-intel", "health"]

ENTRYPOINT ["/bin/sh", "/app/scripts/entrypoint.sh"]
CMD ["identity"]
