"""bounded discovery ring

Revision ID: b71d4e2c9a30
Revises: 9f3c2a1b7d10
Create Date: 2026-07-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b71d4e2c9a30"
down_revision: Union[str, Sequence[str], None] = "9f3c2a1b7d10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("minimum_interest_score", sa.Float(), nullable=False),
        sa.Column("maximum_story_age_hours", sa.Integer(), nullable=False),
        sa.Column("cooldown_hours", sa.Integer(), nullable=False),
        sa.Column("maximum_cycles_per_story", sa.Integer(), nullable=False),
        sa.Column("maximum_queries_per_cycle", sa.Integer(), nullable=False),
        sa.Column("results_per_query", sa.Integer(), nullable=False),
        sa.Column("maximum_results_per_cycle", sa.Integer(), nullable=False),
        sa.Column("global_cycles_per_hour", sa.Integer(), nullable=False),
        sa.Column("provider_requests_per_hour", sa.Integer(), nullable=False),
        sa.Column("request_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("cache_hours", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("region", sa.String(10), nullable=False),
        sa.Column("attribution_phrases", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    run_status = sa.Enum("PENDING", "RUNNING", "COMPLETED", "PARTIAL", "SKIPPED", "FAILED", name="discoveryrunstatus")
    op.create_table(
        "discovery_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("story_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column("eligibility_reason", sa.Text(), nullable=False),
        sa.Column("queries", sa.Text(), nullable=False),
        sa.Column("query_reasons", sa.Text(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("raw_result_count", sa.Integer(), nullable=False),
        sa.Column("accepted_result_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_result_count", sa.Integer(), nullable=False),
        sa.Column("filtered_result_count", sa.Integer(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("budget_state", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["story_id"], ["editorial_stories.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_discovery_runs_story_id", "discovery_runs", ["story_id"])
    op.create_index("ix_discovery_runs_provider", "discovery_runs", ["provider"])
    op.create_index("ix_discovery_runs_status", "discovery_runs", ["status"])
    op.create_index("ix_discovery_runs_started_at", "discovery_runs", ["started_at"])

    relationship = sa.Enum(
        "INDEPENDENT", "FOLLOW_UP", "CITES_KNOWN_SOURCE", "CITES_LIKELY_ORIGIN",
        "POSSIBLE_ORIGIN", "SYNDICATED", "UNKNOWN", name="discoveryrelationship",
    )
    op.create_table(
        "discovery_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("story_id", sa.Integer(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("provider_result_id", sa.String(500), nullable=True),
        sa.Column("original_url", sa.String(2048), nullable=False),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column("canonical_domain", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("publication_name", sa.String(255), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("provider_rank", sa.Integer(), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("relationship", relationship, nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("supporting_phrase", sa.String(500), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["discovery_runs.id"]),
        sa.ForeignKeyConstraint(["story_id"], ["editorial_stories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "canonical_url"),
    )
    for column in ("run_id", "story_id", "provider", "canonical_url", "canonical_domain", "published_at", "relevance_score", "accepted"):
        op.create_index(f"ix_discovery_results_{column}", "discovery_results", [column])


def downgrade() -> None:
    op.drop_table("discovery_results")
    op.drop_table("discovery_runs")
    op.drop_table("discovery_settings")
