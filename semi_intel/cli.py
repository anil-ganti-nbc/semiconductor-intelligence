"""CLI entry point.

Every command opens its own session against SEMI_INTEL_DB_URL (default: a
local sqlite file). This is deliberate -- each CLI invocation is a short-lived
process, so there is no long-running connection to manage yet. When this
platform grows a scheduled ingestion process, that process will manage its
own session lifecycle separately from the CLI.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import List, Optional

import typer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semi_intel.claim_engine.entity_matcher import find_entity_mentions
from semi_intel.claim_engine.suggestion_service import SuggestionService
from semi_intel.contradiction_engine.memory_rules import check_memory_configuration
from semi_intel.contradiction_engine.service import MemorySpecClaimService
from semi_intel.db import get_engine, get_sessionmaker
from semi_intel.db import init_db as _init_db
from semi_intel.domain.enums import (
    ClaimStatus,
    EntityType,
    EvidenceStance,
    RelationType,
    SignalCandidateState,
    NotificationEventType,
    NotificationFeedbackRating,
    NotificationSeverity,
    SourceType,
    SuggestionStatus,
)
from semi_intel.domain.models import (
    DiscoveryRun,
    EditorialStory,
    Entity,
    Evidence,
    ProviderRun,
    Relationship,
    SignalCandidate,
    Notification,
    NotificationDigest,
    ProviderIncident,
    Source,
)
from semi_intel.discovery.service import DiscoveryService
from semi_intel.editorial.service import EditorialDiscoveryService
from semi_intel.ingestion.hashing import hash_content
from semi_intel.ingestion.plugins.pci_ids_plugin import DEFAULT_PCI_IDS_URL, PciIdsSourcePlugin
from semi_intel.ingestion.plugins.rss_plugin import RSSSourcePlugin
from semi_intel.ingestion.service import IngestionService
from semi_intel.source_intelligence.service import SourceIntelligenceService
from semi_intel.graph.queries import find_by_relation, related_entities
from semi_intel.story_scoring.service import StoryScoringService
from semi_intel.pipeline.service import PipelineService
from semi_intel.repository.repositories import (
    ClaimRepository,
    EntityRepository,
    EvidenceRepository,
    RelationshipRepository,
    SourceRepository,
    SuggestionRepository,
)
from semi_intel.source_identity import find_source_by_feed_url
from semi_intel.signals.analysis import analyze_unprocessed, reprocess_stale_items
from semi_intel.signals.candidate_state import dismiss as dismiss_candidate
from semi_intel.signals.candidate_state import mark_seen as mark_candidate_seen
from semi_intel.signals.candidate_state import mark_unseen as mark_candidate_unseen
from semi_intel.signals.clustering import cluster_unclustered_items
from semi_intel.signals.collection import CollectionService, get_collection_settings
from semi_intel.signals.promotion import (
    PromotionBlocked,
    check_automatic_eligibility,
    get_promotion_settings,
    merge_candidate_into_story,
    promote_candidate,
    run_automatic_promotion,
)
from semi_intel.signals.scoring import rescore_active_candidates
from semi_intel.legacy_import import IMPORT_CATEGORIES, LegacyRadarImporter
from semi_intel.notifications.service import NotificationService, aware
from semi_intel.notifications.digest import DigestService
from semi_intel.notifications.delivery import DeliveryService, InAppAdapter
from semi_intel.operations.quality import NotificationQualityService, SavedViewService
from semi_intel.operations.webhook import ExternalDeliveryService, WebhookConfigurationService

app = typer.Typer(help="Semiconductor Intelligence Platform -- claims, evidence, and the graph behind them.")
entity_app = typer.Typer(help="Manage knowledge-graph entities (products, companies, nodes, ...).")
source_app = typer.Typer(help="Manage sources.")
evidence_app = typer.Typer(help="Manage evidence.")
claim_app = typer.Typer(help="Manage claims.")
ingest_app = typer.Typer(help="Run source plugins to pull in new evidence.")
suggest_app = typer.Typer(help="Rules-based suggestions linking evidence to claims -- always human-reviewed.")
check_app = typer.Typer(help="Rules-based engineering consistency checks -- calculator utilities, no persistence.")
graph_app = typer.Typer(help="Knowledge-graph queries over entities and relationships.")
story_app = typer.Typer(help="Story scoring -- which open claims deserve investigation right now.")
web_app = typer.Typer(help="Web dashboard -- browse and edit (requires the 'web' extra).")
pipeline_app = typer.Typer(help="Scheduled pipeline -- ingest every registered source, then run suggestions.")
editorial_app = typer.Typer(help="Automated editorial inbox maintenance and backfill.")
discovery_app = typer.Typer(help="Bounded targeted searches around already-interesting stories.")
db_app = typer.Typer(help="Schema migrations (Alembic), callable without a separate `alembic` install.")
radar_app = typer.Typer(
    help="Signal Radar-absorbed sensory pipeline: provider collection, signal candidates, promotion."
)
notification_app = typer.Typer(
    help="Alerts, provider incidents, daily digests and notification preferences."
)

app.add_typer(entity_app, name="entity")
app.add_typer(source_app, name="source")
app.add_typer(evidence_app, name="evidence")
app.add_typer(claim_app, name="claim")
app.add_typer(ingest_app, name="ingest")
app.add_typer(suggest_app, name="suggest")
app.add_typer(check_app, name="check")
app.add_typer(graph_app, name="graph")
app.add_typer(story_app, name="story")
app.add_typer(web_app, name="web")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(editorial_app, name="editorial")
app.add_typer(discovery_app, name="discovery")
app.add_typer(db_app, name="db")
app.add_typer(radar_app, name="radar")
app.add_typer(notification_app, name="notifications")


def _session() -> Session:
    engine = get_engine()
    _init_db(engine)
    return get_sessionmaker(engine)()


def _project_root() -> Path:
    """Where alembic.ini and migrations/ live. When running from source
    that's the repo root (two levels up from this file); when frozen into a
    standalone executable (PyInstaller), it's the bundle's extracted temp
    directory, exposed as sys._MEIPASS -- see packaging/semi_intel.spec,
    which adds alembic.ini and migrations/ as data files at the bundle
    root so this resolves the same way either way.

    SEMINTEL_PROJECT_ROOT is a third, portability-only override for a real
    `pip install .` (non-editable, non-frozen) -- e.g. inside a container.
    That kind of install moves this *package* into site-packages while
    alembic.ini/migrations/ (project files, not part of the package) stay
    wherever they were copied, so `parent.parent` no longer points at them.
    Defaults to the prior parent.parent behavior when unset, so source/
    editable-checkout use (the only way this ran before) is unaffected."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    override = os.environ.get("SEMINTEL_PROJECT_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent


def _alembic_config():
    """Builds an Alembic Config in-process so `semi-intel db upgrade/downgrade`
    works without a separate `alembic` executable on PATH -- the one thing
    that would otherwise force a standalone-exe user to also have Python +
    pip install alembic separately just to manage the schema. `alembic.ini`'s
    `%(here)s` token resolves relative to the ini file itself, so pointing
    Config at the bundled/repo-root alembic.ini is enough; it finds
    migrations/ on its own."""
    from alembic.config import Config

    ini_path = _project_root() / "alembic.ini"
    if not ini_path.exists():
        typer.secho(f"Could not find alembic.ini at {ini_path}.", fg=typer.colors.RED)
        raise typer.Exit(1)
    # alembic.config.Config's `stdout` parameter defaults to whatever
    # sys.stdout was bound to the moment the alembic.config module was
    # first imported into this process (a plain function-default-argument,
    # evaluated once) -- not the live sys.stdout at each call. In a
    # long-lived process that runs more than one `db` command (or, on the
    # test side, Typer's CliRunner, which swaps and closes sys.stdout once
    # per invoke), that stale reference can point at an already-closed
    # stream. Passing stdout=sys.stdout explicitly forces it to bind
    # whatever is live right now, every time.
    cfg = Config(str(ini_path), stdout=sys.stdout)
    # migrations/env.py checks this and skips logging.config.fileConfig()
    # when it's False -- see the comment there for why reconfiguring global
    # logging on every in-process `db` command is both unnecessary and, in
    # a long-lived process that runs more than one `db` command, unsafe.
    cfg.attributes["configure_logging"] = False
    return cfg


def upgrade_or_stamp_to_head() -> str:
    """Runs `alembic upgrade head`. If that fails because the tables
    already exist -- a database that got created via plain
    Base.metadata.create_all() (which is what `_session()` here and
    `get_session()`/`create_app()` in semi_intel/web/app.py all do the
    first time ANY command/request opens one, lazily, before install/
    update/the dashboard ever ran Alembic) rather than through Alembic
    itself -- falls back to stamping the database at head instead of
    trying to re-create tables that are already there.

    This fallback is safe specifically because create_all() and `alembic
    upgrade head` are verified to produce byte-identical schemas (see
    tests/test_migrations.py's test_alembic_upgrade_matches_create_all) --
    so "tables already exist" here reliably means "already at head", not
    "some other, incompatible structure." Without this, a completely
    ordinary sequence like running `semintel status` before `semintel
    install` would leave the database permanently unable to self-heal via
    `semintel update`, failing instead with a raw SQL error.

    Shared by semintel's `install`/`update` commands and the web
    dashboard's startup (`create_app()`) so every supported entry point
    reconciles the same way instead of the dashboard silently leaving
    `alembic_version` stale (or, for a future non-additive migration,
    silently masking it) by calling create_all() directly.

    Returns "upgraded" or "stamped" to describe what actually happened.
    """
    from alembic import command

    cfg = _alembic_config()
    try:
        command.upgrade(cfg, "head")
        return "upgraded"
    except Exception as exc:
        if "already exists" in str(exc).lower():
            command.stamp(cfg, "head")
            return "stamped"
        raise


@app.command("init-db")
def init_db_cmd() -> None:
    """Create all tables if they don't exist yet. Fine for a throwaway/dev
    database -- it only ever adds missing tables, it cannot alter an
    existing one. Once a database has real data in it, use `db upgrade`
    instead; don't mix the two against the same database."""
    engine = get_engine()
    _init_db(engine)
    typer.echo("Database initialized.")


# --- version / identity / health (cloud-migration runtime bridge) ---------
#
# Additive only -- every command above and below this block keeps its
# existing behavior and output format. `semintel health` (semi_intel/
# operator.py) is a separate, richer operator-facing report and is
# untouched; these three are the thin, container-oriented contract used by
# `docker build`'s HEALTHCHECK and by anyone driving this image externally.


@app.command("version")
def version_cmd() -> None:
    """Print the application version."""
    from semi_intel import __version__

    typer.echo(f"semi-intel {__version__}")


@app.command("identity")
def identity_cmd() -> None:
    """Print the runtime identity as JSON. `release_channel` reflects
    SEMINTEL_RELEASE_CHANNEL (default: "soaking") -- never hard-coded."""
    from semi_intel.runtime_bridge import as_jsonable, get_identity

    typer.echo(json.dumps(as_jsonable(get_identity()), indent=2, default=str))


@app.command("health")
def health_cmd() -> None:
    """Print truthful runtime health as JSON. Exits non-zero only when
    failed. Does not run collectors or migrations; only performs the same
    lazy create-tables-if-missing every other command already does against
    a fresh database."""
    from semi_intel.runtime_bridge import as_jsonable, get_health

    payload = as_jsonable(get_health())
    typer.echo(json.dumps(payload, indent=2, default=str))
    state = payload.get("operational_state")
    if state == "failed":
        raise typer.Exit(code=1)


# --- db (migrations) -------------------------------------------------------


@db_app.command("upgrade")
def db_upgrade(revision: str = typer.Argument("head")) -> None:
    """Upgrade the schema to `revision` (default: the latest). Reads
    SEMI_INTEL_DB_URL the same way every other command does."""
    from alembic import command

    command.upgrade(_alembic_config(), revision)
    typer.echo(f"Upgraded to {revision}.")


@db_app.command("downgrade")
def db_downgrade(revision: str = typer.Argument("base")) -> None:
    """Downgrade the schema to `revision` (default: base -- drops
    everything Alembic created). Dev/test use mainly; think twice before
    downgrading a database with real data."""
    from alembic import command

    command.downgrade(_alembic_config(), revision)
    typer.echo(f"Downgraded to {revision}.")


@db_app.command("current")
def db_current() -> None:
    """Show the schema revision the current database is stamped at."""
    from alembic import command

    command.current(_alembic_config(), verbose=True)


@db_app.command("stamp")
def db_stamp(revision: str = typer.Argument("head")) -> None:
    """Mark a database as being at `revision` without running any
    migration -- the escape hatch for a database that was created with
    `init-db` and needs to switch to Alembic-managed migrations."""
    from alembic import command

    command.stamp(_alembic_config(), revision)
    typer.echo(f"Stamped {revision}.")


# --- entity ---------------------------------------------------------------


@entity_app.command("add")
def entity_add(
    name: str,
    type: EntityType = typer.Option(..., "--type", case_sensitive=False),
    alias: List[str] = typer.Option([], "--alias", help="Repeatable."),
    attr: List[str] = typer.Option([], "--attr", help="key=value, repeatable."),
) -> None:
    session = _session()
    repo = EntityRepository(session)
    if repo.find_by_name(name):
        typer.secho(f"Entity '{name}' already exists.", fg=typer.colors.RED)
        raise typer.Exit(1)
    attrs = dict(a.split("=", 1) for a in attr) if attr else {}
    entity = Entity(type=type, name=name, aliases=json.dumps(alias), attributes=json.dumps(attrs))
    repo.add(entity)
    session.commit()
    typer.echo(f"Created entity #{entity.id}: {entity.name} ({entity.type.value})")


@entity_app.command("list")
def entity_list(type: Optional[EntityType] = typer.Option(None, "--type")) -> None:
    session = _session()
    entities = EntityRepository(session).list()
    if type:
        entities = [e for e in entities if e.type == type]
    for e in entities:
        typer.echo(f"#{e.id}\t{e.type.value}\t{e.name}")


@entity_app.command("show")
def entity_show(entity_id: int) -> None:
    session = _session()
    repo = EntityRepository(session)
    rel_repo = RelationshipRepository(session)
    e = repo.get(entity_id)
    if not e:
        typer.secho("Not found.", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo(f"#{e.id} {e.name} ({e.type.value})")
    typer.echo(f"aliases: {e.aliases}")
    typer.echo(f"attributes: {e.attributes}")
    rels = rel_repo.for_entity(e.id)
    if rels:
        typer.echo("relationships:")
        for r in rels:
            other_id = r.to_entity_id if r.from_entity_id == e.id else r.from_entity_id
            other = repo.get(other_id)
            arrow = "->" if r.from_entity_id == e.id else "<-"
            typer.echo(f"  {arrow} {r.relation_type.value} {arrow} {other.name if other else '?'}")


@entity_app.command("relate")
def entity_relate(
    from_id: int,
    to_id: int,
    type: RelationType = typer.Option(..., "--type", case_sensitive=False),
    note: Optional[str] = typer.Option(None, "--note"),
) -> None:
    session = _session()
    entity_repo = EntityRepository(session)
    if not entity_repo.get(from_id) or not entity_repo.get(to_id):
        typer.secho("Both entities must exist.", fg=typer.colors.RED)
        raise typer.Exit(1)
    session.add(Relationship(from_entity_id=from_id, to_entity_id=to_id, relation_type=type, note=note))
    session.commit()
    typer.echo(f"Related #{from_id} --{type.value}--> #{to_id}")


# --- source ----------------------------------------------------------------


@source_app.command("add")
def source_add(
    name: str,
    type: SourceType = typer.Option(..., "--type", case_sensitive=False),
    url: Optional[str] = typer.Option(None, "--url"),
    description: Optional[str] = typer.Option(None, "--description"),
    trust_weight: float = typer.Option(0.5, "--trust-weight", min=0.0, max=1.0),
) -> None:
    session = _session()
    repo = SourceRepository(session)
    if repo.find_by_name(name):
        typer.secho(f"Source '{name}' already exists.", fg=typer.colors.RED)
        raise typer.Exit(1)
    if type == SourceType.RSS:
        existing_feed = find_source_by_feed_url(session, url)
        if existing_feed:
            typer.secho(
                f"This feed is already registered as '{existing_feed.name}'.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)
    source = Source(name=name, type=type, url=url, description=description, trust_weight=trust_weight)
    repo.add(source)
    session.commit()
    typer.echo(f"Created source #{source.id}: {source.name} (trust={source.trust_weight})")


@source_app.command("list")
def source_list() -> None:
    session = _session()
    for s in SourceRepository(session).list():
        typer.echo(f"#{s.id}\t{s.type.value}\t{s.name}\ttrust={s.trust_weight}")


@source_app.command("stats")
def source_stats(source_id: int) -> None:
    """Accuracy track record for a source, derived from resolved claims
    only. Needs `claim resolve` to have been used on claims this source's
    evidence was linked to -- with no resolved claims yet it just reports
    "no track record yet" rather than a misleading number."""
    session = _session()
    report = SourceIntelligenceService(session).report_for(source_id)
    if report is None:
        typer.secho("Not found.", fg=typer.colors.RED)
        raise typer.Exit(1)
    acc = report.overall.accuracy
    typer.echo(f"#{report.source_id} {report.source_name}")
    if acc is None:
        typer.echo(f"Overall: no track record yet ({report.open_claim_count} still-open claim(s) linked)")
    else:
        typer.echo(f"Overall: {report.overall.correct}/{report.overall.total} correct ({acc:.0%})")
    if report.by_company:
        typer.echo("By company:")
        for company, bucket in sorted(report.by_company.items()):
            bacc = bucket.accuracy
            suffix = f" ({bacc:.0%})" if bacc is not None else ""
            typer.echo(f"  {company}: {bucket.correct}/{bucket.total}{suffix}")
    typer.echo(f"weakens (excluded from accuracy): {report.weakens_count}")
    typer.echo(f"retracted claims: {report.retracted_claim_count}")


@source_app.command("rank")
def source_rank() -> None:
    """All sources ranked by overall accuracy. Sources with no resolved
    track record yet sort to the bottom rather than being hidden."""
    session = _session()
    reports = SourceIntelligenceService(session).report_all()

    def sort_key(r):
        acc = r.overall.accuracy
        return (acc is None, -(acc or 0.0), -r.overall.total)

    for r in sorted(reports, key=sort_key):
        acc = r.overall.accuracy
        summary = f"{acc:.0%} ({r.overall.correct}/{r.overall.total})" if acc is not None else "no track record yet"
        typer.echo(f"#{r.source_id}\t{r.source_name}\t{summary}")



# --- evidence ----------------------------------------------------------------


@evidence_app.command("add")
def evidence_add(
    source_id: int,
    title: str = typer.Option(..., "--title"),
    content: str = typer.Option(..., "--content"),
    entity_id: Optional[int] = typer.Option(None, "--entity-id"),
    url: Optional[str] = typer.Option(None, "--url"),
    observed_at: Optional[str] = typer.Option(None, "--observed-at", help="ISO date/time, e.g. 2026-07-18"),
) -> None:
    session = _session()
    src_repo = SourceRepository(session)
    ev_repo = EvidenceRepository(session)
    if not src_repo.get(source_id):
        typer.secho(f"Source #{source_id} does not exist.", fg=typer.colors.RED)
        raise typer.Exit(1)
    content_hash = hash_content(content)
    if ev_repo.find_by_hash(content_hash):
        typer.secho("Duplicate evidence (identical content already captured).", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    observed = dt.datetime.fromisoformat(observed_at) if observed_at else None
    evidence = Evidence(
        source_id=source_id,
        entity_id=entity_id,
        title=title,
        raw_content=content,
        content_hash=content_hash,
        url=url,
        observed_at=observed,
    )
    ev_repo.add(evidence)
    session.commit()
    typer.echo(f"Captured evidence #{evidence.id}: {evidence.title}")


@evidence_app.command("list")
def evidence_list(
    source_id: Optional[int] = typer.Option(None, "--source-id"),
    entity_id: Optional[int] = typer.Option(None, "--entity-id"),
) -> None:
    session = _session()
    items = EvidenceRepository(session).list()
    if source_id is not None:
        items = [e for e in items if e.source_id == source_id]
    if entity_id is not None:
        items = [e for e in items if e.entity_id == entity_id]
    for e in items:
        typer.echo(f"#{e.id}\tsrc={e.source_id}\tentity={e.entity_id}\t{e.title}")


@evidence_app.command("entities")
def evidence_entities(evidence_id: int) -> None:
    """Show which known entities are mentioned in a piece of evidence's text.

    Read-only and side-effect free -- this is the same matcher the claim
    engine uses internally, exposed directly so you can see why a suggestion
    did or didn't get generated."""
    session = _session()
    ev = EvidenceRepository(session).get(evidence_id)
    if not ev:
        typer.secho("Not found.", fg=typer.colors.RED)
        raise typer.Exit(1)
    entities = EntityRepository(session).list()
    text = f"{ev.title}\n{ev.raw_content}"
    mentions = find_entity_mentions(text, entities)
    if not mentions:
        typer.echo("No known entities detected.")
        return
    for m in mentions:
        typer.echo(f'#{m.entity_id}\t{m.entity_name}\t(matched: "{m.matched_term}")')


# --- claim ----------------------------------------------------------------


@claim_app.command("create")
def claim_create(
    statement: str,
    subject_entity_id: Optional[int] = typer.Option(None, "--subject-entity-id"),
) -> None:
    session = _session()
    claim = ClaimRepository(session).create(statement, subject_entity_id)
    session.commit()
    typer.echo(f"Created claim #{claim.id} (confidence={claim.confidence})")


@claim_app.command("list")
def claim_list(status: Optional[ClaimStatus] = typer.Option(None, "--status")) -> None:
    session = _session()
    claims = ClaimRepository(session).list()
    if status:
        claims = [c for c in claims if c.status == status]
    for c in claims:
        typer.echo(f"#{c.id}\t[{c.status.value}]\tconf={c.confidence}\t{c.statement}")


@claim_app.command("show")
def claim_show(claim_id: int) -> None:
    session = _session()
    repo = ClaimRepository(session)
    claim = repo.get(claim_id)
    if not claim:
        typer.secho("Not found.", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo(f"#{claim.id} [{claim.status.value}] confidence={claim.confidence}")
    typer.echo(claim.statement)
    typer.echo("evidence:")
    ev_repo = EvidenceRepository(session)
    for link in repo.links_for(claim):
        ev = ev_repo.get(link.evidence_id)
        typer.echo(f"  [{link.stance.value}] evidence #{ev.id}: {ev.title}")


@claim_app.command("link-evidence")
def claim_link_evidence(
    claim_id: int,
    evidence_id: int,
    stance: EvidenceStance = typer.Option(..., "--stance", case_sensitive=False),
    note: Optional[str] = typer.Option(None, "--note"),
) -> None:
    session = _session()
    repo = ClaimRepository(session)
    ev_repo = EvidenceRepository(session)
    claim = repo.get(claim_id)
    evidence = ev_repo.get(evidence_id)
    if not claim or not evidence:
        typer.secho("Claim or evidence not found.", fg=typer.colors.RED)
        raise typer.Exit(1)
    repo.link_evidence(claim, evidence, stance, note)
    session.commit()
    typer.echo(
        f"Linked evidence #{evidence_id} to claim #{claim_id} as {stance.value}. "
        f"New confidence: {claim.confidence}"
    )


@claim_app.command("resolve")
def claim_resolve(
    claim_id: int,
    status: ClaimStatus = typer.Option(..., "--status", case_sensitive=False),
    note: Optional[str] = typer.Option(None, "--note"),
) -> None:
    session = _session()
    repo = ClaimRepository(session)
    claim = repo.get(claim_id)
    if not claim:
        typer.secho("Not found.", fg=typer.colors.RED)
        raise typer.Exit(1)
    repo.resolve(claim, status, note)
    session.commit()
    typer.echo(f"Claim #{claim_id} resolved as {status.value}.")


@claim_app.command("timeline")
def claim_timeline(claim_id: int) -> None:
    session = _session()
    repo = ClaimRepository(session)
    claim = repo.get(claim_id)
    if not claim:
        typer.secho("Not found.", fg=typer.colors.RED)
        raise typer.Exit(1)
    for event in repo.events_for(claim):
        typer.echo(
            f"{event.created_at.isoformat()}\t{event.event_type.value}\t"
            f"conf={event.confidence_after}\t{event.note or ''}"
        )


@claim_app.command("create-memory-spec")
def claim_create_memory_spec(
    statement: str,
    bus_width: int = typer.Option(..., "--bus-width", help="Bus width in bits, e.g. 384."),
    chip_density_gbit: float = typer.Option(..., "--chip-density-gbit", help="Per-chip density in Gbit, e.g. 16."),
    total_gb: float = typer.Option(..., "--total-gb", help="Claimed total VRAM in GB, e.g. 16."),
    subject_entity_id: Optional[int] = typer.Option(None, "--subject-entity-id"),
    memory_type: Optional[str] = typer.Option(None, "--memory-type", help="Informational only, e.g. GDDR6X."),
) -> None:
    """Create a claim with a structured memory configuration and immediately
    check it against GDDR packaging constraints. The check is recorded in
    the claim's timeline either way -- it never blocks creation or changes
    status/confidence on its own."""
    session = _session()
    result = MemorySpecClaimService(session).create(
        statement=statement,
        bus_width_bits=bus_width,
        chip_density_gbit=chip_density_gbit,
        claimed_total_gb=total_gb,
        subject_entity_id=subject_entity_id,
        memory_type=memory_type,
    )
    session.commit()
    typer.echo(f"Created claim #{result.claim.id} (confidence={result.claim.confidence})")
    if result.check.is_consistent:
        typer.secho(f"[CONSISTENT] {result.check.explanation}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"[CONTRADICTION] {result.check.explanation}", fg=typer.colors.RED)


@claim_app.command("memory-spec")
def claim_memory_spec(claim_id: int) -> None:
    """Show a claim's structured memory spec and re-run the consistency check."""
    session = _session()
    result = MemorySpecClaimService(session).recheck(claim_id)
    if result is None:
        typer.secho("No memory spec found for that claim.", fg=typer.colors.RED)
        raise typer.Exit(1)
    spec = result.spec
    typer.echo(
        f"claim #{claim_id}: bus={spec.bus_width_bits}-bit, "
        f"chip={spec.chip_density_gbit:g} Gbit, claimed_total={spec.claimed_total_gb:g} GB"
        + (f", type={spec.memory_type}" if spec.memory_type else "")
    )
    if result.check.is_consistent:
        typer.secho(f"[CONSISTENT] {result.check.explanation}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"[CONTRADICTION] {result.check.explanation}", fg=typer.colors.RED)



# --- ingest ----------------------------------------------------------------


@ingest_app.command("rss")
def ingest_rss(
    source_name: str,
    feed_url: str,
    trust_weight: float = typer.Option(0.5, "--trust-weight", min=0.0, max=1.0),
) -> None:
    """Fetch an RSS/Atom feed and capture new entries as evidence.

    The source is created on first use (named `source_name`) and reused on
    every subsequent run. Re-running is always safe -- unchanged items are
    skipped by content hash, not re-inserted.
    """
    session = _session()
    plugin = RSSSourcePlugin(name=source_name, feed_url=feed_url, default_trust_weight=trust_weight)
    result = IngestionService(session).run(plugin)
    typer.echo(str(result))
    for msg in result.error_messages:
        typer.secho(f"  error: {msg}", fg=typer.colors.RED)


@ingest_app.command("pci-ids")
def ingest_pci_ids(
    source_name: str = typer.Option("PCI ID Repository", "--source-name"),
    url: str = typer.Option(DEFAULT_PCI_IDS_URL, "--url"),
    trust_weight: float = typer.Option(0.9, "--trust-weight", min=0.0, max=1.0),
) -> None:
    """Fetch the pci.ids database and capture newly-registered vendor/device
    pairs as evidence. A new device ID showing up is often the earliest
    public trace of unannounced silicon."""
    session = _session()
    plugin = PciIdsSourcePlugin(name=source_name, url=url, default_trust_weight=trust_weight)
    result = IngestionService(session).run(plugin)
    typer.echo(str(result))
    for msg in result.error_messages:
        typer.secho(f"  error: {msg}", fg=typer.colors.RED)


# --- suggest ----------------------------------------------------------------


@suggest_app.command("run")
def suggest_run(
    evidence_id: List[int] = typer.Option(
        [], "--evidence-id", help="Repeatable; scan specific evidence only. Default: scan all evidence."
    ),
) -> None:
    """Scan evidence against every open claim and propose links. Never writes
    a ClaimEvidenceLink -- only pending suggestions for you to review with
    `suggest list` / `suggest accept` / `suggest reject`."""
    session = _session()
    result = SuggestionService(session).run(evidence_ids=list(evidence_id) or None)
    typer.echo(
        f"Scanned {result.evidence_scanned} evidence item(s): "
        f"{result.suggestions_created} new suggestion(s), "
        f"{result.skipped_existing_pairs} pair(s) already suggested."
    )


@suggest_app.command("list")
def suggest_list(status: Optional[SuggestionStatus] = typer.Option(None, "--status", case_sensitive=False)) -> None:
    session = _session()
    for s in SuggestionRepository(session).list_by_status(status):
        typer.echo(f"#{s.id}\t[{s.status.value}]\tscore={s.score}\tevidence=#{s.evidence_id}\tclaim=#{s.claim_id}")


@suggest_app.command("show")
def suggest_show(suggestion_id: int) -> None:
    session = _session()
    s = SuggestionRepository(session).get(suggestion_id)
    if not s:
        typer.secho("Not found.", fg=typer.colors.RED)
        raise typer.Exit(1)
    evidence = EvidenceRepository(session).get(s.evidence_id)
    claim = ClaimRepository(session).get(s.claim_id)
    typer.echo(f"#{s.id} [{s.status.value}] score={s.score}")
    typer.echo(f"evidence #{evidence.id}: {evidence.title}")
    typer.echo(f"  {evidence.raw_content[:300]}")
    typer.echo(f"claim #{claim.id}: {claim.statement}")
    reasons = json.loads(s.reasons or "[]")
    if reasons:
        typer.echo("reasons:")
        for r in reasons:
            typer.echo(f"  - {r}")


@suggest_app.command("accept")
def suggest_accept(
    suggestion_id: int,
    stance: EvidenceStance = typer.Option(..., "--stance", case_sensitive=False),
    note: Optional[str] = typer.Option(None, "--note"),
) -> None:
    session = _session()
    repo = SuggestionRepository(session)
    s = repo.get(suggestion_id)
    if not s:
        typer.secho("Not found.", fg=typer.colors.RED)
        raise typer.Exit(1)
    if s.status != SuggestionStatus.PENDING:
        typer.secho(f"Suggestion already {s.status.value}.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    repo.accept(s, stance, note)
    session.commit()
    claim = ClaimRepository(session).get(s.claim_id)
    typer.echo(
        f"Linked evidence #{s.evidence_id} to claim #{s.claim_id} as {stance.value}. "
        f"New confidence: {claim.confidence}"
    )


@suggest_app.command("reject")
def suggest_reject(suggestion_id: int, note: Optional[str] = typer.Option(None, "--note")) -> None:
    session = _session()
    repo = SuggestionRepository(session)
    s = repo.get(suggestion_id)
    if not s:
        typer.secho("Not found.", fg=typer.colors.RED)
        raise typer.Exit(1)
    if s.status != SuggestionStatus.PENDING:
        typer.secho(f"Suggestion already {s.status.value}.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    repo.reject(s, note)
    session.commit()
    typer.echo(f"Suggestion #{suggestion_id} rejected.")


# --- check -------------------------------------------------------------


@check_app.command("memory-config")
def check_memory_config(
    bus_width: int = typer.Option(..., "--bus-width", help="Bus width in bits, e.g. 384."),
    chip_density_gbit: float = typer.Option(..., "--chip-density-gbit", help="Per-chip density in Gbit, e.g. 16."),
    total_gb: float = typer.Option(..., "--total-gb", help="Claimed total VRAM in GB, e.g. 16."),
) -> None:
    """Ad hoc calculator, no persistence: sanity-check a memory configuration
    you heard about before deciding whether it's worth a claim at all."""
    result = check_memory_configuration(bus_width, chip_density_gbit, total_gb)
    if result.is_consistent:
        typer.secho(f"[CONSISTENT] {result.explanation}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"[CONTRADICTION] {result.explanation}", fg=typer.colors.RED)


# --- graph -------------------------------------------------------------


@graph_app.command("related")
def graph_related(
    entity_id: int,
    depth: int = typer.Option(2, "--depth", min=1, max=5, help="Max hops to traverse."),
) -> None:
    """Breadth-first traversal from an entity, edges treated as undirected.
    "Show everything related to Nova Lake" -- not just its direct edges."""
    session = _session()
    if not EntityRepository(session).get(entity_id):
        typer.secho("Not found.", fg=typer.colors.RED)
        raise typer.Exit(1)
    nodes = related_entities(session, entity_id, max_depth=depth)
    if not nodes:
        typer.echo("No related entities found.")
        return
    for n in nodes:
        typer.echo(f"depth={n.depth}\t#{n.entity_id}\t{n.type}\t{n.name}\t(via {n.via_relation} from {n.from_entity_name})")


@graph_app.command("find")
def graph_find(
    relation_type: RelationType = typer.Option(..., "--relation-type", case_sensitive=False),
    target: Optional[str] = typer.Option(None, "--target", help="Filter to edges pointing at this entity name."),
    source: Optional[str] = typer.Option(None, "--source", help="Filter to edges originating from this entity name."),
) -> None:
    """All edges of one relation type, optionally filtered by endpoint name.
    "Show every product linked to LPDDR5X": --relation-type uses_memory --target LPDDR5X."""
    session = _session()
    matches = find_by_relation(session, relation_type, target_name=target, source_name=source)
    if not matches:
        typer.echo("No matches.")
        return
    for m in matches:
        typer.echo(f"#{m.from_entity_id} {m.from_entity_name} --{m.relation_type}--> #{m.to_entity_id} {m.to_entity_name}")


# --- story -------------------------------------------------------------


@story_app.command("rank")
def story_rank(limit: int = typer.Option(10, "--limit", min=1)) -> None:
    """Rank OPEN claims by novelty + corroboration + momentum -- a triage
    signal for "what deserves a look today," not an editorial verdict."""
    session = _session()
    ranked = StoryScoringService(session).rank(limit=limit)
    if not ranked:
        typer.echo("No open claims.")
        return
    for r in ranked:
        typer.echo(f"#{r.claim.id}\tscore={r.score.total}\t{r.claim.statement}")
        for reason in r.score.reasons:
            typer.echo(f"    - {reason}")


# --- web ---------------------------------------------------------------


@web_app.command("serve")
def web_serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    r"""Serve the web dashboard (browse and edit claims, evidence, sources,
    and suggestions). Requires `pip install -e ".\[web]"` -- fastapi/uvicorn
    are imported here, lazily, so the base CLI never needs them just to run
    `entity add` or `claim create`."""
    from semi_intel.web.security import require_loopback_host

    try:
        require_loopback_host(host)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(2)
    try:
        import uvicorn

        from semi_intel.web.app import create_app
    except ImportError:
        typer.secho(
            "Web dashboard dependencies are not installed. Run: pip install -e \".[web]\"",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    typer.echo(f"Serving dashboard at http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port)


# --- pipeline ------------------------------------------------------------


@editorial_app.command("backfill")
def editorial_backfill() -> None:
    """Idempotently process existing evidence into topics, stories, and source leads."""
    session = _session()
    try:
        result = EditorialDiscoveryService(session).backfill()
        session.commit()
        typer.echo(
            f"Scanned {result.evidence_scanned} evidence item(s): "
            f"{result.stories_created} story/stories, {result.matches_created} topic match(es), "
            f"{result.citations_created} citation(s) created."
        )
    finally:
        session.close()


@discovery_app.command("run")
def discovery_run(
    story_id: Optional[int] = typer.Option(None, "--story-id", help="Search one eligible story instead of the queue."),
) -> None:
    """Run bounded targeted discovery; never crawls result articles."""
    session = _session()
    try:
        service = DiscoveryService(session)
        if story_id is not None:
            story = session.get(EditorialStory, story_id)
            if not story:
                typer.echo(f"Story #{story_id} not found.", err=True)
                raise typer.Exit(1)
            runs = [service.run_story(story, automatic=False)]
        else:
            runs = service.run_eligible(automatic=False)
        session.commit()
        if not runs:
            typer.echo("No stories are currently eligible.")
        for run in runs:
            typer.echo(
                f"Story #{run.story_id}: {run.status.value}; {run.request_count} request(s), "
                f"{run.accepted_result_count} accepted, {run.filtered_result_count} filtered. "
                f"{run.eligibility_reason}"
            )
    finally:
        session.close()


@discovery_app.command("status")
def discovery_status() -> None:
    """Show settings, persisted hourly budgets, and recent activity."""
    session = _session()
    try:
        service = DiscoveryService(session)
        settings = service.settings()
        budget = service.budget_status()
        recent = list(session.scalars(select(DiscoveryRun).order_by(DiscoveryRun.started_at.desc()).limit(10)))
        session.commit()
        typer.echo(f"Provider: {settings.provider}")
        typer.echo(f"Enabled: {settings.enabled}; automatic: {settings.automatic}")
        typer.echo(
            f"Hourly cycles: {budget['cycles_used']}/{budget['cycles_limit']}; "
            f"provider requests: {budget['provider_requests_used']}/{budget['provider_requests_limit']}"
        )
        for run in recent:
            typer.echo(f"  #{run.id} story #{run.story_id}: {run.status.value} ({run.accepted_result_count} accepted)")
    finally:
        session.close()


@discovery_app.command("backfill")
def discovery_backfill() -> None:
    """Search only currently eligible recent stories, within all budgets."""
    discovery_run(story_id=None)


@pipeline_app.command("run")
def pipeline_run(
    skip_pci_ids: bool = typer.Option(False, "--skip-pci-ids", help="Skip the pci.ids poll this run."),
) -> None:
    """One idempotent pass: poll every registered RSS source (any Source
    with type=rss and a url set), poll pci.ids, then run the suggestion
    scanner. This is the one command an external scheduler (cron, a
    systemd timer, Windows Task Scheduler) should call periodically --
    see `pipeline loop` for a zero-dependency fallback if you don't have
    one available."""
    session = _session()
    result = PipelineService(session).run_once(include_pci_ids=not skip_pci_ids)
    typer.echo(str(result))


@pipeline_app.command("loop")
def pipeline_loop(
    interval_minutes: int = typer.Option(60, "--interval-minutes", min=1),
    skip_pci_ids: bool = typer.Option(False, "--skip-pci-ids"),
) -> None:
    """Zero-dependency fallback scheduler for environments without cron or
    a systemd timer: runs `pipeline run` in a blocking loop, sleeping
    `--interval-minutes` between passes, until interrupted (Ctrl+C).

    Prefer cron/systemd/Task Scheduler in production -- they survive a
    reboot and a crashed process; this loop does not."""
    session = _session()
    service = PipelineService(session)
    loop_started_at = dt.datetime.utcnow()
    typer.echo(f"Running every {interval_minutes} minute(s). Press Ctrl+C to stop.")
    try:
        while True:
            started = dt.datetime.utcnow()
            typer.echo(f"[{started.isoformat()}] pipeline run starting")
            result = service.run_once(include_pci_ids=not skip_pci_ids, loop_started_at=loop_started_at)
            typer.echo(str(result))
            time.sleep(interval_minutes * 60)
    except KeyboardInterrupt:
        typer.echo("Stopped.")


@radar_app.command("status")
def radar_status() -> None:
    """Collection/promotion settings and recent provider health -- the
    quickest way to answer "is anything running, and is it safe."""
    session = _session()
    try:
        settings = get_collection_settings(session)
        session.commit()
        typer.echo(f"Collection enabled: {settings.collection_enabled}")
        typer.echo(f"X provider enabled: {settings.x_provider_enabled}")
        typer.echo(f"Startup stagger: {settings.startup_stagger_seconds}s")
        typer.echo(f"Media download enabled: {settings.media_download_enabled}")
        typer.echo(f"OCR enabled: {settings.ocr_enabled}")

        recent = list(session.scalars(
            select(ProviderRun).order_by(ProviderRun.started_at.desc()).limit(10)
        ))
        if not recent:
            typer.echo("No provider runs yet.")
        for run in recent:
            typer.echo(
                f"  #{run.id} {run.provider}/source={run.source_id}: {run.status.value} "
                f"({run.items_collected} item(s))"
            )
    finally:
        session.close()


@radar_app.command("import")
def radar_import(
    database: Path = typer.Option(
        ..., "--database", exists=True, file_okay=True, dir_okay=False, readable=True,
        help="Pre-merge Signal Radar SQLite database.",
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Commit the import. Without this flag, only a preview is produced."
    ),
    categories: str = typer.Option(
        ",".join(IMPORT_CATEGORIES),
        help="Comma-separated categories: sources,posts,media,provider_runs,source_suggestions.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print a machine-readable JSON report."),
) -> None:
    """Preview or transactionally import a legacy Signal Radar database.

    Legacy stories/scores/entities are never trusted as canonical. Raw posts
    enter this version's pending signal pipeline and external collection
    remains disabled for every imported source.
    """
    selected = [value.strip() for value in categories.split(",") if value.strip()]
    session = _session()
    try:
        importer = LegacyRadarImporter(session, database)
        report = importer.apply(selected) if apply else importer.preview(selected)
        if apply:
            session.commit()
        typer.echo(json.dumps(report.to_dict(), indent=2) if json_output else report.human_text())
        if apply and report.categories.get("posts", None):
            typer.echo("Next: run `semi-intel radar cluster` to analyze, cluster, and score imported posts.")
    except (ValueError, sqlite3.DatabaseError) as exc:
        session.rollback()
        typer.secho(f"Import failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@radar_app.command("collect")
def radar_collect(
    provider: Optional[str] = typer.Option(None, help="Restrict to one provider (e.g. rss, x)."),
    source_id: Optional[int] = typer.Option(None, help="Collect exactly one source by id."),
) -> None:
    """Manual, one-off collection. Works regardless of
    signal_collection_settings.collection_enabled (that flag only gates the
    automatic `pipeline run`/`pipeline loop` path) -- X specifically still
    requires x_provider_enabled=True either way, since it drives a real
    browser."""
    session = _session()
    try:
        service = CollectionService(session)
        if source_id is not None:
            source = session.get(Source, source_id)
            if source is None:
                typer.echo(f"No source with id={source_id}", err=True)
                raise typer.Exit(code=1)
            run = service.collect_source(source)
            typer.echo(f"{run.provider}/source={run.source_id}: {run.status.value}, {run.items_collected} item(s)"
                       + (f" -- {run.error}" if run.error else ""))
            return
        summary = service.collect_due_sources(provider_name=provider, automatic=False)
        if not summary.runs:
            typer.echo("No due sources to collect.")
            return
        typer.echo(str(summary))
    finally:
        session.close()


@radar_app.command("reprocess")
def radar_reprocess(
    stale_only: bool = typer.Option(
        True, help="Only re-analyze items whose processing_version is behind the current "
                   "extraction rules (default). Pass --no-stale-only to also pick up any "
                   "items stuck pending (equivalent to running analysis again)."
    ),
) -> None:
    """Re-run signal analysis over stored SignalItems after extraction rules
    change -- no recollection needed (brief section 23). Idempotent: deletes
    each item's prior topic-match/mention/label rows before re-analyzing, so
    nothing is duplicated."""
    session = _session()
    try:
        stale_count = reprocess_stale_items(session)
        typer.echo(f"Reprocessed {stale_count} stale item(s).")
        if not stale_only:
            pending_count = analyze_unprocessed(session)
            typer.echo(f"Analyzed {pending_count} previously-unprocessed item(s).")
    finally:
        session.close()


@radar_app.command("candidates")
def radar_candidates(
    state: str = typer.Option("active", help="active/promoted/dismissed/snoozed/stale/merged/all"),
    limit: int = typer.Option(20, min=1, max=200),
) -> None:
    """List Signal Candidates with their attention score and top attach
    reasons -- the plain-language explanation the GUI (Phase 6) will
    surface, available from the CLI today."""
    session = _session()
    try:
        stmt = select(SignalCandidate).order_by(SignalCandidate.attention_score.desc()).limit(limit)
        if state != "all":
            try:
                target_state = SignalCandidateState(state)
            except ValueError:
                typer.echo(f"Unknown state {state!r}. Use one of: active, promoted, dismissed, snoozed, stale, merged, all.", err=True)
                raise typer.Exit(code=1)
            stmt = stmt.where(SignalCandidate.state == target_state)
        candidates = list(session.scalars(stmt))
        session.commit()
        if not candidates:
            typer.echo("No candidates.")
            return
        for c in candidates:
            seen = "seen" if c.seen_at else "unseen"
            typer.echo(
                f"#{c.id} [{c.state.value}/{seen}] score={c.attention_score:.2f} "
                f"items={c.item_count} sources={c.distinct_source_count} "
                f"groups={c.independent_source_group_count} -- {c.title}"
            )
    finally:
        session.close()


@radar_app.command("candidate-seen")
def radar_candidate_seen(candidate_id: int, unseen: bool = typer.Option(False, "--unseen")) -> None:
    session = _session()
    try:
        candidate = session.get(SignalCandidate, candidate_id)
        if not candidate:
            typer.echo(f"No candidate with id={candidate_id}", err=True)
            raise typer.Exit(code=1)
        if unseen:
            mark_candidate_unseen(candidate)
        else:
            mark_candidate_seen(candidate)
        session.commit()
        typer.echo(f"Candidate #{candidate.id}: seen_at={candidate.seen_at}")
    finally:
        session.close()


@radar_app.command("candidate-dismiss")
def radar_candidate_dismiss(candidate_id: int, reason: str = typer.Option(..., help="Why this candidate is being dismissed.")) -> None:
    session = _session()
    try:
        candidate = session.get(SignalCandidate, candidate_id)
        if not candidate:
            typer.echo(f"No candidate with id={candidate_id}", err=True)
            raise typer.Exit(code=1)
        dismiss_candidate(candidate, reason=reason)
        session.commit()
        typer.echo(f"Candidate #{candidate.id} dismissed: {reason}")
    finally:
        session.close()


@radar_app.command("cluster")
def radar_cluster() -> None:
    """Analyze pending items, then cluster and rescore them -- normally
    handled automatically each `pipeline run`."""
    session = _session()
    try:
        analyzed = analyze_unprocessed(session)
        summary = cluster_unclustered_items(session)
        session.commit()
        scored = rescore_active_candidates(session)
        typer.echo(
            f"Analyzed {analyzed}. Clustering: {summary.attached_to_existing} attached, {summary.new_candidates} new, "
            f"{summary.suppressed_no_topic_or_artifact} suppressed. Rescored {scored} candidate(s)."
        )
    finally:
        session.close()


@radar_app.command("promote")
def radar_promote(
    candidate_id: int,
    merge_into_story: Optional[int] = typer.Option(None, help="Attach into this existing EditorialStory instead of creating a new one."),
    by: str = typer.Option("human:cli", help="Recorded in the promotion audit event."),
) -> None:
    """Manual promotion -- works regardless of automatic_promotion_enabled
    (that flag only gates the scheduled path). Idempotent: re-running
    against an already-promoted candidate returns the same story."""
    session = _session()
    try:
        candidate = session.get(SignalCandidate, candidate_id)
        if not candidate:
            typer.echo(f"No candidate with id={candidate_id}", err=True)
            raise typer.Exit(code=1)
        if merge_into_story is not None:
            story_obj = session.get(EditorialStory, merge_into_story)
            if not story_obj:
                typer.echo(f"No editorial story with id={merge_into_story}", err=True)
                raise typer.Exit(code=1)
            story = merge_candidate_into_story(session, candidate, story_obj, by=by)
        else:
            story = promote_candidate(session, candidate, by=by)
        typer.echo(f"Candidate #{candidate.id} -> EditorialStory #{story.id}: {story.headline}")
    except PromotionBlocked as exc:
        typer.echo(f"Promotion blocked: {exc}", err=True)
        raise typer.Exit(code=1)
    finally:
        session.close()


@radar_app.command("promote-eligible")
def radar_promote_eligible(dry_run: bool = typer.Option(True, help="Show what would be promoted without doing it.")) -> None:
    """Run the automatic-promotion eligibility scan on demand -- honors the
    same settings/budget as the scheduled pipeline path."""
    session = _session()
    try:
        settings = get_promotion_settings(session)
        if dry_run:
            import datetime as _dt
            stmt = select(SignalCandidate).where(SignalCandidate.state == SignalCandidateState.ACTIVE)
            for candidate in session.scalars(stmt):
                result = check_automatic_eligibility(session, candidate, settings, now=_dt.datetime.utcnow())
                status = "ELIGIBLE" if result.eligible else "skip: " + "; ".join(result.reasons)
                typer.echo(f"#{candidate.id} {candidate.title}: {status}")
            session.commit()
        else:
            summary = run_automatic_promotion(session)
            typer.echo(
                f"Promoted {len(summary.promoted)}: {summary.promoted}. "
                f"Skipped {len(summary.skipped)}."
                + (" Hourly budget exhausted." if summary.budget_exhausted else "")
            )
    finally:
        session.close()


@radar_app.command("provider-health")
def radar_provider_health(limit: int = typer.Option(20, min=1, max=200)) -> None:
    """Per-source last success/failure, for spotting a dead feed or an
    expired X session before it silently stops contributing signal."""
    session = _session()
    try:
        stmt = (
            select(Source)
            .where(Source.polling_enabled.is_(True))
            .order_by(Source.provider, Source.name)
            .limit(limit)
        )
        sources = list(session.scalars(stmt))
        session.commit()
        if not sources:
            typer.echo("No polling-enabled sources.")
            return
        for source in sources:
            status = "OK" if not source.error_state else f"ERROR: {source.error_state}"
            last = source.last_success_at.isoformat() if source.last_success_at else "never"
            typer.echo(f"[{source.provider}] {source.name}: last success {last} -- {status}")
    finally:
        session.close()


def _notification_payload(notification: Notification) -> dict:
    return {
        "id": notification.id,
        "event_type": notification.event_type.value,
        "severity": notification.severity.value,
        "title": notification.title,
        "body": notification.body,
        "reason": notification.reason,
        "candidate_id": notification.candidate_id,
        "story_id": notification.story_id,
        "topic_id": notification.topic_id,
        "source_suggestion_id": notification.source_suggestion_id,
        "source_id": notification.source_id,
        "event_at": aware(notification.event_at).isoformat(),
        "read": notification.read_at is not None,
        "dismissed": notification.dismissed_at is not None,
        "muted": notification.muted,
        "occurrence_count": notification.occurrence_count,
        "delivery_state": notification.delivery_state.value,
    }


@notification_app.command("status")
def notification_status(json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        settings = NotificationService(session).settings()
        payload = {
            "in_app_enabled": settings.in_app_enabled,
            "external_delivery_enabled": settings.external_delivery_enabled,
            "daily_digest_enabled": settings.daily_digest_enabled,
            "activation_at": aware(settings.activation_at).isoformat(),
            "unread": session.scalar(select(func.count()).select_from(Notification).where(
                Notification.read_at.is_(None), Notification.dismissed_at.is_(None),
                Notification.muted.is_(False),
            )) or 0,
            "open_provider_incidents": session.scalar(
                select(func.count()).select_from(ProviderIncident).where(
                    ProviderIncident.resolved_at.is_(None)
                )
            ) or 0,
            "digests": session.scalar(select(func.count()).select_from(NotificationDigest)) or 0,
        }
        session.commit()
        if json_output:
            typer.echo(json.dumps(payload, indent=2))
        else:
            typer.echo(f"In-app alerts: {'enabled' if payload['in_app_enabled'] else 'disabled'}")
            typer.echo(f"External delivery: {'enabled' if payload['external_delivery_enabled'] else 'disabled'}")
            typer.echo(f"Daily digest: {'enabled' if payload['daily_digest_enabled'] else 'disabled'}")
            typer.echo(f"Unread alerts: {payload['unread']}")
            typer.echo(f"Open provider incidents: {payload['open_provider_incidents']}")
            typer.echo(f"Activation watermark: {payload['activation_at']}")
    finally:
        session.close()


@notification_app.command("generate")
def notification_generate(json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        summary = NotificationService(session).generate()
        session.commit()
        try:
            from semi_intel.notifications.windows_desktop import WindowsDesktopDeliveryService
            desktop = WindowsDesktopDeliveryService(session).deliver_pending()
        except Exception:  # noqa: BLE001 - local alerts never fail generation
            session.rollback()
            desktop = {"notifications": 0}
        payload = {
            "created": summary.created,
            "updated": summary.updated,
            "seeded_historical_candidates": summary.seeded_historical_candidates,
            "by_type": summary.by_type,
            "windows_desktop_notifications": desktop["notifications"],
        }
        typer.echo(json.dumps(payload, indent=2) if json_output else (
            f"Created {len(summary.created)} alert(s), updated {len(summary.updated)}; "
            f"seeded {summary.seeded_historical_candidates} historical watermark(s)."
        ))
    finally:
        session.close()


@notification_app.command("list")
def notification_list(
    state: str = typer.Option("unread", help="unread/read/dismissed/all"),
    event_type: str | None = typer.Option(None, help="Filter by event type."),
    severity: str | None = typer.Option(None, help="Filter by severity."),
    limit: int = typer.Option(50, min=1, max=500),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    session = _session()
    try:
        stmt = select(Notification).order_by(Notification.event_at.desc()).limit(limit)
        if state == "unread":
            stmt = stmt.where(Notification.read_at.is_(None), Notification.dismissed_at.is_(None))
        elif state == "read":
            stmt = stmt.where(Notification.read_at.is_not(None), Notification.dismissed_at.is_(None))
        elif state == "dismissed":
            stmt = stmt.where(Notification.dismissed_at.is_not(None))
        elif state != "all":
            raise typer.BadParameter("state must be unread, read, dismissed or all")
        if event_type:
            try:
                stmt = stmt.where(Notification.event_type == NotificationEventType(event_type))
            except ValueError as exc:
                raise typer.BadParameter(f"unknown event type {event_type!r}") from exc
        if severity:
            try:
                stmt = stmt.where(Notification.severity == NotificationSeverity(severity))
            except ValueError as exc:
                raise typer.BadParameter(f"unknown severity {severity!r}") from exc
        rows = list(session.scalars(stmt))
        if json_output:
            typer.echo(json.dumps([_notification_payload(row) for row in rows], indent=2))
        elif not rows:
            typer.echo("No alerts in this view.")
        else:
            for row in rows:
                marker = "unread" if row.read_at is None else "read"
                typer.echo(
                    f"#{row.id} [{row.severity.value}/{marker}] {row.title} "
                    f"({row.event_type.value}, x{row.occurrence_count})"
                )
    finally:
        session.close()


@notification_app.command("show")
def notification_show(notification_id: int, json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        notification = session.get(Notification, notification_id)
        if not notification:
            typer.echo(f"No alert with id={notification_id}", err=True)
            raise typer.Exit(1)
        payload = _notification_payload(notification)
        if json_output:
            typer.echo(json.dumps(payload, indent=2))
        else:
            typer.echo(f"{notification.title}\n{notification.body}\nWhy: {notification.reason}")
    finally:
        session.close()


def _notification_state_action(notification_id: int, action: str) -> None:
    session = _session()
    try:
        notification = session.get(Notification, notification_id)
        if not notification:
            typer.echo(f"No alert with id={notification_id}", err=True)
            raise typer.Exit(1)
        service = NotificationService(session)
        if action == "read":
            service.set_read([notification_id], read=True)
        elif action == "unread":
            service.set_read([notification_id], read=False)
        elif action == "dismiss":
            service.dismiss(notification)
        elif action == "restore":
            service.restore(notification)
        session.commit()
        typer.echo(f"Alert #{notification_id}: {action}.")
    finally:
        session.close()


@notification_app.command("read")
def notification_read(notification_id: int) -> None:
    _notification_state_action(notification_id, "read")


@notification_app.command("unread")
def notification_unread(notification_id: int) -> None:
    _notification_state_action(notification_id, "unread")


@notification_app.command("dismiss")
def notification_dismiss(notification_id: int) -> None:
    _notification_state_action(notification_id, "dismiss")


@notification_app.command("restore")
def notification_restore(notification_id: int) -> None:
    _notification_state_action(notification_id, "restore")


@notification_app.command("digest")
def notification_digest(json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        digest = DigestService(session).generate()
        session.commit()
        if json_output:
            typer.echo(json.dumps({
                "id": digest.id,
                "window_start": aware(digest.window_start).isoformat(),
                "window_end": aware(digest.window_end).isoformat(),
                "sections": json.loads(digest.structured_sections),
                "text": digest.rendered_text,
            }, indent=2))
        else:
            typer.echo(digest.rendered_text)
    finally:
        session.close()


@notification_app.command("settings")
def notification_settings(
    minimum_score: float | None = typer.Option(None, min=0.0, max=1.0),
    timezone: str | None = typer.Option(None),
    digest_time: str | None = typer.Option(None, help="HH:MM local time."),
    enable_digest: bool = typer.Option(False, "--enable-digest"),
    disable_digest: bool = typer.Option(False, "--disable-digest"),
    reactivate_now: bool = typer.Option(False, help="Reset transition watermarks from now."),
    mute_event: str | None = typer.Option(None, help="Mute one notification event type."),
    unmute_event: str | None = typer.Option(None, help="Unmute one notification event type."),
    mute_topic: int | None = typer.Option(None, help="Mute alerts for one monitored-topic ID."),
    unmute_topic: int | None = typer.Option(None, help="Unmute alerts for one monitored-topic ID."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    if enable_digest and disable_digest:
        raise typer.BadParameter("choose either --enable-digest or --disable-digest")
    session = _session()
    try:
        service = NotificationService(session)
        settings = service.settings()
        changed = any(value is not None for value in (
            minimum_score, timezone, digest_time, mute_event, unmute_event,
            mute_topic, unmute_topic,
        )) or enable_digest or disable_digest or reactivate_now
        if minimum_score is not None:
            settings.minimum_attention_score = minimum_score
        if timezone is not None:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            try:
                ZoneInfo(timezone)
            except ZoneInfoNotFoundError as exc:
                raise typer.BadParameter(f"unknown timezone {timezone!r}") from exc
            settings.timezone = timezone
        if digest_time is not None:
            dt.time.fromisoformat(digest_time)
            settings.digest_time = digest_time
        if enable_digest:
            settings.daily_digest_enabled = True
        if disable_digest:
            settings.daily_digest_enabled = False
        if reactivate_now:
            settings = service.reset_activation()
        for raw, muted in ((mute_event, True), (unmute_event, False)):
            if raw:
                try:
                    settings = service.set_event_muted(NotificationEventType(raw), muted=muted)
                except ValueError as exc:
                    raise typer.BadParameter(f"unknown event type {raw!r}") from exc
        for topic_id, muted in ((mute_topic, True), (unmute_topic, False)):
            if topic_id is not None:
                try:
                    settings = service.set_topic_muted(topic_id, muted=muted)
                except ValueError as exc:
                    raise typer.BadParameter(str(exc)) from exc
        if changed:
            from semi_intel.operations.scheduler import get_scheduler_settings
            get_scheduler_settings(session).active_notification_preset = "custom"
        session.commit()
        payload = {
            "minimum_attention_score": settings.minimum_attention_score,
            "timezone": settings.timezone,
            "digest_time": settings.digest_time,
            "daily_digest_enabled": settings.daily_digest_enabled,
            "external_delivery_enabled": settings.external_delivery_enabled,
            "activation_at": aware(settings.activation_at).isoformat(),
            "muted_event_types": json.loads(settings.muted_event_types or "[]"),
            "muted_topic_ids": json.loads(settings.muted_topic_ids or "[]"),
        }
        typer.echo(json.dumps(payload, indent=2) if json_output else "\n".join(
            f"{key}: {value}" for key, value in payload.items()
        ))
    finally:
        session.close()


@notification_app.command("test")
def notification_test() -> None:
    session = _session()
    try:
        notification = NotificationService(session).create_test_notification()
        session.commit()
        typer.echo(f"Created test alert #{notification.id}. No external message was sent.")
    finally:
        session.close()


@notification_app.command("deliver")
def notification_deliver(notification_id: int) -> None:
    """Record local in-app delivery. Phase 8 ships no network adapter."""
    session = _session()
    try:
        notification = session.get(Notification, notification_id)
        if not notification:
            typer.echo(f"No alert with id={notification_id}", err=True)
            raise typer.Exit(1)
        attempt = DeliveryService(session).deliver_notification(notification, InAppAdapter())
        session.commit()
        typer.echo(
            f"Alert #{notification_id}: {attempt.status.value} via in-app adapter. "
            "No external adapter is configured."
        )
    finally:
        session.close()


@notification_app.command("preset-list")
def notification_preset_list(json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        service = NotificationQualityService(session)
        payload = service.presets()
        active = service.feedback_summary()["active_preset"]
        if json_output:
            typer.echo(json.dumps({"active": active, "presets": payload}, indent=2))
        else:
            typer.echo(f"Active preset: {active}")
            for name in payload:
                typer.echo(f"- {name.replace('_', ' ').title()}")
    finally:
        session.close()


@notification_app.command("preset-preview")
def notification_preset_preview(
    name: str, json_output: bool = typer.Option(False, "--json")
) -> None:
    session = _session()
    try:
        try:
            payload = NotificationQualityService(session).preview_preset(name)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if json_output:
            typer.echo(json.dumps(payload, indent=2))
        elif not payload["changes"]:
            typer.echo(f"{name.replace('_', ' ').title()} is already applied.")
        else:
            typer.echo("Proposed changes (nothing has been changed):")
            for field, change in payload["changes"].items():
                typer.echo(f"- {field.replace('_', ' ')}: {change['from']} -> {change['to']}")
    finally:
        session.close()


@notification_app.command("preset-apply")
def notification_preset_apply(name: str) -> None:
    session = _session()
    try:
        try:
            payload = NotificationQualityService(session).apply_preset(name)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(
            f"Applied {name.replace('_', ' ').title()}; "
            f"{len(payload['changes'])} setting(s) changed. External delivery was preserved."
        )
    finally:
        session.close()


@notification_app.command("feedback")
def notification_feedback(
    notification_id: int,
    rating: NotificationFeedbackRating = typer.Option(..., "--rating"),
    reason: str | None = typer.Option(None),
    note: str | None = typer.Option(None),
) -> None:
    session = _session()
    try:
        try:
            row = NotificationQualityService(session).feedback(
                notification_id, rating, reason=reason, note=note
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(f"Feedback saved for alert #{row.notification_id}: {row.rating.value}.")
    finally:
        session.close()


@notification_app.command("feedback-summary")
def notification_feedback_summary(json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        payload = NotificationQualityService(session).feedback_summary()
        if json_output:
            typer.echo(json.dumps(payload, indent=2))
        else:
            typer.echo(
                f"{payload['useful']} useful, {payload['not_useful']} not useful "
                f"({payload['total']} rated)."
            )
            for observation in payload["observations"]:
                typer.echo(f"- {observation}")
    finally:
        session.close()


@notification_app.command("delivery-status")
def notification_external_status(json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        service = WebhookConfigurationService(session)
        payload = service.status()
        session.commit()
        typer.echo(json.dumps(payload, indent=2) if json_output else "\n".join(
            f"{key.replace('_', ' ').title()}: {value}" for key, value in payload.items()
        ))
    finally:
        session.close()


@notification_app.command("delivery-preview")
def notification_external_preview(json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        payload = WebhookConfigurationService(session).preview()
        typer.echo(json.dumps(payload, indent=2) if json_output else (
            f"Destination: {payload['host'] or 'not configured'}\n"
            f"Network contacted: {payload['network_contacted']}\n"
            f"Test title: {payload['payload']['title']}"
        ))
    finally:
        session.close()


@notification_app.command("delivery-test")
def notification_external_test(
    yes: bool = typer.Option(False, "--yes", help="Confirm the synthetic network test.")
) -> None:
    if not yes and not typer.confirm("Send one synthetic test to the configured webhook?"):
        raise typer.Abort()
    session = _session()
    try:
        result = WebhookConfigurationService(session).test()
        if not result.delivered:
            typer.secho(result.error or "Webhook test failed.", fg=typer.colors.RED)
            raise typer.Exit(1)
        typer.secho("Synthetic webhook test succeeded.", fg=typer.colors.GREEN)
    finally:
        session.close()


@notification_app.command("retry")
def notification_external_retry(json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        payload = ExternalDeliveryService(session).deliver_pending()
        if json_output:
            typer.echo(json.dumps(payload, indent=2))
        elif payload["disabled"]:
            typer.echo("External delivery is disabled or unconfigured; nothing was sent.")
        else:
            typer.echo(
                f"Processed {payload['notifications']} alert(s) and "
                f"{payload['digests']} digest(s)."
            )
    finally:
        session.close()


@notification_app.command("view-list")
def notification_view_list(json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        rows = SavedViewService(session).list()
        payload = [{"id": row.id, "name": row.name, "state": row.state_filter} for row in rows]
        if json_output:
            typer.echo(json.dumps(payload, indent=2))
        elif not rows:
            typer.echo("No saved notification views.")
        else:
            for row in payload:
                typer.echo(f"#{row['id']} {row['name']} ({row['state']})")
    finally:
        session.close()


@notification_app.command("view-save")
def notification_view_save(
    name: str, state: str = typer.Option("unread"), search: str = typer.Option("")
) -> None:
    session = _session()
    try:
        try:
            row = SavedViewService(session).save(
                name=name, state_filter=state, search_text=search
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(f"Saved notification view #{row.id}: {row.name}.")
    finally:
        session.close()


@notification_app.command("view-delete")
def notification_view_delete(view_id: int) -> None:
    session = _session()
    try:
        try:
            SavedViewService(session).delete(view_id)
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1)
        typer.echo(f"Deleted saved notification view #{view_id}.")
    finally:
        session.close()


if __name__ == "__main__":
    app()
