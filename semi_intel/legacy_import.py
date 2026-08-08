"""Transactional, idempotent import of the pre-merge Signal Radar database.

Only Radar's trustworthy sensory layer is accepted.  Its derived stories,
entities, review queue and scores are reported but deliberately not imported:
the 3.0 analyzer and candidate pipeline remain the sole editorial authority.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import (
    ProviderRunStatus,
    SignalMediaDownloadState,
    SignalProcessingState,
    SourceSuggestionKind,
    SourceSuggestionStatus,
    SourceType,
)
from semi_intel.domain.models import (
    ProviderRun,
    SignalItem,
    SignalMedia,
    Source,
    SourceSuggestion,
)
from semi_intel.source_identity import canonical_feed_key, find_source_by_feed_url

IMPORT_CATEGORIES = ("sources", "posts", "media", "provider_runs", "source_suggestions")
UNSUPPORTED_TABLES = (
    "stories", "story_entities", "story_scores", "evidence", "entities",
    "post_entities", "post_labels", "relationships", "review_queue",
    "notifications", "score_weights", "reliability_history",
)
_SECRET_KEYS = {
    "auth_token", "ct0", "cookie", "cookies", "password", "secret",
    "token", "api_key", "apikey", "webhook", "session", "storage_state",
    "authorization", "x-csrf-token", "bearer",
}


@dataclass
class ImportCount:
    detected: int = 0
    importable: int = 0
    imported: int = 0
    duplicate: int = 0
    invalid: int = 0


@dataclass
class ImportReport:
    mode: str
    source_format: str
    categories: dict[str, ImportCount] = field(default_factory=dict)
    unsupported: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["totals"] = {
            key: sum(getattr(count, key) for count in self.categories.values())
            for key in ("detected", "importable", "imported", "duplicate", "invalid")
        }
        return value

    def human_text(self) -> str:
        lines = [f"Legacy import {self.mode}: {self.source_format}"]
        for name, count in self.categories.items():
            lines.append(
                f"  {name}: detected={count.detected}, importable={count.importable}, "
                f"imported={count.imported}, duplicate={count.duplicate}, invalid={count.invalid}"
            )
        if self.unsupported:
            lines.append("  Deliberately not imported: " + ", ".join(
                f"{name} ({rows})" for name, rows in self.unsupported.items()
            ))
        lines.extend(f"  Warning: {warning}" for warning in self.warnings)
        return "\n".join(lines)


def _parse_datetime(value: Any) -> dt.datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError, ValueError):
        return None


def _json_value(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _scrub(item)
            for key, item in value.items()
            if str(key).lower() not in _SECRET_KEYS
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _clamp(value: Any, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


class LegacyRadarImporter:
    """Inspect or import one legacy Signal Radar SQLite database."""

    def __init__(self, session: Session, database: str | Path):
        self.session = session
        self.database = Path(database).expanduser().resolve()

    def _connect(self) -> sqlite3.Connection:
        if not self.database.is_file():
            raise ValueError(f"Database file does not exist: {self.database}")
        if self.database.stat().st_size < 16:
            raise ValueError("The selected file is not a SQLite database.")
        connection = sqlite3.connect(
            f"{self.database.as_uri()}?mode=ro", uri=True, timeout=5
        )
        connection.row_factory = sqlite3.Row
        try:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity check failed.")
        except sqlite3.DatabaseError as exc:
            connection.close()
            raise ValueError("The selected file is not a readable SQLite database.") from exc
        return connection

    @staticmethod
    def _tables(connection: sqlite3.Connection) -> set[str]:
        return {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    def _validate_format(self, tables: set[str]) -> None:
        if not {"sources", "posts"}.issubset(tables):
            raise ValueError(
                "Unsupported database format. Select a pre-merge Signal Radar database "
                "containing the sources and posts tables."
            )

    @staticmethod
    def _selected(categories: Iterable[str] | None) -> tuple[str, ...]:
        requested = tuple(dict.fromkeys(categories or IMPORT_CATEGORIES))
        unknown = sorted(set(requested) - set(IMPORT_CATEGORIES))
        if unknown:
            raise ValueError(f"Unknown import categories: {', '.join(unknown)}")
        if "posts" in requested and "sources" not in requested:
            requested = ("sources", *requested)
        if "media" in requested and "posts" not in requested:
            requested = ("sources", "posts", *requested)
        return tuple(dict.fromkeys(requested))

    def preview(self, categories: Iterable[str] | None = None) -> ImportReport:
        selected = self._selected(categories)
        with closing(self._connect()) as legacy:
            tables = self._tables(legacy)
            self._validate_format(tables)
            report = ImportReport(
                mode="preview",
                source_format="signal_radar",
                categories={name: ImportCount() for name in selected},
            )
            report.unsupported = {
                table: legacy.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in UNSUPPORTED_TABLES if table in tables
            }
            source_map, source_counts = self._plan_sources(legacy, apply=False)
            if "sources" in report.categories:
                report.categories["sources"] = source_counts
            item_map: dict[int, SignalItem] = {}
            if "posts" in report.categories:
                item_map, report.categories["posts"] = self._plan_posts(
                    legacy, source_map, apply=False
                )
            if "media" in report.categories:
                report.categories["media"] = self._plan_media(legacy, item_map, apply=False)
            if "provider_runs" in report.categories:
                report.categories["provider_runs"] = self._plan_runs(legacy, source_map, apply=False)
            if "source_suggestions" in report.categories:
                report.categories["source_suggestions"] = self._plan_suggestions(legacy, apply=False)
            report.warnings.append(
                "Legacy stories, scores, extracted entities and labels are intentionally skipped; "
                "raw posts are re-evaluated by the 3.0 pipeline."
            )
            return report

    def apply(self, categories: Iterable[str] | None = None) -> ImportReport:
        selected = self._selected(categories)
        report = ImportReport(
            mode="apply",
            source_format="signal_radar",
            categories={name: ImportCount() for name in selected},
        )
        try:
            with closing(self._connect()) as legacy:
                tables = self._tables(legacy)
                self._validate_format(tables)
                report.unsupported = {
                    table: legacy.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    for table in UNSUPPORTED_TABLES if table in tables
                }
                source_map, counts = self._plan_sources(legacy, apply="sources" in selected)
                if "sources" in report.categories:
                    report.categories["sources"] = counts
                item_map: dict[int, SignalItem] = {}
                if "posts" in selected:
                    item_map, report.categories["posts"] = self._plan_posts(
                        legacy, source_map, apply=True
                    )
                if "media" in selected:
                    report.categories["media"] = self._plan_media(legacy, item_map, apply=True)
                if "provider_runs" in selected:
                    report.categories["provider_runs"] = self._plan_runs(
                        legacy, source_map, apply=True
                    )
                if "source_suggestions" in selected:
                    report.categories["source_suggestions"] = self._plan_suggestions(
                        legacy, apply=True
                    )
                self.session.flush()
                self._resolve_lineage(item_map)
                report.warnings.append(
                    "Imported posts are pending analysis. Run Radar's reprocess/cluster action "
                    "after reviewing this report."
                )
                return report
        except Exception:
            self.session.rollback()
            raise

    def _source_for_row(self, row: sqlite3.Row) -> Source | None:
        platform = (row["platform"] or "").strip().lower()
        handle = (row["handle"] or "").strip()
        if platform == "twitter":
            platform = "x"
        if platform == "rss":
            return find_source_by_feed_url(self.session, handle)
        return self.session.scalar(select(Source).where(
            func.lower(Source.provider) == platform,
            func.lower(Source.provider_key) == handle.lstrip("@").lower(),
        ))

    def _plan_sources(
        self, legacy: sqlite3.Connection, *, apply: bool
    ) -> tuple[dict[int, Source], ImportCount]:
        count = ImportCount()
        mapping: dict[int, Source] = {}
        for row in legacy.execute("SELECT * FROM sources ORDER BY id"):
            count.detected += 1
            platform = (row["platform"] or "").strip().lower()
            handle = (row["handle"] or "").strip()
            if platform == "twitter":
                platform = "x"
            if (
                platform not in {"rss", "x", "replay"}
                or not handle
                or (platform == "rss" and canonical_feed_key(handle) is None)
            ):
                count.invalid += 1
                continue
            existing = self._source_for_row(row)
            if existing:
                mapping[row["id"]] = existing
                count.duplicate += 1
                continue
            count.importable += 1
            if not apply:
                mapping[row["id"]] = Source(
                    name=(row["display_name"] or handle)[:255],
                    type=SourceType.RSS if platform == "rss" else SourceType.SOCIAL,
                    provider=platform,
                    provider_key=handle if platform == "rss" else handle.lstrip("@"),
                )
                continue
            name = (row["display_name"] or handle).strip()[:255]
            base = name
            suffix = 2
            while self.session.scalar(select(Source.id).where(Source.name == name)):
                name = f"{base[:230]} (imported {suffix})"
                suffix += 1
            key = handle if platform == "rss" else handle.lstrip("@")
            source = Source(
                name=name,
                type=SourceType.RSS if platform == "rss" else SourceType.SOCIAL,
                url=handle if platform == "rss" else None,
                description=(row["notes"] or None),
                trust_weight=_clamp(row["reliability"]),
                created_at=_parse_datetime(row["created_at"]) or dt.datetime.utcnow(),
                provider=platform,
                provider_key=key,
                enabled=bool(row["enabled"]),
                polling_enabled=False,
                muted=bool(row["muted"]),
                priority=max(1, min(5, int(row["priority"] or 3))),
                languages=json.dumps(_json_value(row["languages"], [])),
                expertise=json.dumps(_json_value(row["expertise"], [])),
                signal_types=json.dumps(_json_value(row["signal_types"], [])),
                notes=row["notes"],
                cursor=row["cursor"],
                last_observed_item_at=_parse_datetime(row["last_seen_at"]),
                provider_metadata=json.dumps({
                    "imported_from": "signal_radar",
                    "legacy_source_id": row["id"],
                }),
            )
            self.session.add(source)
            self.session.flush()
            mapping[row["id"]] = source
            count.imported += 1
        return mapping, count

    def _plan_posts(
        self, legacy: sqlite3.Connection, sources: dict[int, Source], *, apply: bool
    ) -> tuple[dict[int, SignalItem], ImportCount]:
        count = ImportCount()
        mapping: dict[int, SignalItem] = {}
        for row in legacy.execute("SELECT * FROM posts ORDER BY id"):
            count.detected += 1
            provider = (row["platform"] or "").strip().lower()
            provider = "x" if provider == "twitter" else provider
            external_id = str(row["external_id"] or "").strip()
            source = sources.get(row["source_id"])
            if not source or not provider or not external_id:
                count.invalid += 1
                continue
            existing = self.session.scalar(select(SignalItem).where(
                SignalItem.provider == provider, SignalItem.external_id == external_id
            ))
            if existing:
                mapping[row["id"]] = existing
                count.duplicate += 1
                continue
            count.importable += 1
            if not apply:
                mapping[row["id"]] = SignalItem(
                    source_id=source.id or 0,
                    provider=provider,
                    external_id=external_id,
                    raw_payload="{}",
                    normalized_text="",
                    content_hash="preview",
                )
                continue
            raw = _scrub(_json_value(row["raw"], {}))
            if not isinstance(raw, dict):
                raw = {"legacy_raw": raw}
            raw["legacy_import"] = {"database": "signal_radar", "post_id": row["id"]}
            links = _json_value(row["links"], [])
            links = links if isinstance(links, list) else []
            text = row["text"] or ""
            item = SignalItem(
                source_id=source.id,
                provider=provider,
                external_id=external_id,
                raw_payload=json.dumps(raw, ensure_ascii=False),
                normalized_text=text,
                title=(raw.get("title") or None) if isinstance(raw, dict) else None,
                url=links[0] if links else None,
                expanded_links=json.dumps(links),
                author_handle=source.provider_key,
                author_display_name=source.name,
                posted_at=_parse_datetime(row["posted_at"]),
                collected_at=_parse_datetime(row["collected_at"]) or dt.datetime.utcnow(),
                language=row["language"],
                fidelity=row["fidelity"] or "full",
                quoted_external_id=row["quoted_external_id"],
                reply_to_external_id=row["reply_to_external_id"],
                deleted_observed_at=_parse_datetime(row["seen_deleted_at"]),
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                processing_version=0,
                processing_state=SignalProcessingState.PENDING,
            )
            self.session.add(item)
            self.session.flush()
            mapping[row["id"]] = item
            count.imported += 1
        return mapping, count

    def _plan_media(
        self, legacy: sqlite3.Connection, items: dict[int, SignalItem], *, apply: bool
    ) -> ImportCount:
        count = ImportCount()
        if "media" not in self._tables(legacy):
            return count
        for row in legacy.execute("SELECT * FROM media ORDER BY id"):
            count.detected += 1
            item = items.get(row["post_id"])
            url = (row["url"] or "").strip()
            if not item or not url:
                count.invalid += 1
                continue
            if not apply:
                existing = (
                    self.session.scalar(select(SignalMedia).where(
                        SignalMedia.signal_item_id == item.id,
                        SignalMedia.remote_url == url,
                    ))
                    if item.id else None
                )
                if existing:
                    count.duplicate += 1
                else:
                    count.importable += 1
                continue
            existing = self.session.scalar(select(SignalMedia).where(
                SignalMedia.signal_item_id == item.id, SignalMedia.remote_url == url
            ))
            if existing:
                count.duplicate += 1
                continue
            count.importable += 1
            self.session.add(SignalMedia(
                signal_item_id=item.id,
                media_type=(row["kind"] or "image")[:20],
                remote_url=url,
                alt_text=row["alt_text"],
                from_quoted=bool(row["from_quoted"]),
                local_path=None,
                download_state=SignalMediaDownloadState.PENDING,
            ))
            count.imported += 1
        return count

    def _plan_runs(
        self, legacy: sqlite3.Connection, sources: dict[int, Source], *, apply: bool
    ) -> ImportCount:
        count = ImportCount()
        if "provider_runs" not in self._tables(legacy):
            return count
        valid_status = {item.value for item in ProviderRunStatus}
        for row in legacy.execute("SELECT * FROM provider_runs ORDER BY id"):
            count.detected += 1
            started = _parse_datetime(row["started_at"])
            source = sources.get(row["source_id"])
            if not started:
                count.invalid += 1
                continue
            existing = (
                self.session.scalar(select(ProviderRun).where(
                    ProviderRun.provider == (row["provider"] or "unknown"),
                    ProviderRun.source_id == source.id,
                    ProviderRun.started_at == started,
                ))
                if source and source.id else None
            )
            if existing:
                count.duplicate += 1
                continue
            count.importable += 1
            if apply:
                status = row["status"] if row["status"] in valid_status else "failed"
                self.session.add(ProviderRun(
                    provider=row["provider"] or "unknown",
                    source_id=source.id if source else None,
                    started_at=started,
                    finished_at=_parse_datetime(row["finished_at"]),
                    items_collected=int(row["items_collected"] or 0),
                    duplicates_skipped=int(row["duplicates_skipped"] or 0),
                    cursor_before=row["cursor_before"],
                    cursor_after=row["cursor_after"],
                    status=ProviderRunStatus(status),
                    error=row["error"],
                ))
                count.imported += 1
        return count

    def _plan_suggestions(self, legacy: sqlite3.Connection, *, apply: bool) -> ImportCount:
        count = ImportCount()
        if "source_candidates" not in self._tables(legacy):
            return count
        for row in legacy.execute("SELECT * FROM source_candidates ORDER BY id"):
            count.detected += 1
            platform = (row["platform"] or "unknown").strip().lower()
            handle = (row["handle"] or "").strip().lstrip("@")
            if not handle:
                count.invalid += 1
                continue
            existing = self.session.scalar(select(SourceSuggestion).where(
                SourceSuggestion.kind == SourceSuggestionKind.HANDLE,
                SourceSuggestion.platform == platform,
                func.lower(SourceSuggestion.provider_key) == handle.lower(),
            ))
            if existing:
                count.duplicate += 1
                continue
            count.importable += 1
            if apply:
                old_status = (row["status"] or "pending").lower()
                status = {
                    "accepted": SourceSuggestionStatus.ADDED,
                    "dismissed": SourceSuggestionStatus.IGNORED,
                    "blocked": SourceSuggestionStatus.BLOCKED,
                }.get(old_status, SourceSuggestionStatus.PENDING)
                supporting = _json_value(row["supporting_post_ids"], [])
                appearances = len(supporting) if isinstance(supporting, list) else 0
                self.session.add(SourceSuggestion(
                    domain=f"legacy-handle:{platform}:{handle.lower()}"[:255],
                    kind=SourceSuggestionKind.HANDLE,
                    platform=platform,
                    provider_key=handle,
                    inferred_name=handle,
                    score=_clamp(row["est_reliability"]),
                    inferred_reliability=_clamp(row["est_reliability"]),
                    reasons=json.dumps([row["reason"] or "Imported legacy source candidate"]),
                    appearances=appearances,
                    status=status,
                    first_seen_at=_parse_datetime(row["created_at"]) or dt.datetime.utcnow(),
                    last_seen_at=_parse_datetime(row["created_at"]) or dt.datetime.utcnow(),
                ))
                count.imported += 1
        return count

    def _resolve_lineage(self, items: dict[int, SignalItem]) -> None:
        for item in items.values():
            if item.quoted_external_id:
                parent = self.session.scalar(select(SignalItem).where(
                    SignalItem.provider == item.provider,
                    SignalItem.external_id == item.quoted_external_id,
                ))
                item.quoted_signal_item_id = parent.id if parent else None
            if item.reply_to_external_id:
                parent = self.session.scalar(select(SignalItem).where(
                    SignalItem.provider == item.provider,
                    SignalItem.external_id == item.reply_to_external_id,
                ))
                item.reply_to_signal_item_id = parent.id if parent else None
