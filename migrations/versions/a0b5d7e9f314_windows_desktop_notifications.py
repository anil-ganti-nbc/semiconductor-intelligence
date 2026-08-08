"""Add opt-in Windows desktop notification setting.

Revision ID: a0b5d7e9f314
Revises: f9a4c6d8e203
Create Date: 2026-08-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a0b5d7e9f314"
down_revision: Union[str, None] = "f9a4c6d8e203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notification_settings",
        sa.Column(
            "windows_desktop_notifications_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("notification_settings") as batch_op:
        batch_op.drop_column("windows_desktop_notifications_enabled")
