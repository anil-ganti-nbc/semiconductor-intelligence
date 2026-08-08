"""Controlled vocabularies for the platform.

These are intentionally small for M0. They will grow as new domains (packaging,
regulatory filings, benchmarks, ...) get their own source plugins -- but every
addition should be a deliberate decision, not an AI-invented free-text field.
"""

from __future__ import annotations

from enum import Enum


class EntityType(str, Enum):
    PRODUCT = "product"
    COMPANY = "company"
    PERSON = "person"
    ARCHITECTURE = "architecture"
    FOUNDRY_NODE = "foundry_node"
    PACKAGING_TECH = "packaging_tech"
    MEMORY_TECH = "memory_tech"
    BENCHMARK = "benchmark"
    OTHER = "other"


class RelationType(str, Enum):
    MANUFACTURED_BY = "manufactured_by"
    USES_NODE = "uses_node"
    USES_PACKAGING = "uses_packaging"
    USES_MEMORY = "uses_memory"
    SUCCESSOR_OF = "successor_of"
    PART_OF = "part_of"
    COMPETES_WITH = "competes_with"
    RELATED_TO = "related_to"


class SourceType(str, Enum):
    RSS = "rss"
    FORUM = "forum"
    SOCIAL = "social"
    RETAIL = "retail"
    REGULATORY = "regulatory"
    KERNEL = "kernel"
    REGISTRY = "registry"  # structured public databases: pci.ids, usb.ids, ...
    PATENT = "patent"
    MANUAL = "manual"
    OTHER = "other"


class ClaimStatus(str, Enum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    DEBUNKED = "debunked"
    RETRACTED = "retracted"


class EvidenceStance(str, Enum):
    SUPPORTS = "supports"
    WEAKENS = "weakens"
    CONTRADICTS = "contradicts"


class ClaimEventType(str, Enum):
    CREATED = "created"
    EVIDENCE_LINKED = "evidence_linked"
    EVIDENCE_UNLINKED = "evidence_unlinked"
    CONFIDENCE_UPDATED = "confidence_updated"
    STATUS_CHANGED = "status_changed"
    # Written by the contradiction engine (semi_intel/contradiction_engine/)
    # when a structured sub-claim (e.g. a memory configuration) is checked
    # against engineering constraints. Never changes confidence or status by
    # itself -- it only records what the rules engine found, in the claim's
    # permanent history, for a human to act on.
    CONTRADICTION_CHECK_PASSED = "contradiction_check_passed"
    CONTRADICTION_DETECTED = "contradiction_detected"


class SuggestionStatus(str, Enum):
    """Status of a rules-based claim-link suggestion (see claim_engine/).
    A suggestion is a proposal, never a fact -- it only becomes a real
    ClaimEvidenceLink once a human accepts it."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class SourceSuggestionStatus(str, Enum):
    """Editorial review state for an automatically discovered website."""

    PENDING = "pending"
    IGNORED = "ignored"
    BLOCKED = "blocked"
    ADDED = "added"


class DiscoveryRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED = "failed"


class DiscoveryRelationship(str, Enum):
    INDEPENDENT = "independent_coverage"
    FOLLOW_UP = "likely_follow_up"
    CITES_KNOWN_SOURCE = "explicitly_cites_known_source"
    CITES_LIKELY_ORIGIN = "explicitly_cites_likely_origin"
    POSSIBLE_ORIGIN = "possible_original_report"
    SYNDICATED = "syndicated_or_duplicated"
    UNKNOWN = "unknown"


# --- Signal Radar absorption: sensory-layer vocabularies -------------------
# These back the raw collection -> analysis -> candidate pipeline (see
# semi_intel/domain/models.py "Signal layer" section). They are deliberately
# plain strings on the model columns (not SAEnum) for `provider`/platform,
# since new providers must be addable without a migration; these Python
# enums exist for state machines only, where a closed set is correct.


class SignalProcessingState(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class SignalMediaDownloadState(str, Enum):
    PENDING = "pending"
    DOWNLOADED = "downloaded"
    FAILED = "failed"
    SKIPPED = "skipped"


class SignalMentionStatus(str, Enum):
    RESOLVED = "resolved"
    CANDIDATE = "candidate"
    REJECTED = "rejected"
    IGNORED = "ignored"


class ProviderRunStatus(str, Enum):
    RUNNING = "running"
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


class SignalCandidateState(str, Enum):
    ACTIVE = "active"
    PROMOTED = "promoted"
    DISMISSED = "dismissed"
    SNOOZED = "snoozed"
    STALE = "stale"
    MERGED = "merged"


class CandidateEntityRole(str, Enum):
    SUBJECT = "subject"
    MENTIONED = "mentioned"


class CandidateRelationType(str, Enum):
    MERGED_INTO = "merged_into"
    RELATED_TO = "related_to"
    DUPLICATE_OF = "duplicate_of"


class SourceSuggestionKind(str, Enum):
    DOMAIN = "domain"
    HANDLE = "handle"


# --- Phase 8: deterministic notifications and digest delivery ---------------


class NotificationEventType(str, Enum):
    HIGH_ATTENTION = "high_attention"
    SCORE_INCREASE = "score_increase"
    INDEPENDENT_CORROBORATION = "independent_corroboration"
    PROMOTION_READY = "promotion_ready"
    CANDIDATE_PROMOTED = "candidate_promoted"
    TOPIC_ACTIVITY = "topic_activity"
    SOURCE_SUGGESTION = "source_suggestion"
    PROVIDER_FAILURE = "provider_failure"
    PROVIDER_RECOVERY = "provider_recovery"
    TEST = "test"


class NotificationSeverity(str, Enum):
    INFORMATIONAL = "informational"
    NOTABLE = "notable"
    IMPORTANT = "important"
    URGENT = "urgent"


class NotificationDeliveryState(str, Enum):
    IN_APP = "in_app"
    PENDING = "pending"
    DEFERRED = "deferred"
    DELIVERED = "delivered"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class DeliveryAttemptStatus(str, Enum):
    DELIVERED = "delivered"
    DEFERRED = "deferred"
    FAILED = "failed"


class NotificationDigestStatus(str, Enum):
    READY = "ready"
    DELIVERED = "delivered"


# --- Phase 9: operational automation ---------------------------------------


class OperationalJobType(str, Enum):
    PIPELINE = "pipeline"
    NOTIFICATION_GENERATION = "notification_generation"
    DAILY_DIGEST = "daily_digest"
    DELIVERY_RETRY = "delivery_retry"
    BACKUP = "backup"
    DATABASE_MAINTENANCE = "database_maintenance"
    RETENTION_CLEANUP = "retention_cleanup"
    HEALTH_CHECK = "health_check"


class OperationalTriggerType(str, Enum):
    MANUAL_CLI = "manual_cli"
    MANUAL_GUI = "manual_gui"
    SCHEDULER = "scheduler"
    STARTUP_CATCHUP = "startup_catchup"
    RETRY = "retry"
    TEST = "test"


class OperationalJobStatus(str, Enum):
    SCHEDULED = "scheduled"
    RUNNING = "running"
    SUCCESSFUL = "successful"
    PARTIAL = "partial"
    FAILED = "failed"
    DEFERRED = "deferred"
    SKIPPED = "skipped"
    ABANDONED = "abandoned"


class NotificationFeedbackRating(str, Enum):
    USEFUL = "useful"
    NOT_USEFUL = "not_useful"


class BackupStatus(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    PRUNED = "pruned"
