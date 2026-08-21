"""Plain-language consolidated operational health."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from semi_intel.domain.enums import (
    BackupStatus, DeliveryAttemptStatus, NotificationSeverity,
    OperationalJobStatus, OperationalJobType,
)
from semi_intel.domain.models import (
    BackupRecord, CandidatePromotionSettings, Notification,
    NotificationDeliveryAttempt, NotificationDigest, NotificationSettings,
    OperationalJobLease, OperationalJobRun, ProviderIncident, ProviderRun,
    SchedulerSettings, SignalCollectionSettings, Source,
)
from semi_intel.notifications.service import aware, get_settings, utcnow
from semi_intel.operations.scheduler import get_scheduler_settings
from semi_intel.operations.webhook import WebhookConfigurationService


EXPECTED_HEAD = "c2a7f1e9b453"


class HealthService:
    def __init__(self, session: Session):
        self.session = session

    def report(self, *, now: dt.datetime | None = None) -> dict:
        now = now or utcnow()
        scheduler = get_scheduler_settings(self.session)
        notification_settings = get_settings(self.session, now=now)
        issues: list[dict] = []

        last_pipeline = self.session.scalar(select(OperationalJobRun).where(
            OperationalJobRun.job_type == OperationalJobType.PIPELINE
        ).order_by(OperationalJobRun.started_at.desc()))
        active_leases = list(self.session.scalars(select(OperationalJobLease)))
        stale_leases = [
            lease for lease in active_leases if aware(lease.expires_at) <= now
        ]
        if scheduler.scheduler_enabled and not last_pipeline:
            issues.append(self._issue(
                "attention_needed", "Automation has not completed its first pipeline run.",
                "Run the pipeline now or inspect Windows Task Scheduler.",
            ))
        elif scheduler.scheduler_enabled and last_pipeline:
            age_minutes = (now - aware(last_pipeline.started_at)).total_seconds() / 60
            threshold = scheduler.pipeline_interval_minutes + scheduler.missed_run_warning_minutes
            if age_minutes > threshold:
                issues.append(self._issue(
                    "degraded", f"The pipeline has not run for {round(age_minutes)} minutes.",
                    "Run an automation cycle and inspect recent job failures.",
                ))
        if stale_leases:
            issues.append(self._issue(
                "degraded", f"{len(stale_leases)} stale operational lease(s) need recovery.",
                "Run the affected job again; stale leases recover automatically.",
            ))

        # A job whose process was killed outright (not a clean exception --
        # run_job()'s own try/except/finally never got to run) leaves its
        # OperationalJobRun row stuck at RUNNING with no finished_at
        # forever; nothing else ever revisits it. stale_run_threshold_minutes
        # is exactly the operator-configurable setting for this (exposed in
        # settings/schemas since Phase 9) but was never actually consulted
        # anywhere -- wiring it up here, mirroring the stale-lease check
        # immediately above, is what "identified according to existing
        # policy" means for a run stuck like this. The job's own lease (if
        # still present) already self-heals via LeaseManager.acquire()'s
        # stale-lease takeover on the next real attempt; this only makes the
        # stuck historical row visible instead of silently invisible.
        stale_running = list(self.session.scalars(select(OperationalJobRun).where(
            OperationalJobRun.status == OperationalJobStatus.RUNNING,
            OperationalJobRun.started_at <= now - dt.timedelta(minutes=scheduler.stale_run_threshold_minutes),
        )))
        if stale_running:
            issues.append(self._issue(
                "degraded",
                f"{len(stale_running)} operational run(s) have been RUNNING for over "
                f"{scheduler.stale_run_threshold_minutes} minutes and likely did not finish cleanly.",
                "Inspect recent job history; a stuck job's lease recovers automatically on the next attempt.",
            ))

        open_incidents = self.session.scalar(select(func.count()).select_from(ProviderIncident).where(
            ProviderIncident.resolved_at.is_(None)
        )) or 0
        if open_incidents:
            issues.append(self._issue(
                "attention_needed", f"{open_incidents} provider incident(s) are open.",
                "Review provider health and source configuration.",
            ))

        failed_deliveries = self.session.scalar(select(func.count()).select_from(
            NotificationDeliveryAttempt
        ).where(NotificationDeliveryAttempt.status == DeliveryAttemptStatus.FAILED)) or 0
        deferred_deliveries = self.session.scalar(select(func.count()).select_from(
            NotificationDeliveryAttempt
        ).where(NotificationDeliveryAttempt.status == DeliveryAttemptStatus.DEFERRED)) or 0
        if failed_deliveries:
            issues.append(self._issue(
                "attention_needed", f"{failed_deliveries} delivery attempt(s) failed.",
                "Review delivery status; permanent failures require configuration changes.",
            ))

        important_unread = self.session.scalar(select(func.count()).select_from(Notification).where(
            Notification.read_at.is_(None), Notification.dismissed_at.is_(None),
            Notification.severity.in_([NotificationSeverity.IMPORTANT, NotificationSeverity.URGENT]),
        )) or 0
        old_unread = self.session.scalar(select(func.count()).select_from(Notification).where(
            Notification.read_at.is_(None), Notification.dismissed_at.is_(None),
            Notification.created_at < now - dt.timedelta(days=7),
        )) or 0

        latest_backup = self.session.scalar(select(BackupRecord).where(
            BackupRecord.status == BackupStatus.VERIFIED
        ).order_by(BackupRecord.verified_at.desc()))
        if scheduler.backup_enabled and not latest_backup:
            issues.append(self._issue(
                "attention_needed", "Automated backups are enabled but no verified backup exists.",
                "Create and verify a backup now.",
            ))

        try:
            revision = self.session.execute(text(
                "SELECT version_num FROM alembic_version LIMIT 1"
            )).scalar()
        except Exception:  # create_all development databases have no Alembic marker
            self.session.rollback()
            revision = None
        integrity = self.session.execute(text("PRAGMA quick_check")).scalar()
        if revision != EXPECTED_HEAD:
            issues.append(self._issue(
                "degraded", f"Database revision is {revision or 'unknown'}, expected {EXPECTED_HEAD}.",
                "Run the database upgrade command after creating a backup.",
            ))
        if integrity != "ok":
            issues.append(self._issue(
                "degraded", f"Database quick check returned {integrity}.",
                "Stop automation and restore from a verified backup.",
            ))

        engine = self.session.get_bind()
        db_size = None
        wal_size = None
        if engine.url.get_backend_name() == "sqlite" and engine.url.database:
            path = Path(engine.url.database)
            if path.exists():
                db_size = path.stat().st_size
                wal = Path(str(path) + "-wal")
                wal_size = wal.stat().st_size if wal.exists() else 0

        collection = self.session.get(SignalCollectionSettings, 1)
        promotion = self.session.get(CandidatePromotionSettings, 1)
        webhook = WebhookConfigurationService(self.session).status()
        latest_digest = self.session.scalar(select(NotificationDigest).order_by(
            NotificationDigest.generated_at.desc()
        ))
        latest_success = self.session.scalar(select(OperationalJobRun).where(
            OperationalJobRun.status.in_([
                OperationalJobStatus.SUCCESSFUL, OperationalJobStatus.PARTIAL
            ])
        ).order_by(OperationalJobRun.finished_at.desc()))

        overall = "healthy"
        if any(issue["state"] == "degraded" for issue in issues):
            overall = "degraded"
        elif issues:
            overall = "attention_needed"
        elif not scheduler.scheduler_enabled:
            overall = "disabled"
        summary = {
            "healthy": "Everything is running normally.",
            "attention_needed": "The platform is working, but some items need attention.",
            "degraded": "One or more operational safeguards require attention.",
            "disabled": "Automation is disabled; manual workflows remain available.",
        }[overall]
        return {
            "overall": overall, "summary": summary, "issues": issues,
            "scheduler": {
                "state": "enabled" if scheduler.scheduler_enabled else "disabled",
                "last_heartbeat": aware(scheduler.last_scheduler_heartbeat).isoformat()
                if scheduler.last_scheduler_heartbeat else None,
                "last_scheduler_invocation": aware(scheduler.last_scheduler_invocation).isoformat()
                if scheduler.last_scheduler_invocation else None,
                "last_successful_job_commit": aware(scheduler.last_successful_job_commit).isoformat()
                if scheduler.last_successful_job_commit else None,
                "last_pipeline": self._job(last_pipeline),
                "active_leases": len(active_leases), "stale_leases": len(stale_leases),
                "stale_running_jobs": len(stale_running),
            },
            "pipeline": {"last_successful_job": self._job(latest_success)},
            "providers": {"open_incidents": open_incidents},
            "notifications": {
                "unread_important": important_unread, "old_unread": old_unread,
                "last_digest": aware(latest_digest.generated_at).isoformat() if latest_digest else None,
                "deferred_deliveries": deferred_deliveries, "failed_deliveries": failed_deliveries,
                "muted_event_types": json.loads(notification_settings.muted_event_types or "[]"),
                "muted_topic_ids": json.loads(notification_settings.muted_topic_ids or "[]"),
                "external_delivery": webhook,
            },
            "database": {
                "revision": revision, "expected_head": EXPECTED_HEAD,
                "integrity": integrity, "size_bytes": db_size, "wal_size_bytes": wal_size,
                "latest_verified_backup": self._backup(latest_backup),
            },
            "configuration": {
                "collection_enabled": collection.collection_enabled if collection else False,
                "x_enabled": collection.x_provider_enabled if collection else False,
                "automatic_promotion_enabled": promotion.automatic_promotion_enabled if promotion else False,
                "scheduler_enabled": scheduler.scheduler_enabled,
                "digest_enabled": scheduler.digest_enabled,
                "external_delivery_enabled": webhook["enabled"],
            },
        }

    @staticmethod
    def _issue(state: str, explanation: str, action: str) -> dict:
        return {"state": state, "explanation": explanation, "recommended_action": action}

    @staticmethod
    def _job(job: OperationalJobRun | None) -> dict | None:
        if not job:
            return None
        return {
            "id": job.id, "job_type": job.job_type.value, "status": job.status.value,
            "started_at": aware(job.started_at).isoformat(),
            "finished_at": aware(job.finished_at).isoformat() if job.finished_at else None,
            "summary": job.summary, "error_summary": job.error_summary,
        }

    @staticmethod
    def _backup(record: BackupRecord | None) -> dict | None:
        if not record:
            return None
        return {
            "id": record.id, "filename": record.filename,
            "verified_at": aware(record.verified_at).isoformat() if record.verified_at else None,
            "size_bytes": record.size_bytes, "sha256": record.sha256,
        }
