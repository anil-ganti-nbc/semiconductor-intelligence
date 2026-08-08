"""editorial discovery inbox

Revision ID: 9f3c2a1b7d10
Revises: 71747eaa2044
Create Date: 2026-07-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "9f3c2a1b7d10"
down_revision: Union[str, Sequence[str], None] = "71747eaa2044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitored_topics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("keyword", sa.String(255), nullable=False),
        sa.Column("aliases", sa.Text(), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("priority", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_monitored_topics_name", "monitored_topics", ["name"], unique=True)
    op.create_index("ix_monitored_topics_normalized_name", "monitored_topics", ["normalized_name"], unique=True)
    op.create_index("ix_monitored_topics_category", "monitored_topics", ["category"])
    op.create_index("ix_monitored_topics_enabled", "monitored_topics", ["enabled"])

    op.create_table(
        "editorial_stories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("canonical_key", sa.String(64), nullable=False),
        sa.Column("headline", sa.String(500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("interest_score", sa.Float(), nullable=False),
        sa.Column("score_reasons", sa.Text(), nullable=False),
        sa.Column("coverage_count", sa.Integer(), nullable=False),
        sa.Column("seen_at", sa.DateTime(), nullable=True),
        sa.Column("new_coverage_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("latest_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column, unique in (
        ("canonical_key", True), ("interest_score", False), ("seen_at", False),
        ("created_at", False), ("latest_at", False),
    ):
        op.create_index(f"ix_editorial_stories_{column}", "editorial_stories", [column], unique=unique)

    op.create_table(
        "story_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("story_id", sa.Integer(), nullable=False),
        sa.Column("evidence_id", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["editorial_stories.id"]),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("story_id", "evidence_id"),
    )
    op.create_index("ix_story_evidence_story_id", "story_evidence", ["story_id"])
    op.create_index("ix_story_evidence_evidence_id", "story_evidence", ["evidence_id"])

    op.create_table(
        "topic_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("story_id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("matched_text", sa.String(255), nullable=False),
        sa.Column("match_score", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["editorial_stories.id"]),
        sa.ForeignKeyConstraint(["topic_id"], ["monitored_topics.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("story_id", "topic_id"),
    )
    op.create_index("ix_topic_matches_story_id", "topic_matches", ["story_id"])
    op.create_index("ix_topic_matches_topic_id", "topic_matches", ["topic_id"])

    op.create_table(
        "citations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("evidence_id", sa.Integer(), nullable=False),
        sa.Column("destination_url", sa.String(2048), nullable=False),
        sa.Column("destination_domain", sa.String(255), nullable=False),
        sa.Column("link_text", sa.String(500), nullable=True),
        sa.Column("is_editorial", sa.Boolean(), nullable=False),
        sa.Column("discovered_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_id", "destination_url"),
    )
    op.create_index("ix_citations_evidence_id", "citations", ["evidence_id"])
    op.create_index("ix_citations_destination_domain", "citations", ["destination_domain"])

    suggestion_status = sa.Enum("PENDING", "IGNORED", "BLOCKED", "ADDED", name="sourcesuggestionstatus")
    op.create_table(
        "source_suggestions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("inferred_name", sa.String(255), nullable=False),
        sa.Column("feed_url", sa.String(1024), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reasons", sa.Text(), nullable=False),
        sa.Column("appearances", sa.Integer(), nullable=False),
        sa.Column("story_count", sa.Integer(), nullable=False),
        sa.Column("topic_count", sa.Integer(), nullable=False),
        sa.Column("status", suggestion_status, nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_suggestions_domain", "source_suggestions", ["domain"], unique=True)
    op.create_index("ix_source_suggestions_score", "source_suggestions", ["score"])
    op.create_index("ix_source_suggestions_status", "source_suggestions", ["status"])


def downgrade() -> None:
    op.drop_table("source_suggestions")
    op.drop_table("citations")
    op.drop_table("topic_matches")
    op.drop_table("story_evidence")
    op.drop_table("editorial_stories")
    op.drop_table("monitored_topics")
