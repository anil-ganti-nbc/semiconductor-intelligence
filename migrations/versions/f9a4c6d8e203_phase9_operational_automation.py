"""Phase 9 operational automation, feedback, delivery and backups

Revision ID: f9a4c6d8e203
Revises: e8b7c2d4a901
Create Date: 2026-07-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f9a4c6d8e203"
down_revision: Union[str, None] = "e8b7c2d4a901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

job_type = sa.Enum(
    "PIPELINE", "NOTIFICATION_GENERATION", "DAILY_DIGEST", "DELIVERY_RETRY",
    "BACKUP", "DATABASE_MAINTENANCE", "RETENTION_CLEANUP", "HEALTH_CHECK",
    name="operationaljobtype",
)
trigger_type = sa.Enum(
    "MANUAL_CLI", "MANUAL_GUI", "SCHEDULER", "STARTUP_CATCHUP", "RETRY", "TEST",
    name="operationaltriggertype",
)
job_status = sa.Enum(
    "SCHEDULED", "RUNNING", "SUCCESSFUL", "PARTIAL", "FAILED", "DEFERRED",
    "SKIPPED", "ABANDONED", name="operationaljobstatus",
)
feedback_rating = sa.Enum("USEFUL", "NOT_USEFUL", name="notificationfeedbackrating")
backup_status = sa.Enum("VERIFIED", "FAILED", "PRUNED", name="backupstatus")


def upgrade() -> None:
    op.create_table(
        "scheduler_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scheduler_enabled", sa.Boolean(), nullable=False),
        sa.Column("pipeline_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("digest_enabled", sa.Boolean(), nullable=False),
        sa.Column("digest_time", sa.String(5), nullable=False),
        sa.Column("backup_enabled", sa.Boolean(), nullable=False),
        sa.Column("backup_time", sa.String(5), nullable=False),
        sa.Column("maintenance_enabled", sa.Boolean(), nullable=False),
        sa.Column("maintenance_time", sa.String(5), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("maximum_job_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("retry_delay_minutes", sa.Integer(), nullable=False),
        sa.Column("maximum_automatic_retries", sa.Integer(), nullable=False),
        sa.Column("stale_run_threshold_minutes", sa.Integer(), nullable=False),
        sa.Column("missed_run_warning_minutes", sa.Integer(), nullable=False),
        sa.Column("startup_catchup_enabled", sa.Boolean(), nullable=False),
        sa.Column("backup_directory", sa.String(1000), nullable=False),
        sa.Column("backup_retention_count", sa.Integer(), nullable=False),
        sa.Column("backup_retention_days", sa.Integer(), nullable=False),
        sa.Column("last_scheduler_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_notification_preset", sa.String(50), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_scheduler_settings_last_scheduler_heartbeat", "scheduler_settings", ["last_scheduler_heartbeat"])

    op.create_table(
        "operational_job_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", job_type, nullable=False),
        sa.Column("trigger_type", trigger_type, nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", job_status, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("parent_retry_id", sa.Integer(), sa.ForeignKey("operational_job_runs.id"), nullable=True),
        sa.Column("owner_identity", sa.String(255), nullable=False),
        sa.Column("lock_token", sa.String(255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("result_counts", sa.Text(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("diagnostic_reference", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in ("job_type", "trigger_type", "started_at", "status", "parent_retry_id", "next_retry_at"):
        op.create_index(f"ix_operational_job_runs_{name}", "operational_job_runs", [name])

    op.create_table(
        "operational_job_leases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", job_type, nullable=False),
        sa.Column("owner_identity", sa.String(255), nullable=False),
        sa.Column("lock_token", sa.String(255), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_type"),
        sa.UniqueConstraint("lock_token"),
    )
    op.create_index("ix_operational_job_leases_job_type", "operational_job_leases", ["job_type"], unique=True)
    op.create_index("ix_operational_job_leases_lock_token", "operational_job_leases", ["lock_token"], unique=True)
    op.create_index("ix_operational_job_leases_expires_at", "operational_job_leases", ["expires_at"])

    op.create_table(
        "notification_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("notification_id", sa.Integer(), sa.ForeignKey("notifications.id"), nullable=False),
        sa.Column("rating", feedback_rating, nullable=False),
        sa.Column("reason", sa.String(100), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("notification_id"),
    )
    op.create_index("ix_notification_feedback_notification_id", "notification_feedback", ["notification_id"], unique=True)
    op.create_index("ix_notification_feedback_rating", "notification_feedback", ["rating"])
    op.create_index("ix_notification_feedback_reason", "notification_feedback", ["reason"])

    op.create_table(
        "saved_notification_views",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("state_filter", sa.String(30), nullable=False),
        sa.Column("event_types", sa.Text(), nullable=False),
        sa.Column("severities", sa.Text(), nullable=False),
        sa.Column("topic_ids", sa.Text(), nullable=False),
        sa.Column("relation_filters", sa.Text(), nullable=False),
        sa.Column("date_window_days", sa.Integer(), nullable=True),
        sa.Column("search_text", sa.String(500), nullable=False),
        sa.Column("sort_order", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_saved_notification_views_name", "saved_notification_views", ["name"], unique=True)

    op.create_table(
        "delivery_adapter_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("adapter_type", sa.String(50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("configured_host", sa.String(255), nullable=True),
        sa.Column("configuration_present", sa.Boolean(), nullable=False),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_successful", sa.Boolean(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_summary", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "backup_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("status", backup_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("application_version", sa.String(50), nullable=False),
        sa.Column("alembic_revision", sa.String(100), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("integrity_result", sa.String(100), nullable=False),
        sa.Column("table_count", sa.Integer(), nullable=False),
        sa.Column("record_counts", sa.Text(), nullable=False),
        sa.Column("manifest_path", sa.String(1000), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.UniqueConstraint("filename"),
    )
    op.create_index("ix_backup_records_filename", "backup_records", ["filename"], unique=True)
    op.create_index("ix_backup_records_status", "backup_records", ["status"])
    op.create_index("ix_backup_records_created_at", "backup_records", ["created_at"])


def downgrade() -> None:
    op.drop_table("backup_records")
    op.drop_table("delivery_adapter_status")
    op.drop_table("saved_notification_views")
    op.drop_table("notification_feedback")
    op.drop_table("operational_job_leases")
    op.drop_table("operational_job_runs")
    op.drop_table("scheduler_settings")

    backup_status.drop(op.get_bind(), checkfirst=True)
    feedback_rating.drop(op.get_bind(), checkfirst=True)
    job_status.drop(op.get_bind(), checkfirst=True)
    trigger_type.drop(op.get_bind(), checkfirst=True)
    job_type.drop(op.get_bind(), checkfirst=True)
