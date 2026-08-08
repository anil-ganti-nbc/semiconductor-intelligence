from __future__ import annotations

import datetime as dt
import json
import sqlite3
import zipfile
from pathlib import Path

from sqlalchemy import select, text

from semi_intel.domain.enums import ProviderRunStatus, SourceType
from semi_intel.domain.models import ProviderRun, Source
from semi_intel.operations.backup import BACKUP_PREFIX, BackupService
from semi_intel.operations.diagnostics import DiagnosticsService
from semi_intel.operations.health import HealthService
from semi_intel.operations.scheduler import get_scheduler_settings


BASE = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)


def _mark_migrated(session):
    session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
    session.execute(text("DELETE FROM alembic_version"))
    session.execute(text("INSERT INTO alembic_version VALUES ('c2a7f1e9b453')"))
    session.commit()


def test_consistent_backup_verify_unique_and_restore_dry_run(db_session, tmp_path):
    _mark_migrated(db_session)
    service = BackupService(db_session, backup_directory=tmp_path / "backups")
    first = service.create(now=BASE)
    second = service.create(now=BASE + dt.timedelta(seconds=1))
    assert first.filename != second.filename
    assert Path(first.path).exists()
    verification = service.verify(Path(first.path))
    assert verification["integrity_result"] == "ok"
    assert verification["alembic_revision"] == "c2a7f1e9b453"
    manifest = json.loads(Path(first.manifest_path).read_text())
    assert manifest["sha256"] == first.sha256
    assert service.restore(Path(first.path), dry_run=True)["dry_run"] is True


def test_rehearse_passes_and_confirms_schema_currency_and_orm_counts(db_session, tmp_path):
    """Rehearsal must go further than verify()/restore(dry_run=True): it
    proves the backup actually loads through the real SQLAlchemy engine and
    the application's own models, not just that sqlite3 can open the file."""
    _mark_migrated(db_session)
    db_session.add(Source(name="Rehearsal Source", type=SourceType.RSS, trust_weight=0.5))
    db_session.commit()
    service = BackupService(db_session, backup_directory=tmp_path / "backups")
    record = service.create(now=BASE)

    report = service.rehearse(Path(record.path))

    assert report["passed"] is True
    assert report["error"] is None
    assert report["schema_revision"] == "c2a7f1e9b453"
    assert report["schema_up_to_date"] is True
    assert report["orm_record_counts"]["sources"] == 1
    # the temp copy used for rehearsal must never be left behind
    assert not any(tmp_path.glob("**/semi-intel-rehearsal-*"))


def test_rehearse_flags_a_backup_stamped_behind_the_installed_head(db_session, tmp_path):
    _mark_migrated(db_session)
    db_session.execute(text("UPDATE alembic_version SET version_num = 'a6a1b2c73e08'"))
    db_session.commit()
    service = BackupService(db_session, backup_directory=tmp_path / "backups")
    record = service.create(now=BASE)

    report = service.rehearse(Path(record.path))

    assert report["passed"] is True
    assert report["schema_revision"] == "a6a1b2c73e08"
    assert report["schema_up_to_date"] is False
    assert report["expected_head"] == "c2a7f1e9b453"


def test_rehearse_reports_failure_without_raising_for_a_corrupt_backup(db_session, tmp_path):
    _mark_migrated(db_session)
    service = BackupService(db_session, backup_directory=tmp_path / "backups")
    record = service.create(now=BASE)
    Path(record.path).write_bytes(b"not a real sqlite file")

    report = service.rehearse(Path(record.path))

    assert report["passed"] is False
    assert report["error"] is not None
    assert report["orm_record_counts"] is None


def test_backup_pruning_is_bounded_and_dry_run(db_session, tmp_path):
    _mark_migrated(db_session)
    settings = get_scheduler_settings(db_session)
    settings.backup_retention_count = 1
    settings.backup_retention_days = 365
    service = BackupService(db_session, backup_directory=tmp_path / "backups")
    first = service.create(now=BASE)
    second = service.create(now=BASE + dt.timedelta(seconds=1))
    targets = service.prune(now=BASE + dt.timedelta(seconds=2), dry_run=True)
    assert targets == [Path(first.path)]
    assert Path(first.path).exists() and Path(second.path).exists()
    removed = service.prune(now=BASE + dt.timedelta(seconds=2), dry_run=False)
    assert removed == targets
    assert not Path(first.path).exists() and Path(second.path).exists()


def test_health_and_diagnostics_are_secret_safe(db_session, tmp_path):
    _mark_migrated(db_session)
    db_session.add(ProviderRun(
        provider="rss", status=ProviderRunStatus.FAILED,
        started_at=BASE, finished_at=BASE, error="token=TOP-SECRET",
    ))
    db_session.commit()
    report = HealthService(db_session).report(now=BASE)
    assert report["overall"] in {"disabled", "healthy", "attention_needed"}
    result = DiagnosticsService(db_session).create(tmp_path, now=BASE)
    with zipfile.ZipFile(result["path"]) as archive:
        names = archive.namelist()
        content = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore") for name in names
        )
    assert "semi_intel.db" not in names
    assert "TOP-SECRET" not in content
    assert "token=" not in content.lower()
    assert len(result["sha256"]) == 64
