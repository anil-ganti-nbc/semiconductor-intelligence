"""Bounded single-cycle scheduling with persistent SQLite-safe leases."""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semi_intel.domain.enums import (
    OperationalJobStatus, OperationalJobType, OperationalTriggerType,
)
from semi_intel.domain.models import (
    OperationalJobLease, OperationalJobRun, SchedulerSettings,
)
from semi_intel.notifications.digest import DigestService
from semi_intel.notifications.service import NotificationService, aware, safe_error, utcnow
from semi_intel.pipeline.service import PipelineService


def owner_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def get_scheduler_settings(session: Session) -> SchedulerSettings:
    settings = session.get(SchedulerSettings, 1)
    if settings is None:
        settings = SchedulerSettings(id=1)
        session.add(settings)
        try:
            session.flush()
        except IntegrityError:
            # Another concurrent request's session already inserted row 1
            # (e.g. two dashboard tab-load requests racing on a brand-new
            # database) -- roll back and use the row it committed instead.
            session.rollback()
            settings = session.get(SchedulerSettings, 1)
    return settings


def _daily_due(now: dt.datetime, timezone: str, clock: str) -> dt.datetime:
    zone = ZoneInfo(timezone)
    local = aware(now).astimezone(zone)
    target = dt.time.fromisoformat(clock)
    candidate = dt.datetime.combine(local.date(), target, tzinfo=zone)
    if candidate <= local:
        candidate += dt.timedelta(days=1)
    return candidate.astimezone(dt.UTC)


def most_recent_daily_boundary(now: dt.datetime, timezone: str, clock: str) -> dt.datetime:
    zone = ZoneInfo(timezone)
    local = aware(now).astimezone(zone)
    target = dt.time.fromisoformat(clock)
    candidate = dt.datetime.combine(local.date(), target, tzinfo=zone)
    if candidate > local:
        candidate -= dt.timedelta(days=1)
    return candidate.astimezone(dt.UTC)


def next_runs(settings: SchedulerSettings, *, now: dt.datetime | None = None) -> dict[str, dt.datetime]:
    now = now or utcnow()
    return {
        "pipeline": now + dt.timedelta(minutes=settings.pipeline_interval_minutes),
        "daily_digest": _daily_due(now, settings.timezone, settings.digest_time),
        "backup": _daily_due(now, settings.timezone, settings.backup_time),
        "database_maintenance": _daily_due(now, settings.timezone, settings.maintenance_time),
    }


def effective_automation_state(
    settings: SchedulerSettings, task_status: dict, *, now: dt.datetime | None = None
) -> dict:
    now = now or utcnow()
    heartbeat = aware(settings.last_scheduler_heartbeat)
    if not settings.scheduler_enabled:
        return {"state": "disabled", "explanation": "Persisted automation is disabled.", "healthy": False}
    if not task_status.get("supported", False):
        return {"state": "task_status_unavailable", "explanation": "Windows Task Scheduler status is unavailable.", "healthy": False}
    if not task_status.get("installed", False):
        return {"state": "task_not_installed", "explanation": "Automation is enabled, but the Windows task is not installed.", "healthy": False}
    if not task_status.get("path_exists", False):
        return {"state": "task_path_invalid", "explanation": "The installed task points to an executable that no longer exists.", "healthy": False}
    if not task_status.get("path_matches_current", False):
        return {"state": "task_path_mismatch", "explanation": "The installed task points to a different checkpoint executable.", "healthy": False}
    if task_status.get("action_matches_current") is False:
        return {"state": "task_action_mismatch", "explanation": "The installed task has incorrect arguments or working directory and needs repair.", "healthy": False}
    last_result = task_status.get("last_result")
    if last_result not in (None, 0, 0x41300, 0x41301, 0x41303):
        return {"state": "task_last_run_failed", "explanation": task_status.get("last_result_explanation") or "The last scheduled invocation failed.", "healthy": False}
    if heartbeat is None:
        return {"state": "task_never_ran", "explanation": "The task is installed but has never recorded a scheduler heartbeat.", "healthy": False}
    stale_after = dt.timedelta(
        minutes=settings.pipeline_interval_minutes + settings.missed_run_warning_minutes
    )
    if heartbeat < now - stale_after:
        return {"state": "heartbeat_stale", "explanation": "The scheduler heartbeat is stale.", "healthy": False}
    return {"state": "running_normally", "explanation": "Automation is installed and reporting on schedule.", "healthy": True}


@dataclass
class LeaseResult:
    acquired: bool
    lease: OperationalJobLease | None = None
    recovered_owner: str | None = None


class LeaseManager:
    def __init__(self, session: Session):
        self.session = session

    def acquire(
        self, job_type: OperationalJobType, *, duration_minutes: int,
        now: dt.datetime | None = None, owner: str | None = None,
    ) -> LeaseResult:
        now = now or utcnow()
        owner = owner or owner_identity()
        existing = self.session.scalar(select(OperationalJobLease).where(
            OperationalJobLease.job_type == job_type
        ))
        recovered_owner = None
        if existing:
            if aware(existing.expires_at) > now:
                return LeaseResult(False, existing)
            recovered_owner = existing.owner_identity
            self.session.delete(existing)
            self.session.flush()
            self.session.add(OperationalJobRun(
                job_type=job_type, trigger_type=OperationalTriggerType.SCHEDULER,
                started_at=now, finished_at=now, status=OperationalJobStatus.ABANDONED,
                owner_identity=recovered_owner,
                summary=f"Recovered stale {job_type.value} lease from {recovered_owner}.",
            ))
            self.session.flush()
        lease = OperationalJobLease(
            job_type=job_type, owner_identity=owner, lock_token=uuid.uuid4().hex,
            acquired_at=now, refreshed_at=now,
            expires_at=now + dt.timedelta(minutes=duration_minutes),
        )
        self.session.add(lease)
        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            existing = self.session.scalar(select(OperationalJobLease).where(
                OperationalJobLease.job_type == job_type
            ))
            return LeaseResult(False, existing)
        return LeaseResult(True, lease, recovered_owner)

    def refresh(
        self, lease: OperationalJobLease, *, duration_minutes: int,
        now: dt.datetime | None = None,
    ) -> None:
        now = now or utcnow()
        lease.refreshed_at = now
        lease.expires_at = now + dt.timedelta(minutes=duration_minutes)
        self.session.flush()

    def release(self, lease: OperationalJobLease) -> None:
        self.session.delete(lease)
        self.session.flush()

    def active(self, *, now: dt.datetime | None = None) -> list[OperationalJobLease]:
        now = now or utcnow()
        return list(self.session.scalars(select(OperationalJobLease).where(
            OperationalJobLease.expires_at > now
        )))


class OperationalScheduler:
    def __init__(self, session: Session):
        self.session = session

    def settings(self) -> SchedulerSettings:
        return get_scheduler_settings(self.session)

    def status(self, *, now: dt.datetime | None = None) -> dict:
        now = now or utcnow()
        settings = self.settings()
        last = self.session.scalar(select(OperationalJobRun).order_by(
            OperationalJobRun.started_at.desc()
        ))
        return {
            "enabled": settings.scheduler_enabled,
            "timezone": settings.timezone,
            "last_heartbeat": aware(settings.last_scheduler_heartbeat).isoformat()
            if settings.last_scheduler_heartbeat else None,
            "next_runs": {key: value.isoformat() for key, value in next_runs(settings, now=now).items()},
            "active_leases": [
                {"job_type": lease.job_type.value, "owner": lease.owner_identity,
                 "expires_at": aware(lease.expires_at).isoformat()}
                for lease in LeaseManager(self.session).active(now=now)
            ],
            "last_job": self.job_dict(last) if last else None,
        }

    @staticmethod
    def job_dict(job: OperationalJobRun) -> dict:
        return {
            "id": job.id, "job_type": job.job_type.value,
            "trigger_type": job.trigger_type.value, "status": job.status.value,
            "scheduled_at": aware(job.scheduled_at).isoformat() if job.scheduled_at else None,
            "started_at": aware(job.started_at).isoformat(),
            "finished_at": aware(job.finished_at).isoformat() if job.finished_at else None,
            "attempt_number": job.attempt_number, "summary": job.summary,
            "result_counts": json.loads(job.result_counts or "{}"),
            "error_summary": job.error_summary,
            "next_retry_at": aware(job.next_retry_at).isoformat() if job.next_retry_at else None,
        }

    def run_job(
        self, job_type: OperationalJobType, *,
        trigger: OperationalTriggerType = OperationalTriggerType.MANUAL_CLI,
        now: dt.datetime | None = None,
    ) -> OperationalJobRun:
        now = now or utcnow()
        settings = self.settings()
        lease_result = LeaseManager(self.session).acquire(
            job_type, duration_minutes=settings.maximum_job_duration_minutes, now=now
        )
        if not lease_result.acquired:
            job = OperationalJobRun(
                job_type=job_type, trigger_type=trigger, started_at=now, finished_at=now,
                status=OperationalJobStatus.SKIPPED, owner_identity=owner_identity(),
                summary=f"Skipped: another {job_type.value} job is active.",
            )
            self.session.add(job)
            self.session.commit()
            return job
        lease = lease_result.lease
        job = OperationalJobRun(
            job_type=job_type, trigger_type=trigger, started_at=now,
            status=OperationalJobStatus.RUNNING, owner_identity=owner_identity(),
            lock_token=lease.lock_token,
        )
        self.session.add(job)
        self.session.commit()
        try:
            counts, summary, partial = self._execute(job_type, now=now)
            job.status = OperationalJobStatus.PARTIAL if partial else OperationalJobStatus.SUCCESSFUL
            job.result_counts = json.dumps(counts, sort_keys=True)
            job.summary = summary
        except Exception as exc:  # noqa: BLE001 - job boundary must persist failure
            self.session.rollback()
            job = self.session.get(OperationalJobRun, job.id)
            job.status = OperationalJobStatus.FAILED
            job.error_summary = safe_error(str(exc))
            job.summary = f"{job_type.value.replace('_', ' ').title()} failed."
            if job.attempt_number <= settings.maximum_automatic_retries:
                job.next_retry_at = now + dt.timedelta(minutes=settings.retry_delay_minutes)
        finally:
            job.finished_at = utcnow()
            active_lease = self.session.scalar(select(OperationalJobLease).where(
                OperationalJobLease.lock_token == lease.lock_token
            ))
            if active_lease:
                LeaseManager(self.session).release(active_lease)
            self.session.commit()
        return job

    def _execute(self, job_type: OperationalJobType, *, now: dt.datetime) -> tuple[dict, str, bool]:
        if job_type == OperationalJobType.PIPELINE:
            result = PipelineService(self.session).run_once(loop_started_at=now)
            counts = {
                "ingestion_runs": len(result.ingestion_results),
                "failures": len(result.failures),
                "signal_items_analyzed": result.signal_items_analyzed,
                "candidates_scored": result.signal_candidates_scored,
                "handle_suggestions_created_or_updated": result.handle_suggestions_created_or_updated,
                "notifications_created": result.notifications_created,
                "digest_id": result.digest_id,
            }
            return counts, str(result) or "Pipeline completed with no new material.", bool(result.failures)
        if job_type == OperationalJobType.NOTIFICATION_GENERATION:
            generated = NotificationService(self.session).generate(now=now)
            self.session.commit()
            try:
                from semi_intel.notifications.windows_desktop import WindowsDesktopDeliveryService
                WindowsDesktopDeliveryService(self.session).deliver_pending(now=now)
            except Exception:  # noqa: BLE001 - local alerts never fail the job
                self.session.rollback()
            return {"created": generated.created_count, "updated": len(generated.updated)}, (
                f"Generated {generated.created_count} notification(s)."
            ), False
        if job_type == OperationalJobType.DAILY_DIGEST:
            digest = DigestService(self.session).generate(now=now)
            self.session.commit()
            return {"digest_id": digest.id}, f"Digest #{digest.id} is ready.", False
        if job_type == OperationalJobType.RETENTION_CLEANUP:
            removed = NotificationService(self.session).cleanup_retention(now=now)
            self.session.commit()
            return {"removed": removed}, f"Removed {removed} expired notification(s).", False
        if job_type == OperationalJobType.DATABASE_MAINTENANCE:
            integrity = self.session.execute(text("PRAGMA integrity_check")).scalar()
            self.session.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))
            self.session.execute(text("ANALYZE"))
            self.session.commit()
            return {"integrity_ok": integrity == "ok"}, f"Database integrity: {integrity}.", integrity != "ok"
        if job_type == OperationalJobType.BACKUP:
            from semi_intel.operations.backup import BackupService
            record = BackupService(self.session).create()
            return {"backup_id": record.id, "size_bytes": record.size_bytes}, (
                f"Verified backup {record.filename}."
            ), False
        if job_type == OperationalJobType.HEALTH_CHECK:
            from semi_intel.operations.health import HealthService
            report = HealthService(self.session).report(now=now)
            return {"issues": len(report["issues"])}, report["summary"], report["overall"] == "degraded"
        if job_type == OperationalJobType.DELIVERY_RETRY:
            from semi_intel.operations.webhook import ExternalDeliveryService
            result = ExternalDeliveryService(self.session).deliver_pending(now=now)
            try:
                from semi_intel.notifications.windows_desktop import WindowsDesktopDeliveryService
                result["windows_desktop"] = WindowsDesktopDeliveryService(
                    self.session
                ).deliver_pending(now=now)["notifications"]
            except Exception:  # noqa: BLE001 - independent local adapter
                self.session.rollback()
                result["windows_desktop"] = 0
            total = result["notifications"] + result["digests"] + result["windows_desktop"]
            return result, (
                "Webhook and Windows desktop delivery are disabled or unavailable."
                if result["disabled"] and result["windows_desktop"] == 0
                else f"Processed {total} eligible delivery target(s)."
            ), False
        raise ValueError(f"Unsupported job type {job_type.value}")

    def cycle(self, *, now: dt.datetime | None = None) -> list[OperationalJobRun]:
        now = now or utcnow()
        settings = self.settings()
        settings.last_scheduler_heartbeat = now
        self.session.commit()
        if not settings.scheduler_enabled:
            return []
        jobs: list[OperationalJobRun] = []
        last_pipeline = self.session.scalar(select(OperationalJobRun).where(
            OperationalJobRun.job_type == OperationalJobType.PIPELINE,
            OperationalJobRun.status.in_([
                OperationalJobStatus.SUCCESSFUL, OperationalJobStatus.PARTIAL
            ]),
        ).order_by(OperationalJobRun.finished_at.desc()))
        if not last_pipeline or aware(last_pipeline.finished_at) <= now - dt.timedelta(
            minutes=settings.pipeline_interval_minutes
        ):
            jobs.append(self.run_job(OperationalJobType.PIPELINE, trigger=OperationalTriggerType.SCHEDULER, now=now))
        daily_jobs = (
            (settings.digest_enabled, OperationalJobType.DAILY_DIGEST, settings.digest_time),
            (settings.backup_enabled, OperationalJobType.BACKUP, settings.backup_time),
            (settings.maintenance_enabled, OperationalJobType.DATABASE_MAINTENANCE, settings.maintenance_time),
        )
        for enabled, job_type, clock in daily_jobs:
            if enabled and self._daily_job_due(job_type, clock, now):
                jobs.append(self.run_job(job_type, trigger=OperationalTriggerType.SCHEDULER, now=now))
        return jobs

    def _daily_job_due(
        self, job_type: OperationalJobType, clock: str, now: dt.datetime
    ) -> bool:
        boundary = most_recent_daily_boundary(now, self.settings().timezone, clock)
        latest = self.session.scalar(select(OperationalJobRun).where(
            OperationalJobRun.job_type == job_type,
            OperationalJobRun.status.in_([
                OperationalJobStatus.SUCCESSFUL, OperationalJobStatus.PARTIAL
            ]),
        ).order_by(OperationalJobRun.finished_at.desc()))
        return latest is None or aware(latest.finished_at) < boundary

    def retry(self, job_id: int, *, now: dt.datetime | None = None) -> OperationalJobRun:
        original = self.session.get(OperationalJobRun, job_id)
        if not original:
            raise ValueError(f"No operational job with id={job_id}.")
        job = self.run_job(original.job_type, trigger=OperationalTriggerType.RETRY, now=now)
        job.parent_retry_id = original.id
        job.attempt_number = original.attempt_number + 1
        self.session.commit()
        return job

    def reconcile_stale_runs(self, *, now: dt.datetime | None = None) -> list[int]:
        """Mark unprotected stale RUNNING rows abandoned, preserving history."""
        now = now or utcnow()
        settings = self.settings()
        cutoff = now - dt.timedelta(minutes=settings.stale_run_threshold_minutes)
        active_tokens = set(self.session.scalars(
            select(OperationalJobLease.lock_token).where(OperationalJobLease.expires_at > now)
        ))
        rows = list(self.session.scalars(
            select(OperationalJobRun).where(
                OperationalJobRun.status == OperationalJobStatus.RUNNING,
                OperationalJobRun.started_at <= cutoff,
            ).order_by(OperationalJobRun.started_at.asc())
        ))
        reconciled: list[int] = []
        for row in rows:
            if row.lock_token and row.lock_token in active_tokens:
                continue
            row.status = OperationalJobStatus.ABANDONED
            row.finished_at = now
            row.summary = (
                f"Reconciled stale {row.job_type.value} run after more than "
                f"{settings.stale_run_threshold_minutes} minutes without an active lease."
            )
            row.error_summary = "The previous worker stopped before recording a terminal result."
            reconciled.append(row.id)
        if reconciled:
            stale_leases = list(self.session.scalars(
                select(OperationalJobLease).where(OperationalJobLease.expires_at <= now)
            ))
            for lease in stale_leases:
                self.session.delete(lease)
        self.session.commit()
        return reconciled
