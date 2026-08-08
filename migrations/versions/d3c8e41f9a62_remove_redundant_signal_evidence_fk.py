"""remove redundant SignalItem-to-Evidence provenance link

Revision ID: d3c8e41f9a62
Revises: a6a1b2c73e08
Create Date: 2026-07-26

Evidence.origin_signal_item_id is unique and already provides an idempotent,
queryable provenance link.  The reverse SignalItem.origin_evidence_id column
duplicated that relationship and created a circular foreign-key dependency.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d3c8e41f9a62"
down_revision: Union[str, None] = "a6a1b2c73e08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("signal_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_signal_items_origin_evidence_id"))
        batch_op.drop_column("origin_evidence_id")


def downgrade() -> None:
    with op.batch_alter_table("signal_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("origin_evidence_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_signal_items_origin_evidence_id_evidence",
            "evidence",
            ["origin_evidence_id"],
            ["id"],
        )
        batch_op.create_index(
            batch_op.f("ix_signal_items_origin_evidence_id"),
            ["origin_evidence_id"],
            unique=False,
        )
