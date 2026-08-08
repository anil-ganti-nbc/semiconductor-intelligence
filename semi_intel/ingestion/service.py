"""Turns a plugin's raw output into Source/Evidence rows.

This is the one place ingestion logic lives, on purpose: every plugin routes
through the same find-or-create-source step and the same dedup rule, so
adding a new source can never accidentally add a new dedup strategy too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from sqlalchemy.orm import Session

from semi_intel.domain.models import Evidence, Source
from semi_intel.editorial.service import EditorialDiscoveryService
from semi_intel.ingestion.base import RawItem, SourcePlugin
from semi_intel.ingestion.hashing import hash_content
from semi_intel.repository.repositories import EvidenceRepository, SourceRepository


@dataclass
class IngestionResult:
    source_name: str
    created: int = 0
    skipped_duplicate: int = 0
    errors: int = 0
    error_messages: List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.error_messages is None:
            self.error_messages = []

    def __str__(self) -> str:  # pragma: no cover - display only
        return (
            f"{self.source_name}: {self.created} new, "
            f"{self.skipped_duplicate} duplicate(s), {self.errors} error(s)"
        )


class IngestionService:
    def __init__(self, session: Session):
        self.session = session
        self.source_repo = SourceRepository(session)
        self.evidence_repo = EvidenceRepository(session)

    def ensure_source(self, plugin: SourcePlugin) -> Source:
        existing = self.source_repo.find_by_name(plugin.name)
        if existing:
            return existing
        source = Source(
            name=plugin.name,
            type=plugin.source_type,
            trust_weight=plugin.default_trust_weight,
        )
        self.source_repo.add(source)
        self.session.commit()
        return source

    def run(self, plugin: SourcePlugin) -> IngestionResult:
        source = self.ensure_source(plugin)
        result = IngestionResult(source_name=source.name)

        for item in plugin.fetch():
            try:
                self._ingest_one(source, item, result)
            except Exception as exc:  # noqa: BLE001 - one bad item shouldn't kill a run
                result.errors += 1
                result.error_messages.append(str(exc))

        self.session.commit()
        return result

    def _ingest_one(self, source: Source, item: RawItem, result: IngestionResult) -> None:
        content_hash = hash_content(item.content)
        if self.evidence_repo.find_by_hash(content_hash):
            result.skipped_duplicate += 1
            return
        evidence = Evidence(
            source_id=source.id,
            title=item.title[:500],
            raw_content=item.content,
            content_hash=content_hash,
            external_id=item.external_id,
            url=item.url,
            observed_at=item.observed_at,
        )
        self.evidence_repo.add(evidence)
        self.session.flush()
        EditorialDiscoveryService(self.session).process_evidence(evidence)
        result.created += 1
