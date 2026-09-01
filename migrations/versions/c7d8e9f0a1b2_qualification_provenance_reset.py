"""Add an additive qualification provenance and reset projection.

Revision ID: c7d8e9f0a1b2
Revises: bf599f950d56
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "c7d8e9f0a1b2"
down_revision = "bf599f950d56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qualification_epochs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("material_identity", sa.String(128), nullable=False),
        sa.Column("material_payload", sa.Text(), nullable=False),
        sa.Column("prior_material_identity", sa.String(128), nullable=True),
        sa.Column("reset_reason", sa.Text(), nullable=True),
        sa.Column("opened_by_execution_id", sa.Integer(), nullable=True),
        sa.Column("authority_provenance", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_type", "material_identity", name="uq_qualification_epochs_job_material"),
    )
    op.create_index("ix_qualification_epochs_job_type", "qualification_epochs", ["job_type"])
    op.create_index("ix_qualification_epochs_material_identity", "qualification_epochs", ["material_identity"])
    op.create_index("ix_qualification_epochs_opened_by_execution_id", "qualification_epochs", ["opened_by_execution_id"])
    op.create_index("ix_qualification_epochs_created_at", "qualification_epochs", ["created_at"])

    with op.batch_alter_table("operational_job_runs") as batch:
        batch.add_column(sa.Column("qualification_provenance", sa.String(50), nullable=True))
        batch.add_column(sa.Column("qualification_material_identity", sa.String(128), nullable=True))
        batch.add_column(
            sa.Column("qualification_epoch_id", sa.Integer(), nullable=True)
        )
        batch.create_index("ix_operational_job_runs_qualification_provenance", ["qualification_provenance"])
        batch.create_index("ix_operational_job_runs_qualification_material_identity", ["qualification_material_identity"])
        batch.create_index("ix_operational_job_runs_qualification_epoch_id", ["qualification_epoch_id"])
        batch.create_foreign_key(
            "fk_operational_job_runs_qualification_epoch_id",
            "qualification_epochs",
            ["qualification_epoch_id"],
            ["id"],
        )

    op.create_table(
        "qualification_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("epoch_id", sa.Integer(), sa.ForeignKey("qualification_epochs.id"), nullable=False),
        sa.Column("execution_id", sa.Integer(), sa.ForeignKey("operational_job_runs.id"), nullable=True),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("material_identity", sa.String(128), nullable=False),
        sa.Column("prior_material_identity", sa.String(128), nullable=True),
        sa.Column("new_material_identity", sa.String(128), nullable=True),
        sa.Column("provenance", sa.String(50), nullable=False),
        sa.Column("terminal_status", sa.String(30), nullable=True),
        sa.Column("healthy", sa.Boolean(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_type", "execution_id", name="uq_qualification_events_type_execution"),
    )
    op.create_index("ix_qualification_events_epoch_id", "qualification_events", ["epoch_id"])
    op.create_index("ix_qualification_events_execution_id", "qualification_events", ["execution_id"])
    op.create_index("ix_qualification_events_job_type", "qualification_events", ["job_type"])
    op.create_index("ix_qualification_events_event_type", "qualification_events", ["event_type"])
    op.create_index("ix_qualification_events_material_identity", "qualification_events", ["material_identity"])
    op.create_index("ix_qualification_events_created_at", "qualification_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("qualification_events")
    with op.batch_alter_table("operational_job_runs") as batch:
        batch.drop_constraint("fk_operational_job_runs_qualification_epoch_id", type_="foreignkey")
        batch.drop_index("ix_operational_job_runs_qualification_epoch_id")
        batch.drop_index("ix_operational_job_runs_qualification_material_identity")
        batch.drop_index("ix_operational_job_runs_qualification_provenance")
        batch.drop_column("qualification_epoch_id")
        batch.drop_column("qualification_material_identity")
        batch.drop_column("qualification_provenance")
    op.drop_index("ix_qualification_epochs_created_at", table_name="qualification_epochs")
    op.drop_index("ix_qualification_epochs_opened_by_execution_id", table_name="qualification_epochs")
    op.drop_index("ix_qualification_epochs_material_identity", table_name="qualification_epochs")
    op.drop_index("ix_qualification_epochs_job_type", table_name="qualification_epochs")
    op.drop_table("qualification_epochs")
