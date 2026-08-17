"""FastAPI app factory. Import this module only after confirming fastapi is
installed (the `web` extra) -- the CLI does exactly that, importing it lazily
inside `web serve` (and the operator CLI's `gui` command) rather than at
module load time, so the base CLI never requires fastapi/uvicorn just to run
`entity add` or `claim create`.

Every route delegates to the same repository/service classes the CLI uses:
ClaimRepository, EvidenceRepository, SourceRepository, EntityRepository,
RelationshipRepository, SuggestionRepository, SourceIntelligenceService (M4),
the graph queries (M5), and StoryScoringService (M6). This file only wires
HTTP <-> those classes and serializes the result -- no new business logic.

Write endpoints (added after the original read-only M7 dashboard) are each a
direct HTTP mirror of one CLI command -- same repository call, same
duplicate/not-found checks, same session.commit() placement -- so the CLI
and the browser dashboard can never drift into different rules for what's
allowed. See semi_intel/cli.py's entity_add/source_add/evidence_add/
claim_create/claim_link_evidence/claim_resolve/suggest_accept/suggest_reject
for the commands each endpoint below mirrors.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Literal, Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semi_intel.claim_engine.suggestion_service import SuggestionService
from semi_intel.db import get_engine, get_sessionmaker
from semi_intel.db import init_db as _init_db
from semi_intel.domain.enums import (
    ClaimStatus, EntityType, OperationalJobType, OperationalTriggerType, RelationType,
    SignalMentionStatus, SourceSuggestionStatus, SourceType, SuggestionStatus,
)
from semi_intel.domain.models import (
    Citation, Claim, DiscoveryResult, DiscoveryRun, EditorialStory, Entity, Evidence,
    MonitoredTopic, Source, SourceSuggestion, StoryEvidence, TopicMatch,
)
from semi_intel.discovery.service import DiscoveryService
from semi_intel.editorial.feed_discovery import discover_feeds
from semi_intel.editorial.service import (
    EditorialDiscoveryService, TopicService, delete_topic, normalize_phrase,
)
from semi_intel.entities.service import CanonicalEntityService
from semi_intel.graph.queries import find_by_relation, related_entities
from semi_intel.ingestion.hashing import hash_content
from semi_intel.repository.repositories import (
    ClaimRepository,
    EntityRepository,
    EvidenceRepository,
    RelationshipRepository,
    SourceRepository,
    SuggestionRepository,
)
from semi_intel.source_identity import find_source_by_feed_url
from semi_intel.source_intelligence.service import SourceIntelligenceService
from semi_intel.story_scoring.service import StoryScoringService
from semi_intel.web.schemas import (
    ClaimCreate,
    EntityCreate,
    EntityUpdate,
    MentionDispositionRequest,
    MentionResolveRequest,
    EvidenceCreate,
    LinkEvidenceRequest,
    RadarClaimCreate,
    SignalEvidenceRequest,
    ResolveClaimRequest,
    SourceCreate,
    SuggestionAcceptRequest,
    SuggestionRejectRequest,
    AddSuggestedSourceRequest,
    SourceSuggestionAction,
    StorySeenRequest,
    TopicCreate,
    TopicUpdate,
    BlockDomainRequest,
    DiscoverySettingsUpdate,
    CandidateSeenRequest,
    CandidateDismissRequest,
    CandidateSnoozeRequest,
    CandidatePromoteRequest,
    RadarSourceCreate,
    RadarSourceUpdate,
    RadarSourcePollingBulkRequest,
    RadarSettingsUpdate,
    SourceSuggestionReviewRequest,
    SourceReputationOverrideRequest,
    NotificationReadRequest,
    DigestGenerateRequest,
    WindowsTaskInstallRequest,
    NotificationSettingsUpdate,
    AdapterEnableRequest,
    BackupPruneRequest,
    BackupVerifyRequest,
    NotificationFeedbackRequest,
    SavedNotificationViewRequest,
    SchedulerSettingsUpdate,
)
from semi_intel.domain.enums import (
    NotificationEventType, SignalCandidateState, SourceSuggestionKind,
)
from semi_intel.domain.models import (
    CandidateEntity,
    ClaimLinkSuggestion,
    ClaimEvidenceLink,
    CandidatePromotionEvent,
    CandidateSignalItem,
    CandidateTopicMatch,
    ProviderRun,
    SignalCandidate,
    SignalEntityMention,
    SignalIndependenceGroup,
    SignalIndependenceGroupMember,
    SignalItem,
    SignalLabel,
    SignalTopicMatch,
    Notification,
    NotificationDigest,
    OperationalJobRun,
    ProviderIncident,
    SavedNotificationView,
)
from semi_intel.signals.candidate_state import (
    dismiss as dismiss_candidate,
    mark_seen as mark_candidate_seen,
    mark_unseen as mark_candidate_unseen,
    restore as restore_candidate,
    snooze as snooze_candidate,
)
from semi_intel.signals.analysis import analyze_unprocessed
from semi_intel.signals.aging import CandidateAge, CandidateAgingService
from semi_intel.signals.clustering import cluster_unclustered_items
from semi_intel.signals.collection import CollectionService, get_collection_settings
from semi_intel.signals.source_management import SourceManagementService, x_session_status
from semi_intel.signals.promotion import (
    PromotionBlocked,
    check_automatic_eligibility,
    get_promotion_settings,
    merge_candidate_into_story,
    promote_candidate,
    evidence_for_signal_item,
)
from semi_intel.signals.providers import ProviderUnavailable
from semi_intel.signals.providers.replay import ReplayProvider
from semi_intel.signals.providers.rss import RSSProvider
from semi_intel.signals.scoring import get_scoring_settings, rescore_active_candidates
from semi_intel.signals.suggestions import accept_source_suggestion, refresh_handle_suggestions
from semi_intel.legacy_import import IMPORT_CATEGORIES, LegacyRadarImporter
from semi_intel.notifications.digest import DigestService
from semi_intel.notifications.query import NotificationQueryFilters, NotificationQueryService
from semi_intel.notifications.service import NotificationService, aware, safe_error
from semi_intel.notifications.windows_desktop import WindowsDesktopDeliveryService
from semi_intel.operations.backup import BackupService
from semi_intel.operations.diagnostics import DiagnosticsService
from semi_intel.operations.health import HealthService
from semi_intel.operations.quality import (
    NotificationQualityService, SavedViewNotFoundError, SavedViewService,
)
from semi_intel.operations.scheduler import OperationalScheduler, effective_automation_state
from semi_intel.operations.windows_task import (
    WindowsTaskStatusService, current_executable, execute as execute_task_command,
    install_command as windows_task_install_command,
)
from semi_intel.operations.trends import OperationalTrendService
from semi_intel.operations.webhook import ExternalDeliveryService, WebhookConfigurationService
from semi_intel.web.serializers import (
    serialize_claim,
    serialize_entity,
    serialize_evidence,
    serialize_event,
    serialize_link,
    serialize_source,
    serialize_suggestion,
)

def _static_dir() -> Path:
    """Where index.html lives. Under normal execution that's this file's
    own directory; under a PyInstaller-frozen build, pure-Python modules
    don't have a real on-disk __file__, so packaging/semi_intel.spec bundles
    static/ as a data file instead, landing at sys._MEIPASS/semi_intel/web/static."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "semi_intel" / "web" / "static"  # type: ignore[attr-defined]
    return Path(__file__).parent / "static"


STATIC_DIR = _static_dir()


def get_session():
    """Type marker for `Depends(get_session)` in every route below.
    `create_app()` overrides this via `app.dependency_overrides` with a
    closure bound to one cached engine/sessionmaker for the app's lifetime --
    calling this un-overridden version directly (e.g. from a script) still
    works, just without that reuse."""
    engine = get_engine()
    _init_db(engine)
    session = get_sessionmaker(engine)()
    try:
        yield session
    finally:
        session.close()


def _topic_dict(topic: MonitoredTopic, session: Session) -> dict:
    match_count = session.scalar(
        select(func.count()).select_from(TopicMatch).where(TopicMatch.topic_id == topic.id)
    ) or 0
    last_match = session.scalar(
        select(func.max(TopicMatch.created_at)).where(TopicMatch.topic_id == topic.id)
    )
    return {
        "id": topic.id,
        "name": topic.name,
        "keyword": topic.keyword,
        "aliases": json.loads(topic.aliases or "[]"),
        "category": topic.category,
        "priority": topic.priority,
        "enabled": topic.enabled,
        "notes": topic.notes,
        "created_at": topic.created_at.isoformat(),
        "updated_at": topic.updated_at.isoformat(),
        "match_count": match_count,
        "last_matched_at": last_match.isoformat() if last_match else None,
    }


def _story_dict(story: EditorialStory, session: Session, detail: bool = False) -> dict:
    topic_rows = list(session.execute(
        select(MonitoredTopic, TopicMatch)
        .join(TopicMatch, TopicMatch.topic_id == MonitoredTopic.id)
        .where(TopicMatch.story_id == story.id)
    ))
    evidence_rows = list(session.scalars(
        select(Evidence).join(StoryEvidence, StoryEvidence.evidence_id == Evidence.id)
        .where(StoryEvidence.story_id == story.id)
        .order_by(Evidence.observed_at, Evidence.collected_at)
    ))
    source_ids = {e.source_id for e in evidence_rows}
    sources = {source.id: source for source in session.scalars(select(Source).where(Source.id.in_(source_ids)))} if source_ids else {}
    payload = {
        "id": story.id,
        "headline": story.headline,
        "summary": story.summary,
        "interest_score": story.interest_score,
        "reasons": json.loads(story.score_reasons or "[]"),
        "coverage_count": story.coverage_count,
        "seen": story.seen_at is not None,
        "seen_at": story.seen_at.isoformat() if story.seen_at else None,
        "new_coverage_count": story.new_coverage_count,
        "created_at": story.created_at.isoformat(),
        "latest_at": story.latest_at.isoformat(),
        "topics": [
            {"id": topic.id, "name": topic.name, "category": topic.category, "matched_text": match.matched_text}
            for topic, match in topic_rows
        ],
        "sources": [{"id": sid, "name": sources[sid].name} for sid in sorted(source_ids) if sid in sources],
    }
    if detail:
        payload["articles"] = [
            {
                **serialize_evidence(evidence),
                "source_name": sources[evidence.source_id].name if evidence.source_id in sources else None,
                "citations": [
                    {
                        "url": citation.destination_url,
                        "domain": citation.destination_domain,
                        "link_text": citation.link_text,
                    }
                    for citation in session.scalars(
                        select(Citation).where(Citation.evidence_id == evidence.id)
                    )
                ],
            }
            for evidence in evidence_rows
        ]
        discovery = DiscoveryService(session)
        eligibility = discovery.eligibility(story)
        latest_run = session.scalar(select(DiscoveryRun).where(
            DiscoveryRun.story_id == story.id
        ).order_by(DiscoveryRun.started_at.desc()).limit(1))
        discovered = list(session.scalars(select(DiscoveryResult).where(
            DiscoveryResult.story_id == story.id,
            DiscoveryResult.accepted.is_(True),
        ).order_by(DiscoveryResult.relevance_score.desc(), DiscoveryResult.published_at.desc())))
        payload["discovery"] = {
            "eligible": eligibility.eligible,
            "reason": eligibility.reason,
            "next_eligible_at": eligibility.next_eligible_at.isoformat() if eligibility.next_eligible_at else None,
            "latest_run": _discovery_run_dict(latest_run) if latest_run else None,
            "results": [_discovery_result_dict(item) for item in discovered],
            "budget": discovery.budget_status(),
        }
    return payload


def _discovery_result_dict(item: DiscoveryResult) -> dict:
    return {
        "id": item.id, "run_id": item.run_id, "story_id": item.story_id,
        "title": item.title, "url": item.original_url, "domain": item.canonical_domain,
        "publication_name": item.publication_name, "published_at": item.published_at.isoformat() if item.published_at else None,
        "provider": item.provider, "rank": item.provider_rank,
        "relevance_score": item.relevance_score, "accepted": item.accepted,
        "relationship": item.relationship.value,
        "explanation": json.loads(item.explanation or "[]"),
        "supporting_phrase": item.supporting_phrase,
    }


def _discovery_run_dict(run: DiscoveryRun) -> dict:
    return {
        "id": run.id, "story_id": run.story_id, "provider": run.provider,
        "status": run.status.value, "eligibility_reason": run.eligibility_reason,
        "queries": json.loads(run.queries or "[]"),
        "query_reasons": json.loads(run.query_reasons or "[]"),
        "request_count": run.request_count, "raw_result_count": run.raw_result_count,
        "accepted_result_count": run.accepted_result_count,
        "duplicate_result_count": run.duplicate_result_count,
        "filtered_result_count": run.filtered_result_count, "cache_hit": run.cache_hit,
        "budget": json.loads(run.budget_state or "{}"), "error": run.error_message,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def create_app() -> FastAPI:
    # Reconcile the schema to head via the exact same Alembic-aware path
    # `semintel install`/`update` already use, instead of a bare
    # create_all(). The dashboard is a supported entry point on its own
    # (`semi-intel web serve`, `semintel gui`) that someone can launch
    # directly against an existing database without ever running
    # `semintel install`/`db upgrade` first; a bare create_all() only adds
    # missing tables, so it silently leaves `alembic_version` stale for an
    # older-but-compatible database, and would silently mask a future
    # migration that isn't purely additive. `upgrade_or_stamp_to_head()`
    # falls back to stamping (not re-creating) when the tables already
    # exist -- safe because create_all() and `alembic upgrade head` are
    # verified byte-identical (tests/test_migrations.py) -- and creates
    # the schema from scratch via real migrations on a truly fresh
    # database, so a bare create_all() call is no longer needed here.
    from semi_intel.cli import upgrade_or_stamp_to_head

    upgrade_or_stamp_to_head()

    # Seed once before the server accepts concurrent dashboard requests.
    # Seeding inside the per-request dependency allowed the initial topic and
    # story requests to race each other on a brand-new database.
    startup_engine = get_engine()
    startup_session = get_sessionmaker(startup_engine)()
    try:
        TopicService(startup_session).seed()
        startup_session.commit()
    finally:
        startup_session.close()

    # Reuse that same engine (and its connection pool) for every request
    # instead of the module-level get_session() default, which used to build
    # a brand-new engine AND re-run schema reflection/create_all() on every
    # single HTTP call. Under the dashboard's own concurrent Promise.all()
    # page-load bursts, that repeatedly stacked dozens of fresh SQLite
    # connections against the same file with no room to spare, producing
    # "database is locked" errors. One long-lived engine per app instance
    # (still per-request Sessions, so no cross-request state leaks) fixes it.
    request_session_factory = get_sessionmaker(startup_engine)

    def _request_scoped_session():
        session = request_session_factory()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI(
        title="Semiconductor Intelligence Platform",
        description="Dashboard over claims, evidence, sources, and the knowledge graph -- "
        "browse and edit from the browser or the semi-intel/semintel CLIs, same data either way.",
    )
    app.dependency_overrides[get_session] = _request_scoped_session

    @app.middleware("http")
    async def field_test_read_only(request: Request, call_next):
        """Keep the native field-test dashboard observational and offline-safe."""
        if (
            os.environ.get("SEMINTEL_FIELD_TEST_READ_ONLY") == "1"
            and request.method not in {"GET", "HEAD", "OPTIONS"}
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "Changes and collection are disabled in the macOS field-test app."},
            )
        return await call_next(request)

    # --- reads ---------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/runtime/identity")
    def runtime_identity():
        from semi_intel.runtime_bridge import as_jsonable, get_identity, get_version_info

        payload = as_jsonable(get_identity())
        payload.update(get_version_info())
        payload["field_test_read_only"] = os.environ.get("SEMINTEL_FIELD_TEST_READ_ONLY") == "1"
        return payload

    @app.get("/api/runtime/health")
    def runtime_health():
        from semi_intel.runtime_bridge import as_jsonable, get_health

        return as_jsonable(get_health())

    @app.get("/api/topics")
    def list_topics(
        enabled: Optional[bool] = None,
        search: Optional[str] = None,
        session: Session = Depends(get_session),
    ):
        topics = list(session.scalars(select(MonitoredTopic).order_by(MonitoredTopic.category, MonitoredTopic.name)))
        if enabled is not None:
            topics = [topic for topic in topics if topic.enabled == enabled]
        if search:
            needle = normalize_phrase(search)
            topics = [
                topic for topic in topics
                if needle in normalize_phrase(" ".join([
                    topic.name, topic.keyword, topic.category or "", *json.loads(topic.aliases or "[]")
                ]))
            ]
        return [_topic_dict(topic, session) for topic in topics]

    @app.post("/api/topics", status_code=201)
    def create_topic(body: TopicCreate, session: Session = Depends(get_session)):
        keyword = body.keyword or body.name
        try:
            TopicService(session).validate_unique(body.name, keyword, body.aliases)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        topic = MonitoredTopic(
            name=body.name.strip(),
            normalized_name=normalize_phrase(body.name),
            keyword=keyword.strip(),
            aliases=json.dumps([alias.strip() for alias in body.aliases if alias.strip()]),
            category=body.category.strip() if body.category else None,
            priority=body.priority,
            enabled=body.enabled,
            notes=body.notes,
        )
        session.add(topic)
        session.commit()
        return _topic_dict(topic, session)

    @app.put("/api/topics/{topic_id}")
    def update_topic(topic_id: int, body: TopicUpdate, session: Session = Depends(get_session)):
        topic = session.get(MonitoredTopic, topic_id)
        if not topic:
            raise HTTPException(status_code=404, detail="Monitored topic not found")
        keyword = body.keyword or body.name
        try:
            TopicService(session).validate_unique(body.name, keyword, body.aliases, exclude_id=topic_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        topic.name = body.name.strip()
        topic.normalized_name = normalize_phrase(body.name)
        topic.keyword = keyword.strip()
        topic.aliases = json.dumps([alias.strip() for alias in body.aliases if alias.strip()])
        topic.category = body.category.strip() if body.category else None
        topic.priority = body.priority
        topic.enabled = body.enabled
        topic.notes = body.notes
        topic.updated_at = dt.datetime.utcnow()
        session.commit()
        return _topic_dict(topic, session)

    @app.delete("/api/topics/{topic_id}", status_code=204)
    def remove_topic(topic_id: int, session: Session = Depends(get_session)):
        topic = session.get(MonitoredTopic, topic_id)
        if not topic:
            raise HTTPException(status_code=404, detail="Monitored topic not found")
        delete_topic(session, topic)
        session.commit()

    @app.get("/api/editorial/stories")
    def editorial_stories(
        state: str = "unseen",
        topic_id: Optional[int] = None,
        category: Optional[str] = None,
        source_id: Optional[int] = None,
        min_score: float = 0.0,
        sort: str = "interest",
        limit: int = 100,
        session: Session = Depends(get_session),
    ):
        stmt = select(EditorialStory).where(EditorialStory.interest_score >= min_score)
        if state == "unseen":
            stmt = stmt.where(EditorialStory.seen_at.is_(None))
        elif state == "seen":
            stmt = stmt.where(EditorialStory.seen_at.is_not(None))
        elif state != "all":
            raise HTTPException(status_code=400, detail="state must be unseen, seen, or all")
        if topic_id is not None:
            stmt = stmt.where(EditorialStory.id.in_(
                select(TopicMatch.story_id).where(TopicMatch.topic_id == topic_id)
            ))
        if category:
            stmt = stmt.where(EditorialStory.id.in_(
                select(TopicMatch.story_id).join(
                    MonitoredTopic, MonitoredTopic.id == TopicMatch.topic_id
                ).where(MonitoredTopic.category == category)
            ))
        if source_id is not None:
            stmt = stmt.where(EditorialStory.id.in_(
                select(StoryEvidence.story_id).join(
                    Evidence, Evidence.id == StoryEvidence.evidence_id
                ).where(Evidence.source_id == source_id)
            ))
        ordering = {
            "interest": (EditorialStory.interest_score.desc(), EditorialStory.latest_at.desc()),
            "newest": (EditorialStory.latest_at.desc(),),
            "oldest": (EditorialStory.latest_at.asc(),),
            "coverage": (EditorialStory.coverage_count.desc(), EditorialStory.latest_at.desc()),
        }
        if sort not in ordering:
            raise HTTPException(status_code=400, detail="Unknown sort order")
        stories = list(session.scalars(stmt.distinct().order_by(*ordering[sort]).limit(min(limit, 500))))
        return [_story_dict(story, session) for story in stories]

    @app.get("/api/editorial/stories/{story_id}")
    def editorial_story_detail(story_id: int, session: Session = Depends(get_session)):
        story = session.get(EditorialStory, story_id)
        if not story:
            raise HTTPException(status_code=404, detail="Editorial story not found")
        return _story_dict(story, session, detail=True)

    @app.post("/api/editorial/stories/seen")
    def set_story_seen(body: StorySeenRequest, session: Session = Depends(get_session)):
        stories = list(session.scalars(select(EditorialStory).where(EditorialStory.id.in_(body.story_ids))))
        if len(stories) != len(set(body.story_ids)):
            raise HTTPException(status_code=404, detail="One or more editorial stories were not found")
        now = dt.datetime.utcnow()
        for story in stories:
            story.seen_at = now if body.seen else None
            if body.seen:
                story.new_coverage_count = 0
        session.commit()
        return {"updated": len(stories), "seen": body.seen}

    @app.post("/api/editorial/backfill")
    def editorial_backfill(session: Session = Depends(get_session)):
        result = EditorialDiscoveryService(session).backfill()
        session.commit()
        return {
            "evidence_scanned": result.evidence_scanned,
            "stories_created": result.stories_created,
            "matches_created": result.matches_created,
            "citations_created": result.citations_created,
        }

    @app.get("/api/discovery/status")
    def discovery_status(session: Session = Depends(get_session)):
        service = DiscoveryService(session)
        service.recover_stale_runs()
        settings = service.settings()
        recent = list(session.scalars(
            select(DiscoveryRun).order_by(DiscoveryRun.started_at.desc()).limit(50)
        ))
        session.commit()
        return {
            "provider": settings.provider,
            "enabled": settings.enabled,
            "automatic": settings.automatic,
            "budget": service.budget_status(),
            "recent_runs": [_discovery_run_dict(run) for run in recent],
        }

    @app.get("/api/discovery/settings")
    def discovery_settings(session: Session = Depends(get_session)):
        settings = DiscoveryService(session).settings()
        session.commit()
        return {
            "enabled": settings.enabled, "automatic": settings.automatic,
            "provider": settings.provider,
            "minimum_interest_score": settings.minimum_interest_score,
            "maximum_story_age_hours": settings.maximum_story_age_hours,
            "cooldown_hours": settings.cooldown_hours,
            "maximum_cycles_per_story": settings.maximum_cycles_per_story,
            "maximum_queries_per_cycle": settings.maximum_queries_per_cycle,
            "results_per_query": settings.results_per_query,
            "maximum_results_per_cycle": settings.maximum_results_per_cycle,
            "global_cycles_per_hour": settings.global_cycles_per_hour,
            "provider_requests_per_hour": settings.provider_requests_per_hour,
            "request_timeout_seconds": settings.request_timeout_seconds,
            "cache_hours": settings.cache_hours, "language": settings.language,
            "region": settings.region,
        }

    @app.put("/api/discovery/settings")
    def update_discovery_settings(body: DiscoverySettingsUpdate, session: Session = Depends(get_session)):
        settings = DiscoveryService(session).settings()
        for field, value in body.model_dump().items():
            setattr(settings, field, value)
        settings.updated_at = dt.datetime.utcnow()
        session.commit()
        return discovery_settings(session)

    @app.get("/api/discovery/runs")
    def discovery_runs(limit: int = 50, session: Session = Depends(get_session)):
        rows = list(session.scalars(
            select(DiscoveryRun).order_by(DiscoveryRun.started_at.desc()).limit(min(limit, 200))
        ))
        return [_discovery_run_dict(run) for run in rows]

    @app.get("/api/discovery/runs/{run_id}")
    def discovery_run_detail(run_id: int, session: Session = Depends(get_session)):
        run = session.get(DiscoveryRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Discovery run not found")
        results = list(session.scalars(
            select(DiscoveryResult).where(DiscoveryResult.run_id == run.id)
            .order_by(DiscoveryResult.accepted.desc(), DiscoveryResult.relevance_score.desc())
        ))
        return {**_discovery_run_dict(run), "results": [_discovery_result_dict(item) for item in results]}

    @app.post("/api/editorial/stories/{story_id}/discover")
    def discover_story(story_id: int, session: Session = Depends(get_session)):
        story = session.get(EditorialStory, story_id)
        if not story:
            raise HTTPException(status_code=404, detail="Editorial story not found")
        run = DiscoveryService(session).run_story(story)
        session.commit()
        return _discovery_run_dict(run)

    @app.post("/api/discovery/block-domain")
    def block_discovery_domain(body: BlockDomainRequest, session: Session = Depends(get_session)):
        from semi_intel.editorial.service import canonical_domain
        domain = canonical_domain(body.domain)
        suggestion = session.scalar(select(SourceSuggestion).where(SourceSuggestion.domain == domain))
        if not suggestion:
            suggestion = SourceSuggestion(domain=domain, inferred_name=domain.split(".")[0].title())
            session.add(suggestion)
        suggestion.status = SourceSuggestionStatus.BLOCKED
        session.commit()
        return {"domain": domain, "status": "blocked"}

    @app.post("/api/discovery/results/{result_id}/promote")
    def promote_discovery_result(result_id: int, session: Session = Depends(get_session)):
        result = session.get(DiscoveryResult, result_id)
        if not result:
            raise HTTPException(status_code=404, detail="Discovery result not found")
        suggestion = session.scalar(select(SourceSuggestion).where(
            SourceSuggestion.domain == result.canonical_domain
        ))
        if not suggestion:
            suggestion = SourceSuggestion(
                domain=result.canonical_domain,
                inferred_name=result.publication_name or result.canonical_domain.split(".")[0].title(),
                reasons=json.dumps(["Promoted from targeted story discovery"]),
            )
            session.add(suggestion)
        if suggestion.status == SourceSuggestionStatus.BLOCKED:
            raise HTTPException(status_code=409, detail="Domain is blocked")
        suggestion.status = SourceSuggestionStatus.PENDING
        session.commit()
        return {"suggestion_id": suggestion.id, "domain": suggestion.domain}

    @app.get("/api/source-suggestions")
    def source_suggestions(
        status: SourceSuggestionStatus = SourceSuggestionStatus.PENDING,
        session: Session = Depends(get_session),
    ):
        rows = list(session.scalars(
            select(SourceSuggestion).where(SourceSuggestion.status == status)
            .order_by(SourceSuggestion.score.desc(), SourceSuggestion.last_seen_at.desc())
        ))
        return [
            {
                "id": item.id, "domain": item.domain, "inferred_name": item.inferred_name,
                "feed_url": item.feed_url, "score": item.score,
                "reasons": json.loads(item.reasons or "[]"), "appearances": item.appearances,
                "story_count": item.story_count, "topic_count": item.topic_count,
                "status": item.status.value, "first_seen_at": item.first_seen_at.isoformat(),
                "last_seen_at": item.last_seen_at.isoformat(),
                # Provider context (brief section 14 absorption): exposed so the
                # Suggested Sources UI can pick the correct workflow per row
                # instead of always assuming website/RSS discovery -- see
                # SourceSuggestion.kind/platform/provider_key docstring.
                "kind": item.kind.value, "platform": item.platform,
                "provider_key": item.provider_key,
            }
            for item in rows
        ]

    @app.post("/api/source-suggestions/{suggestion_id}/review")
    def review_source_suggestion(
        suggestion_id: int, body: SourceSuggestionAction, session: Session = Depends(get_session)
    ):
        suggestion = session.get(SourceSuggestion, suggestion_id)
        if not suggestion:
            raise HTTPException(status_code=404, detail="Source suggestion not found")
        states = {
            "ignore": SourceSuggestionStatus.IGNORED,
            "block": SourceSuggestionStatus.BLOCKED,
            "restore": SourceSuggestionStatus.PENDING,
        }
        if body.action not in states:
            raise HTTPException(status_code=400, detail="action must be ignore, block, or restore")
        suggestion.status = states[body.action]
        session.commit()
        return {"id": suggestion.id, "status": suggestion.status.value}

    def _deterministic_feed_candidate(suggestion: SourceSuggestion) -> Optional[str]:
        """Reddit/GitHub domain-kind suggestions know their exact feed URL
        shape up front (no crawling needed) -- see
        semi_intel/signals/source_discovery.py's module docstring for why
        they're kind=DOMAIN with a synthetic `domain` rather than a new
        provider."""
        if suggestion.platform == "reddit" and suggestion.provider_key:
            return f"https://www.reddit.com/r/{suggestion.provider_key}/.rss"
        if suggestion.platform == "github" and suggestion.provider_key:
            return f"https://github.com/{suggestion.provider_key}/releases.atom"
        return None

    @app.post("/api/source-suggestions/{suggestion_id}/discover-feed")
    def discover_source_feed(suggestion_id: int, session: Session = Depends(get_session)):
        suggestion = session.get(SourceSuggestion, suggestion_id)
        if not suggestion:
            raise HTTPException(status_code=404, detail="Source suggestion not found")
        if suggestion.kind == SourceSuggestionKind.HANDLE:
            raise HTTPException(
                status_code=400,
                detail="This suggestion is a platform handle, not a website -- use the "
                "handle add workflow instead of feed discovery.",
            )
        # Reddit/GitHub suggestions carry a synthetic, non-DNS `domain`
        # identity (e.g. "reddit:r/hardware") -- discover_feeds() expects a
        # real website to crawl, which https://{domain} is not for these.
        # The correct feed URL is deterministic for both platforms, so
        # retry that single known URL instead of a website-wide discovery
        # crawl (same "one bounded fetch" policy the discovery generators use).
        deterministic_url = _deterministic_feed_candidate(suggestion)
        if deterministic_url:
            from semi_intel.signals.source_discovery import _validate_deterministic_feed
            validated = _validate_deterministic_feed(deterministic_url)
            if validated:
                suggestion.feed_url = validated
                session.commit()
            return {"feeds": [validated] if validated else [], "selected": suggestion.feed_url}
        feeds = discover_feeds(f"https://{suggestion.domain}")
        if feeds:
            suggestion.feed_url = feeds[0]
            session.commit()
        return {"feeds": feeds, "selected": suggestion.feed_url}

    @app.post("/api/source-suggestions/{suggestion_id}/add", status_code=201)
    def add_suggested_source(
        suggestion_id: int, body: AddSuggestedSourceRequest, session: Session = Depends(get_session)
    ):
        suggestion = session.get(SourceSuggestion, suggestion_id)
        if not suggestion:
            raise HTTPException(status_code=404, detail="Source suggestion not found")
        if suggestion.kind == SourceSuggestionKind.HANDLE:
            raise HTTPException(
                status_code=400,
                detail="This suggestion is a platform handle, not a website -- use the "
                "handle add workflow (POST /api/radar/source-suggestions/{id}/review) instead.",
            )
        feed_url = body.feed_url or suggestion.feed_url
        if not feed_url:
            raise HTTPException(status_code=400, detail="No feed detected. Supply a feed URL before adding.")
        name = (body.name or suggestion.inferred_name).strip()
        if SourceRepository(session).find_by_name(name):
            raise HTTPException(status_code=409, detail=f"Source '{name}' already exists")
        source = Source(
            name=name, type=SourceType.RSS, url=feed_url, trust_weight=body.trust_weight,
            description=f"Discovered automatically from citations to {suggestion.domain}",
        )
        session.add(source)
        suggestion.status = SourceSuggestionStatus.ADDED
        session.commit()
        return serialize_source(source)

    def _evidence_payload(evidence: Evidence, session: Session) -> dict:
        payload = serialize_evidence(evidence)
        source = session.get(Source, evidence.source_id)
        payload["source_name"] = source.name if source else None
        candidate_ids: list[int] = []
        if evidence.origin_signal_item_id is not None:
            candidate_ids = list(session.scalars(
                select(CandidateSignalItem.candidate_id).where(
                    CandidateSignalItem.signal_item_id == evidence.origin_signal_item_id
                ).order_by(CandidateSignalItem.candidate_id)
            ))
        payload["radar_candidate_ids"] = candidate_ids
        return payload

    @app.get("/api/claims")
    def list_claims(
        status: Optional[ClaimStatus] = None,
        q: str = "",
        session: Session = Depends(get_session),
    ):
        stmt = select(Claim)
        if status:
            stmt = stmt.where(Claim.status == status)
        if q.strip():
            stmt = stmt.where(func.lower(Claim.statement).contains(q.strip().lower()))
        claims = list(session.scalars(stmt.order_by(Claim.updated_at.desc(), Claim.id.desc())))
        return [serialize_claim(c) for c in claims]

    @app.get("/api/claims/{claim_id}")
    def get_claim(claim_id: int, session: Session = Depends(get_session)):
        repo = ClaimRepository(session)
        claim = repo.get(claim_id)
        if claim is None:
            raise HTTPException(status_code=404, detail="Claim not found")
        ev_repo = EvidenceRepository(session)
        links = [
            {**serialize_link(link), "evidence": _evidence_payload(ev_repo.get(link.evidence_id), session)}
            for link in repo.links_for(claim)
        ]
        events = [serialize_event(e) for e in repo.events_for(claim)]
        return {**serialize_claim(claim), "evidence_links": links, "timeline": events}

    @app.get("/api/evidence")
    def list_evidence(session: Session = Depends(get_session)):
        return [_evidence_payload(e, session) for e in EvidenceRepository(session).list()]

    @app.get("/api/evidence/{evidence_id}")
    def get_evidence(evidence_id: int, session: Session = Depends(get_session)):
        evidence = EvidenceRepository(session).get(evidence_id)
        if evidence is None:
            raise HTTPException(status_code=404, detail="Evidence not found")
        return _evidence_payload(evidence, session)

    @app.get("/api/sources")
    def list_sources(session: Session = Depends(get_session)):
        return [serialize_source(s) for s in SourceRepository(session).list()]

    @app.get("/api/sources/rank")
    def rank_sources(session: Session = Depends(get_session)):
        reports = SourceIntelligenceService(session).report_all()

        def sort_key(r):
            acc = r.overall.accuracy
            return (acc is None, -(acc or 0.0), -r.overall.total)

        return [
            {
                "source_id": r.source_id,
                "source_name": r.source_name,
                "overall": {"total": r.overall.total, "correct": r.overall.correct, "accuracy": r.overall.accuracy},
                "by_company": {
                    company: {"total": b.total, "correct": b.correct, "accuracy": b.accuracy}
                    for company, b in r.by_company.items()
                },
                "weakens_count": r.weakens_count,
                "retracted_claim_count": r.retracted_claim_count,
                "open_claim_count": r.open_claim_count,
            }
            for r in sorted(reports, key=sort_key)
        ]

    @app.get("/api/stories/rank")
    def rank_stories(limit: int = 10, session: Session = Depends(get_session)):
        ranked = StoryScoringService(session).rank(limit=limit)
        return [
            {
                "claim": serialize_claim(r.claim),
                "score": {
                    "novelty": r.score.novelty,
                    "corroboration": r.score.corroboration,
                    "momentum": r.score.momentum,
                    "total": r.score.total,
                    "reasons": r.score.reasons,
                },
            }
            for r in ranked
        ]

    @app.get("/api/entities")
    def list_entities(
        search: str = "", type: Optional[EntityType] = None,
        session: Session = Depends(get_session),
    ):
        return CanonicalEntityService(session).list_entities(search=search, entity_type=type)

    @app.get("/api/entities/summary")
    def entity_summary(session: Session = Depends(get_session)):
        return CanonicalEntityService(session).summary()

    @app.get("/api/entities/mention-proposals")
    def entity_mention_proposals(
        search: str = "", proposed_type: str = "", offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=100), session: Session = Depends(get_session),
    ):
        return CanonicalEntityService(session).mention_proposals(
            search=search, proposed_type=proposed_type, offset=offset, limit=limit
        )

    @app.get("/api/entities/{entity_id}")
    def get_entity(entity_id: int, session: Session = Depends(get_session)):
        entity = EntityRepository(session).get(entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        return CanonicalEntityService(session).entity_payload(entity, include_detail=True)

    @app.get("/api/graph/related/{entity_id}")
    def graph_related(entity_id: int, depth: int = 2, session: Session = Depends(get_session)):
        if EntityRepository(session).get(entity_id) is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        nodes = related_entities(session, entity_id, max_depth=depth)
        return [
            {
                "entity_id": n.entity_id,
                "name": n.name,
                "type": n.type,
                "depth": n.depth,
                "via_relation": n.via_relation,
                "from_entity_name": n.from_entity_name,
            }
            for n in nodes
        ]

    @app.get("/api/graph/find")
    def graph_find(
        relation_type: RelationType,
        target: Optional[str] = None,
        source: Optional[str] = None,
        session: Session = Depends(get_session),
    ):
        matches = find_by_relation(session, relation_type, target_name=target, source_name=source)
        return [
            {
                "from_entity_id": m.from_entity_id,
                "from_entity_name": m.from_entity_name,
                "relation_type": m.relation_type,
                "to_entity_id": m.to_entity_id,
                "to_entity_name": m.to_entity_name,
            }
            for m in matches
        ]

    def _suggestion_payload(row, session: Session) -> dict:
        payload = serialize_suggestion(row)
        claim = session.get(Claim, row.claim_id)
        evidence = session.get(Evidence, row.evidence_id)
        source = session.get(Source, evidence.source_id) if evidence else None
        subject = session.get(Entity, claim.subject_entity_id) if claim and claim.subject_entity_id else None
        candidate_id = None
        if evidence and evidence.origin_signal_item_id:
            candidate_id = session.scalar(select(CandidateSignalItem.candidate_id).where(
                CandidateSignalItem.signal_item_id == evidence.origin_signal_item_id
            ).order_by(CandidateSignalItem.candidate_id).limit(1))
        payload.update({
            "claim_statement": claim.statement if claim else None,
            "claim_status": claim.status.value if claim else None,
            "subject_entity_id": subject.id if subject else None,
            "subject_entity_name": subject.name if subject else None,
            "evidence_title": evidence.title if evidence else None,
            "evidence_excerpt": (evidence.raw_content[:500] if evidence else None),
            "evidence_url": evidence.url if evidence else None,
            "source_name": source.name if source else None,
            "origin_signal_item_id": evidence.origin_signal_item_id if evidence else None,
            "origin_candidate_id": candidate_id,
        })
        return payload

    @app.get("/api/suggestions/readiness")
    def suggestion_readiness(session: Session = Depends(get_session)):
        return {
            "canonical_entities": session.scalar(select(func.count()).select_from(Entity)) or 0,
            "open_claims": session.scalar(select(func.count()).select_from(Claim).where(
                Claim.status == ClaimStatus.OPEN)) or 0,
            "claims_without_subject_entities": session.scalar(select(func.count()).select_from(Claim).where(
                Claim.status == ClaimStatus.OPEN, Claim.subject_entity_id.is_(None))) or 0,
            "canonical_evidence": session.scalar(select(func.count()).select_from(Evidence)) or 0,
            "existing_evidence_links": session.scalar(select(func.count()).select_from(ClaimEvidenceLink)) or 0,
            "pending_suggestions": session.scalar(select(func.count()).select_from(
                ClaimLinkSuggestion).where(ClaimLinkSuggestion.status == SuggestionStatus.PENDING)) or 0,
        }

    @app.get("/api/suggestions")
    def list_suggestions(
        status: Optional[SuggestionStatus] = None, claim_id: Optional[int] = None,
        subject_entity_id: Optional[int] = None, q: str = "",
        session: Session = Depends(get_session),
    ):
        rows = SuggestionRepository(session).list_by_status(status)
        payloads = [_suggestion_payload(row, session) for row in rows]
        needle = q.casefold().strip()
        if claim_id is not None:
            payloads = [row for row in payloads if row["claim_id"] == claim_id]
        if subject_entity_id is not None:
            payloads = [row for row in payloads if row["subject_entity_id"] == subject_entity_id]
        if needle:
            payloads = [row for row in payloads if needle in " ".join(str(row.get(key) or "") for key in (
                "claim_statement", "subject_entity_name", "evidence_title", "evidence_excerpt", "source_name"
            )).casefold()]
        return payloads

    # --- writes ----------------------------------------------------------
    # Each of these mirrors one semi_intel/cli.py command exactly -- same
    # repository call, same validation, same commit point -- so creating a
    # source/claim/etc. from the browser and from the CLI always behave
    # identically. See the module docstring above for the exact mapping.

    @app.post("/api/sources", status_code=201)
    def create_source(body: SourceCreate, session: Session = Depends(get_session)):
        repo = SourceRepository(session)
        if repo.find_by_name(body.name):
            raise HTTPException(status_code=400, detail=f"Source '{body.name}' already exists.")
        if body.type == SourceType.RSS:
            existing_feed = find_source_by_feed_url(session, body.url)
            if existing_feed:
                raise HTTPException(
                    status_code=400,
                    detail=f"This feed is already registered as '{existing_feed.name}'.",
                )
        source = Source(
            name=body.name,
            type=body.type,
            url=body.url,
            description=body.description,
            trust_weight=body.trust_weight,
        )
        repo.add(source)
        session.commit()
        return serialize_source(source)

    @app.post("/api/entities", status_code=201)
    def create_entity(body: EntityCreate, session: Session = Depends(get_session)):
        try:
            entity = CanonicalEntityService(session).create_entity(
                name=body.name, entity_type=body.type, aliases=body.aliases, attributes=body.attributes
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        session.commit()
        return serialize_entity(entity)

    @app.put("/api/entities/{entity_id}")
    def update_entity(entity_id: int, body: EntityUpdate, session: Session = Depends(get_session)):
        entity = session.get(Entity, entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        try:
            CanonicalEntityService(session).update_entity(
                entity, name=body.name, entity_type=body.type,
                aliases=body.aliases, attributes=body.attributes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        session.commit()
        return CanonicalEntityService(session).entity_payload(entity, include_detail=True)

    @app.post("/api/entities/mention-proposals/resolve")
    def resolve_entity_mention(body: MentionResolveRequest, session: Session = Depends(get_session)):
        service = CanonicalEntityService(session)
        entity = session.get(Entity, body.entity_id) if body.entity_id else None
        if body.entity_id and entity is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        if entity is None:
            if not body.name or body.type is None:
                raise HTTPException(status_code=422, detail="Name and canonical type are required for a new entity.")
            try:
                entity = service.create_entity(
                    name=body.name, entity_type=body.type, aliases=body.aliases,
                    attributes=body.attributes,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        try:
            result = service.resolve_group(
                candidate_text=body.candidate_text, proposed_type=body.proposed_entity_type,
                entity=entity, add_alias=body.add_observed_as_alias,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        session.commit()
        return {"entity": serialize_entity(entity), "mentions_updated": result.mentions_updated,
                "candidate_links_created": result.candidate_links_created}

    @app.post("/api/entities/mention-proposals/disposition")
    def dispose_entity_mention(body: MentionDispositionRequest, session: Session = Depends(get_session)):
        try:
            status = SignalMentionStatus(body.action)
        except ValueError:
            raise HTTPException(status_code=422, detail="Action must be rejected or ignored.")
        if status not in (SignalMentionStatus.REJECTED, SignalMentionStatus.IGNORED):
            raise HTTPException(status_code=422, detail="Action must be rejected or ignored.")
        try:
            result = CanonicalEntityService(session).reject_group(
                candidate_text=body.candidate_text, proposed_type=body.proposed_entity_type,
                status=status, reason=body.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        session.commit()
        return {"status": status.value, "mentions_updated": result.mentions_updated}

    @app.post("/api/evidence", status_code=201)
    def create_evidence(body: EvidenceCreate, session: Session = Depends(get_session)):
        src_repo = SourceRepository(session)
        ev_repo = EvidenceRepository(session)
        if not src_repo.get(body.source_id):
            raise HTTPException(status_code=400, detail=f"Source #{body.source_id} does not exist.")
        content_hash = hash_content(body.content)
        if ev_repo.find_by_hash(content_hash):
            raise HTTPException(status_code=400, detail="Duplicate evidence (identical content already captured).")
        try:
            observed = dt.datetime.fromisoformat(body.observed_at) if body.observed_at else None
        except ValueError:
            raise HTTPException(status_code=400, detail=f"'{body.observed_at}' isn't a valid ISO date/time.")
        evidence = Evidence(
            source_id=body.source_id,
            entity_id=body.entity_id,
            title=body.title,
            raw_content=body.content,
            content_hash=content_hash,
            url=body.url,
            observed_at=observed,
        )
        ev_repo.add(evidence)
        session.flush()
        EditorialDiscoveryService(session).process_evidence(evidence)
        session.commit()
        return serialize_evidence(evidence)

    @app.post("/api/claims", status_code=201)
    def create_claim(body: ClaimCreate, session: Session = Depends(get_session)):
        if body.subject_entity_id is not None and not EntityRepository(session).get(body.subject_entity_id):
            raise HTTPException(status_code=400, detail=f"Entity #{body.subject_entity_id} does not exist.")
        claim = ClaimRepository(session).create(body.statement, body.subject_entity_id)
        session.commit()
        return serialize_claim(claim)

    @app.post("/api/claims/{claim_id}/link-evidence")
    def link_evidence(claim_id: int, body: LinkEvidenceRequest, session: Session = Depends(get_session)):
        repo = ClaimRepository(session)
        ev_repo = EvidenceRepository(session)
        claim = repo.get(claim_id)
        evidence = ev_repo.get(body.evidence_id)
        if not claim or not evidence:
            raise HTTPException(status_code=404, detail="Claim or evidence not found.")
        existing = session.execute(select(ClaimEvidenceLink).where(
            ClaimEvidenceLink.claim_id == claim.id,
            ClaimEvidenceLink.evidence_id == evidence.id,
        )).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail="That evidence is already linked to this claim.")
        repo.link_evidence(claim, evidence, body.stance, body.note)
        session.commit()
        return serialize_claim(claim)

    @app.put("/api/claims/{claim_id}/evidence/{evidence_id}")
    def update_claim_evidence(
        claim_id: int,
        evidence_id: int,
        body: LinkEvidenceRequest,
        session: Session = Depends(get_session),
    ):
        if body.evidence_id != evidence_id:
            raise HTTPException(status_code=422, detail="Evidence ID in the request does not match the URL.")
        repo = ClaimRepository(session)
        claim = repo.get(claim_id)
        evidence = EvidenceRepository(session).get(evidence_id)
        if not claim or not evidence:
            raise HTTPException(status_code=404, detail="Claim or evidence not found.")
        try:
            link = repo.update_evidence_link(claim, evidence, body.stance, body.note)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        session.commit()
        return serialize_link(link)

    @app.delete("/api/claims/{claim_id}/evidence/{evidence_id}", status_code=204)
    def unlink_claim_evidence(claim_id: int, evidence_id: int, session: Session = Depends(get_session)):
        repo = ClaimRepository(session)
        claim = repo.get(claim_id)
        evidence = EvidenceRepository(session).get(evidence_id)
        if not claim or not evidence:
            raise HTTPException(status_code=404, detail="Claim or evidence not found.")
        try:
            repo.unlink_evidence(claim, evidence)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        session.commit()
        return None

    @app.post("/api/claims/{claim_id}/resolve")
    def resolve_claim(claim_id: int, body: ResolveClaimRequest, session: Session = Depends(get_session)):
        repo = ClaimRepository(session)
        claim = repo.get(claim_id)
        if not claim:
            raise HTTPException(status_code=404, detail="Claim not found.")
        repo.resolve(claim, body.status, body.note)
        session.commit()
        return serialize_claim(claim)

    @app.post("/api/suggestions/run")
    def run_suggestions(session: Session = Depends(get_session)):
        result = SuggestionService(session).run()
        session.commit()
        return {
            "entities_loaded": result.entities_loaded,
            "open_claims": result.open_claims,
            "claims_without_subject_entities": result.claims_without_subject_entities,
            "evidence_scanned": result.evidence_scanned,
            "pairs_evaluated": result.pairs_evaluated,
            "pairs_below_threshold": result.pairs_below_threshold,
            "suggestions_created": result.suggestions_created,
            "skipped_existing_pairs": result.skipped_existing_pairs,
            "skipped_existing_links": result.skipped_existing_links,
        }

    @app.post("/api/suggestions/{suggestion_id}/accept")
    def accept_suggestion(suggestion_id: int, body: SuggestionAcceptRequest, session: Session = Depends(get_session)):
        repo = SuggestionRepository(session)
        s = repo.get(suggestion_id)
        if not s:
            raise HTTPException(status_code=404, detail="Suggestion not found.")
        if s.status != SuggestionStatus.PENDING:
            raise HTTPException(status_code=400, detail=f"Suggestion already {s.status.value}.")
        try:
            repo.accept(s, body.stance, body.note)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        session.commit()
        return serialize_suggestion(s)

    @app.post("/api/suggestions/{suggestion_id}/reject")
    def reject_suggestion(suggestion_id: int, body: SuggestionRejectRequest, session: Session = Depends(get_session)):
        repo = SuggestionRepository(session)
        s = repo.get(suggestion_id)
        if not s:
            raise HTTPException(status_code=404, detail="Suggestion not found.")
        if s.status != SuggestionStatus.PENDING:
            raise HTTPException(status_code=400, detail=f"Suggestion already {s.status.value}.")
        repo.reject(s, body.note)
        session.commit()
        return serialize_suggestion(s)

    # --- Signal Radar (Phase 6) -----------------------------------------

    def _candidate_summary(
        candidate: SignalCandidate,
        session: Session,
        *,
        age_info: CandidateAge | None = None,
        age_days: int = 7,
    ) -> dict:
        topic_name = None
        if candidate.primary_topic_id:
            topic = session.get(MonitoredTopic, candidate.primary_topic_id)
            topic_name = topic.name if topic else None
        explanation = json.loads(candidate.score_explanation or "{}")
        ranked_reasons = sorted(
            (
                (float(component.get("contribution", 0.0)), str(component.get("detail", "")))
                for component in explanation.get("components", {}).values()
                if component.get("detail")
            ),
            reverse=True,
        )
        payload = {
            "id": candidate.id,
            "fingerprint": candidate.fingerprint,
            "title": candidate.title,
            "state": candidate.state.value,
            "seen": candidate.seen_at is not None,
            "attention_score": candidate.attention_score,
            "item_count": candidate.item_count,
            "distinct_source_count": candidate.distinct_source_count,
            "independent_source_group_count": candidate.independent_source_group_count,
            "primary_topic": topic_name,
            "strongest_artifact_type": candidate.strongest_artifact_type,
            "promoted_story_id": candidate.promoted_story_id,
            "first_observed_at": candidate.first_observed_at.isoformat() if candidate.first_observed_at else None,
            "latest_observed_at": candidate.latest_observed_at.isoformat() if candidate.latest_observed_at else None,
            "seen_at": candidate.seen_at.isoformat() if candidate.seen_at else None,
            "snoozed_until": candidate.snoozed_until.isoformat() if candidate.snoozed_until else None,
            "dismissed_at": candidate.dismissed_at.isoformat() if candidate.dismissed_at else None,
            "dismissed_reason": candidate.dismissed_reason,
            "why_interesting": [detail for contribution, detail in ranked_reasons if contribution > 0][:2],
            # v1.0.0 Candidate Intelligence: persisted the last time
            # GET .../intelligence was called for this candidate (not
            # recomputed on every list request) -- null until then.
            "confidence_score": candidate.confidence_score,
            "editorial_value_score": candidate.editorial_value_score,
            "timeline_stage": candidate.timeline_stage,
        }
        payload.update((age_info or CandidateAgingService(session).classify(
            candidate, age_days=age_days
        )).to_dict())
        return payload

    def _candidate_detail(candidate: SignalCandidate, session: Session, *, age_days: int = 7) -> dict:
        base = _candidate_summary(candidate, session, age_days=age_days)
        base["score_explanation"] = json.loads(candidate.score_explanation or "{}")

        member_rows = list(session.execute(
            select(SignalItem, CandidateSignalItem.attach_reasons, Source.name)
            .join(CandidateSignalItem, CandidateSignalItem.signal_item_id == SignalItem.id)
            .join(Source, Source.id == SignalItem.source_id)
            .where(CandidateSignalItem.candidate_id == candidate.id)
            .order_by(SignalItem.posted_at.asc().nullslast())
        ))
        item_ids = [item.id for item, _, _ in member_rows]
        evidence_by_signal_item = dict(
            session.execute(
                select(Evidence.origin_signal_item_id, Evidence.id).where(
                    Evidence.origin_signal_item_id.in_(item_ids)
                )
            ).tuples().all()
        ) if item_ids else {}
        timeline = []
        for item, attach_reasons, source_name in member_rows:
            mentions = list(session.scalars(
                select(SignalEntityMention).where(SignalEntityMention.signal_item_id == item.id)
            ))
            labels = list(session.scalars(select(SignalLabel).where(SignalLabel.signal_item_id == item.id)))
            topics = list(session.execute(
                select(MonitoredTopic.name, SignalTopicMatch.matched_text)
                .join(SignalTopicMatch, SignalTopicMatch.topic_id == MonitoredTopic.id)
                .where(SignalTopicMatch.signal_item_id == item.id)
                .order_by(MonitoredTopic.name)
            ))
            timeline.append({
                "signal_item_id": item.id,
                "source_id": item.source_id,
                "source": source_name,
                "provider": item.provider,
                "title": item.title,
                "text": item.normalized_text,
                "url": item.url,
                "posted_at": item.posted_at.isoformat() if item.posted_at else None,
                "quoted_signal_item_id": item.quoted_signal_item_id,
                "reply_to_signal_item_id": item.reply_to_signal_item_id,
                "fidelity": item.fidelity,
                "attach_reasons": json.loads(attach_reasons or "[]"),
                "mentions": [
                    {"text": m.candidate_text, "type": m.proposed_entity_type, "status": m.status.value,
                     "confidence": m.confidence, "reason": m.reason, "resolved_entity_id": m.resolved_entity_id}
                    for m in mentions
                ],
                "labels": [{"label": l.label, "confidence": l.confidence} for l in labels],
                "topics": [{"name": name, "matched_text": matched_text} for name, matched_text in topics],
                "origin_evidence_id": evidence_by_signal_item.get(item.id),
            })
        base["timeline"] = timeline

        groups = list(session.scalars(
            select(SignalIndependenceGroup).where(SignalIndependenceGroup.candidate_id == candidate.id)
        ))
        base["independence_groups"] = [
            {
                "id": g.id,
                "reason": g.reason,
                "origin_signal_item_id": g.origin_signal_item_id,
                "member_signal_item_ids": [
                    row[0] for row in session.execute(
                        select(SignalIndependenceGroupMember.signal_item_id).where(
                            SignalIndependenceGroupMember.group_id == g.id
                        )
                    )
                ],
            }
            for g in groups
        ]
        groups_by_item: dict[int, list[dict]] = {}
        for group in base["independence_groups"]:
            for signal_item_id in group["member_signal_item_ids"]:
                groups_by_item.setdefault(signal_item_id, []).append({
                    "id": group["id"],
                    "reason": group["reason"],
                    "origin_signal_item_id": group["origin_signal_item_id"],
                })
        for item in timeline:
            item["independence_groups"] = groups_by_item.get(item["signal_item_id"], [])

        entities = list(session.execute(
            select(CandidateEntity, Entity).join(Entity, Entity.id == CandidateEntity.entity_id)
            .where(CandidateEntity.candidate_id == candidate.id)
        ))
        base["resolved_entities"] = [
            {"id": e.id, "name": e.name, "type": e.type.value, "role": ce.role.value} for ce, e in entities
        ]

        settings = get_promotion_settings(session)
        eligibility = check_automatic_eligibility(session, candidate, settings)
        base["automatic_promotion_eligibility"] = {"eligible": eligibility.eligible, "reasons": eligibility.reasons}
        return base

    @app.get("/api/radar/status")
    def radar_status(session: Session = Depends(get_session)):
        collection_settings = get_collection_settings(session)
        promotion_settings = get_promotion_settings(session)

        def _count(state: SignalCandidateState, **extra) -> int:
            stmt = select(func.count()).select_from(SignalCandidate).where(SignalCandidate.state == state)
            return session.scalar(stmt) or 0

        unseen_active = session.scalar(
            select(func.count()).select_from(SignalCandidate).where(
                SignalCandidate.state == SignalCandidateState.ACTIVE, SignalCandidate.seen_at.is_(None)
            )
        ) or 0
        high_attention = session.scalar(
            select(func.count()).select_from(SignalCandidate).where(
                SignalCandidate.state == SignalCandidateState.ACTIVE, SignalCandidate.attention_score >= 0.6
            )
        ) or 0
        recent_promotions = session.scalar(
            select(func.count()).select_from(CandidatePromotionEvent).where(
                CandidatePromotionEvent.created_at >= dt.datetime.utcnow() - dt.timedelta(hours=24)
            )
        ) or 0
        recent_runs = list(session.scalars(
            select(ProviderRun).order_by(ProviderRun.started_at.desc()).limit(10)
        ))
        return {
            "collection_enabled": collection_settings.collection_enabled,
            "x_provider_enabled": collection_settings.x_provider_enabled,
            "automatic_promotion_enabled": promotion_settings.automatic_promotion_enabled,
            "counts": {
                "unseen_active": unseen_active,
                "high_attention": high_attention,
                "active": _count(SignalCandidateState.ACTIVE),
                "stale": _count(SignalCandidateState.STALE),
                "snoozed": _count(SignalCandidateState.SNOOZED),
                "dismissed": _count(SignalCandidateState.DISMISSED),
                "promoted": _count(SignalCandidateState.PROMOTED),
                "promoted_last_24h": recent_promotions,
            },
            "recent_provider_runs": [
                {
                    "id": r.id, "provider": r.provider, "source_id": r.source_id, "status": r.status.value,
                    "items_collected": r.items_collected, "started_at": r.started_at.isoformat(),
                    "error": safe_error(r.error),
                }
                for r in recent_runs
            ],
        }

    async def _run_legacy_import(
        request: Request, categories: str, apply: bool, session: Session
    ) -> dict:
        payload = await request.body()
        if not payload:
            raise HTTPException(status_code=400, detail="Choose a Signal Radar database file.")
        if len(payload) > 128 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Database file exceeds the 128 MB import limit.")
        if not payload.startswith(b"SQLite format 3\x00"):
            raise HTTPException(status_code=400, detail="The selected file is not a SQLite database.")
        selected = [value.strip() for value in categories.split(",") if value.strip()]
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temporary:
                temporary.write(payload)
                temporary_path = Path(temporary.name)
            importer = LegacyRadarImporter(session, temporary_path)
            report = importer.apply(selected) if apply else importer.preview(selected)
            if apply:
                session.commit()
            return report.to_dict()
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            session.rollback()
            raise
        finally:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)

    @app.post("/api/radar/import/preview")
    async def radar_import_preview(
        request: Request,
        categories: str = ",".join(IMPORT_CATEGORIES),
        session: Session = Depends(get_session),
    ):
        return await _run_legacy_import(request, categories, False, session)

    @app.post("/api/radar/import/apply")
    async def radar_import_apply(
        request: Request,
        categories: str = ",".join(IMPORT_CATEGORIES),
        session: Session = Depends(get_session),
    ):
        return await _run_legacy_import(request, categories, True, session)

    @app.get("/api/radar/candidates")
    def radar_candidates(
        state: str = "active",
        min_score: float = Query(0.0, ge=0.0, le=1.0),
        topic_id: Optional[int] = None,
        sort: Literal["score", "newest", "oldest"] = "score",
        age: Literal["current", "older", "all"] = "current",
        age_days: int = Query(7),
        limit: int = Query(100, ge=1, le=500),
        session: Session = Depends(get_session),
    ):
        try:
            CandidateAgingService.validate_window(age_days)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        stmt = select(SignalCandidate).where(SignalCandidate.attention_score >= min_score)
        if state == "unseen":
            stmt = stmt.where(SignalCandidate.state == SignalCandidateState.ACTIVE, SignalCandidate.seen_at.is_(None))
        elif state != "all":
            try:
                target = SignalCandidateState(state)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Unknown state {state!r}")
            stmt = stmt.where(SignalCandidate.state == target)
        if topic_id is not None:
            stmt = stmt.where(SignalCandidate.id.in_(
                select(CandidateTopicMatch.candidate_id).where(CandidateTopicMatch.topic_id == topic_id)
            ))
        
        candidates = list(session.scalars(stmt))
        ages = CandidateAgingService(session).classify_many(candidates, age_days=age_days)
        if age != "all":
            candidates = [candidate for candidate in candidates if ages[candidate.id].classification == age]

        epoch = dt.datetime(1970, 1, 1)
        if sort == "score":
            candidates.sort(key=lambda candidate: (-candidate.attention_score, candidate.id))
        elif sort == "newest":
            candidates.sort(key=lambda candidate: (
                -(ages[candidate.id].activity_at - epoch).total_seconds(), candidate.id
            ))
        else:
            candidates.sort(key=lambda candidate: (
                (ages[candidate.id].activity_at - epoch).total_seconds(), candidate.id
            ))
        candidates = candidates[:limit]
        return [
            _candidate_summary(candidate, session, age_info=ages[candidate.id], age_days=age_days)
            for candidate in candidates
        ]

    @app.get("/api/radar/candidates/{candidate_id}")
    def radar_candidate_detail(
        candidate_id: int,
        age_days: int = Query(7),
        session: Session = Depends(get_session),
    ):
        try:
            CandidateAgingService.validate_window(age_days)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        candidate = session.get(SignalCandidate, candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Signal candidate not found")
        return _candidate_detail(candidate, session, age_days=age_days)

    @app.get("/api/radar/candidates/{candidate_id}/intelligence")
    def radar_candidate_intelligence(candidate_id: int, session: Session = Depends(get_session)):
        """v1.0.0 Candidate Intelligence (deterministic origin graph, claim
        extraction, novelty, confidence, editorial value, contradictions,
        verification checklist, timeline stage). One consolidated,
        structured endpoint rather than seven thin wrappers around the
        same underlying computation -- see docs/CANDIDATE_INTELLIGENCE.md
        for the rationale. Confidence/editorial-value/timeline-stage are
        also written back onto the candidate row (persist=True) so list
        views can sort on them without recomputing every candidate."""
        from semi_intel.signals.candidate_intelligence import compute_candidate_intelligence
        candidate = session.get(SignalCandidate, candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Signal candidate not found")
        result = compute_candidate_intelligence(session, candidate)
        session.commit()
        return result.to_dict()

    def _require_candidate_item(session: Session, candidate_id: int, signal_item_id: int) -> SignalItem:
        item = session.get(SignalItem, signal_item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Signal report not found")
        membership = session.execute(select(CandidateSignalItem.id).where(
            CandidateSignalItem.candidate_id == candidate_id,
            CandidateSignalItem.signal_item_id == signal_item_id,
        )).scalar_one_or_none()
        if membership is None:
            raise HTTPException(status_code=400, detail="That report does not belong to this candidate.")
        return item

    @app.post("/api/radar/items/{signal_item_id}/evidence")
    def radar_item_to_evidence(
        signal_item_id: int,
        body: SignalEvidenceRequest,
        session: Session = Depends(get_session),
    ):
        item = session.get(SignalItem, signal_item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Signal report not found")
        if body.candidate_id is not None:
            item = _require_candidate_item(session, body.candidate_id, signal_item_id)
        existing = session.execute(
            select(Evidence).where(Evidence.origin_signal_item_id == signal_item_id)
        ).scalar_one_or_none()
        evidence = evidence_for_signal_item(session, item)
        session.commit()
        return {"created": existing is None, "evidence": _evidence_payload(evidence, session)}

    @app.post("/api/radar/candidates/{candidate_id}/claims", status_code=201)
    def radar_candidate_create_claim(
        candidate_id: int,
        body: RadarClaimCreate,
        session: Session = Depends(get_session),
    ):
        candidate = session.get(SignalCandidate, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Signal candidate not found")
        if body.subject_entity_id is not None and not EntityRepository(session).get(body.subject_entity_id):
            raise HTTPException(status_code=400, detail=f"Entity #{body.subject_entity_id} does not exist.")

        item = None
        evidence = None
        link = None
        if body.signal_item_id is not None:
            item = _require_candidate_item(session, candidate_id, body.signal_item_id)

        repo = ClaimRepository(session)
        claim = repo.create(body.statement.strip(), body.subject_entity_id)
        if item is not None:
            evidence = evidence_for_signal_item(session, item)
            provenance_note = body.note or f"Created from Radar candidate #{candidate_id}, report #{item.id}"
            link = repo.link_evidence(claim, evidence, body.stance, provenance_note)
        session.commit()
        return {
            "claim": serialize_claim(claim),
            "evidence": _evidence_payload(evidence, session) if evidence else None,
            "link": serialize_link(link) if link else None,
        }

    @app.post("/api/radar/candidates/seen")
    def radar_candidates_seen(body: CandidateSeenRequest, session: Session = Depends(get_session)):
        candidates = list(session.scalars(select(SignalCandidate).where(SignalCandidate.id.in_(body.candidate_ids))))
        if len(candidates) != len(set(body.candidate_ids)):
            raise HTTPException(status_code=404, detail="One or more candidates were not found")
        for candidate in candidates:
            if body.seen:
                mark_candidate_seen(candidate)
            else:
                mark_candidate_unseen(candidate)
        session.commit()
        return [_candidate_summary(c, session) for c in candidates]

    @app.post("/api/radar/candidates/{candidate_id}/dismiss")
    def radar_candidate_dismiss_route(candidate_id: int, body: CandidateDismissRequest, session: Session = Depends(get_session)):
        candidate = session.get(SignalCandidate, candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Signal candidate not found")
        dismiss_candidate(candidate, reason=body.reason)
        session.commit()
        return _candidate_summary(candidate, session)

    @app.post("/api/radar/candidates/{candidate_id}/restore")
    def radar_candidate_restore_route(candidate_id: int, session: Session = Depends(get_session)):
        candidate = session.get(SignalCandidate, candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Signal candidate not found")
        restore_candidate(candidate)
        session.commit()
        return _candidate_summary(candidate, session)

    @app.post("/api/radar/candidates/{candidate_id}/snooze")
    def radar_candidate_snooze_route(candidate_id: int, body: CandidateSnoozeRequest, session: Session = Depends(get_session)):
        candidate = session.get(SignalCandidate, candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Signal candidate not found")
        try:
            until = dt.datetime.fromisoformat(body.until)
        except ValueError:
            raise HTTPException(status_code=422, detail="until must be an ISO datetime string")
        snooze_candidate(candidate, until=until)
        session.commit()
        return _candidate_summary(candidate, session)

    @app.post("/api/radar/candidates/{candidate_id}/promote")
    def radar_candidate_promote_route(candidate_id: int, body: CandidatePromoteRequest, session: Session = Depends(get_session)):
        candidate = session.get(SignalCandidate, candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Signal candidate not found")
        try:
            if body.merge_into_story_id is not None:
                story = session.get(EditorialStory, body.merge_into_story_id)
                if not story:
                    raise HTTPException(status_code=404, detail="Target editorial story not found")
                result_story = merge_candidate_into_story(session, candidate, story, by=body.by)
            else:
                result_story = promote_candidate(session, candidate, by=body.by)
        except PromotionBlocked as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if body.headline:
            result_story.headline = body.headline.strip()
            session.commit()
        return {"candidate": _candidate_summary(candidate, session), "story_id": result_story.id}

    @app.get("/api/radar/settings")
    def radar_settings_get(session: Session = Depends(get_session)):
        collection = get_collection_settings(session)
        scoring = get_scoring_settings(session)
        promotion = get_promotion_settings(session)
        session.commit()
        return {
            "collection_enabled": collection.collection_enabled,
            "x_provider_enabled": collection.x_provider_enabled,
            "startup_stagger_seconds": collection.startup_stagger_seconds,
            "media_download_enabled": collection.media_download_enabled,
            "ocr_enabled": collection.ocr_enabled,
            "weight_topic_relevance": scoring.weight_topic_relevance,
            "weight_novelty": scoring.weight_novelty,
            "weight_momentum": scoring.weight_momentum,
            "weight_source_diversity": scoring.weight_source_diversity,
            "weight_artifact_strength": scoring.weight_artifact_strength,
            "weight_source_quality": scoring.weight_source_quality,
            "momentum_window_hours": scoring.momentum_window_hours,
            "staleness_days": scoring.staleness_days,
            "automatic_promotion_enabled": promotion.automatic_promotion_enabled,
            "minimum_attention_score": promotion.minimum_attention_score,
            "required_topic_match": promotion.required_topic_match,
            "maximum_candidate_age_hours": promotion.maximum_candidate_age_hours,
            "hourly_promotion_budget": promotion.hourly_promotion_budget,
        }

    @app.put("/api/radar/settings")
    def radar_settings_update(body: RadarSettingsUpdate, session: Session = Depends(get_session)):
        collection = get_collection_settings(session)
        scoring = get_scoring_settings(session)
        promotion = get_promotion_settings(session)
        collection.collection_enabled = body.collection_enabled
        collection.x_provider_enabled = body.x_provider_enabled
        collection.startup_stagger_seconds = body.startup_stagger_seconds
        collection.media_download_enabled = body.media_download_enabled
        collection.ocr_enabled = body.ocr_enabled
        scoring.weight_topic_relevance = body.weight_topic_relevance
        scoring.weight_novelty = body.weight_novelty
        scoring.weight_momentum = body.weight_momentum
        scoring.weight_source_diversity = body.weight_source_diversity
        scoring.weight_artifact_strength = body.weight_artifact_strength
        scoring.weight_source_quality = body.weight_source_quality
        scoring.momentum_window_hours = body.momentum_window_hours
        scoring.staleness_days = body.staleness_days
        promotion.automatic_promotion_enabled = body.automatic_promotion_enabled
        promotion.minimum_attention_score = body.minimum_attention_score
        promotion.required_topic_match = body.required_topic_match
        promotion.maximum_candidate_age_hours = body.maximum_candidate_age_hours
        promotion.hourly_promotion_budget = body.hourly_promotion_budget
        session.commit()
        return radar_settings_get(session=session)

    @app.get("/api/radar/sources")
    def radar_sources(session: Session = Depends(get_session)):
        stmt = select(Source).where(Source.provider != "manual").order_by(Source.provider, Source.name)
        sources = list(session.scalars(stmt))
        manager = SourceManagementService(session)
        return [manager.serialize(source) for source in sources]

    @app.get("/api/radar/source-reputations")
    def radar_source_reputations(session: Session = Depends(get_session)):
        from semi_intel.domain.models import SourceReputation
        from semi_intel.signals.reputation import effective_authority
        rows = list(session.execute(select(SourceReputation, Source.name).join(Source, Source.id == SourceReputation.source_id)))
        return [
            {
                "source_id": rep.source_id, "source_name": name,
                "authority": rep.authority, "authority_override": rep.authority_override,
                "effective_authority": effective_authority(rep),
                "historical_accuracy": rep.historical_accuracy,
                "editorial_yield": rep.editorial_yield, "noise_rate": rep.noise_rate,
                "originality": rep.originality, "specializations": json.loads(rep.specializations or "[]"),
                "lead_time_hours": rep.lead_time_hours, "verification_count": rep.verification_count,
                "false_positive_count": rep.false_positive_count, "items_contributed": rep.items_contributed,
                "last_updated": rep.last_updated.isoformat(),
            }
            for rep, name in rows
        ]

    @app.post("/api/radar/source-reputations/recompute")
    def radar_source_reputations_recompute(session: Session = Depends(get_session)):
        from semi_intel.signals.reputation import recompute_all_source_reputations
        count = recompute_all_source_reputations(session)
        return {"sources_recomputed": count}

    @app.put("/api/radar/source-reputations/{source_id}/override")
    def radar_source_reputation_override(source_id: int, body: SourceReputationOverrideRequest, session: Session = Depends(get_session)):
        from semi_intel.domain.models import SourceReputation
        from semi_intel.signals.reputation import get_or_create_reputation
        if not session.get(Source, source_id):
            raise HTTPException(status_code=404, detail="Source not found")
        rep = get_or_create_reputation(session, source_id)
        rep.authority_override = body.authority_override
        session.commit()
        return {"source_id": source_id, "authority_override": rep.authority_override}

    def _detect_provider(handle: str) -> str:
        """Auto-detect likely provider from a pasted URL/handle (brief
        section 19): an x.com/twitter.com URL or a bare @handle is X;
        anything else starting with http(s) is RSS; a bare word with no
        URL is treated as an X handle (the common case for pasting a
        leaker's handle directly)."""
        low = handle.lower()
        if handle.startswith("@"):
            return "x"
        if low.startswith("http://") or low.startswith("https://"):
            host = urlparse(low).netloc
            if host.startswith("www."):
                host = host[4:]
            if host in ("x.com", "twitter.com"):
                return "x"
            return "rss"
        return "x"

    @app.post("/api/radar/sources", status_code=201)
    def radar_sources_add(body: RadarSourceCreate, session: Session = Depends(get_session)):
        handle = body.handle_or_url.strip()
        provider_name = body.provider or _detect_provider(handle)

        if provider_name == "rss":
            candidate = RSSProvider().validate(handle)
        elif provider_name == "x":
            candidate = ReplayProvider(name="x").validate(handle)  # validate() is pure parsing, safe without playwright
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported provider {provider_name!r}")

        if hasattr(candidate, "reason"):  # ValidationError
            raise HTTPException(status_code=422, detail=candidate.reason)

        existing = session.execute(
            select(Source).where(Source.provider == provider_name, Source.provider_key == candidate.provider_key)
        ).scalar_one_or_none()
        if existing:
            return {
                "id": existing.id, "name": existing.name, "provider": existing.provider,
                "provider_key": existing.provider_key, "already_existed": True,
            }

        if provider_name == "rss":
            existing_feed = find_source_by_feed_url(session, candidate.profile_url)
            if existing_feed:
                return {
                    "id": existing_feed.id,
                    "name": existing_feed.name,
                    "provider": existing_feed.provider,
                    "provider_key": existing_feed.provider_key,
                    "already_existed": True,
                }

        name = body.display_name or candidate.display_name
        base_name, suffix = name, 2
        while session.execute(select(Source.id).where(Source.name == name)).first():
            name = f"{base_name} ({suffix})"
            suffix += 1

        source = Source(
            name=name,
            type=SourceType.RSS if provider_name == "rss" else SourceType.SOCIAL,
            url=candidate.profile_url if provider_name == "rss" else None,
            trust_weight=body.trust_weight,
            provider=provider_name,
            provider_key=candidate.provider_key,
            priority=body.priority,
            enabled=True,
            polling_enabled=body.polling_enabled,
        )
        session.add(source)
        session.commit()
        return {
            "id": source.id, "name": source.name, "provider": source.provider,
            "provider_key": source.provider_key, "already_existed": False,
        }

    @app.put("/api/radar/sources/polling")
    def radar_sources_polling_bulk(
        body: RadarSourcePollingBulkRequest, session: Session = Depends(get_session)
    ):
        sources = list(session.scalars(select(Source).where(Source.id.in_(body.source_ids))))
        x_count = sum(source.provider == "x" for source in sources)
        if body.polling_enabled and x_count:
            if not body.confirmed:
                raise HTTPException(
                    status_code=409,
                    detail=f"Explicit confirmation is required before enabling {x_count} X source(s).",
                )
            settings = get_collection_settings(session)
            if not settings.x_provider_enabled:
                raise HTTPException(status_code=409, detail="X collection is globally disabled.")
            session_state = x_session_status()
            if not session_state["usable"]:
                raise HTTPException(status_code=409, detail=session_state["reason"])
        try:
            return SourceManagementService(session).set_polling(
                body.source_ids, enabled=body.polling_enabled
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post("/api/radar/sources/{source_id}/collect")
    def radar_source_collect(source_id: int, session: Session = Depends(get_session)):
        source = session.get(Source, source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        if not source.enabled:
            raise HTTPException(status_code=409, detail="Enable this source before collecting it.")
        run = CollectionService(session).collect_source(source)
        return {
            "status": run.status.value, "items_collected": run.items_collected,
            "duplicates_skipped": run.duplicates_skipped, "error": safe_error(run.error),
        }

    @app.put("/api/radar/sources/{source_id}")
    def radar_source_update(
        source_id: int, body: RadarSourceUpdate, session: Session = Depends(get_session)
    ):
        source = session.get(Source, source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        try:
            updated = SourceManagementService(session).update(
                source,
                name=body.display_name,
                handle_or_url=body.handle_or_url,
                priority=body.priority,
                trust_weight=body.trust_weight,
                enabled=body.enabled,
                polling_enabled=body.polling_enabled,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return SourceManagementService(session).serialize(updated)

    @app.post("/api/radar/cluster")
    def radar_cluster_route(session: Session = Depends(get_session)):
        analyzed = analyze_unprocessed(session)
        summary = cluster_unclustered_items(session)
        session.commit()
        scored = rescore_active_candidates(session)
        return {
            "analyzed": analyzed,
            "attached_to_existing": summary.attached_to_existing,
            "new_candidates": summary.new_candidates,
            "suppressed_no_topic_or_artifact": summary.suppressed_no_topic_or_artifact,
            "rescored": scored,
        }

    @app.get("/api/radar/source-suggestions")
    def radar_source_suggestions_list(
        status: str = "pending", kind: Optional[str] = None, session: Session = Depends(get_session),
    ):
        stmt = select(SourceSuggestion)
        if status != "all":
            try:
                target = SourceSuggestionStatus(status)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Unknown status {status!r}")
            stmt = stmt.where(SourceSuggestion.status == target)
        if kind:
            try:
                target_kind = SourceSuggestionKind(kind)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Unknown kind {kind!r}")
            stmt = stmt.where(SourceSuggestion.kind == target_kind)
        rows = list(session.scalars(stmt.order_by(SourceSuggestion.score.desc())))
        return [
            {
                "id": r.id, "kind": r.kind.value, "domain": r.domain, "platform": r.platform,
                "provider_key": r.provider_key, "inferred_name": r.inferred_name, "score": r.score,
                "reasons": json.loads(r.reasons or "[]"), "appearances": r.appearances,
                "story_count": r.story_count, "topic_count": r.topic_count,
                "independent_origin_count": r.independent_origin_count, "status": r.status.value,
                "first_seen_at": r.first_seen_at.isoformat(), "last_seen_at": r.last_seen_at.isoformat(),
            }
            for r in rows
        ]

    @app.post("/api/radar/source-suggestions/refresh")
    def radar_source_suggestions_refresh(session: Session = Depends(get_session)):
        handle_count = refresh_handle_suggestions(session)
        session.commit()
        return {"handle_suggestions_created_or_updated": handle_count}

    @app.post("/api/radar/source-suggestions/discover")
    def radar_source_suggestions_discover(session: Session = Depends(get_session)):
        """Bounded, operator-triggered multi-provider discovery (v0.9.4):
        runs every registered generator (website/forum, subreddit, GitHub
        repository, attribution-handle) over already-collected signal
        data, each fault-isolated -- see
        semi_intel/signals/source_discovery.py's run_source_discovery()."""
        from semi_intel.signals.source_discovery import run_source_discovery
        report = run_source_discovery(session)
        return report.to_dict()

    @app.post("/api/radar/source-suggestions/{suggestion_id}/review")
    def radar_source_suggestion_review(suggestion_id: int, body: SourceSuggestionReviewRequest, session: Session = Depends(get_session)):
        suggestion = session.get(SourceSuggestion, suggestion_id)
        if not suggestion:
            raise HTTPException(status_code=404, detail="Source suggestion not found")
        if body.action == "accept":
            source = accept_source_suggestion(session, suggestion)
            session.commit()
            return {"status": "accepted", "source_id": source.id}
        elif body.action == "dismiss":
            suggestion.status = SourceSuggestionStatus.IGNORED
            session.commit()
            return {"status": "dismissed"}
        elif body.action == "block":
            suggestion.status = SourceSuggestionStatus.BLOCKED
            session.commit()
            return {"status": "blocked"}
        raise HTTPException(status_code=400, detail="action must be accept, dismiss, or block")

    # --- Alerts & Digest ---------------------------------------------------

    def _notification_dict(row: Notification) -> dict:
        return {
            "id": row.id, "event_type": row.event_type.value, "severity": row.severity.value,
            "title": row.title, "body": row.body, "reason": row.reason,
            "candidate_id": row.candidate_id, "story_id": row.story_id,
            "topic_id": row.topic_id, "source_suggestion_id": row.source_suggestion_id,
            "provider_run_id": row.provider_run_id, "source_id": row.source_id,
            "metadata": json.loads(row.event_metadata or "{}"),
            "created_at": aware(row.created_at).isoformat(), "event_at": aware(row.event_at).isoformat(),
            "latest_occurrence_at": aware(row.latest_occurrence_at).isoformat(),
            "occurrence_count": row.occurrence_count,
            "read_at": aware(row.read_at).isoformat() if row.read_at else None,
            "dismissed_at": aware(row.dismissed_at).isoformat() if row.dismissed_at else None,
            "muted": row.muted, "delivery_state": row.delivery_state.value,
        }

    def _digest_dict(row: NotificationDigest, *, full: bool = False) -> dict:
        result = {
            "id": row.id, "timezone": row.timezone,
            "window_start": aware(row.window_start).isoformat(),
            "window_end": aware(row.window_end).isoformat(),
            "generated_at": aware(row.generated_at).isoformat(),
            "status": row.status.value, "delivery_state": row.delivery_state.value,
            "sections": json.loads(row.structured_sections or "{}"),
        }
        if full:
            result["rendered_text"] = row.rendered_text
            result["notification_ids"] = json.loads(row.notification_ids or "[]")
        return result

    def _notification_settings_dict(settings) -> dict:
        names = (
            "in_app_enabled", "external_delivery_enabled",
            "windows_desktop_notifications_enabled", "minimum_attention_score",
            "minimum_score_increase", "required_independent_group_count", "required_topic_match",
            "high_score_enabled", "score_increase_enabled", "corroboration_enabled",
            "promotion_ready_enabled", "promotion_completed_enabled", "source_suggestion_enabled",
            "provider_health_enabled", "topic_activity_enabled", "daily_digest_enabled",
            "digest_time", "timezone", "quiet_hours_start", "quiet_hours_end",
            "maximum_immediate_per_hour", "maximum_delivery_attempts",
            "provider_failure_threshold", "provider_recovery_enabled",
            "source_suggestion_minimum_score", "topic_activity_minimum_candidates",
            "topic_activity_window_hours", "retention_days", "digest_maximum_items_per_section",
        )
        result = {name: getattr(settings, name) for name in names}
        result["activation_at"] = aware(settings.activation_at).isoformat()
        result["muted_event_types"] = json.loads(settings.muted_event_types or "[]")
        result["muted_topic_ids"] = json.loads(settings.muted_topic_ids or "[]")
        result["external_adapter_available"] = False
        return result

    @app.get("/api/notifications/status")
    def notifications_status(session: Session = Depends(get_session)):
        settings = NotificationService(session).settings()
        webhook = WebhookConfigurationService(session).status()
        counts = {
            "total": session.scalar(select(func.count()).select_from(Notification)) or 0,
            "unread": session.scalar(select(func.count()).select_from(Notification).where(
                Notification.read_at.is_(None), Notification.dismissed_at.is_(None),
                Notification.muted.is_(False),
            )) or 0,
            "dismissed": session.scalar(select(func.count()).select_from(Notification).where(
                Notification.dismissed_at.is_not(None)
            )) or 0,
            "open_incidents": session.scalar(select(func.count()).select_from(ProviderIncident).where(
                ProviderIncident.resolved_at.is_(None)
            )) or 0,
        }
        session.commit()
        return {
            "counts": counts, "settings": _notification_settings_dict(settings),
            "delivery": {
                "in_app": True, "external_enabled": settings.external_delivery_enabled,
                "external_adapter_available": webhook["configured"],
                "windows_desktop": WindowsDesktopDeliveryService(session).status(),
                "message": (
                    "Generic HTTPS webhook delivery is configured."
                    if webhook["configured"] else
                    "External delivery is not configured; local alerts and digests remain available."
                ),
            },
        }

    @app.get("/api/notifications")
    def notifications_list(
        state: str = "unread",
        event_type: List[str] = Query(default=[]),
        severity: List[str] = Query(default=[]),
        topic_id: List[int] = Query(default=[]),
        date_window_days: Optional[int] = None,
        sort: str = "newest",
        candidate_id: Optional[int] = None, story_id: Optional[int] = None,
        source_id: Optional[int] = None, source_suggestion_id: Optional[int] = None,
        date_from: Optional[dt.datetime] = None, date_to: Optional[dt.datetime] = None,
        search: Optional[str] = None, limit: int = 100,
        session: Session = Depends(get_session),
    ):
        filters = NotificationQueryFilters(
            state=state, event_types=event_type, severities=severity, topic_ids=topic_id,
            date_window_days=date_window_days, search_text=search or "", sort_order=sort,
            candidate_id=candidate_id, story_id=story_id, source_id=source_id,
            source_suggestion_id=source_suggestion_id, date_from=date_from, date_to=date_to,
            limit=limit,
        )
        try:
            rows = NotificationQueryService(session).run(filters)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return [_notification_dict(row) for row in rows]

    @app.post("/api/notifications/generate")
    def notifications_generate(session: Session = Depends(get_session)):
        summary = NotificationService(session).generate()
        session.commit()
        try:
            desktop = WindowsDesktopDeliveryService(session).deliver_pending()
        except Exception:  # noqa: BLE001 - desktop delivery never breaks generation
            session.rollback()
            desktop = {"notifications": 0, "disabled": False, "supported": False,
                       "error": "Desktop delivery failed without affecting alert generation."}
        return {
            "created": summary.created, "updated": summary.updated,
            "created_count": summary.created_count, "by_type": summary.by_type,
            "seeded_historical_candidates": summary.seeded_historical_candidates,
            "windows_desktop_delivery": desktop,
        }

    @app.post("/api/notifications/read")
    def notifications_read(body: NotificationReadRequest, session: Session = Depends(get_session)):
        rows = list(session.scalars(select(Notification).where(
            Notification.id.in_(body.notification_ids)
        )))
        if len(rows) != len(set(body.notification_ids)):
            raise HTTPException(status_code=404, detail="One or more notifications were not found")
        NotificationService(session).set_read(body.notification_ids, read=body.read)
        session.commit()
        return [_notification_dict(row) for row in rows]

    @app.post("/api/notifications/{notification_id}/dismiss")
    def notification_dismiss(notification_id: int, session: Session = Depends(get_session)):
        row = session.get(Notification, notification_id)
        if not row:
            raise HTTPException(status_code=404, detail="Notification not found")
        NotificationService(session).dismiss(row)
        session.commit()
        return _notification_dict(row)

    @app.post("/api/notifications/{notification_id}/restore")
    def notification_restore(notification_id: int, session: Session = Depends(get_session)):
        row = session.get(Notification, notification_id)
        if not row:
            raise HTTPException(status_code=404, detail="Notification not found")
        NotificationService(session).restore(row)
        session.commit()
        return _notification_dict(row)

    @app.post("/api/notifications/test")
    def notification_test(session: Session = Depends(get_session)):
        row = NotificationService(session).create_test_notification()
        session.commit()
        return _notification_dict(row)

    @app.get("/api/notifications/settings")
    def notification_settings_get(session: Session = Depends(get_session)):
        settings = NotificationService(session).settings()
        session.commit()
        return _notification_settings_dict(settings)

    @app.put("/api/notifications/settings")
    def notification_settings_put(
        body: NotificationSettingsUpdate, session: Session = Depends(get_session)
    ):
        from semi_intel.notifications.digest import digest_window
        try:
            digest_window(dt.datetime.now(dt.UTC), body.timezone, body.digest_time)
            dt.time.fromisoformat(body.quiet_hours_start)
            dt.time.fromisoformat(body.quiet_hours_end)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        settings = NotificationService(session).settings()
        values = body.model_dump()
        adapter_status = WebhookConfigurationService(session).status()
        if values["external_delivery_enabled"] and not adapter_status["enabled"]:
            raise HTTPException(
                status_code=409,
                detail="Test and explicitly enable the webhook adapter before external delivery.",
            )
        desktop_service = WindowsDesktopDeliveryService(session)
        if (
            values["windows_desktop_notifications_enabled"]
            and not desktop_service.status()["supported"]
        ):
            raise HTTPException(
                status_code=409,
                detail=desktop_service.status()["message"],
            )
        for name, value in values.items():
            if name in {"muted_event_types", "muted_topic_ids"}:
                setattr(settings, name, json.dumps(value))
            elif name == "external_delivery_enabled":
                # A future configured adapter may opt in. Phase 8 never silently
                # pretends an external channel exists.
                settings.external_delivery_enabled = bool(value)
            else:
                setattr(settings, name, value)
        OperationalScheduler(session).settings().active_notification_preset = "custom"
        session.commit()
        return _notification_settings_dict(settings)

    @app.get("/api/notifications/windows-desktop/status")
    def windows_desktop_status(session: Session = Depends(get_session)):
        return WindowsDesktopDeliveryService(session).status()

    @app.post("/api/notifications/windows-desktop/test")
    def windows_desktop_test(session: Session = Depends(get_session)):
        result = WindowsDesktopDeliveryService(session).test()
        if not result.delivered:
            raise HTTPException(status_code=409, detail=result.error or "Desktop notification failed.")
        return {"delivered": True, "external_message_id": result.external_message_id}

    @app.post("/api/notifications/activate")
    def notification_activate(session: Session = Depends(get_session)):
        settings = NotificationService(session).reset_activation()
        session.commit()
        return _notification_settings_dict(settings)

    @app.post("/api/notifications/mute-event/{event_type}")
    def notification_mute_event(
        event_type: NotificationEventType, muted: bool = True,
        session: Session = Depends(get_session),
    ):
        settings = NotificationService(session).set_event_muted(event_type, muted=muted)
        session.commit()
        return _notification_settings_dict(settings)

    @app.post("/api/notifications/mute-topic/{topic_id}")
    def notification_mute_topic(
        topic_id: int, muted: bool = True, session: Session = Depends(get_session)
    ):
        try:
            settings = NotificationService(session).set_topic_muted(topic_id, muted=muted)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        session.commit()
        return _notification_settings_dict(settings)

    @app.post("/api/notifications/digest")
    def notification_digest_generate(
        body: DigestGenerateRequest = DigestGenerateRequest(),
        session: Session = Depends(get_session),
    ):
        service = DigestService(session)
        if body.generate_notifications:
            row, notification_generation = service.generate_manual()
        else:
            row = service.generate(refresh=body.refresh)
            notification_generation = {"created": 0, "updated": 0, "seeded_historical_candidates": 0}
        session.commit()
        result = _digest_dict(row, full=True)
        result["notification_generation"] = notification_generation
        result["delivery"] = None
        if body.deliver:
            result["delivery"] = ExternalDeliveryService(session).deliver_pending()
            result.update(_digest_dict(row, full=True))
        return result

    @app.get("/api/notifications/digests")
    def notification_digests(limit: int = 30, session: Session = Depends(get_session)):
        rows = session.scalars(
            select(NotificationDigest).order_by(NotificationDigest.generated_at.desc())
            .limit(min(limit, 100))
        )
        return [_digest_dict(row) for row in rows]

    @app.get("/api/notifications/digests/current")
    def notification_digest_current(session: Session = Depends(get_session)):
        row = session.scalar(
            select(NotificationDigest).order_by(NotificationDigest.generated_at.desc())
        )
        if not row:
            raise HTTPException(status_code=404, detail="No digest has been generated")
        return _digest_dict(row, full=True)

    @app.get("/api/notifications/digests/{digest_id}")
    def notification_digest_detail(digest_id: int, session: Session = Depends(get_session)):
        row = session.get(NotificationDigest, digest_id)
        if not row:
            raise HTTPException(status_code=404, detail="Digest not found")
        return _digest_dict(row, full=True)

    @app.get("/api/notifications/incidents")
    def notification_incidents(session: Session = Depends(get_session)):
        rows = session.scalars(select(ProviderIncident).order_by(
            ProviderIncident.resolved_at.asc(), ProviderIncident.latest_failure_at.desc()
        ))
        return [{
            "id": row.id, "provider": row.provider, "source_id": row.source_id,
            "opened_at": aware(row.opened_at).isoformat(),
            "latest_failure_at": aware(row.latest_failure_at).isoformat(),
            "resolved_at": aware(row.resolved_at).isoformat() if row.resolved_at else None,
            "consecutive_failures": row.consecutive_failures,
            "latest_error_summary": row.latest_error_summary,
        } for row in rows]

    @app.get("/api/notifications/delivery-status")
    def notification_delivery_status(session: Session = Depends(get_session)):
        service = WebhookConfigurationService(session)
        status = service.status()
        settings = NotificationService(session).settings()
        status.update({
            "in_app_available": True,
            "external_enabled": settings.external_delivery_enabled,
            "external_adapter_available": status["configured"],
            "quiet_hours": {
                "timezone": settings.timezone,
                "start": settings.quiet_hours_start,
                "end": settings.quiet_hours_end,
            },
            "message": (
                "Generic HTTPS webhook is configured."
                if status["configured"] else
                "No external delivery endpoint is configured; local alerts remain available."
            ),
        })
        session.commit()
        return status

    def _automation_status(session: Session) -> dict:
        scheduler = OperationalScheduler(session)
        status = scheduler.status()
        task = WindowsTaskStatusService().status(
            expected_executable=current_executable(), expected_working_directory=Path.cwd()
        )
        effective = effective_automation_state(scheduler.settings(), task)
        status["windows_task"] = task
        status["effective"] = effective
        status["calculated_next_runs"] = dict(status["next_runs"])
        if not effective["healthy"]:
            status["next_runs"] = {key: None for key in status["next_runs"]}
        return status

    @app.get("/api/operations/health")
    def operations_health(session: Session = Depends(get_session)):
        report = HealthService(session).report()
        automation = _automation_status(session)
        report["scheduler"]["effective"] = automation["effective"]
        report["scheduler"]["windows_task"] = automation["windows_task"]
        if automation["enabled"] and not automation["effective"]["healthy"]:
            explanation = automation["effective"]["explanation"]
            if not any(issue["explanation"] == explanation for issue in report["issues"]):
                report["issues"].insert(0, {
                    "state": "degraded", "explanation": explanation,
                    "recommended_action": "Inspect or repair the Windows scheduled task below.",
                })
            report["overall"] = "degraded"
            report["summary"] = "Automation is enabled in settings but is not running reliably."
        session.commit()
        return report

    @app.get("/api/operations/trends")
    def operations_trends(days: int = 30, session: Session = Depends(get_session)):
        try:
            return OperationalTrendService(session).summarize(days)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/api/operations/scheduler")
    def operations_scheduler(session: Session = Depends(get_session)):
        scheduler = OperationalScheduler(session)
        status = _automation_status(session)
        settings = scheduler.settings()
        status["settings"] = {
            key: getattr(settings, key) for key in (
                "scheduler_enabled", "pipeline_interval_minutes", "digest_enabled",
                "digest_time", "backup_enabled", "backup_time", "maintenance_enabled",
                "maintenance_time", "timezone", "maximum_job_duration_minutes",
                "retry_delay_minutes", "maximum_automatic_retries",
                "stale_run_threshold_minutes", "missed_run_warning_minutes",
                "startup_catchup_enabled", "backup_retention_count",
                "backup_retention_days", "active_notification_preset",
            )
        }
        session.commit()
        return status

    @app.put("/api/operations/scheduler")
    def operations_scheduler_update(
        body: SchedulerSettingsUpdate, session: Session = Depends(get_session)
    ):
        scheduler = OperationalScheduler(session)
        settings = scheduler.settings()
        for key, value in body.model_dump().items():
            setattr(settings, key, value)
        try:
            scheduler.status()
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        session.commit()
        return _automation_status(session)

    @app.post("/api/operations/scheduler/{enabled}")
    def operations_scheduler_toggle(enabled: bool, session: Session = Depends(get_session)):
        scheduler = OperationalScheduler(session)
        scheduler.settings().scheduler_enabled = enabled
        session.commit()
        return _automation_status(session)

    @app.get("/api/operations/windows-task")
    def operations_windows_task(session: Session = Depends(get_session)):
        settings = OperationalScheduler(session).settings()
        executable = current_executable()
        command = windows_task_install_command(
            executable, Path.cwd(), interval_minutes=settings.pipeline_interval_minutes
        )
        status = WindowsTaskStatusService().status(
            expected_executable=executable, expected_working_directory=Path.cwd()
        )
        status["can_install"] = bool(getattr(sys, "frozen", False))
        status["install_preview"] = subprocess.list2cmdline(command)
        return status

    @app.post("/api/operations/windows-task/install")
    def operations_windows_task_install(
        body: WindowsTaskInstallRequest, session: Session = Depends(get_session)
    ):
        if not body.confirmed:
            raise HTTPException(status_code=409, detail="Explicit confirmation is required.")
        if not getattr(sys, "frozen", False):
            raise HTTPException(status_code=409, detail="Task installation requires the packaged semintel.exe.")
        settings = OperationalScheduler(session).settings()
        executable = current_executable()
        command = windows_task_install_command(
            executable, Path.cwd(), interval_minutes=settings.pipeline_interval_minutes
        )
        result = execute_task_command(command)
        if result.returncode:
            raise HTTPException(
                status_code=409,
                detail=safe_error(result.stderr or result.stdout) or "Windows Task Scheduler rejected the command.",
            )
        return WindowsTaskStatusService().status(
            expected_executable=executable, expected_working_directory=Path.cwd()
        )

    @app.post("/api/operations/reconcile-stale")
    def operations_reconcile_stale(session: Session = Depends(get_session)):
        reconciled = OperationalScheduler(session).reconcile_stale_runs()
        return {"reconciled_job_ids": reconciled, "count": len(reconciled)}

    @app.post("/api/operations/run/{job_type}")
    def operations_run(job_type: OperationalJobType, session: Session = Depends(get_session)):
        row = OperationalScheduler(session).run_job(
            job_type, trigger=OperationalTriggerType.MANUAL_GUI
        )
        return OperationalScheduler.job_dict(row)

    @app.get("/api/operations/jobs")
    def operations_jobs(limit: int = 50, session: Session = Depends(get_session)):
        rows = session.scalars(select(OperationalJobRun).order_by(
            OperationalJobRun.started_at.desc()
        ).limit(min(max(limit, 1), 200)))
        return [OperationalScheduler.job_dict(row) for row in rows]

    @app.get("/api/operations/jobs/{job_id}")
    def operations_job(job_id: int, session: Session = Depends(get_session)):
        row = session.get(OperationalJobRun, job_id)
        if not row:
            raise HTTPException(status_code=404, detail="Operational job not found")
        return OperationalScheduler.job_dict(row)

    @app.post("/api/operations/jobs/{job_id}/retry")
    def operations_job_retry(job_id: int, session: Session = Depends(get_session)):
        try:
            row = OperationalScheduler(session).retry(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return OperationalScheduler.job_dict(row)

    @app.get("/api/notifications/presets")
    def notification_presets(session: Session = Depends(get_session)):
        service = NotificationQualityService(session)
        return {
            "presets": service.presets(),
            "active": OperationalScheduler(session).settings().active_notification_preset,
        }

    @app.get("/api/notifications/presets/{name}/preview")
    def notification_preset_preview(name: str, session: Session = Depends(get_session)):
        try:
            return NotificationQualityService(session).preview_preset(name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/notifications/presets/{name}/apply")
    def notification_preset_apply(name: str, session: Session = Depends(get_session)):
        try:
            return NotificationQualityService(session).apply_preset(name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/notifications/{notification_id}/feedback")
    def notification_feedback(
        notification_id: int, body: NotificationFeedbackRequest,
        session: Session = Depends(get_session),
    ):
        try:
            row = NotificationQualityService(session).feedback(
                notification_id, body.rating, reason=body.reason, note=body.note
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {
            "notification_id": row.notification_id, "rating": row.rating.value,
            "reason": row.reason, "note": row.note,
            "updated_at": aware(row.updated_at).isoformat(),
        }

    @app.get("/api/notifications/feedback-summary")
    def notification_feedback_summary(session: Session = Depends(get_session)):
        return NotificationQualityService(session).feedback_summary()

    def _saved_view_dict(row: SavedNotificationView, session: Session) -> dict:
        return {
            "id": row.id, "name": row.name, "state_filter": row.state_filter,
            "event_types": json.loads(row.event_types), "severities": json.loads(row.severities),
            "topic_ids": json.loads(row.topic_ids),
            "relation_filters": json.loads(row.relation_filters),
            "date_window_days": row.date_window_days, "search_text": row.search_text,
            "sort_order": row.sort_order,
            "description": SavedViewService(session).describe(row),
        }

    @app.get("/api/notifications/saved-views")
    def saved_views_list(session: Session = Depends(get_session)):
        return [_saved_view_dict(row, session) for row in SavedViewService(session).list()]

    @app.get("/api/notifications/saved-views/{view_id}")
    def saved_views_get(view_id: int, session: Session = Depends(get_session)):
        try:
            row = SavedViewService(session).get(view_id)
        except SavedViewNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return _saved_view_dict(row, session)

    @app.post("/api/notifications/saved-views", status_code=201)
    def saved_views_create(
        body: SavedNotificationViewRequest, session: Session = Depends(get_session)
    ):
        try:
            row = SavedViewService(session).save(**body.model_dump())
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return _saved_view_dict(row, session)

    @app.put("/api/notifications/saved-views/{view_id}")
    def saved_views_update(
        view_id: int, body: SavedNotificationViewRequest,
        session: Session = Depends(get_session),
    ):
        try:
            row = SavedViewService(session).save(
                view_id=view_id, **body.model_dump(exclude_unset=True)
            )
        except SavedViewNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return _saved_view_dict(row, session)

    @app.post("/api/notifications/saved-views/{view_id}/duplicate", status_code=201)
    def saved_views_duplicate(view_id: int, session: Session = Depends(get_session)):
        try:
            row = SavedViewService(session).duplicate(view_id)
        except SavedViewNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return _saved_view_dict(row, session)

    @app.delete("/api/notifications/saved-views/{view_id}", status_code=204)
    def saved_views_delete(view_id: int, session: Session = Depends(get_session)):
        try:
            SavedViewService(session).delete(view_id)
        except SavedViewNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/api/notifications/saved-views/{view_id}/apply")
    def saved_views_apply(
        view_id: int, limit: int = 100, session: Session = Depends(get_session),
    ):
        try:
            view = SavedViewService(session).get(view_id)
        except SavedViewNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        filters = NotificationQueryFilters(
            state=view.state_filter,
            event_types=json.loads(view.event_types),
            severities=json.loads(view.severities),
            topic_ids=json.loads(view.topic_ids),
            date_window_days=view.date_window_days,
            search_text=view.search_text,
            sort_order=view.sort_order,
            limit=limit,
        )
        try:
            rows = NotificationQueryService(session).run(filters)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {
            "view": _saved_view_dict(view, session),
            "notifications": [_notification_dict(row) for row in rows],
        }

    @app.get("/api/notifications/delivery-preview")
    def notification_delivery_preview(session: Session = Depends(get_session)):
        return WebhookConfigurationService(session).preview()

    @app.post("/api/notifications/delivery-test")
    def notification_delivery_test(session: Session = Depends(get_session)):
        result = WebhookConfigurationService(session).test()
        return {
            "delivered": result.delivered, "error": result.error,
            "external_message_id": result.external_message_id,
        }

    @app.post("/api/notifications/delivery-enable")
    def notification_delivery_enable(
        body: AdapterEnableRequest, session: Session = Depends(get_session)
    ):
        try:
            return WebhookConfigurationService(session).set_enabled(
                body.enabled, allow_untested=body.allow_untested
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/api/notifications/delivery-retry")
    def notification_delivery_retry(session: Session = Depends(get_session)):
        return ExternalDeliveryService(session).deliver_pending()

    @app.get("/api/operations/backups")
    def operations_backups(session: Session = Depends(get_session)):
        return [{
            "id": row.id, "filename": row.filename, "status": row.status.value,
            "created_at": aware(row.created_at).isoformat(), "size_bytes": row.size_bytes,
            "application_version": row.application_version,
            "alembic_revision": row.alembic_revision, "sha256": row.sha256,
        } for row in BackupService(session).list()]

    @app.post("/api/operations/backups", status_code=201)
    def operations_backup_create(session: Session = Depends(get_session)):
        try:
            row = BackupService(session).create()
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"id": row.id, "filename": row.filename, "status": row.status.value}

    @app.post("/api/operations/backups/verify")
    def operations_backup_verify(
        body: BackupVerifyRequest, session: Session = Depends(get_session)
    ):
        try:
            return BackupService(session).verify(Path(body.path))
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post("/api/operations/backups/prune")
    def operations_backup_prune(
        body: BackupPruneRequest, session: Session = Depends(get_session)
    ):
        try:
            paths = BackupService(session).prune(dry_run=not body.apply)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {"applied": body.apply, "files": [path.name for path in paths]}

    @app.post("/api/operations/diagnostics", status_code=201)
    def operations_diagnostics(session: Session = Depends(get_session)):
        from semi_intel.paths import get_diagnostics_dir
        result = DiagnosticsService(session).create(get_diagnostics_dir())
        return {"filename": Path(result["path"]).name, "sha256": result["sha256"]}

    # Keep the dynamic route last so fixed paths such as /settings, /digests
    # and /incidents are never interpreted as integer notification IDs.
    @app.get("/api/notifications/{notification_id}")
    def notification_detail(notification_id: int, session: Session = Depends(get_session)):
        row = session.get(Notification, notification_id)
        if not row:
            raise HTTPException(status_code=404, detail="Notification not found")
        return _notification_dict(row)

    return app
