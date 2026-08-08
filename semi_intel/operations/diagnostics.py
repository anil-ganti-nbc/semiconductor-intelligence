"""Privacy-bounded diagnostics bundle: metadata only, never database content."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
import re
import sys
import zipfile
from pathlib import Path

from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from semi_intel import __version__
from semi_intel.domain.models import (
    NotificationSettings, OperationalJobRun, ProviderIncident, ProviderRun, SchedulerSettings,
)
from semi_intel.notifications.service import aware, safe_error, utcnow
from semi_intel.operations.health import HealthService
from semi_intel.operations.scheduler import get_scheduler_settings


SECRET_PATTERN = re.compile(
    r"(?i)(bearer\s+\S+|(?:api[_-]?key|token|password|secret|cookie|authorization|webhook)"
    r"\s*[:=]\s*\S+|https?://[^\s]+[?&][^\s]+)"
)


def redact(value):
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_PATTERN.sub("[redacted]", value)[:2000]
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DiagnosticsService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, output_directory: Path, *, now: dt.datetime | None = None) -> dict:
        now = now or utcnow()
        output_directory = output_directory.resolve()
        output_directory.mkdir(parents=True, exist_ok=True)
        filename = f"semi-intel-diagnostics-{now.strftime('%Y%m%dT%H%M%S.%fZ')}.zip"
        destination = output_directory / filename
        if destination.exists():
            raise FileExistsError(destination)

        scheduler = get_scheduler_settings(self.session)
        notification_settings = self.session.get(NotificationSettings, 1)
        jobs = list(self.session.scalars(select(OperationalJobRun).order_by(
            OperationalJobRun.started_at.desc()
        ).limit(50)))
        provider_runs = list(self.session.scalars(select(ProviderRun).order_by(
            ProviderRun.started_at.desc()
        ).limit(50)))
        incidents = list(self.session.scalars(select(ProviderIncident).order_by(
            ProviderIncident.opened_at.desc()
        ).limit(50)))
        try:
            revision = self.session.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
        except Exception:  # create_all development database has no Alembic marker
            self.session.rollback()
            revision = None
        tables = inspect(self.session.get_bind()).get_table_names()
        payload = redact({
            "manifest": {
                "application_version": __version__, "alembic_revision": revision,
                "created_at": now.isoformat(), "platform": platform.platform(),
                "python": sys.version.split()[0], "table_count": len(tables),
            },
            "settings": {
                "scheduler_enabled": scheduler.scheduler_enabled,
                "pipeline_interval_minutes": scheduler.pipeline_interval_minutes,
                "digest_enabled": scheduler.digest_enabled,
                "backup_enabled": scheduler.backup_enabled,
                "maintenance_enabled": scheduler.maintenance_enabled,
                "timezone": scheduler.timezone,
                "active_notification_preset": scheduler.active_notification_preset,
                "notification_external_enabled": (
                    notification_settings.external_delivery_enabled if notification_settings else False
                ),
            },
            "health": HealthService(self.session).report(now=now),
            "jobs": [{
                "id": row.id, "type": row.job_type.value, "trigger": row.trigger_type.value,
                "status": row.status.value, "started_at": aware(row.started_at).isoformat(),
                "finished_at": aware(row.finished_at).isoformat() if row.finished_at else None,
                "summary": row.summary, "error": safe_error(row.error_summary),
            } for row in jobs],
            "provider_runs": [{
                "id": row.id, "provider": row.provider, "source_id": row.source_id,
                "status": row.status.value, "started_at": aware(row.started_at).isoformat(),
                "items_collected": row.items_collected, "error": safe_error(row.error),
            } for row in provider_runs],
            "incidents": [{
                "id": row.id, "provider": row.provider, "source_id": row.source_id,
                "opened_at": aware(row.opened_at).isoformat(),
                "resolved_at": aware(row.resolved_at).isoformat() if row.resolved_at else None,
                "failures": row.consecutive_failures,
                "error": safe_error(row.latest_error_summary),
            } for row in incidents],
        })
        manifest_text = json.dumps(payload, indent=2, sort_keys=True)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("diagnostics.json", manifest_text + "\n")
            archive.writestr("README.txt", (
                "Semi Intel privacy-bounded diagnostics. Contains operational metadata only; "
                "no database, cookies, sessions, webhook URLs, credentials, or article bodies.\n"
            ))
        return {"path": str(destination), "sha256": _sha256(destination), "size_bytes": destination.stat().st_size}
