"""Semiconductor-specific qualification provenance/reset regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from semi_intel.domain.enums import (
    OperationalJobStatus,
    OperationalJobType,
    OperationalTriggerType,
    QualificationProvenance,
)
from semi_intel.domain.models import OperationalJobRun, QualificationEpoch, QualificationEvent
from semi_intel.operations.qualification import (
    QualificationMaterial,
    QualificationService,
    provenance_for_trigger,
)
from semi_intel.operations.scheduler import OperationalScheduler


ROOT = Path(__file__).resolve().parents[1]


def material(revision: str = "rev-a", config: str = "cfg-a") -> QualificationMaterial:
    return QualificationMaterial.for_job(
        OperationalJobType.PIPELINE,
        application_version="3.3.13",
        implementation_revision=revision,
        config_fingerprint=config,
    )


def make_job(session, *, trigger=OperationalTriggerType.SCHEDULER, status=OperationalJobStatus.SUCCESSFUL):
    job = OperationalJobRun(
        job_type=OperationalJobType.PIPELINE,
        trigger_type=trigger,
        status=status,
        owner_identity="qualification-test",
        summary="test terminal",
        result_counts=json.dumps({"ok": 1}),
    )
    session.add(job)
    session.flush()
    return job


def test_authoritative_provenance_survives_scheduler_persistence(db_session):
    job = OperationalScheduler(db_session).run_job(
        OperationalJobType.HEALTH_CHECK,
        trigger=OperationalTriggerType.SCHEDULER,
        material=QualificationMaterial.for_job(
            OperationalJobType.HEALTH_CHECK,
            application_version="3.3.13",
            implementation_revision="rev-a",
            config_fingerprint="cfg-a",
        ),
    )
    db_session.expire_all()
    persisted = db_session.get(OperationalJobRun, job.id)
    assert persisted.qualification_provenance == QualificationProvenance.SCHEDULED.value
    assert persisted.qualification_epoch_id is not None
    terminal = db_session.scalar(select(QualificationEvent).where(QualificationEvent.execution_id == job.id, QualificationEvent.event_type == "TERMINAL"))
    assert terminal.provenance == QualificationProvenance.SCHEDULED.value


def test_absent_provenance_remains_unknown(db_session):
    job = make_job(db_session)
    service = QualificationService(db_session)
    service.prepare(job, material=material())
    job.qualification_provenance = None
    terminal = service.record_terminal(job)
    assert terminal.provenance == QualificationProvenance.UNKNOWN.value


def test_downstream_cannot_fabricate_trusted_provenance(db_session):
    job = make_job(db_session, trigger=OperationalTriggerType.MANUAL_CLI)
    service = QualificationService(db_session)
    service.prepare(job, material=material())
    job.qualification_provenance = QualificationProvenance.SCHEDULED.value
    terminal = service.record_terminal(job)
    assert terminal.provenance == QualificationProvenance.UNKNOWN.value


def test_stable_material_identity_reuses_epoch(db_session):
    service = QualificationService(db_session)
    first = make_job(db_session)
    second = make_job(db_session)
    epoch_a = service.prepare(first, material=material())
    epoch_b = service.prepare(second, material=material())
    assert epoch_a.id == epoch_b.id
    assert db_session.scalar(select(func.count()).select_from(QualificationEpoch)) == 1


def test_changed_material_creates_new_epoch(db_session):
    service = QualificationService(db_session)
    first = make_job(db_session)
    second = make_job(db_session)
    epoch_a = service.prepare(first, material=material("rev-a"))
    epoch_b = service.prepare(second, material=material("rev-b"))
    assert epoch_a.id != epoch_b.id
    assert epoch_b.prior_material_identity == epoch_a.material_identity


def test_first_changed_job_cannot_consume_prior_epoch_evidence(db_session):
    service = QualificationService(db_session)
    first = make_job(db_session)
    service.prepare(first, material=material("rev-a"))
    service.record_terminal(first)
    assert service.gate(OperationalJobType.PIPELINE, material=material("rev-a")).qualified

    changed = make_job(db_session)
    service.prepare(changed, material=material("rev-b"))
    decision = service.gate(OperationalJobType.PIPELINE, material=material("rev-b"))
    assert decision.qualified is False
    assert "terminal" in decision.reason


def test_old_evidence_history_remains_preserved_after_reset(db_session):
    service = QualificationService(db_session)
    first = make_job(db_session)
    epoch_a = service.prepare(first, material=material("rev-a"))
    service.record_terminal(first)
    changed = make_job(db_session)
    epoch_b = service.prepare(changed, material=material("rev-b"))
    assert db_session.get(QualificationEpoch, epoch_a.id).material_identity == material("rev-a").identity
    assert db_session.get(QualificationEpoch, epoch_b.id).material_identity == material("rev-b").identity
    assert db_session.scalar(select(func.count()).select_from(QualificationEvent).where(QualificationEvent.execution_id == first.id)) == 2


def test_reset_records_explicit_prior_and_new_identity(db_session):
    service = QualificationService(db_session)
    first = make_job(db_session)
    service.prepare(first, material=material("rev-a"))
    changed = make_job(db_session)
    service.prepare(changed, material=material("rev-b"))
    reset = db_session.scalar(select(QualificationEvent).where(QualificationEvent.execution_id == changed.id, QualificationEvent.event_type == "RESET"))
    assert reset.prior_material_identity == material("rev-a").identity
    assert reset.new_material_identity == material("rev-b").identity
    assert reset.reason


def test_legacy_rows_keep_null_qualification_lineage(db_session):
    legacy = make_job(db_session)
    db_session.commit()
    db_session.expire_all()
    persisted = db_session.get(OperationalJobRun, legacy.id)
    assert persisted.qualification_provenance is None
    assert persisted.qualification_material_identity is None
    assert persisted.qualification_epoch_id is None


def test_reset_preparation_is_idempotent(db_session):
    service = QualificationService(db_session)
    first = make_job(db_session)
    service.prepare(first, material=material("rev-a"))
    changed = make_job(db_session)
    first_epoch = service.prepare(changed, material=material("rev-b"))
    second_epoch = service.prepare(changed, material=material("rev-b"))
    assert first_epoch.id == second_epoch.id
    assert db_session.scalar(select(func.count()).select_from(QualificationEvent).where(QualificationEvent.execution_id == changed.id, QualificationEvent.event_type == "RESET")) == 1


def test_terminal_evidence_persists_after_reset(db_session):
    service = QualificationService(db_session)
    first = make_job(db_session)
    service.prepare(first, material=material("rev-a"))
    service.record_terminal(first)
    changed = make_job(db_session)
    service.prepare(changed, material=material("rev-b"))
    terminal = service.record_terminal(changed)
    assert terminal is not None
    assert terminal.epoch_id == changed.qualification_epoch_id
    assert terminal.terminal_status == OperationalJobStatus.SUCCESSFUL.value


def test_reset_and_terminal_coexist_for_one_execution(db_session):
    service = QualificationService(db_session)
    first = make_job(db_session)
    service.prepare(first, material=material("rev-a"))
    changed = make_job(db_session)
    service.prepare(changed, material=material("rev-b"))
    service.record_terminal(changed)
    event_types = set(db_session.scalars(select(QualificationEvent.event_type).where(QualificationEvent.execution_id == changed.id)))
    assert {"RESET", "TERMINAL"} <= event_types


def test_terminal_recording_is_idempotent(db_session):
    service = QualificationService(db_session)
    job = make_job(db_session)
    service.prepare(job, material=material())
    first = service.record_terminal(job)
    second = service.record_terminal(job)
    assert first.id == second.id
    assert db_session.scalar(select(func.count()).select_from(QualificationEvent).where(QualificationEvent.event_type == "TERMINAL", QualificationEvent.execution_id == job.id)) == 1


def test_stale_material_fails_current_gate(db_session):
    service = QualificationService(db_session)
    job = make_job(db_session)
    service.prepare(job, material=material("rev-a"))
    service.record_terminal(job)
    decision = service.gate(OperationalJobType.PIPELINE, material=material("rev-b"))
    assert decision.qualified is False
    assert "stale" in decision.reason


def test_normal_unchanged_successful_execution_remains_intact(db_session):
    job = OperationalScheduler(db_session).run_job(
        OperationalJobType.HEALTH_CHECK,
        trigger=OperationalTriggerType.MANUAL_CLI,
        material=QualificationMaterial.for_job(
            OperationalJobType.HEALTH_CHECK,
            application_version="3.3.13",
            implementation_revision="rev-a",
            config_fingerprint="cfg-a",
        ),
    )
    assert job.status in {OperationalJobStatus.SUCCESSFUL, OperationalJobStatus.PARTIAL}


def test_all_scheduler_entry_points_are_prepared(db_session):
    material_by_job = {
        job_type: QualificationMaterial.for_job(
            job_type,
            application_version="3.3.13",
            implementation_revision="rev-a",
            config_fingerprint="cfg-a",
        )
        for job_type in OperationalJobType
    }
    for trigger in OperationalTriggerType:
        job = OperationalScheduler(db_session).run_job(
            OperationalJobType.HEALTH_CHECK,
            trigger=trigger,
            material=material_by_job[OperationalJobType.HEALTH_CHECK],
        )
        assert job.qualification_epoch_id is not None
        assert job.qualification_provenance == provenance_for_trigger(trigger).value
        assert db_session.scalar(select(QualificationEvent).where(QualificationEvent.execution_id == job.id, QualificationEvent.event_type == "TERMINAL")) is not None


def test_material_identity_is_deterministic_and_observation_independent():
    a = material()
    b = QualificationMaterial.for_job(
        OperationalJobType.PIPELINE,
        config_fingerprint="cfg-a",
        implementation_revision="rev-a",
        application_version="3.3.13",
    )
    assert a.payload() == b.payload()
    assert a.identity == b.identity
    assert a.identity.startswith("siq1-")
    assert "timestamp" not in a.payload()


def test_unknown_material_fails_closed(db_session):
    service = QualificationService(db_session)
    job = make_job(db_session)
    unknown = QualificationMaterial.for_job(
        OperationalJobType.PIPELINE,
        application_version="3.3.13",
        implementation_revision="unknown",
        config_fingerprint="cfg-a",
    )
    service.prepare(job, material=unknown)
    service.record_terminal(job)
    decision = service.gate(OperationalJobType.PIPELINE, material=unknown)
    assert decision.qualified is False
    assert "untrusted" in decision.reason


def test_material_must_match_execution_job_type(db_session):
    service = QualificationService(db_session)
    job = make_job(db_session)
    wrong_job_material = QualificationMaterial.for_job(
        OperationalJobType.HEALTH_CHECK,
        application_version="3.3.13",
        implementation_revision="rev-a",
        config_fingerprint="cfg-a",
    )
    with pytest.raises(ValueError, match="does not match"):
        service.prepare(job, material=wrong_job_material)


def test_provenance_vocabulary_has_no_unsupported_authority_path():
    assert set(QualificationProvenance) == {
        QualificationProvenance.SCHEDULED,
        QualificationProvenance.MANUAL_CLI,
        QualificationProvenance.MANUAL_GUI,
        QualificationProvenance.STARTUP_CATCHUP,
        QualificationProvenance.RETRY,
        QualificationProvenance.TEST,
        QualificationProvenance.UNKNOWN,
    }
    assert provenance_for_trigger(None) is QualificationProvenance.UNKNOWN
    assert provenance_for_trigger("deploy") is QualificationProvenance.UNKNOWN
    assert provenance_for_trigger("recovery") is QualificationProvenance.UNKNOWN


def test_qualification_migration_is_additive_and_chained_from_current_head():
    migration = (ROOT / "migrations" / "versions" / "c7d8e9f0a1b2_qualification_provenance_reset.py").read_text(encoding="utf-8")
    assert 'revision = "c7d8e9f0a1b2"' in migration
    assert 'down_revision = "bf599f950d56"' in migration
    upgrade = migration.split("def downgrade", 1)[0]
    assert "drop_table" not in upgrade
    assert "drop_column" not in upgrade
    for table in ("qualification_epochs", "qualification_events"):
        assert f'op.create_table(\n        "{table}"' in upgrade
    for column in (
        '"qualification_provenance"',
        '"qualification_material_identity"',
        '"qualification_epoch_id"',
    ):
        assert column in upgrade
