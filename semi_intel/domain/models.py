"""Core schema.

Design decisions worth stating explicitly:

- The knowledge graph is `Entity` + `Relationship`, both plain relational tables.
  Every graph query in M0 ("what's related to Nova Lake?") is a SQL join. A
  dedicated graph database is deliberately deferred until traversal-heavy
  queries actually become a bottleneck -- see the architecture discussion.
- `Evidence` is treated as immutable once captured and deduplicated by content
  hash. If a source edits or deletes a post, that's a *new* evidence row, not
  a mutation of an old one -- provenance matters more than tidiness here.
- `Claim` is the atomic unit. It never stores raw text scraped from a source;
  it stores a human-authored (for now) assertion, linked to the evidence that
  supports, weakens, or contradicts it. Claim itself stays domain-agnostic on
  purpose -- structured, checkable sub-claims (like memory configuration) get
  their own small side table (`MemorySpecClaim`) rather than growing Claim's
  columns per domain.
- `ClaimEvent` is an append-only log, the seed of the Timeline Engine: every
  claim should be browsable as a history, not just a current snapshot.
- `ClaimLinkSuggestion` is a rules-based *proposal* that a piece of evidence
  is relevant to a claim. It is never treated as fact -- see claim_engine/
  for how suggestions are generated and repository/repositories.py for the
  only path (SuggestionRepository.accept) from suggestion to a real
  ClaimEvidenceLink, which always requires a human-chosen stance.
- `MemorySpecClaim` is a structured, checkable sub-claim about a memory
  configuration (bus width / chip density / total capacity). The
  contradiction engine (semi_intel/contradiction_engine/) validates it
  against GDDR packaging constraints and records the result as a
  ClaimEvent -- it never overwrites the claim itself.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from semi_intel.domain.enums import (
    CandidateEntityRole,
    CandidateRelationType,
    ClaimEventType,
    ClaimStatus,
    DiscoveryRelationship,
    DiscoveryRunStatus,
    EntityType,
    EvidenceStance,
    ProviderRunStatus,
    NotificationEventType,
    NotificationSeverity,
    NotificationDeliveryState,
    DeliveryAttemptStatus,
    NotificationDigestStatus,
    OperationalJobType,
    OperationalTriggerType,
    OperationalJobStatus,
    NotificationFeedbackRating,
    BackupStatus,
    RelationType,
    SignalCandidateState,
    SignalMediaDownloadState,
    SignalMentionStatus,
    SignalProcessingState,
    SourceType,
    SourceSuggestionStatus,
    SourceSuggestionKind,
    SuggestionStatus,
)


class Base(DeclarativeBase):
    pass


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Entity(Base):
    """A node in the knowledge graph: a product, company, node, packaging tech, etc."""

    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[EntityType] = mapped_column(SAEnum(EntityType), index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    aliases: Mapped[str] = mapped_column(Text, default="[]")  # JSON-encoded list[str]
    attributes: Mapped[str] = mapped_column(Text, default="{}")  # JSON-encoded dict
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Entity(id={self.id}, type={self.type.value}, name={self.name!r})"


class Relationship(Base):
    """A directed, typed edge between two entities. This IS the knowledge graph."""

    __tablename__ = "relationships"
    __table_args__ = (UniqueConstraint("from_entity_id", "to_entity_id", "relation_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    from_entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    to_entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    relation_type: Mapped[RelationType] = mapped_column(SAEnum(RelationType))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Source(Base):
    """A discovery channel: a forum, a repo, a filing office, a manual note.

    `type` (SourceType) is the broad editorial category and predates the
    Signal Radar merge -- left untouched so every existing plugin, scoring
    rule and query keeps working unchanged. `provider` is a new, separate
    axis: which collection mechanism owns this source (e.g. "rss", "x",
    "manual", "pci_ids", "replay"). A source's `type` and `provider` are
    independent on purpose: a curated news feed is typically type=RSS,
    provider="rss", but a manually-entered registry note is type=MANUAL,
    provider="manual" with no collection at all.

    Identity for idempotent import/dedup is `(provider, provider_key)`, not
    `name` -- see IngestionService.ensure_source's historical name-only dedup,
    which is deliberately NOT reused for the new signal-collection path.
    provider_key is nullable (existing/manual sources have none), and SQLite
    unique constraints treat NULLs as distinct, so this is compatible with
    every pre-merge row.
    """

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("provider", "provider_key", name="uq_sources_provider_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    type: Mapped[SourceType] = mapped_column(SAEnum(SourceType))
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    trust_weight: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    # --- Signal Radar absorption: operational collection fields -----------
    provider: Mapped[str] = mapped_column(String(50), default="manual", index=True)
    provider_key: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    polling_enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    muted: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    languages: Mapped[str] = mapped_column(Text, default="[]")  # JSON list[str]
    expertise: Mapped[str] = mapped_column(Text, default="[]")  # JSON list[str]
    signal_types: Mapped[str] = mapped_column(Text, default="[]")  # JSON list[str]
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)  # opaque provider cursor
    last_success_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_observed_item_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    error_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    provider_metadata: Mapped[str] = mapped_column(Text, default="{}")  # JSON dict, no secrets


class SourceReputation(Base):
    """Deterministic, counter-based reputation for one Source (v1.0.0
    Candidate Intelligence, Phase 1). Deliberately separate from
    `Source.trust_weight` (a static, manually-set field) and from
    `source_intelligence.SourceAccuracyReport` (dynamically computed from
    resolved Claim outcomes, a different denominator) -- this table is the
    candidate-level counterpart, fed by `SignalIndependenceGroup` origin
    attribution and candidate promotion/dismissal outcomes.

    Every numeric field here is a deterministic counter or ratio computed
    by `semi_intel.signals.reputation.recompute_source_reputation()` --
    never a machine-learned weight. `*_override` fields let an operator
    manually pin a value (e.g. "this is an official OEM account, authority
    is Very High regardless of history"); the computed field is always
    still stored and shown, so an override never hides the underlying
    evidence.
    """

    __tablename__ = "source_reputations"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), unique=True, index=True)

    authority: Mapped[float] = mapped_column(Float, default=0.5)  # 0..1, computed
    authority_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    historical_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)  # from source_intelligence, when available
    editorial_yield: Mapped[float] = mapped_column(Float, default=0.0)  # promoted candidates / items contributed to
    noise_rate: Mapped[float] = mapped_column(Float, default=0.0)  # dismissed candidates / items contributed to
    originality: Mapped[float] = mapped_column(Float, default=0.0)  # times this source was the origin of an independence group / total groups it appears in
    specializations: Mapped[str] = mapped_column(Text, default="[]")  # JSON list[str], topic names this source most often matches
    lead_time_hours: Mapped[float | None] = mapped_column(Float, nullable=True)  # avg hours this source's origin items lead the next confirmation
    verification_count: Mapped[int] = mapped_column(Integer, default=0)  # candidates this source originated that were later promoted
    false_positive_count: Mapped[int] = mapped_column(Integer, default=0)  # candidates this source originated that were later dismissed

    items_contributed: Mapped[int] = mapped_column(Integer, default=0)
    independence_groups_originated: Mapped[int] = mapped_column(Integer, default=0)
    independence_groups_appeared_in: Mapped[int] = mapped_column(Integer, default=0)

    last_updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Evidence(Base):
    """An immutable observation captured from a source."""

    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    raw_content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # The source's own identifier for this item (RSS guid, "vendor_id:device_id"
    # for a PCI ID entry, etc.), when the source plugin has one. Not used for
    # dedup -- content_hash owns that -- but useful provenance for tracing an
    # evidence row back to exactly what the source called it.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    observed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    collected_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    # Set only when this Evidence row was created by promoting a SignalCandidate.
    # Nullable/unique: lets promotion be idempotent (reprocessing or re-running
    # promotion for the same signal item must not create a second Evidence row)
    # without affecting the many Evidence rows that never went through Radar's
    # sensory pipeline at all (direct RSS/PCI-ID plugins, manual entry).  This
    # is the sole provenance link; keeping a reverse FK on SignalItem would be
    # redundant and would create a circular table dependency.
    origin_signal_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("signal_items.id"), nullable=True, unique=True, index=True
    )


class Claim(Base):
    """The atomic unit of the platform: a falsifiable, trackable assertion."""

    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement: Mapped[str] = mapped_column(Text)
    subject_entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"), nullable=True, index=True)
    status: Mapped[ClaimStatus] = mapped_column(SAEnum(ClaimStatus), default=ClaimStatus.OPEN, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class ClaimEvidenceLink(Base):
    """Ties one piece of evidence to one claim, with the stance it takes."""

    __tablename__ = "claim_evidence_links"
    __table_args__ = (UniqueConstraint("claim_id", "evidence_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("evidence.id"), index=True)
    stance: Mapped[EvidenceStance] = mapped_column(SAEnum(EvidenceStance))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class ClaimEvent(Base):
    """Append-only timeline of everything that happened to a claim."""

    __tablename__ = "claim_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    event_type: Mapped[ClaimEventType] = mapped_column(SAEnum(ClaimEventType))
    confidence_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class ClaimLinkSuggestion(Base):
    """A rules-based proposal that a piece of evidence is relevant to an open
    claim. Always starts PENDING; a human resolves it to ACCEPTED (which
    creates a real ClaimEvidenceLink with a human-chosen stance) or REJECTED.
    Never mutated by the scoring engine after creation -- rerunning the
    scanner only ever adds new suggestions for pairs that don't have one yet.
    """

    __tablename__ = "claim_link_suggestions"
    __table_args__ = (UniqueConstraint("evidence_id", "claim_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("evidence.id"), index=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    score: Mapped[float] = mapped_column(Float)
    reasons: Mapped[str] = mapped_column(Text, default="[]")  # JSON-encoded list[str]
    status: Mapped[SuggestionStatus] = mapped_column(
        SAEnum(SuggestionStatus), default=SuggestionStatus.PENDING, index=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class MemorySpecClaim(Base):
    """The structured half of a memory-configuration claim: bus width, per-chip
    density, and the total capacity being claimed. One per Claim. The
    contradiction engine (semi_intel/contradiction_engine/memory_rules.py)
    checks whether claimed_total_gb is arithmetically buildable from
    bus_width_bits and chip_density_gbit under standard or clamshell GDDR
    population, and records the result as a ClaimEvent -- this table itself
    is never mutated by the checker, only read.
    """

    __tablename__ = "memory_spec_claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), unique=True, index=True)
    memory_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. "GDDR6X"; informational only in M3
    bus_width_bits: Mapped[int] = mapped_column(Integer)
    chip_density_gbit: Mapped[float] = mapped_column(Float)
    claimed_total_gb: Mapped[float] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class MonitoredTopic(Base):
    """An editor-managed subject used to discover relevant evidence."""

    __tablename__ = "monitored_topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    keyword: Mapped[str] = mapped_column(String(255))
    aliases: Mapped[str] = mapped_column(Text, default="[]")
    category: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    priority: Mapped[float] = mapped_column(Float, default=0.5)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class EditorialStory(Base):
    """A conservative cluster of evidence articles for the editorial inbox."""

    __tablename__ = "editorial_stories"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    headline: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text, default="")
    interest_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    score_reasons: Mapped[str] = mapped_column(Text, default="[]")
    coverage_count: Mapped[int] = mapped_column(Integer, default=1)
    seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    new_coverage_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)
    latest_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class StoryEvidence(Base):
    __tablename__ = "story_evidence"
    __table_args__ = (UniqueConstraint("story_id", "evidence_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("editorial_stories.id"), index=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("evidence.id"), index=True)
    added_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class TopicMatch(Base):
    __tablename__ = "topic_matches"
    __table_args__ = (UniqueConstraint("story_id", "topic_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("editorial_stories.id"), index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("monitored_topics.id"), index=True)
    matched_text: Mapped[str] = mapped_column(String(255))
    match_score: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (UniqueConstraint("evidence_id", "destination_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("evidence.id"), index=True)
    destination_url: Mapped[str] = mapped_column(String(2048))
    destination_domain: Mapped[str] = mapped_column(String(255), index=True)
    link_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_editorial: Mapped[bool] = mapped_column(Boolean, default=True)
    discovered_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class SourceSuggestion(Base):
    """Unified suggested-sources workflow (brief section 14).

    Pre-merge this was domain-only (citation mining from Evidence HTML).
    `kind` distinguishes that original domain suggestion from a new
    platform/handle suggestion (an X or other social account repeatedly
    credited/quoted by collected signal items); `domain` stays the unique
    key for kind=domain rows, while kind=handle rows key off
    (platform, provider_key) instead -- `domain` is left blank for those.
    """

    __tablename__ = "source_suggestions"
    __table_args__ = (
        UniqueConstraint("platform", "provider_key", name="uq_source_suggestions_platform_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    inferred_name: Mapped[str] = mapped_column(String(255))
    feed_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    reasons: Mapped[str] = mapped_column(Text, default="[]")
    appearances: Mapped[int] = mapped_column(Integer, default=0)
    story_count: Mapped[int] = mapped_column(Integer, default=0)
    topic_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[SourceSuggestionStatus] = mapped_column(
        SAEnum(SourceSuggestionStatus), default=SourceSuggestionStatus.PENDING, index=True
    )
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    # --- Signal Radar absorption: platform/handle suggestions -------------
    kind: Mapped[SourceSuggestionKind] = mapped_column(
        SAEnum(SourceSuggestionKind), default=SourceSuggestionKind.DOMAIN, index=True
    )
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    provider_key: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    independent_origin_count: Mapped[int] = mapped_column(Integer, default=0)
    inferred_reliability: Mapped[float | None] = mapped_column(Float, nullable=True)


class DiscoverySettings(Base):
    """Single-user persisted controls for the bounded discovery ring."""

    __tablename__ = "discovery_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str] = mapped_column(String(50), default="google_news_rss")
    minimum_interest_score: Mapped[float] = mapped_column(Float, default=0.55)
    maximum_story_age_hours: Mapped[int] = mapped_column(Integer, default=48)
    cooldown_hours: Mapped[int] = mapped_column(Integer, default=6)
    maximum_cycles_per_story: Mapped[int] = mapped_column(Integer, default=3)
    maximum_queries_per_cycle: Mapped[int] = mapped_column(Integer, default=3)
    results_per_query: Mapped[int] = mapped_column(Integer, default=10)
    maximum_results_per_cycle: Mapped[int] = mapped_column(Integer, default=30)
    global_cycles_per_hour: Mapped[int] = mapped_column(Integer, default=5)
    provider_requests_per_hour: Mapped[int] = mapped_column(Integer, default=15)
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, default=8)
    cache_hours: Mapped[int] = mapped_column(Integer, default=6)
    language: Mapped[str] = mapped_column(String(20), default="en-US")
    region: Mapped[str] = mapped_column(String(10), default="US")
    attribution_phrases: Mapped[str] = mapped_column(
        Text,
        default='["according to","via","reports","reported by","citing","source","exclusive","originally published by"]',
    )
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("editorial_stories.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[DiscoveryRunStatus] = mapped_column(SAEnum(DiscoveryRunStatus), index=True)
    eligibility_reason: Mapped[str] = mapped_column(Text)
    queries: Mapped[str] = mapped_column(Text, default="[]")
    query_reasons: Mapped[str] = mapped_column(Text, default="[]")
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_result_count: Mapped[int] = mapped_column(Integer, default=0)
    accepted_result_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_result_count: Mapped[int] = mapped_column(Integer, default=0)
    filtered_result_count: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    budget_state: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class DiscoveryResult(Base):
    __tablename__ = "discovery_results"
    __table_args__ = (UniqueConstraint("run_id", "canonical_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("discovery_runs.id"), index=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("editorial_stories.id"), index=True)
    query: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    provider_result_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    original_url: Mapped[str] = mapped_column(String(2048))
    canonical_url: Mapped[str] = mapped_column(String(2048), index=True)
    canonical_domain: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(500))
    snippet: Mapped[str] = mapped_column(Text, default="")
    publication_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    provider_rank: Mapped[int] = mapped_column(Integer)
    relevance_score: Mapped[float] = mapped_column(Float, index=True)
    accepted: Mapped[bool] = mapped_column(Boolean, index=True)
    relationship: Mapped[DiscoveryRelationship] = mapped_column(SAEnum(DiscoveryRelationship))
    explanation: Mapped[str] = mapped_column(Text, default="[]")
    supporting_phrase: Mapped[str | None] = mapped_column(String(500), nullable=True)
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


# =============================================================================
# Signal layer (Signal Radar absorption)
#
# Everything below implements the pipeline described in the merge brief:
#
#   Provider collection -> SignalItem (raw, immutable) -> signal analysis
#   (SignalEntityMention/SignalLabel, proposals only) -> SignalCandidate
#   (bounded, attention-scored cluster) -> promotion -> Evidence/EditorialStory
#   (unchanged canonical layer above).
#
# None of this is wired into EditorialDiscoveryService's clustering or
# Claim/Evidence trust rules -- it is a new, separate front end that
# eventually *produces* Evidence rows (see Evidence.origin_signal_item_id),
# the same way the existing ingestion plugins do today. Signal Radar's own
# `stories`/`story_entities`/`evidence` tables are intentionally not modeled
# here at all: they are not canonical and are never imported as such (see
# PHASE0_AUDIT.md section 6).
# =============================================================================


class SignalItem(Base):
    """One raw, immutable observation collected by a provider.

    Distinct from `Evidence`: an Evidence row is canonical, trusted, and
    feeds claims/editorial scoring directly. A SignalItem is unfiltered raw
    material -- most SignalItems never become Evidence at all. Deduplication
    is `(provider, external_id)` first (provider identity is authoritative);
    `content_hash` is only an additional guard, e.g. to catch a provider that
    reissues the same content under a new external_id.

    `raw_payload` is never overwritten once written, even when extraction
    rules change -- reprocessing re-reads it and only replaces the derived
    columns (normalized_text stays as originally normalized; a genuine
    reprocessing pass that changes normalization creates a new
    `processing_version`, it does not mutate raw_payload).
    """

    __tablename__ = "signal_items"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_signal_items_provider_external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    external_id: Mapped[str] = mapped_column(String(500), index=True)

    raw_payload: Mapped[str] = mapped_column(Text)  # JSON, immutable
    normalized_text: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    expanded_links: Mapped[str] = mapped_column(Text, default="[]")  # JSON list[str]

    author_handle: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    author_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    collected_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fidelity: Mapped[str] = mapped_column(String(20), default="full")  # full / low_fidelity

    # Lineage kept both as the raw provider-native reference (always available,
    # even before the parent has been collected) and as a resolved internal FK
    # (populated once/if the parent SignalItem exists locally).
    quoted_external_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    quoted_signal_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("signal_items.id"), nullable=True, index=True
    )
    reply_to_external_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reply_to_signal_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("signal_items.id"), nullable=True, index=True
    )

    deleted_observed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)

    processing_version: Mapped[int] = mapped_column(Integer, default=0)
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    processing_state: Mapped[SignalProcessingState] = mapped_column(
        SAEnum(SignalProcessingState), default=SignalProcessingState.PENDING, index=True
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class SignalMedia(Base):
    __tablename__ = "signal_media"
    __table_args__ = (UniqueConstraint("signal_item_id", "remote_url", name="uq_signal_media_item_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_item_id: Mapped[int] = mapped_column(ForeignKey("signal_items.id"), index=True)
    media_type: Mapped[str] = mapped_column(String(20))  # image/video/gif
    remote_url: Mapped[str] = mapped_column(String(2048))
    alt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_quoted: Mapped[bool] = mapped_column(Boolean, default=False)
    local_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    download_state: Mapped[SignalMediaDownloadState] = mapped_column(
        SAEnum(SignalMediaDownloadState), default=SignalMediaDownloadState.PENDING, index=True
    )
    downloaded_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_engine: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class SignalEntityMention(Base):
    """A candidate mention -- NOT automatically a canonical Entity.

    This is the fix for the "Jensen Huang becomes a subject entity" failure
    mode documented in PHASE0_AUDIT.md: an unknown TitleCase-shaped phrase
    stays a `status=candidate` row here, visible for review, until something
    resolves it to a real `Entity` (status=resolved) or a human/rule rejects
    it (status=rejected/ignored). Nothing downstream (SignalCandidate
    clustering, attention scoring) may treat a `candidate` mention as
    equivalent to a resolved one.
    """

    __tablename__ = "signal_entity_mentions"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_item_id: Mapped[int] = mapped_column(ForeignKey("signal_items.id"), index=True)
    candidate_text: Mapped[str] = mapped_column(String(500), index=True)
    proposed_entity_type: Mapped[str] = mapped_column(String(30))  # broader than EntityType: incl. codename, pci_id, ...
    resolved_entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"), nullable=True, index=True)
    span_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extractor: Mapped[str] = mapped_column(String(100))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    processing_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[SignalMentionStatus] = mapped_column(
        SAEnum(SignalMentionStatus), default=SignalMentionStatus.CANDIDATE, index=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class SignalLabel(Base):
    __tablename__ = "signal_labels"
    __table_args__ = (UniqueConstraint("signal_item_id", "label", name="uq_signal_labels_item_label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_item_id: Mapped[int] = mapped_column(ForeignKey("signal_items.id"), index=True)
    label: Mapped[str] = mapped_column(String(100), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    rule: Mapped[str] = mapped_column(String(200))
    processing_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class SignalTopicMatch(Base):
    """A monitored-topic hit on one SignalItem's text (brief section 8, tier
    1: "known monitored topic or alias match -- high confidence"). Reuses
    the exact same word-boundary `match_topic()`/`normalize_phrase()` logic
    already proven in semi_intel/editorial/service.py for canonical
    EditorialStory/TopicMatch -- one matching rule, not two."""

    __tablename__ = "signal_topic_matches"
    __table_args__ = (UniqueConstraint("signal_item_id", "topic_id", name="uq_signal_topic_matches"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_item_id: Mapped[int] = mapped_column(ForeignKey("signal_items.id"), index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("monitored_topics.id"), index=True)
    matched_text: Mapped[str] = mapped_column(String(255))
    processing_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class ProviderRun(Base):
    """Collection telemetry for one provider pass over one source (or a
    provider-wide maintenance pass when source_id is null)."""

    __tablename__ = "provider_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True, index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    items_collected: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_skipped: Mapped[int] = mapped_column(Integer, default=0)
    cursor_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProviderRunStatus] = mapped_column(
        SAEnum(ProviderRunStatus), default=ProviderRunStatus.RUNNING, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SignalCandidate(Base):
    """A bounded, attention-scored cluster of SignalItems: the layer neither
    source project had. Sits between raw collection and the editorial inbox;
    promotion (see semi_intel/candidates/promotion.py, Phase 5) is the only
    path from here into EditorialStory."""

    __tablename__ = "signal_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[SignalCandidateState] = mapped_column(
        SAEnum(SignalCandidateState), default=SignalCandidateState.ACTIVE, index=True
    )

    attention_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    score_explanation: Mapped[str] = mapped_column(Text, default="{}")  # JSON: per-component breakdown

    # --- Candidate Intelligence (v1.0.0) ---------------------------------
    # Deliberately separate from attention_score: attention asks "should a
    # human look at this at all", confidence asks "how likely is this to be
    # true", editorial_value asks "how much would an editor care". Same
    # {total, components: {...}} JSON shape as score_explanation for UI
    # consistency, computed by semi_intel/signals/confidence_engine.py and
    # semi_intel/signals/editorial_value_engine.py respectively -- both
    # persisted here (not recomputed on every read) to match the existing
    # attention_score/score_explanation pattern.
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    confidence_explanation: Mapped[str] = mapped_column(Text, default="{}")
    editorial_value_score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    editorial_value_explanation: Mapped[str] = mapped_column(Text, default="{}")
    timeline_stage: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)

    first_observed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)
    latest_observed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)
    momentum_window_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    item_count: Mapped[int] = mapped_column(Integer, default=0)
    distinct_source_count: Mapped[int] = mapped_column(Integer, default=0)
    independent_source_group_count: Mapped[int] = mapped_column(Integer, default=0)

    primary_topic_id: Mapped[int | None] = mapped_column(ForeignKey("monitored_topics.id"), nullable=True, index=True)
    strongest_artifact_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    likely_origin_signal_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("signal_items.id"), nullable=True, index=True
    )

    promoted_story_id: Mapped[int | None] = mapped_column(ForeignKey("editorial_stories.id"), nullable=True, index=True)

    seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    snoozed_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    dismissed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    dismissed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    clustering_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class CandidateSignalItem(Base):
    """Join: which SignalItems make up a candidate, and why each attached
    (human-readable reasons, per brief section 9 -- never silent)."""

    __tablename__ = "candidate_signal_items"
    __table_args__ = (UniqueConstraint("candidate_id", "signal_item_id", name="uq_candidate_signal_items"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("signal_candidates.id"), index=True)
    signal_item_id: Mapped[int] = mapped_column(ForeignKey("signal_items.id"), index=True)
    attach_reasons: Mapped[str] = mapped_column(Text, default="[]")  # JSON list[str], human-readable
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class CandidateTopicMatch(Base):
    __tablename__ = "candidate_topic_matches"
    __table_args__ = (UniqueConstraint("candidate_id", "topic_id", name="uq_candidate_topic_matches"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("signal_candidates.id"), index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("monitored_topics.id"), index=True)
    matched_text: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class CandidateEntity(Base):
    __tablename__ = "candidate_entities"
    __table_args__ = (UniqueConstraint("candidate_id", "entity_id", name="uq_candidate_entities"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("signal_candidates.id"), index=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    role: Mapped[CandidateEntityRole] = mapped_column(SAEnum(CandidateEntityRole), default=CandidateEntityRole.MENTIONED)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class CandidateRelationship(Base):
    """Candidate<->candidate edges: reversible/audited merges, or a looser
    'related_to'/'duplicate_of' link surfaced for review rather than
    auto-merged."""

    __tablename__ = "candidate_relationships"
    __table_args__ = (
        UniqueConstraint("from_candidate_id", "to_candidate_id", "relation_type", name="uq_candidate_relationships"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    from_candidate_id: Mapped[int] = mapped_column(ForeignKey("signal_candidates.id"), index=True)
    to_candidate_id: Mapped[int] = mapped_column(ForeignKey("signal_candidates.id"), index=True)
    relation_type: Mapped[CandidateRelationType] = mapped_column(SAEnum(CandidateRelationType))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class SignalIndependenceGroup(Base):
    """One dependency cluster within a candidate's items: everything in the
    group is treated as a single origin for source-diversity purposes (brief
    section 10). `reason` records why the group was formed (same_url,
    citation, same_author, syndication, press_release, ...)."""

    __tablename__ = "signal_independence_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("signal_candidates.id"), index=True)
    origin_signal_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("signal_items.id"), nullable=True, index=True
    )
    reason: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class SignalIndependenceGroupMember(Base):
    __tablename__ = "signal_independence_group_members"
    __table_args__ = (UniqueConstraint("group_id", "signal_item_id", name="uq_independence_group_members"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("signal_independence_groups.id"), index=True)
    signal_item_id: Mapped[int] = mapped_column(ForeignKey("signal_items.id"), index=True)


class SignalCollectionSettings(Base):
    """Single-row persisted controls for provider collection. Everything
    defaults to off/conservative so an upgrade never starts new network
    activity on its own (brief sections 17, 26)."""

    __tablename__ = "signal_collection_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    collection_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    x_provider_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    startup_stagger_seconds: Mapped[int] = mapped_column(Integer, default=45)
    media_download_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ocr_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AttentionScoringSettings(Base):
    """Single-row persisted, configurable attention-score weights (brief
    section 11). Weights are validated/normalized to sum to 1.0 by the
    scoring service on save, not enforced at the DB layer."""

    __tablename__ = "attention_scoring_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    weight_topic_relevance: Mapped[float] = mapped_column(Float, default=0.30)
    weight_novelty: Mapped[float] = mapped_column(Float, default=0.20)
    weight_momentum: Mapped[float] = mapped_column(Float, default=0.15)
    weight_source_diversity: Mapped[float] = mapped_column(Float, default=0.15)
    weight_artifact_strength: Mapped[float] = mapped_column(Float, default=0.15)
    weight_source_quality: Mapped[float] = mapped_column(Float, default=0.05)
    momentum_window_hours: Mapped[int] = mapped_column(Integer, default=24)
    staleness_days: Mapped[int] = mapped_column(Integer, default=14)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class CandidatePromotionSettings(Base):
    """Single-row persisted controls for editorial promotion (brief section
    12). Automatic promotion defaults OFF; enabling it requires every
    threshold below to already be persisted with a conservative default."""

    __tablename__ = "candidate_promotion_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    automatic_promotion_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    minimum_attention_score: Mapped[float] = mapped_column(Float, default=0.75)
    required_topic_match: Mapped[bool] = mapped_column(Boolean, default=True)
    maximum_candidate_age_hours: Mapped[int] = mapped_column(Integer, default=72)
    hourly_promotion_budget: Mapped[int] = mapped_column(Integer, default=3)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class CandidatePromotionEvent(Base):
    """Append-only audit trail for candidate promotion (brief section 12,
    point 10: "Record an audit event explaining who or what promoted the
    candidate"). Mirrors ClaimEvent's append-only-history convention rather
    than storing promotion metadata as mutable columns on SignalCandidate."""

    __tablename__ = "candidate_promotion_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("signal_candidates.id"), index=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("editorial_stories.id"), index=True)
    promoted_by: Mapped[str] = mapped_column(String(100))  # e.g. "human:<username>" or "automatic"
    automatic: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)


# --- Phase 8: notifications, digests, delivery and provider incidents -------


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[NotificationEventType] = mapped_column(SAEnum(NotificationEventType), index=True)
    severity: Mapped[NotificationSeverity] = mapped_column(SAEnum(NotificationSeverity), index=True)
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    dedup_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("signal_candidates.id"), nullable=True, index=True)
    story_id: Mapped[int | None] = mapped_column(ForeignKey("editorial_stories.id"), nullable=True, index=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("monitored_topics.id"), nullable=True, index=True)
    source_suggestion_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_suggestions.id"), nullable=True, index=True
    )
    provider_run_id: Mapped[int | None] = mapped_column(ForeignKey("provider_runs.id"), nullable=True, index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True, index=True)

    event_metadata: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    event_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    first_occurrence_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    latest_occurrence_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    dismissed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    muted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    delivery_state: Mapped[NotificationDeliveryState] = mapped_column(
        SAEnum(NotificationDeliveryState), default=NotificationDeliveryState.IN_APP, index=True
    )
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class NotificationSettings(Base):
    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    external_delivery_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    windows_desktop_notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    activation_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    minimum_attention_score: Mapped[float] = mapped_column(Float, default=0.70)
    minimum_score_increase: Mapped[float] = mapped_column(Float, default=0.15)
    required_independent_group_count: Mapped[int] = mapped_column(Integer, default=2)
    required_topic_match: Mapped[bool] = mapped_column(Boolean, default=True)
    high_score_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    score_increase_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    corroboration_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    promotion_ready_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    promotion_completed_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source_suggestion_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    provider_health_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    topic_activity_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_digest_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    digest_time: Mapped[str] = mapped_column(String(5), default="08:00")
    timezone: Mapped[str] = mapped_column(String(100), default="Asia/Kolkata")
    quiet_hours_start: Mapped[str] = mapped_column(String(5), default="22:00")
    quiet_hours_end: Mapped[str] = mapped_column(String(5), default="07:00")
    maximum_immediate_per_hour: Mapped[int] = mapped_column(Integer, default=5)
    maximum_delivery_attempts: Mapped[int] = mapped_column(Integer, default=3)
    provider_failure_threshold: Mapped[int] = mapped_column(Integer, default=3)
    provider_recovery_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source_suggestion_minimum_score: Mapped[float] = mapped_column(Float, default=0.65)
    topic_activity_minimum_candidates: Mapped[int] = mapped_column(Integer, default=2)
    topic_activity_window_hours: Mapped[int] = mapped_column(Integer, default=24)
    retention_days: Mapped[int] = mapped_column(Integer, default=90)
    digest_maximum_items_per_section: Mapped[int] = mapped_column(Integer, default=5)
    muted_event_types: Mapped[str] = mapped_column(Text, default="[]")
    muted_topic_ids: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class NotificationEventState(Base):
    """Transition watermark. Current state alone cannot prove a threshold
    crossing, and this table is what makes repeated generation idempotent."""

    __tablename__ = "notification_event_states"
    __table_args__ = (
        UniqueConstraint("event_type", "subject_kind", "subject_id", name="uq_notification_event_state_subject"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[NotificationEventType] = mapped_column(SAEnum(NotificationEventType), index=True)
    subject_kind: Mapped[str] = mapped_column(String(50), index=True)
    subject_id: Mapped[int] = mapped_column(Integer, index=True)
    last_numeric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_boolean_value: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    state_metadata: Mapped[str] = mapped_column(Text, default="{}")
    last_event_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class NotificationDeliveryAttempt(Base):
    __tablename__ = "notification_delivery_attempts"
    __table_args__ = (
        UniqueConstraint("notification_id", "digest_id", "channel", "attempt_number",
                         name="uq_notification_delivery_attempt"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int | None] = mapped_column(ForeignKey("notifications.id"), nullable=True, index=True)
    digest_id: Mapped[int | None] = mapped_column(ForeignKey("notification_digests.id"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(50), index=True)
    adapter_name: Mapped[str] = mapped_column(String(100))
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[DeliveryAttemptStatus] = mapped_column(SAEnum(DeliveryAttemptStatus), index=True)
    attempted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_after: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), index=True)


class NotificationDigest(Base):
    __tablename__ = "notification_digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    timezone: Mapped[str] = mapped_column(String(100))
    window_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    window_end: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    generated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    status: Mapped[NotificationDigestStatus] = mapped_column(
        SAEnum(NotificationDigestStatus), default=NotificationDigestStatus.READY, index=True
    )
    notification_ids: Mapped[str] = mapped_column(Text, default="[]")
    structured_sections: Mapped[str] = mapped_column(Text, default="{}")
    rendered_text: Mapped[str] = mapped_column(Text, default="")
    delivery_state: Mapped[NotificationDeliveryState] = mapped_column(
        SAEnum(NotificationDeliveryState), default=NotificationDeliveryState.IN_APP, index=True
    )


class ProviderIncident(Base):
    __tablename__ = "provider_incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True, index=True)
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    latest_failure_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    latest_provider_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_runs.id"), nullable=True, index=True
    )
    latest_error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_notification_id: Mapped[int | None] = mapped_column(
        ForeignKey("notifications.id"), nullable=True, index=True
    )
    recovery_notification_id: Mapped[int | None] = mapped_column(
        ForeignKey("notifications.id"), nullable=True, index=True
    )


# --- Phase 9: operational automation, feedback and recovery ----------------


class SchedulerSettings(Base):
    __tablename__ = "scheduler_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    scheduler_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    pipeline_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    digest_time: Mapped[str] = mapped_column(String(5), default="08:00")
    backup_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    backup_time: Mapped[str] = mapped_column(String(5), default="03:00")
    maintenance_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    maintenance_time: Mapped[str] = mapped_column(String(5), default="04:00")
    timezone: Mapped[str] = mapped_column(String(100), default="Asia/Kolkata")
    maximum_job_duration_minutes: Mapped[int] = mapped_column(Integer, default=120)
    retry_delay_minutes: Mapped[int] = mapped_column(Integer, default=15)
    maximum_automatic_retries: Mapped[int] = mapped_column(Integer, default=2)
    stale_run_threshold_minutes: Mapped[int] = mapped_column(Integer, default=180)
    missed_run_warning_minutes: Mapped[int] = mapped_column(Integer, default=90)
    startup_catchup_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    backup_directory: Mapped[str] = mapped_column(String(1000), default="backups")
    backup_retention_count: Mapped[int] = mapped_column(Integer, default=14)
    backup_retention_days: Mapped[int] = mapped_column(Integer, default=30)
    last_scheduler_heartbeat: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_scheduler_invocation: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_successful_job_commit: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    active_notification_preset: Mapped[str] = mapped_column(String(50), default="balanced")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class OperationalJobRun(Base):
    __tablename__ = "operational_job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[OperationalJobType] = mapped_column(SAEnum(OperationalJobType), index=True)
    trigger_type: Mapped[OperationalTriggerType] = mapped_column(
        SAEnum(OperationalTriggerType), index=True
    )
    scheduled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[OperationalJobStatus] = mapped_column(SAEnum(OperationalJobStatus), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    parent_retry_id: Mapped[int | None] = mapped_column(
        ForeignKey("operational_job_runs.id"), nullable=True, index=True
    )
    owner_identity: Mapped[str] = mapped_column(String(255), default="")
    lock_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    result_counts: Mapped[str] = mapped_column(Text, default="{}")
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    diagnostic_reference: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class OperationalJobLease(Base):
    __tablename__ = "operational_job_leases"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[OperationalJobType] = mapped_column(
        SAEnum(OperationalJobType), unique=True, index=True
    )
    owner_identity: Mapped[str] = mapped_column(String(255))
    lock_token: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    acquired_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    refreshed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)


class NotificationFeedback(Base):
    __tablename__ = "notification_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notifications.id"), unique=True, index=True
    )
    rating: Mapped[NotificationFeedbackRating] = mapped_column(
        SAEnum(NotificationFeedbackRating), index=True
    )
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class SavedNotificationView(Base):
    __tablename__ = "saved_notification_views"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    state_filter: Mapped[str] = mapped_column(String(30), default="unread")
    event_types: Mapped[str] = mapped_column(Text, default="[]")
    severities: Mapped[str] = mapped_column(Text, default="[]")
    topic_ids: Mapped[str] = mapped_column(Text, default="[]")
    relation_filters: Mapped[str] = mapped_column(Text, default="{}")
    date_window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    search_text: Mapped[str] = mapped_column(String(500), default="")
    sort_order: Mapped[str] = mapped_column(String(50), default="newest")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class DeliveryAdapterStatus(Base):
    __tablename__ = "delivery_adapter_status"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    adapter_type: Mapped[str] = mapped_column(String(50), default="webhook")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    configured_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    configuration_present: Mapped[bool] = mapped_column(Boolean, default=False)
    last_test_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_successful: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_success_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    path: Mapped[str] = mapped_column(String(1000))
    status: Mapped[BackupStatus] = mapped_column(SAEnum(BackupStatus), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    application_version: Mapped[str] = mapped_column(String(50))
    alembic_revision: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    integrity_result: Mapped[str] = mapped_column(String(100), default="unknown")
    table_count: Mapped[int] = mapped_column(Integer, default=0)
    record_counts: Mapped[str] = mapped_column(Text, default="{}")
    manifest_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
