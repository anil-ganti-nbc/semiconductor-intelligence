"""Separate scheduler invocation from committed-success heartbeat.

Revision ID: a0b1c2d3e404
Revises: c2a7f1e9b453
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "a0b1c2d3e404"
down_revision = "c2a7f1e9b453"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scheduler_settings") as batch:
        batch.add_column(sa.Column("last_scheduler_invocation", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_successful_job_commit", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_scheduler_settings_last_scheduler_invocation", ["last_scheduler_invocation"])
        batch.create_index("ix_scheduler_settings_last_successful_job_commit", ["last_successful_job_commit"])


def downgrade() -> None:
    with op.batch_alter_table("scheduler_settings") as batch:
        batch.drop_index("ix_scheduler_settings_last_successful_job_commit")
        batch.drop_index("ix_scheduler_settings_last_scheduler_invocation")
        batch.drop_column("last_successful_job_commit")
        batch.drop_column("last_scheduler_invocation")
