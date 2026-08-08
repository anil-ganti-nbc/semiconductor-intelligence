"""Plain-dict serializers for the web API.

Deliberately not Pydantic response models: this API is small and read-only
enough that hand-written dicts stay readable, and it avoids stacking a
second ORM-mapping layer on top of SQLAlchemy's own mapped objects.
"""

from __future__ import annotations

from typing import Optional

import json

from semi_intel.domain.models import (
    Claim,
    ClaimEvent,
    ClaimEvidenceLink,
    ClaimLinkSuggestion,
    Entity,
    Evidence,
    Source,
)


def serialize_claim(claim: Claim) -> dict:
    return {
        "id": claim.id,
        "statement": claim.statement,
        "subject_entity_id": claim.subject_entity_id,
        "status": claim.status.value,
        "confidence": claim.confidence,
        "resolution_note": claim.resolution_note,
        "created_at": claim.created_at.isoformat(),
        "updated_at": claim.updated_at.isoformat(),
        "resolved_at": claim.resolved_at.isoformat() if claim.resolved_at else None,
    }


def serialize_evidence(evidence: Optional[Evidence]) -> Optional[dict]:
    if evidence is None:
        return None
    return {
        "id": evidence.id,
        "source_id": evidence.source_id,
        "entity_id": evidence.entity_id,
        "title": evidence.title,
        "raw_content": evidence.raw_content,
        "external_id": evidence.external_id,
        "url": evidence.url,
        "observed_at": evidence.observed_at.isoformat() if evidence.observed_at else None,
        "collected_at": evidence.collected_at.isoformat(),
        "origin_signal_item_id": evidence.origin_signal_item_id,
    }


def serialize_source(source: Source) -> dict:
    return {
        "id": source.id,
        "name": source.name,
        "type": source.type.value,
        "url": source.url,
        "trust_weight": source.trust_weight,
    }


def serialize_entity(entity: Entity) -> dict:
    try:
        aliases = json.loads(entity.aliases or "[]")
    except (TypeError, json.JSONDecodeError):
        aliases = []
    try:
        attributes = json.loads(entity.attributes or "{}")
    except (TypeError, json.JSONDecodeError):
        attributes = {}
    return {
        "id": entity.id,
        "type": entity.type.value,
        "name": entity.name,
        "aliases": aliases if isinstance(aliases, list) else [],
        "attributes": attributes if isinstance(attributes, dict) else {},
    }


def serialize_event(event: ClaimEvent) -> dict:
    return {
        "id": event.id,
        "event_type": event.event_type.value,
        "confidence_after": event.confidence_after,
        "note": event.note,
        "created_at": event.created_at.isoformat(),
    }


def serialize_link(link: ClaimEvidenceLink) -> dict:
    return {
        "id": link.id,
        "evidence_id": link.evidence_id,
        "stance": link.stance.value,
        "note": link.note,
        "created_at": link.created_at.isoformat(),
    }


def serialize_suggestion(suggestion: ClaimLinkSuggestion) -> dict:
    return {
        "id": suggestion.id,
        "evidence_id": suggestion.evidence_id,
        "claim_id": suggestion.claim_id,
        "score": suggestion.score,
        "reasons": json.loads(suggestion.reasons or "[]"),
        "status": suggestion.status.value,
        "created_at": suggestion.created_at.isoformat(),
        "resolved_at": suggestion.resolved_at.isoformat() if suggestion.resolved_at else None,
        "resolved_note": suggestion.resolved_note,
    }
