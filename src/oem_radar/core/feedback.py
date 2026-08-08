"""Alert review / feedback foundation (deterministic, auditable).

change_events.id is the canonical alert identifier. Reviews are stored
separately; absence of a review row means the alert is still NEW.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Sequence

from pydantic import BaseModel, Field, field_validator, model_validator


class StrEnum(str, Enum):
    """3.10-compatible StrEnum."""

    def __str__(self) -> str:  # pragma: no cover
        return self.value


class ReviewOutcome(StrEnum):
    HIT = "HIT"
    INTERESTING = "INTERESTING"
    NOISE = "NOISE"
    BUG = "BUG"


class ReviewState(StrEnum):
    """Derived state: no review row → NEW; presence of a current review → REVIEWED."""

    NEW = "NEW"
    REVIEWED = "REVIEWED"


class ReasonCode(StrEnum):
    ROUTINE_IMAGE_CHANGE = "ROUTINE_IMAGE_CHANGE"
    MARKETING_ASSET_REFRESH = "MARKETING_ASSET_REFRESH"
    CDN_URL_CHURN = "CDN_URL_CHURN"
    MINOR_PRICE_FLUCTUATION = "MINOR_PRICE_FLUCTUATION"
    TEMPORARY_404 = "TEMPORARY_404"
    TEMPORARY_STOCK_CHANGE = "TEMPORARY_STOCK_CHANGE"
    DUPLICATE_SKU = "DUPLICATE_SKU"
    REGIONAL_DUPLICATE = "REGIONAL_DUPLICATE"
    UNCHANGED_DOCUMENT = "UNCHANGED_DOCUMENT"
    DOCUMENT_METADATA_ONLY = "DOCUMENT_METADATA_ONLY"
    SPEC_FORMATTING_ONLY = "SPEC_FORMATTING_ONLY"
    IRRELEVANT_PRODUCT = "IRRELEVANT_PRODUCT"
    OLD_PRODUCT_REDISCOVERED = "OLD_PRODUCT_REDISCOVERED"
    ACCESSORY_OR_COMPONENT = "ACCESSORY_OR_COMPONENT"
    PARSER_ERROR = "PARSER_ERROR"
    ENTITY_MATCH_ERROR = "ENTITY_MATCH_ERROR"
    BAD_BASELINE = "BAD_BASELINE"
    DUPLICATE_ALERT = "DUPLICATE_ALERT"
    LOW_EDITORIAL_VALUE = "LOW_EDITORIAL_VALUE"
    VALID_BUT_TOO_EARLY = "VALID_BUT_TOO_EARLY"
    VALID_CONFIRMATION_SIGNAL = "VALID_CONFIRMATION_SIGNAL"
    OTHER = "OTHER"


class RuleSuggestionStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    IMPLEMENTED = "IMPLEMENTED"
    REVERTED = "REVERTED"


# Stable ordered list used for validation and deterministic sorting.
REASON_CODES: tuple[str, ...] = tuple(c.value for c in ReasonCode)
OUTCOMES: tuple[str, ...] = tuple(o.value for o in ReviewOutcome)
RULE_STATUSES: tuple[str, ...] = tuple(s.value for s in RuleSuggestionStatus)

_REASON_SET = frozenset(REASON_CODES)

# Human-readable labels and display groups (stored codes remain the enums).
REASON_TAXONOMY: list[dict[str, str]] = [
    {"code": "ROUTINE_IMAGE_CHANGE", "label": "Routine image change", "group": "Content/asset churn"},
    {"code": "MARKETING_ASSET_REFRESH", "label": "Marketing asset refresh", "group": "Content/asset churn"},
    {"code": "CDN_URL_CHURN", "label": "CDN URL churn", "group": "Content/asset churn"},
    {"code": "TEMPORARY_404", "label": "Temporary 404", "group": "Availability and temporary state"},
    {"code": "TEMPORARY_STOCK_CHANGE", "label": "Temporary stock change", "group": "Availability and temporary state"},
    {"code": "DUPLICATE_SKU", "label": "Duplicate SKU", "group": "Duplication/entity resolution"},
    {"code": "REGIONAL_DUPLICATE", "label": "Regional duplicate", "group": "Duplication/entity resolution"},
    {"code": "DUPLICATE_ALERT", "label": "Duplicate alert", "group": "Duplication/entity resolution"},
    {"code": "ENTITY_MATCH_ERROR", "label": "Entity match error", "group": "Duplication/entity resolution"},
    {"code": "UNCHANGED_DOCUMENT", "label": "Unchanged document", "group": "Documents and metadata"},
    {"code": "DOCUMENT_METADATA_ONLY", "label": "Document metadata only", "group": "Documents and metadata"},
    {"code": "SPEC_FORMATTING_ONLY", "label": "Spec formatting only", "group": "Documents and metadata"},
    {"code": "MINOR_PRICE_FLUCTUATION", "label": "Minor price fluctuation", "group": "Price/baseline"},
    {"code": "BAD_BASELINE", "label": "Bad baseline", "group": "Price/baseline"},
    {"code": "PARSER_ERROR", "label": "Parser error", "group": "Parser/software defects"},
    {"code": "IRRELEVANT_PRODUCT", "label": "Irrelevant product", "group": "Editorial value"},
    {"code": "OLD_PRODUCT_REDISCOVERED", "label": "Old product rediscovered", "group": "Editorial value"},
    {"code": "ACCESSORY_OR_COMPONENT", "label": "Accessory or component", "group": "Editorial value"},
    {"code": "LOW_EDITORIAL_VALUE", "label": "Low editorial value", "group": "Editorial value"},
    {"code": "VALID_BUT_TOO_EARLY", "label": "Valid but too early", "group": "Editorial value"},
    {"code": "VALID_CONFIRMATION_SIGNAL", "label": "Valid confirmation signal", "group": "Editorial value"},
    {"code": "OTHER", "label": "Other", "group": "Other"},
]


def reason_taxonomy() -> list[dict[str, str]]:
    """Stable list for /api/feedback/reasons and UI grouping."""
    return list(REASON_TAXONOMY)


_OUTCOME_SET = frozenset(OUTCOMES)
_STATUS_SET = frozenset(RULE_STATUSES)

# Length caps (bytes/chars; applied after strip)
MAX_REVIEWER_LEN = 64
MAX_NOTE_LEN = 2000
MAX_CHANGE_NOTE_LEN = 500
MAX_SUGGESTED_RULE_LEN = 2000
MAX_COLLECTOR_LEN = 128
MAX_ALERT_TYPE_LEN = 64


class FeedbackError(ValueError):
    """Raised for invalid outcomes, reason codes, missing events, length caps."""


def normalize_reason_codes(codes: Sequence[str] | None) -> list[str]:
    """Validate, dedupe, and return reason codes in stable taxonomy order.

    Empty list is allowed (reviewer may assign only an outcome).
    Raises FeedbackError on unknown codes.
    """
    if codes is None:
        return []
    if not isinstance(codes, (list, tuple)):
        raise FeedbackError("reason_codes must be a list of strings")
    seen: set[str] = set()
    for c in codes:
        if not isinstance(c, str):
            raise FeedbackError(f"reason code must be str, got {type(c).__name__}")
        if c not in _REASON_SET:
            raise FeedbackError(f"invalid reason code: {c!r}")
        seen.add(c)
    # Deterministic order: taxonomy order, not input order.
    return [c for c in REASON_CODES if c in seen]


def validate_outcome(outcome: str) -> str:
    if outcome not in _OUTCOME_SET:
        raise FeedbackError(f"invalid outcome: {outcome!r}")
    return outcome


def validate_rule_status(status: str) -> str:
    if status not in _STATUS_SET:
        raise FeedbackError(f"invalid rule suggestion status: {status!r}")
    return status


def _clip(s: str | None, max_len: int, field: str) -> str | None:
    if s is None:
        return None
    if not isinstance(s, str):
        raise FeedbackError(f"{field} must be a string")
    s = s.strip()
    if not s:
        return None
    if len(s) > max_len:
        raise FeedbackError(f"{field} exceeds {max_len} characters")
    return s


def reasons_to_json(codes: Sequence[str]) -> str:
    return json.dumps(list(codes), separators=(",", ":"))


def reasons_from_json(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as e:
        raise FeedbackError(f"corrupt reason_codes JSON: {e}") from e
    return normalize_reason_codes(data)


class AlertReview(BaseModel):
    """Current review for one change_event (alert)."""

    id: int | None = None
    alert_id: int  # change_events.id
    outcome: ReviewOutcome
    reason_codes: list[str] = Field(default_factory=list)
    reviewer_note: str | None = None
    reviewed_at: datetime | None = None
    reviewer: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("outcome", mode="before")
    @classmethod
    def _outcome(cls, v: Any) -> str:
        if isinstance(v, ReviewOutcome):
            return v.value
        return validate_outcome(str(v))

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _reasons(cls, v: Any) -> list[str]:
        return normalize_reason_codes(v)

    @field_validator("reviewer_note", mode="before")
    @classmethod
    def _note(cls, v: Any) -> str | None:
        return _clip(v, MAX_NOTE_LEN, "reviewer_note")

    @field_validator("reviewer", mode="before")
    @classmethod
    def _reviewer(cls, v: Any) -> str | None:
        return _clip(v, MAX_REVIEWER_LEN, "reviewer")


class ReviewHistoryEntry(BaseModel):
    id: int | None = None
    alert_id: int
    previous_outcome: str | None = None
    new_outcome: str
    previous_reason_codes: list[str] = Field(default_factory=list)
    new_reason_codes: list[str] = Field(default_factory=list)
    changed_at: datetime | None = None
    changed_by: str | None = None
    change_note: str | None = None


class RuleSuggestion(BaseModel):
    id: int | None = None
    collector: str
    alert_type: str
    reason_code: str | None = None
    suggested_rule: str
    supporting_alert_count: int = 0
    estimated_noise_reduction: float | None = None  # 0..1
    estimated_hit_loss: float | None = None  # 0..1
    status: RuleSuggestionStatus = RuleSuggestionStatus.PROPOSED
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, v: Any) -> str:
        if isinstance(v, RuleSuggestionStatus):
            return v.value
        return validate_rule_status(str(v))

    @field_validator("collector", mode="before")
    @classmethod
    def _collector(cls, v: Any) -> str:
        s = _clip(v, MAX_COLLECTOR_LEN, "collector")
        if not s:
            raise FeedbackError("collector is required")
        return s

    @field_validator("alert_type", mode="before")
    @classmethod
    def _alert_type(cls, v: Any) -> str:
        s = _clip(v, MAX_ALERT_TYPE_LEN, "alert_type")
        if not s:
            raise FeedbackError("alert_type is required")
        return s

    @field_validator("suggested_rule", mode="before")
    @classmethod
    def _rule(cls, v: Any) -> str:
        s = _clip(v, MAX_SUGGESTED_RULE_LEN, "suggested_rule")
        if not s:
            raise FeedbackError("suggested_rule is required")
        return s

    @field_validator("reason_code", mode="before")
    @classmethod
    def _rc(cls, v: Any) -> str | None:
        if v is None or v == "":
            return None
        if str(v) not in _REASON_SET:
            raise FeedbackError(f"invalid reason_code: {v!r}")
        return str(v)


# FeedbackConfig lives in core.config (RadarConfig.feedback) so it loads from radar.yaml.


LEGAL_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "PROPOSED": {"ACCEPTED", "REJECTED"},
    "ACCEPTED": {"IMPLEMENTED", "REJECTED"},
    "IMPLEMENTED": {"REVERTED"},
    "REVERTED": {"ACCEPTED"},
    "REJECTED": set(),  # terminal unless manually re-opened outside this map
}


def validate_status_transition(current: str, new: str) -> str:
    new = validate_rule_status(new)
    current = validate_rule_status(current)
    allowed = LEGAL_STATUS_TRANSITIONS.get(current, set())
    if new == current:
        return new
    if new not in allowed:
        raise FeedbackError(f"illegal status transition {current} -> {new}")
    return new
