"""Phase 8 notifications, digests, delivery attempts and provider incidents

Revision ID: e8b7c2d4a901
Revises: d3c8e41f9a62
Create Date: 2026-07-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8b7c2d4a901"
down_revision: Union[str, None] = "d3c8e41f9a62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

event_type = sa.Enum(
    "HIGH_ATTENTION", "SCORE_INCREASE", "INDEPENDENT_CORROBORATION",
    "PROMOTION_READY", "CANDIDATE_PROMOTED", "TOPIC_ACTIVITY",
    "SOURCE_SUGGESTION", "PROVIDER_FAILURE", "PROVIDER_RECOVERY", "TEST",
    name="notificationeventtype",
)
severity = sa.Enum(
    "INFORMATIONAL", "NOTABLE", "IMPORTANT", "URGENT",
    name="notificationseverity",
)
delivery_state = sa.Enum(
    "IN_APP", "PENDING", "DEFERRED", "DELIVERED", "FAILED", "SUPPRESSED",
    name="notificationdeliverystate",
)
attempt_status = sa.Enum("DELIVERED", "DEFERRED", "FAILED", name="deliveryattemptstatus")
digest_status = sa.Enum("READY", "DELIVERED", name="notificationdigeststatus")


def upgrade() -> None:
    op.create_table(
        "notification_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("in_app_enabled", sa.Boolean(), nullable=False),
        sa.Column("external_delivery_enabled", sa.Boolean(), nullable=False),
        sa.Column("activation_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("minimum_attention_score", sa.Float(), nullable=False),
        sa.Column("minimum_score_increase", sa.Float(), nullable=False),
        sa.Column("required_independent_group_count", sa.Integer(), nullable=False),
        sa.Column("required_topic_match", sa.Boolean(), nullable=False),
        sa.Column("high_score_enabled", sa.Boolean(), nullable=False),
        sa.Column("score_increase_enabled", sa.Boolean(), nullable=False),
        sa.Column("corroboration_enabled", sa.Boolean(), nullable=False),
        sa.Column("promotion_ready_enabled", sa.Boolean(), nullable=False),
        sa.Column("promotion_completed_enabled", sa.Boolean(), nullable=False),
        sa.Column("source_suggestion_enabled", sa.Boolean(), nullable=False),
        sa.Column("provider_health_enabled", sa.Boolean(), nullable=False),
        sa.Column("topic_activity_enabled", sa.Boolean(), nullable=False),
        sa.Column("daily_digest_enabled", sa.Boolean(), nullable=False),
        sa.Column("digest_time", sa.String(length=5), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("quiet_hours_start", sa.String(length=5), nullable=False),
        sa.Column("quiet_hours_end", sa.String(length=5), nullable=False),
        sa.Column("maximum_immediate_per_hour", sa.Integer(), nullable=False),
        sa.Column("maximum_delivery_attempts", sa.Integer(), nullable=False),
        sa.Column("provider_failure_threshold", sa.Integer(), nullable=False),
        sa.Column("provider_recovery_enabled", sa.Boolean(), nullable=False),
        sa.Column("source_suggestion_minimum_score", sa.Float(), nullable=False),
        sa.Column("topic_activity_minimum_candidates", sa.Integer(), nullable=False),
        sa.Column("topic_activity_window_hours", sa.Integer(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("digest_maximum_items_per_section", sa.Integer(), nullable=False),
        sa.Column("muted_event_types", sa.Text(), nullable=False),
        sa.Column("muted_topic_ids", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notification_settings_activation_at"), "notification_settings", ["activation_at"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", event_type, nullable=False),
        sa.Column("severity", severity, nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=True),
        sa.Column("story_id", sa.Integer(), nullable=True),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("source_suggestion_id", sa.Integer(), nullable=True),
        sa.Column("provider_run_id", sa.Integer(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("event_metadata", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_occurrence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_occurrence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("muted", sa.Boolean(), nullable=False),
        sa.Column("delivery_state", delivery_state, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["candidate_id"], ["signal_candidates.id"]),
        sa.ForeignKeyConstraint(["story_id"], ["editorial_stories.id"]),
        sa.ForeignKeyConstraint(["topic_id"], ["monitored_topics.id"]),
        sa.ForeignKeyConstraint(["source_suggestion_id"], ["source_suggestions.id"]),
        sa.ForeignKeyConstraint(["provider_run_id"], ["provider_runs.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key"),
    )
    for column in (
        "event_type", "severity", "dedup_key", "candidate_id", "story_id", "topic_id",
        "source_suggestion_id", "provider_run_id", "source_id", "created_at",
        "event_at", "read_at", "dismissed_at", "muted", "delivery_state", "expires_at",
    ):
        op.create_index(op.f(f"ix_notifications_{column}"), "notifications", [column])

    op.create_table(
        "notification_event_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", event_type, nullable=False),
        sa.Column("subject_kind", sa.String(length=50), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("last_numeric_value", sa.Float(), nullable=True),
        sa.Column("last_boolean_value", sa.Boolean(), nullable=True),
        sa.Column("state_metadata", sa.Text(), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_type", "subject_kind", "subject_id",
                            name="uq_notification_event_state_subject"),
    )
    for column in ("event_type", "subject_kind", "subject_id"):
        op.create_index(op.f(f"ix_notification_event_states_{column}"), "notification_event_states", [column])

    op.create_table(
        "notification_digests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", digest_status, nullable=False),
        sa.Column("notification_ids", sa.Text(), nullable=False),
        sa.Column("structured_sections", sa.Text(), nullable=False),
        sa.Column("rendered_text", sa.Text(), nullable=False),
        sa.Column("delivery_state", delivery_state, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key"),
    )
    for column in ("dedup_key", "window_start", "window_end", "generated_at", "status", "delivery_state"):
        op.create_index(op.f(f"ix_notification_digests_{column}"), "notification_digests", [column])

    op.create_table(
        "notification_delivery_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("notification_id", sa.Integer(), nullable=True),
        sa.Column("digest_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("adapter_name", sa.String(length=100), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", attempt_status, nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"]),
        sa.ForeignKeyConstraint(["digest_id"], ["notification_digests.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_id", "digest_id", "channel", "attempt_number",
                            name="uq_notification_delivery_attempt"),
    )
    for column in (
        "notification_id", "digest_id", "channel", "status", "attempted_at",
        "retry_after", "idempotency_key",
    ):
        op.create_index(
            op.f(f"ix_notification_delivery_attempts_{column}"),
            "notification_delivery_attempts", [column],
        )

    op.create_table(
        "provider_incidents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("incident_key", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_failure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("latest_provider_run_id", sa.Integer(), nullable=True),
        sa.Column("latest_error_summary", sa.Text(), nullable=True),
        sa.Column("failure_notification_id", sa.Integer(), nullable=True),
        sa.Column("recovery_notification_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.ForeignKeyConstraint(["latest_provider_run_id"], ["provider_runs.id"]),
        sa.ForeignKeyConstraint(["failure_notification_id"], ["notifications.id"]),
        sa.ForeignKeyConstraint(["recovery_notification_id"], ["notifications.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_key"),
    )
    for column in (
        "incident_key", "provider", "source_id", "opened_at", "resolved_at",
        "latest_provider_run_id", "failure_notification_id", "recovery_notification_id",
    ):
        op.create_index(op.f(f"ix_provider_incidents_{column}"), "provider_incidents", [column])


def downgrade() -> None:
    op.drop_table("provider_incidents")
    op.drop_table("notification_delivery_attempts")
    op.drop_table("notification_digests")
    op.drop_table("notification_event_states")
    op.drop_table("notifications")
    op.drop_table("notification_settings")
