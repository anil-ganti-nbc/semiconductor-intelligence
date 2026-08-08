"""Deterministic bridge from Radar mention proposals to canonical entities.

Unknown extracted text remains a proposal until an operator explicitly resolves,
creates, rejects, or ignores its exact normalized group.  This module never
creates entities while listing or scanning proposals.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import CandidateEntityRole, ClaimStatus, EntityType, SignalMentionStatus
from semi_intel.domain.models import (
    CandidateEntity, CandidateSignalItem, Claim, Entity, Evidence, Relationship,
    SignalEntityMention, SignalItem, Source,
)
from semi_intel.editorial.service import normalize_phrase


@dataclass(frozen=True)
class MentionResolutionResult:
    entity: Entity | None
    mentions_updated: int
    candidate_links_created: int


def _json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []


class CanonicalEntityService:
    def __init__(self, session: Session):
        self.session = session

    def summary(self) -> dict:
        statuses = {
            status.value: self.session.scalar(
                select(func.count()).select_from(SignalEntityMention).where(
                    SignalEntityMention.status == status
                )
            ) or 0
            for status in SignalMentionStatus
        }
        return {
            "canonical_entities": self.session.scalar(select(func.count()).select_from(Entity)) or 0,
            "unresolved_mentions": statuses[SignalMentionStatus.CANDIDATE.value],
            "resolved_mentions": statuses[SignalMentionStatus.RESOLVED.value],
            "rejected_mentions": statuses[SignalMentionStatus.REJECTED.value],
            "ignored_mentions": statuses[SignalMentionStatus.IGNORED.value],
        }

    def list_entities(self, *, search: str = "", entity_type: EntityType | None = None) -> list[dict]:
        stmt = select(Entity).order_by(Entity.name.asc(), Entity.id.asc())
        if entity_type is not None:
            stmt = stmt.where(Entity.type == entity_type)
        rows = list(self.session.scalars(stmt))
        needle = search.casefold().strip()
        if needle:
            rows = [row for row in rows if needle in row.name.casefold() or any(
                needle in alias.casefold() for alias in _json_list(row.aliases)
            )]
        return [self.entity_payload(row) for row in rows]

    def entity_payload(self, entity: Entity, *, include_detail: bool = False) -> dict:
        payload = {
            "id": entity.id,
            "type": entity.type.value,
            "name": entity.name,
            "aliases": _json_list(entity.aliases),
            "attributes": self._json_dict(entity.attributes),
            "resolved_mention_count": self.session.scalar(
                select(func.count()).select_from(SignalEntityMention).where(
                    SignalEntityMention.resolved_entity_id == entity.id,
                    SignalEntityMention.status == SignalMentionStatus.RESOLVED,
                )
            ) or 0,
            "open_claim_count": self.session.scalar(
                select(func.count()).select_from(Claim).where(
                    Claim.subject_entity_id == entity.id, Claim.status == ClaimStatus.OPEN
                )
            ) or 0,
            "evidence_count": self.session.scalar(
                select(func.count()).select_from(Evidence).where(Evidence.entity_id == entity.id)
            ) or 0,
        }
        if not include_detail:
            return payload
        relationships = []
        for relation in self.session.scalars(select(Relationship).where(
            (Relationship.from_entity_id == entity.id) | (Relationship.to_entity_id == entity.id)
        )):
            other_id = relation.to_entity_id if relation.from_entity_id == entity.id else relation.from_entity_id
            other = self.session.get(Entity, other_id)
            relationships.append({
                "relation_type": relation.relation_type.value,
                "direction": "outgoing" if relation.from_entity_id == entity.id else "incoming",
                "other_entity_id": other_id,
                "other_entity_name": other.name if other else None,
                "note": relation.note,
            })
        payload.update({
            "relationships": relationships,
            "claims": [{"id": row.id, "statement": row.statement, "status": row.status.value}
                       for row in self.session.scalars(select(Claim).where(
                           Claim.subject_entity_id == entity.id).order_by(Claim.updated_at.desc()).limit(20))],
            "evidence": [{"id": row.id, "title": row.title, "url": row.url}
                         for row in self.session.scalars(select(Evidence).where(
                             Evidence.entity_id == entity.id).order_by(Evidence.collected_at.desc()).limit(20))],
            "recent_mentions": self._recent_mentions(entity.id),
        })
        return payload

    def mention_proposals(
        self, *, search: str = "", proposed_type: str = "", offset: int = 0, limit: int = 50
    ) -> dict:
        rows = list(self.session.scalars(select(SignalEntityMention).where(
            SignalEntityMention.status == SignalMentionStatus.CANDIDATE
        ).order_by(SignalEntityMention.id.asc())))
        groups: dict[tuple[str, str], dict] = {}
        needle = search.casefold().strip()
        for row in rows:
            normalized = normalize_phrase(row.candidate_text)
            if not normalized or (proposed_type and row.proposed_entity_type != proposed_type):
                continue
            if needle and needle not in row.candidate_text.casefold():
                continue
            key = (normalized, row.proposed_entity_type)
            group = groups.setdefault(key, {
                "normalized_text": normalized, "candidate_text": row.candidate_text,
                "proposed_entity_type": row.proposed_entity_type, "mention_count": 0,
                "signal_item_ids": set(), "highest_confidence": 0.0, "extractors": set(),
            })
            group["mention_count"] += 1
            group["signal_item_ids"].add(row.signal_item_id)
            group["highest_confidence"] = max(group["highest_confidence"], row.confidence)
            group["extractors"].add(row.extractor)
        ranked = sorted(groups.values(), key=lambda g: (
            -len(g["signal_item_ids"]), -g["mention_count"], g["candidate_text"].casefold(),
            g["proposed_entity_type"],
        ))
        total = len(ranked)
        page = ranked[offset: offset + limit]
        for group in page:
            group["distinct_report_count"] = len(group.pop("signal_item_ids"))
            group["extractors"] = sorted(group["extractors"])
            group["examples"] = self._examples_for_group(
                group["normalized_text"], group["proposed_entity_type"], limit=3
            )
        return {"total": total, "offset": offset, "limit": limit, "items": page}

    def create_entity(self, *, name: str, entity_type: EntityType, aliases: Iterable[str], attributes: dict) -> Entity:
        if self._find_equivalent_entity(name):
            raise ValueError(f"Entity '{name}' already exists or matches an existing alias.")
        cleaned_aliases = self._clean_aliases(aliases, exclude=name)
        entity = Entity(type=entity_type, name=name.strip(), aliases=json.dumps(cleaned_aliases),
                        attributes=json.dumps(attributes or {}))
        self.session.add(entity)
        self.session.flush()
        return entity

    def update_entity(self, entity: Entity, *, name: str, entity_type: EntityType,
                      aliases: Iterable[str], attributes: dict) -> Entity:
        equivalent = self._find_equivalent_entity(name)
        if equivalent and equivalent.id != entity.id:
            raise ValueError(f"Entity '{name}' already exists or matches an existing alias.")
        entity.name = name.strip()
        entity.type = entity_type
        entity.aliases = json.dumps(self._clean_aliases(aliases, exclude=name))
        entity.attributes = json.dumps(attributes or {})
        self.session.flush()
        return entity

    def resolve_group(self, *, candidate_text: str, proposed_type: str, entity: Entity,
                      add_alias: bool = False) -> MentionResolutionResult:
        rows = self._matching_candidate_mentions(candidate_text, proposed_type)
        if not rows:
            raise ValueError("No unresolved mentions match that exact normalized group.")
        if add_alias and normalize_phrase(candidate_text) != normalize_phrase(entity.name):
            aliases = _json_list(entity.aliases)
            if all(normalize_phrase(alias) != normalize_phrase(candidate_text) for alias in aliases):
                aliases.append(candidate_text.strip())
                entity.aliases = json.dumps(self._clean_aliases(aliases, exclude=entity.name))
        item_ids = set()
        for row in rows:
            row.status = SignalMentionStatus.RESOLVED
            row.resolved_entity_id = entity.id
            row.reason = f"operator_resolved_to_entity:{entity.id}"
            item_ids.add(row.signal_item_id)
        created = self._sync_candidate_entities(item_ids, entity.id)
        self.session.flush()
        return MentionResolutionResult(entity, len(rows), created)

    def reject_group(self, *, candidate_text: str, proposed_type: str,
                     status: SignalMentionStatus, reason: str | None = None) -> MentionResolutionResult:
        if status not in (SignalMentionStatus.REJECTED, SignalMentionStatus.IGNORED):
            raise ValueError("Mention group may only be rejected or ignored.")
        rows = self._matching_candidate_mentions(candidate_text, proposed_type)
        if not rows:
            raise ValueError("No unresolved mentions match that exact normalized group.")
        for row in rows:
            row.status = status
            row.reason = reason or f"operator_{status.value}"
        self.session.flush()
        return MentionResolutionResult(None, len(rows), 0)

    def _matching_candidate_mentions(self, text: str, proposed_type: str) -> list[SignalEntityMention]:
        target = normalize_phrase(text)
        rows = self.session.scalars(select(SignalEntityMention).where(
            SignalEntityMention.status == SignalMentionStatus.CANDIDATE,
            SignalEntityMention.proposed_entity_type == proposed_type,
        ))
        return [row for row in rows if normalize_phrase(row.candidate_text) == target]

    def _sync_candidate_entities(self, item_ids: set[int], entity_id: int) -> int:
        if not item_ids:
            return 0
        candidate_ids = set(self.session.scalars(select(CandidateSignalItem.candidate_id).where(
            CandidateSignalItem.signal_item_id.in_(item_ids)
        )))
        created = 0
        for candidate_id in candidate_ids:
            exists = self.session.scalar(select(CandidateEntity.id).where(
                CandidateEntity.candidate_id == candidate_id, CandidateEntity.entity_id == entity_id
            ))
            if exists is None:
                self.session.add(CandidateEntity(candidate_id=candidate_id, entity_id=entity_id,
                                                 role=CandidateEntityRole.MENTIONED))
                created += 1
        return created

    def _examples_for_group(self, normalized: str, proposed_type: str, *, limit: int) -> list[dict]:
        mentions = self.session.scalars(select(SignalEntityMention).where(
            SignalEntityMention.status == SignalMentionStatus.CANDIDATE,
            SignalEntityMention.proposed_entity_type == proposed_type,
        ).order_by(SignalEntityMention.id.desc()))
        examples = []
        seen_items = set()
        for mention in mentions:
            if normalize_phrase(mention.candidate_text) != normalized or mention.signal_item_id in seen_items:
                continue
            item = self.session.get(SignalItem, mention.signal_item_id)
            source = self.session.get(Source, item.source_id) if item else None
            examples.append({"signal_item_id": mention.signal_item_id,
                             "title": item.title if item else None,
                             "source": source.name if source else None,
                             "extractor": mention.extractor,
                             "reason": mention.reason})
            seen_items.add(mention.signal_item_id)
            if len(examples) >= limit:
                break
        return examples

    def _recent_mentions(self, entity_id: int) -> list[dict]:
        rows = self.session.scalars(select(SignalEntityMention).where(
            SignalEntityMention.resolved_entity_id == entity_id,
            SignalEntityMention.status == SignalMentionStatus.RESOLVED,
        ).order_by(SignalEntityMention.id.desc()).limit(20))
        result = []
        for row in rows:
            item = self.session.get(SignalItem, row.signal_item_id)
            result.append({"candidate_text": row.candidate_text, "signal_item_id": row.signal_item_id,
                           "title": item.title if item else None, "extractor": row.extractor,
                           "reason": row.reason})
        return result

    def _find_equivalent_entity(self, text: str) -> Entity | None:
        target = normalize_phrase(text)
        for entity in self.session.scalars(select(Entity)):
            if normalize_phrase(entity.name) == target or any(
                normalize_phrase(alias) == target for alias in _json_list(entity.aliases)
            ):
                return entity
        return None

    @staticmethod
    def _clean_aliases(values: Iterable[str], *, exclude: str) -> list[str]:
        excluded = normalize_phrase(exclude)
        result, seen = [], set()
        for value in values:
            value = value.strip()
            normalized = normalize_phrase(value)
            if not value or not normalized or normalized == excluded or normalized in seen:
                continue
            seen.add(normalized)
            result.append(value)
        return result

    @staticmethod
    def _json_dict(value: str | None) -> dict:
        try:
            parsed = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
