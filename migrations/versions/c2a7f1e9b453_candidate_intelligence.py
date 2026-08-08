"""candidate intelligence (v1.0.0)

Revision ID: c2a7f1e9b453
Revises: a0b5d7e9f314
Create Date: 2026-08-06 23:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2a7f1e9b453'
down_revision: Union[str, Sequence[str], None] = 'a0b5d7e9f314'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('source_reputations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('authority', sa.Float(), nullable=False),
    sa.Column('authority_override', sa.Float(), nullable=True),
    sa.Column('historical_accuracy', sa.Float(), nullable=True),
    sa.Column('editorial_yield', sa.Float(), nullable=False),
    sa.Column('noise_rate', sa.Float(), nullable=False),
    sa.Column('originality', sa.Float(), nullable=False),
    sa.Column('specializations', sa.Text(), nullable=False),
    sa.Column('lead_time_hours', sa.Float(), nullable=True),
    sa.Column('verification_count', sa.Integer(), nullable=False),
    sa.Column('false_positive_count', sa.Integer(), nullable=False),
    sa.Column('items_contributed', sa.Integer(), nullable=False),
    sa.Column('independence_groups_originated', sa.Integer(), nullable=False),
    sa.Column('independence_groups_appeared_in', sa.Integer(), nullable=False),
    sa.Column('last_updated', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_source_reputations_source_id'), 'source_reputations', ['source_id'], unique=True)

    with op.batch_alter_table('signal_candidates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('confidence_score', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('confidence_explanation', sa.Text(), nullable=False, server_default='{}'))
        batch_op.add_column(sa.Column('editorial_value_score', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('editorial_value_explanation', sa.Text(), nullable=False, server_default='{}'))
        batch_op.add_column(sa.Column('timeline_stage', sa.String(length=30), nullable=True))
        batch_op.create_index(op.f('ix_signal_candidates_confidence_score'), ['confidence_score'], unique=False)
        batch_op.create_index(op.f('ix_signal_candidates_editorial_value_score'), ['editorial_value_score'], unique=False)
        batch_op.create_index(op.f('ix_signal_candidates_timeline_stage'), ['timeline_stage'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('signal_candidates', schema=None) as batch_op:
        batch_op.drop_index(op.f('ix_signal_candidates_timeline_stage'))
        batch_op.drop_index(op.f('ix_signal_candidates_editorial_value_score'))
        batch_op.drop_index(op.f('ix_signal_candidates_confidence_score'))
        batch_op.drop_column('timeline_stage')
        batch_op.drop_column('editorial_value_explanation')
        batch_op.drop_column('editorial_value_score')
        batch_op.drop_column('confidence_explanation')
        batch_op.drop_column('confidence_score')

    op.drop_index(op.f('ix_source_reputations_source_id'), table_name='source_reputations')
    op.drop_table('source_reputations')
