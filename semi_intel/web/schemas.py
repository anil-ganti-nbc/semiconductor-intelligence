"""Pydantic request bodies for the web dashboard's write endpoints.

Deliberately request-only -- serializers.py explains why responses stay
hand-written dicts. Request *validation* is different: FastAPI's automatic
422 responses (with a field-by-field explanation) are worth the extra
dependency on the input side, especially for a UI aimed at someone who
isn't a developer and won't know how to read a raw traceback.

Every field here maps 1:1 onto the equivalent CLI flag in semi_intel/cli.py
(entity_add, source_add, evidence_add, claim_create, claim_link_evidence,
claim_resolve, suggest_accept, suggest_reject) -- same names, same
defaults, same validation, so the two front ends never drift apart.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from semi_intel.domain.enums import (
    ClaimStatus,
    EntityType,
    EvidenceStance,
    NotificationFeedbackRating,
    SourceType,
)


class SourceCreate(BaseModel):
    name: str
    type: SourceType
    url: Optional[str] = None
    description: Optional[str] = None
    trust_weight: float = Field(0.5, ge=0.0, le=1.0)


class EntityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: EntityType
    aliases: List[str] = []
    attributes: Dict[str, str] = {}


class EntityUpdate(EntityCreate):
    pass


class MentionResolveRequest(BaseModel):
    candidate_text: str = Field(min_length=1, max_length=500)
    proposed_entity_type: str = Field(min_length=1, max_length=30)
    entity_id: Optional[int] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    type: Optional[EntityType] = None
    aliases: List[str] = []
    attributes: Dict[str, str] = {}
    add_observed_as_alias: bool = False


class MentionDispositionRequest(BaseModel):
    candidate_text: str = Field(min_length=1, max_length=500)
    proposed_entity_type: str = Field(min_length=1, max_length=30)
    action: str
    reason: Optional[str] = Field(default=None, max_length=500)


class EvidenceCreate(BaseModel):
    source_id: int
    title: str
    content: str
    entity_id: Optional[int] = None
    url: Optional[str] = None
    observed_at: Optional[str] = None  # ISO date/time string, same as the CLI's --observed-at


class ClaimCreate(BaseModel):
    statement: str
    subject_entity_id: Optional[int] = None


class LinkEvidenceRequest(BaseModel):
    evidence_id: int
    stance: EvidenceStance
    note: Optional[str] = None


class SignalEvidenceRequest(BaseModel):
    candidate_id: Optional[int] = None


class RadarClaimCreate(BaseModel):
    statement: str = Field(min_length=3, max_length=5000)
    signal_item_id: Optional[int] = None
    stance: EvidenceStance = EvidenceStance.SUPPORTS
    note: Optional[str] = Field(default=None, max_length=2000)
    subject_entity_id: Optional[int] = None


class ResolveClaimRequest(BaseModel):
    status: ClaimStatus
    note: Optional[str] = None


class SuggestionAcceptRequest(BaseModel):
    stance: EvidenceStance
    note: Optional[str] = None


class SuggestionRejectRequest(BaseModel):
    note: Optional[str] = None


class TopicCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    keyword: Optional[str] = Field(default=None, max_length=255)
    aliases: List[str] = []
    category: Optional[str] = Field(default=None, max_length=100)
    priority: float = Field(0.5, ge=0.0, le=1.0)
    enabled: bool = True
    notes: Optional[str] = None


class TopicUpdate(TopicCreate):
    pass


class StorySeenRequest(BaseModel):
    story_ids: List[int] = Field(min_length=1)
    seen: bool = True


class SourceSuggestionAction(BaseModel):
    action: str


class AddSuggestedSourceRequest(BaseModel):
    name: Optional[str] = None
    feed_url: Optional[str] = None
    trust_weight: float = Field(0.5, ge=0.0, le=1.0)


class DiscoverySettingsUpdate(BaseModel):
    enabled: bool
    automatic: bool
    minimum_interest_score: float = Field(ge=0.0, le=1.0)
    maximum_story_age_hours: int = Field(ge=1, le=168)
    cooldown_hours: int = Field(ge=1, le=72)
    maximum_cycles_per_story: int = Field(ge=1, le=10)
    global_cycles_per_hour: int = Field(ge=1, le=50)
    provider_requests_per_hour: int = Field(ge=1, le=150)
    results_per_query: int = Field(ge=1, le=30)


class BlockDomainRequest(BaseModel):
    domain: str = Field(min_length=3, max_length=255)


# --- Signal Radar (Phase 6) -------------------------------------------------

class CandidateSeenRequest(BaseModel):
    candidate_ids: List[int] = Field(min_length=1)
    seen: bool = True


class CandidateDismissRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class CandidateSnoozeRequest(BaseModel):
    until: str  # ISO datetime string


class CandidatePromoteRequest(BaseModel):
    by: str = "human:web"
    merge_into_story_id: Optional[int] = None
    headline: Optional[str] = Field(default=None, min_length=3, max_length=500)


class RadarSourceCreate(BaseModel):
    handle_or_url: str = Field(min_length=1)
    provider: Optional[str] = None  # auto-detected from handle_or_url when omitted
    display_name: Optional[str] = None
    priority: int = Field(3, ge=1, le=5)
    trust_weight: float = Field(0.5, ge=0.0, le=1.0)
    polling_enabled: bool = False


class RadarSourceUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    handle_or_url: str = Field(min_length=1, max_length=2048)
    priority: int = Field(ge=1, le=5)
    trust_weight: float = Field(ge=0.0, le=1.0)
    enabled: bool = True
    polling_enabled: bool = False


class RadarSettingsUpdate(BaseModel):
    collection_enabled: bool
    x_provider_enabled: bool
    startup_stagger_seconds: int = Field(ge=0, le=600)
    media_download_enabled: bool
    ocr_enabled: bool
    weight_topic_relevance: float = Field(ge=0.0, le=1.0)
    weight_novelty: float = Field(ge=0.0, le=1.0)
    weight_momentum: float = Field(ge=0.0, le=1.0)
    weight_source_diversity: float = Field(ge=0.0, le=1.0)
    weight_artifact_strength: float = Field(ge=0.0, le=1.0)
    weight_source_quality: float = Field(ge=0.0, le=1.0)
    momentum_window_hours: int = Field(ge=1, le=168)
    staleness_days: int = Field(ge=1, le=90)
    automatic_promotion_enabled: bool
    minimum_attention_score: float = Field(ge=0.0, le=1.0)
    required_topic_match: bool
    maximum_candidate_age_hours: int = Field(ge=1, le=2000)
    hourly_promotion_budget: int = Field(ge=1, le=50)


class SourceSuggestionReviewRequest(BaseModel):
    action: str  # accept|dismiss|block


class SourceReputationOverrideRequest(BaseModel):
    authority_override: Optional[float] = Field(None, ge=0.0, le=1.0)


# --- Alerts & Digest (Phase 8) ---------------------------------------------

class NotificationReadRequest(BaseModel):
    notification_ids: List[int] = Field(min_length=1)
    read: bool = True


class DigestGenerateRequest(BaseModel):
    refresh: bool = True
    generate_notifications: bool = True
    deliver: bool = False


class WindowsTaskInstallRequest(BaseModel):
    confirmed: bool = False


class NotificationSettingsUpdate(BaseModel):
    in_app_enabled: bool
    external_delivery_enabled: bool = False
    windows_desktop_notifications_enabled: bool = False
    minimum_attention_score: float = Field(ge=0.0, le=1.0)
    minimum_score_increase: float = Field(ge=0.0, le=1.0)
    required_independent_group_count: int = Field(ge=1, le=20)
    required_topic_match: bool
    high_score_enabled: bool
    score_increase_enabled: bool
    corroboration_enabled: bool
    promotion_ready_enabled: bool
    promotion_completed_enabled: bool
    source_suggestion_enabled: bool
    provider_health_enabled: bool
    topic_activity_enabled: bool
    daily_digest_enabled: bool
    digest_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    timezone: str = Field(min_length=1, max_length=100)
    quiet_hours_start: str = Field(pattern=r"^\d{2}:\d{2}$")
    quiet_hours_end: str = Field(pattern=r"^\d{2}:\d{2}$")
    maximum_immediate_per_hour: int = Field(ge=1, le=100)
    maximum_delivery_attempts: int = Field(ge=1, le=10)
    provider_failure_threshold: int = Field(ge=1, le=20)
    provider_recovery_enabled: bool
    source_suggestion_minimum_score: float = Field(ge=0.0, le=1.0)
    topic_activity_minimum_candidates: int = Field(ge=1, le=100)
    topic_activity_window_hours: int = Field(ge=1, le=168)
    retention_days: int = Field(ge=1, le=730)
    digest_maximum_items_per_section: int = Field(ge=1, le=50)
    muted_event_types: List[str] = []
    muted_topic_ids: List[int] = []


# --- Operational automation (Phase 9) --------------------------------------

class SchedulerSettingsUpdate(BaseModel):
    scheduler_enabled: bool
    pipeline_interval_minutes: int = Field(ge=5, le=1440)
    digest_enabled: bool
    digest_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    backup_enabled: bool
    backup_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    maintenance_enabled: bool
    maintenance_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    timezone: str = Field(min_length=1, max_length=100)
    maximum_job_duration_minutes: int = Field(ge=5, le=1440)
    retry_delay_minutes: int = Field(ge=1, le=1440)
    maximum_automatic_retries: int = Field(ge=0, le=10)
    stale_run_threshold_minutes: int = Field(ge=5, le=2880)
    missed_run_warning_minutes: int = Field(ge=5, le=10080)
    startup_catchup_enabled: bool
    backup_retention_count: int = Field(ge=1, le=365)
    backup_retention_days: int = Field(ge=1, le=3650)


class NotificationFeedbackRequest(BaseModel):
    rating: NotificationFeedbackRating
    reason: Optional[str] = Field(default=None, max_length=100)
    note: Optional[str] = Field(default=None, max_length=1000)


class SavedNotificationViewRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    state_filter: str = "unread"
    event_types: List[str] = []
    severities: List[str] = []
    topic_ids: List[int] = []
    relation_filters: Dict[str, str] = {}
    date_window_days: Optional[int] = Field(default=None, ge=1, le=365)
    search_text: str = Field(default="", max_length=500)
    sort_order: str = "newest"


class AdapterEnableRequest(BaseModel):
    enabled: bool
    allow_untested: bool = False


class BackupPruneRequest(BaseModel):
    apply: bool = False


class BackupVerifyRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
