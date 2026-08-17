"""Cloud-migration runtime bridge: version / identity / health.

Thin, read-only adapter. Modeled on the Free Game Tracker clank's
`newsroom/runtime_bridge.py` pattern for this cloud-migration phase, adapted
for this package's own DB access (SQLAlchemy engine via `semi_intel.db`,
paths via `semi_intel.paths`) rather than copied verbatim.

Does not run collectors, does not run migrations, does not invent historical
data, and does not claim a release channel this deployment hasn't been
promoted to. Semiconductor Intelligence is Tier B (staging/soak only) --
`release_channel` therefore defaults to `"soaking"`, the least-trusted
channel this repo is allowed to claim, and is only ever raised by an
operator explicitly setting SEMINTEL_RELEASE_CHANNEL.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semi_intel import __version__ as PACKAGE_VERSION

CLANK_ID = "semi-intel"

try:
    from clank_runtime.contracts.enums import (
        IngestionState,
        OperationalState,
        ReleaseChannel,
    )
    from clank_runtime.contracts.health import HealthPayload
    from clank_runtime.contracts.identity import RuntimeIdentity
    from clank_runtime.version import (
        HEALTH_CONTRACT_VERSION,
        RUNTIME_CONTRACT_VERSION,
        __version__ as RUNTIME_VERSION,
    )

    _HAS_RUNTIME = True
except ImportError:  # pragma: no cover - clank_runtime is an optional, separate package
    _HAS_RUNTIME = False
    RUNTIME_VERSION = "unavailable"
    RUNTIME_CONTRACT_VERSION = "0.1.0-stage0"
    HEALTH_CONTRACT_VERSION = "0.1.0-stage0"
    OperationalState = None  # type: ignore[misc, assignment]
    IngestionState = None  # type: ignore[misc, assignment]
    ReleaseChannel = None  # type: ignore[misc, assignment]
    HealthPayload = None  # type: ignore[misc, assignment]
    RuntimeIdentity = None  # type: ignore[misc, assignment]


def get_release_channel() -> str:
    """Never hard-coded. Tier B (this repo) defaults to "soaking" -- the
    least-trusted channel it's allowed to claim -- so an unconfigured or
    freshly built image never self-reports a maturity it hasn't earned.
    An operator raises this explicitly (e.g. to "staging") only once a
    promotion has actually happened; this module never decides that on its
    own."""
    value = os.environ.get("SEMINTEL_RELEASE_CHANNEL", "").strip()
    return value or "soaking"


def get_version_info() -> dict[str, str]:
    return {
        "clank_id": CLANK_ID,
        "clank_version": PACKAGE_VERSION,
        "package_name": "semi_intel",
        "runtime_version": RUNTIME_VERSION,
        "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
        "health_contract_version": HEALTH_CONTRACT_VERSION,
        "release_channel": get_release_channel(),
        "runtime_bridge": "soak1.0",
        "source_revision": os.environ.get("SEMINTEL_SOURCE_REVISION", "unknown"),
    }


def get_identity() -> Any:
    """Identity always reflects get_release_channel() -- never hard-coded."""
    channel_value = get_release_channel()
    if _HAS_RUNTIME:
        try:
            channel = ReleaseChannel(channel_value)
        except ValueError:
            channel = ReleaseChannel.EXPERIMENTAL
        return RuntimeIdentity(
            runtime_version=RUNTIME_VERSION,
            clank_id=CLANK_ID,
            clank_version=PACKAGE_VERSION,
            release_channel=channel,
        )
    return {
        "contract_version": RUNTIME_CONTRACT_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "clank_id": CLANK_ID,
        "clank_version": PACKAGE_VERSION,
        "release_channel": channel_value,
        "source_revision": os.environ.get("SEMINTEL_SOURCE_REVISION", "unknown"),
    }


def _db_url() -> str:
    from semi_intel.db import DEFAULT_DB_URL

    return os.environ.get("SEMI_INTEL_DB_URL", DEFAULT_DB_URL)


def _sqlite_path_from_url(url: str) -> Path | None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    raw = url[len(prefix):]
    if raw == ":memory:":
        return None
    return Path(raw)


def get_health() -> Any:
    """Build health from process + DB reachability + recorded operational
    job history, when present.

    Semantics (deliberately conservative -- see the brief's health-
    truthfulness requirement):
    - process_liveness: always true if this function runs.
    - application_readiness: the DB file exists (or its parent directory is
      writable so a first run could create it) AND a real query succeeds.
    - last_attempted_run / last_successful_run come from
      operational_job_runs when that table has rows; otherwise both stay
      None ("unknown"), never fabricated as a fresh "just started" success.
    - Zero rows / zero sources is NOT a failure -- an empty, freshly
      installed database is a legitimate, healthy state.
    - This never runs Alembic and never writes anything except the same
      lazy `Base.metadata.create_all()` every other command in this
      codebase already performs against a missing/fresh database.
    """
    from sqlalchemy import func, select

    from semi_intel.db import get_engine, get_sessionmaker
    from semi_intel.db import init_db as _init_db
    from semi_intel.domain.enums import OperationalJobStatus
    from semi_intel.domain.models import OperationalJobRun, Source

    url = _db_url()
    db_path = _sqlite_path_from_url(url)
    reasons: list[str] = []

    db_writable = True
    db_exists: bool | None = None
    if db_path is not None:
        db_exists = db_path.exists()
        parent = db_path.parent
        db_writable = parent.exists() and os.access(parent, os.W_OK)
        if not db_exists:
            reasons.append(f"database file missing: {db_path}")
        if not db_writable:
            reasons.append(f"database parent not writable: {parent}")

    last_attempt: datetime | None = None
    last_success: datetime | None = None
    source_count: int | None = None
    query_ok = False

    try:
        engine = get_engine(url)
        _init_db(engine)
        session = get_sessionmaker(engine)()
        try:
            source_count = session.scalar(select(func.count()).select_from(Source))
            last_run = session.scalar(
                select(OperationalJobRun).order_by(OperationalJobRun.started_at.desc())
            )
            if last_run is not None:
                last_attempt = last_run.started_at
                last_success_row = session.scalar(
                    select(OperationalJobRun)
                    .where(OperationalJobRun.status == OperationalJobStatus.SUCCESSFUL)
                    .order_by(OperationalJobRun.started_at.desc())
                )
                if last_success_row is not None:
                    last_success = last_success_row.started_at
            else:
                reasons.append("no operational job runs recorded yet")
            query_ok = True
        finally:
            session.close()
            engine.dispose()
    except Exception as exc:  # noqa: BLE001 - health must not raise
        reasons.append(f"database query failed: {exc}")
        query_ok = False

    if not query_ok:
        state = "failed"
    elif db_path is not None and not db_exists and not db_writable:
        state = "failed"
    elif db_path is not None and not db_exists:
        state = "degraded"
    else:
        state = "healthy"

    version_info = get_version_info()
    observed = datetime.now(timezone.utc)

    if _HAS_RUNTIME:
        op = {
            "healthy": OperationalState.HEALTHY,
            "degraded": OperationalState.DEGRADED,
            "failed": OperationalState.FAILED,
        }.get(state, OperationalState.UNKNOWN)
        return HealthPayload(
            operational_state=op,
            process_liveness=True,
            application_readiness=query_ok,
            last_attempted_run=last_attempt,
            last_successful_run=last_success,
            database_writable=db_writable,
            evidence_path_writable=db_writable,
            ingestion_state=IngestionState.UNKNOWN,
            version_info=version_info,
            status_reasons=reasons,
            observed_at=observed,
        )

    return {
        "contract_version": HEALTH_CONTRACT_VERSION,
        "operational_state": state,
        "process_liveness": True,
        "application_readiness": query_ok,
        "last_attempted_run": last_attempt.isoformat() if last_attempt else None,
        "last_successful_run": last_success.isoformat() if last_success else None,
        "database_writable": db_writable,
        "database_url": url,
        "source_count": source_count,
        "ingestion_state": "unknown",
        "version_info": version_info,
        "status_reasons": reasons,
        "observed_at": observed.isoformat(),
    }


def as_jsonable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj
