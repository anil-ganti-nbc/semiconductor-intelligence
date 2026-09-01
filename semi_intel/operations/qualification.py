"""Qualification evidence projection for Semiconductor operational jobs.

The editorial signal-promotion model answers a different question from
qualification.  This module records the authority-bound execution, the
material identity in force for that execution, the epoch/reset lineage, and a
separate terminal fact.  It deliberately does not change promotion thresholds
or decide which editorial candidates should be promoted.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel import __version__
from semi_intel.domain.enums import (
    OperationalJobStatus,
    OperationalJobType,
    OperationalTriggerType,
    QualificationProvenance,
)
from semi_intel.domain.models import (
    OperationalJobRun,
    QualificationEpoch,
    QualificationEvent,
)
from semi_intel.notifications.service import utcnow


QUALIFICATION_POLICY_VERSION = "semi-intel-qualification-v1"
UNKNOWN_MATERIAL = "unknown"

_TRIGGER_PROVENANCE = {
    OperationalTriggerType.SCHEDULER: QualificationProvenance.SCHEDULED,
    OperationalTriggerType.MANUAL_CLI: QualificationProvenance.MANUAL_CLI,
    OperationalTriggerType.MANUAL_GUI: QualificationProvenance.MANUAL_GUI,
    OperationalTriggerType.STARTUP_CATCHUP: QualificationProvenance.STARTUP_CATCHUP,
    OperationalTriggerType.RETRY: QualificationProvenance.RETRY,
    OperationalTriggerType.TEST: QualificationProvenance.TEST,
}


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


@dataclass(frozen=True)
class QualificationMaterial:
    """Stable, deterministic material identity for one job qualification.

    The identity contains only target-local qualification inputs.  Volatile
    telemetry, timestamps, host/process details, run IDs, and content hashes
    are intentionally excluded.  Unknown components remain visible and make
    the resulting evidence untrusted at the gate.
    """

    job_type: str
    application_version: str
    implementation_revision: str
    config_fingerprint: str
    policy_version: str = QUALIFICATION_POLICY_VERSION
    execution_scope: str = "operational"

    @classmethod
    def for_job(
        cls,
        job_type: OperationalJobType | str,
        *,
        application_version: str | None = None,
        implementation_revision: str | None = None,
        config_fingerprint: str | None = None,
        policy_version: str = QUALIFICATION_POLICY_VERSION,
        execution_scope: str = "operational",
    ) -> "QualificationMaterial":
        value = job_type.value if isinstance(job_type, OperationalJobType) else str(job_type)
        return cls(
            job_type=value,
            application_version=application_version or __version__,
            implementation_revision=implementation_revision
            or os.environ.get("SEMI_INTEL_SOURCE_REVISION", UNKNOWN_MATERIAL),
            config_fingerprint=config_fingerprint
            or os.environ.get("SEMI_INTEL_CONFIG_FINGERPRINT", UNKNOWN_MATERIAL),
            policy_version=policy_version,
            execution_scope=execution_scope,
        )

    def payload(self) -> dict[str, str]:
        return {
            "application_version": self.application_version,
            "config_fingerprint": self.config_fingerprint,
            "execution_scope": self.execution_scope,
            "implementation_revision": self.implementation_revision,
            "job_type": self.job_type,
            "policy_version": self.policy_version,
        }

    @property
    def identity(self) -> str:
        canonical = json.dumps(self.payload(), sort_keys=True, separators=(",", ":"))
        return "siq1-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def trustworthy(self) -> bool:
        values = (
            self.application_version,
            self.implementation_revision,
            self.config_fingerprint,
            self.policy_version,
            self.execution_scope,
        )
        return all(value and value.strip().lower() not in {"unknown", "null", "none"} for value in values)


@dataclass(frozen=True)
class QualificationDecision:
    """Fail-closed result returned by :meth:`QualificationService.gate`."""

    qualified: bool
    reason: str
    epoch_id: int | None = None
    execution_id: int | None = None
    provenance: QualificationProvenance = QualificationProvenance.UNKNOWN


def provenance_for_trigger(
    trigger: OperationalTriggerType | QualificationProvenance | str | None,
) -> QualificationProvenance:
    """Map only the scheduler's actual authority vocabulary to provenance."""

    if isinstance(trigger, OperationalTriggerType):
        return _TRIGGER_PROVENANCE.get(trigger, QualificationProvenance.UNKNOWN)
    if isinstance(trigger, QualificationProvenance):
        return trigger
    if trigger is None:
        return QualificationProvenance.UNKNOWN
    try:
        raw = str(trigger)
        try:
            return QualificationProvenance(raw)
        except ValueError:
            return _TRIGGER_PROVENANCE.get(OperationalTriggerType(raw), QualificationProvenance.UNKNOWN)
    except ValueError:
        return QualificationProvenance.UNKNOWN


class QualificationService:
    """Prepare epochs and persist auditable qualification facts."""

    def __init__(self, session: Session):
        self.session = session

    def current_epoch(self, job_type: OperationalJobType | str) -> QualificationEpoch | None:
        value = job_type.value if isinstance(job_type, OperationalJobType) else str(job_type)
        return self.session.scalar(
            select(QualificationEpoch)
            .where(QualificationEpoch.job_type == value)
            .order_by(QualificationEpoch.created_at.desc(), QualificationEpoch.id.desc())
        )

    def prepare(
        self,
        job: OperationalJobRun,
        *,
        material: QualificationMaterial | None = None,
        now: dt.datetime | None = None,
    ) -> QualificationEpoch:
        """Bind a job to the current epoch before any gate reads evidence."""

        now = now or utcnow()
        if job.id is None:
            self.session.flush()
        material = material or QualificationMaterial.for_job(job.job_type)
        job_type = job.job_type.value
        if material.job_type != job_type:
            raise ValueError(
                f"qualification material job_type {material.job_type!r} does not match {job_type!r}"
            )
        provenance = provenance_for_trigger(job.trigger_type)
        current = self.current_epoch(job.job_type)

        if current is not None and current.material_identity == material.identity:
            epoch = current
        else:
            epoch = QualificationEpoch(
                job_type=job_type,
                material_identity=material.identity,
                material_payload=json.dumps(material.payload(), sort_keys=True),
                prior_material_identity=current.material_identity if current else None,
                reset_reason=("qualification material identity changed" if current else None),
                opened_by_execution_id=job.id,
                authority_provenance=provenance.value,
                created_at=now,
            )
            self.session.add(epoch)
            self.session.flush()
            event_type = "RESET" if current else "EPOCH_STARTED"
            self._add_event(
                epoch=epoch,
                job=job,
                event_type=event_type,
                material=material,
                provenance=provenance,
                prior_material_identity=current.material_identity if current else None,
                new_material_identity=material.identity,
                reason=("qualification material identity changed" if current else "qualification epoch started"),
                now=now,
            )

        job.qualification_provenance = provenance.value
        job.qualification_material_identity = material.identity
        job.qualification_epoch_id = epoch.id
        self.session.flush()
        return epoch

    def record_terminal(
        self,
        job: OperationalJobRun,
        *,
        now: dt.datetime | None = None,
    ) -> QualificationEvent | None:
        """Persist one terminal fact independently of reset/EPOCH_STARTED."""

        if job.id is None or job.qualification_epoch_id is None:
            return None
        existing = self.session.scalar(
            select(QualificationEvent).where(
                QualificationEvent.event_type == "TERMINAL",
                QualificationEvent.execution_id == job.id,
            )
        )
        if existing is not None:
            return existing

        # A downstream writer cannot upgrade an absent or altered persisted
        # value.  The value must still agree with the authority-bound trigger
        # captured during preparation; otherwise record UNKNOWN.
        authority = provenance_for_trigger(job.trigger_type)
        persisted = job.qualification_provenance
        provenance = authority if persisted == authority.value else QualificationProvenance.UNKNOWN
        material_identity = job.qualification_material_identity or UNKNOWN_MATERIAL
        event = QualificationEvent(
            epoch_id=job.qualification_epoch_id,
            execution_id=job.id,
            job_type=job.job_type.value,
            event_type="TERMINAL",
            material_identity=material_identity,
            provenance=provenance.value,
            terminal_status=job.status.value if isinstance(job.status, OperationalJobStatus) else str(job.status),
            healthy=job.status == OperationalJobStatus.SUCCESSFUL,
            reason=job.summary or job.error_summary or "terminal operational job result",
            payload=job.result_counts or "{}",
            created_at=now or utcnow(),
        )
        self.session.add(event)
        self.session.flush()
        return event

    def gate(
        self,
        job_type: OperationalJobType | str,
        *,
        material: QualificationMaterial | None = None,
        qualifying_provenance: Iterable[QualificationProvenance] = (QualificationProvenance.SCHEDULED,),
    ) -> QualificationDecision:
        """Return a fail-closed qualification decision for current material."""

        material = material or QualificationMaterial.for_job(job_type)
        epoch = self.current_epoch(job_type)
        if epoch is None:
            return QualificationDecision(False, "no qualification epoch exists")
        if epoch.material_identity != material.identity:
            return QualificationDecision(False, "qualification material identity is stale", epoch.id)
        if not material.trustworthy:
            return QualificationDecision(False, "qualification material identity is untrusted", epoch.id)

        terminal = self.session.scalar(
            select(QualificationEvent)
            .where(
                QualificationEvent.epoch_id == epoch.id,
                QualificationEvent.event_type == "TERMINAL",
            )
            .order_by(QualificationEvent.created_at.desc(), QualificationEvent.id.desc())
        )
        if terminal is None:
            return QualificationDecision(False, "no terminal qualification evidence exists", epoch.id)
        if terminal.material_identity != material.identity:
            return QualificationDecision(False, "terminal evidence material identity diverges", epoch.id)
        if terminal.provenance == QualificationProvenance.UNKNOWN.value:
            return QualificationDecision(False, "terminal provenance is UNKNOWN", epoch.id, terminal.execution_id)
        allowed = {value.value for value in qualifying_provenance}
        if terminal.provenance not in allowed:
            return QualificationDecision(
                False,
                "terminal provenance is not qualifying",
                epoch.id,
                terminal.execution_id,
                provenance_for_trigger(terminal.provenance),
            )
        if terminal.terminal_status != OperationalJobStatus.SUCCESSFUL.value or terminal.healthy is not True:
            return QualificationDecision(False, "latest terminal evidence is not healthy", epoch.id, terminal.execution_id)
        return QualificationDecision(
            True,
            "qualification evidence is current and structurally verifiable",
            epoch.id,
            terminal.execution_id,
            provenance_for_trigger(terminal.provenance),
        )

    def _add_event(
        self,
        *,
        epoch: QualificationEpoch,
        job: OperationalJobRun,
        event_type: str,
        material: QualificationMaterial,
        provenance: QualificationProvenance,
        prior_material_identity: str | None,
        new_material_identity: str | None,
        reason: str,
        now: dt.datetime,
    ) -> QualificationEvent:
        existing = self.session.scalar(
            select(QualificationEvent).where(
                QualificationEvent.event_type == event_type,
                QualificationEvent.execution_id == job.id,
            )
        )
        if existing is not None:
            return existing
        event = QualificationEvent(
            epoch_id=epoch.id,
            execution_id=job.id,
            job_type=job.job_type.value,
            event_type=event_type,
            material_identity=material.identity,
            prior_material_identity=prior_material_identity,
            new_material_identity=new_material_identity,
            provenance=provenance.value,
            reason=reason,
            payload=json.dumps(material.payload(), sort_keys=True),
            created_at=now,
        )
        self.session.add(event)
        self.session.flush()
        return event
