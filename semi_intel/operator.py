"""`semintel` -- the operator-friendly CLI.

`semi_intel/cli.py` (the `semi-intel` command) is the full, detailed CLI --
every entity/source/evidence/claim field is its own flag, aimed at someone
comfortable with a terminal and the data model. This module is a second,
much smaller CLI aimed at the person who *owns* the platform but doesn't
write Python: install it, point it at some sources, run it, check on it,
back it up. Nine commands, every one of them safe to run repeatedly, every
one of them printing plain-English output instead of stack traces.

Design rules for this file specifically:
  - Never let a real exception reach the user as a traceback. Catch it,
    explain what probably went wrong in plain language, and suggest the
    next command to run (usually `semintel doctor`).
  - Every command is idempotent -- running it twice is always safe.
  - No new business logic lives here. Every command is a thin, friendlier
    wrapper over the exact same repository/service layer semi_intel/cli.py
    and semi_intel/web/app.py already use. If a rule about claims or
    evidence needs to change, it changes in one place, not three.
  - This CLI decides *where your data lives* once (`semintel install`) and
    remembers it in semintel.config.json, so you don't have to set
    environment variables by hand. See _load_config()/_apply_config_env().
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from sqlalchemy import func, select

from semi_intel.cli import _alembic_config, _project_root, _session, upgrade_or_stamp_to_head
from semi_intel.db import DEFAULT_DB_URL
from semi_intel.domain.enums import (
    ClaimStatus, OperationalJobType, OperationalTriggerType, SourceType, SuggestionStatus,
)
from semi_intel.domain.models import Claim, ClaimLinkSuggestion, Entity, Evidence, Source
from semi_intel.ingestion.plugins.pci_ids_plugin import PciIdsSourcePlugin
from semi_intel.ingestion.plugins.rss_plugin import RSSSourcePlugin
from semi_intel.repository.repositories import SourceRepository
from semi_intel.operations.backup import BackupService
from semi_intel.operations.diagnostics import DiagnosticsService
from semi_intel.operations.health import HealthService
from semi_intel.operations.scheduler import OperationalScheduler
from semi_intel.operations.windows_task import execute as execute_task_command
from semi_intel.operations.windows_task import install_command, remove_command

app = typer.Typer(
    help="semintel -- install, run, and check on the Semiconductor Intelligence Platform.",
    # Deliberately NOT no_args_is_help=True: double-clicking semintel.exe in
    # File Explorer runs it with zero arguments, and a bare usage screen is
    # exactly the confusing dead end that sent an actual user looking for
    # help earlier in this project. See _main() below -- zero arguments now
    # means "set up if needed, then open the dashboard" instead.
    no_args_is_help=False,
)
automation_app = typer.Typer(help="Configure and inspect bounded local automation.")
backups_app = typer.Typer(help="Create, verify, prune and safely restore verified backups.")
diagnostics_app = typer.Typer(help="Create privacy-bounded support diagnostics.")
app.add_typer(automation_app, name="automation")
app.add_typer(backups_app, name="backups")
app.add_typer(diagnostics_app, name="diagnostics")

CONFIG_FILENAME = "semintel.config.json"


# --- where your data lives --------------------------------------------------


def _config_path() -> Path:
    return Path.cwd() / CONFIG_FILENAME


def _load_config() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_config(config: dict) -> None:
    _config_path().write_text(json.dumps(config, indent=2) + "\n")


def _apply_config_env() -> None:
    """If SEMI_INTEL_DB_URL isn't already set in the environment, fall back
    to the db_url `semintel install` recorded in semintel.config.json. This
    is what lets you run `semintel status` tomorrow without re-typing an
    env var -- as long as you run it from the same folder you installed
    in. See OPERATOR_GUIDE.md for why "same folder" matters."""
    if "SEMI_INTEL_DB_URL" not in os.environ:
        db_url = _load_config().get("db_url")
        if db_url:
            os.environ["SEMI_INTEL_DB_URL"] = db_url


@app.callback(invoke_without_command=True)
def _main(ctx: typer.Context) -> None:
    _apply_config_env()
    if ctx.invoked_subcommand is None:
        # No subcommand at all -- overwhelmingly likely to mean someone
        # double-clicked semintel.exe rather than typed a command. Set up
        # (idempotent, same as running `install` by hand -- safe even if
        # this folder is already set up) and open the dashboard, the same
        # thing `semintel gui` does. `semintel --help` still shows the full
        # command list -- Click handles --help before this callback runs.
        typer.echo("No command given -- setting up (if needed) and opening the dashboard.")
        typer.echo("(Run `semintel --help` instead to see all commands.)")
        typer.echo()
        # Calling these directly (not via ctx.invoke) with their real
        # defaults spelled out -- ctx.invoke() on a Typer-decorated function
        # passes its raw Python defaults, which are typer.Option(...)
        # sentinel objects, not the resolved values Click would normally
        # bind; install(data_dir=OptionInfo(...)) would crash.
        install(data_dir=None)
        typer.echo()
        gui(host="127.0.0.1", port=8000, no_browser=False)


def _current_db_url() -> str:
    return os.environ.get("SEMI_INTEL_DB_URL", DEFAULT_DB_URL)


def _data_dir() -> Path:
    config = _load_config()
    if config.get("data_dir"):
        return Path(config["data_dir"])
    return Path.cwd()


# --- schema status (shared by status/doctor) --------------------------------


def _schema_status():
    """Returns (current_revision_or_None, head_revision_or_None, up_to_date: bool | None).
    up_to_date is None if we couldn't determine it (e.g. no database yet)."""
    try:
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        cfg = _alembic_config()
        script = ScriptDirectory.from_config(cfg)
        head = script.get_current_head()

        session = _session()
        try:
            context = MigrationContext.configure(session.connection())
            heads = context.get_current_heads()
        finally:
            session.close()
        current = heads[0] if heads else None
        return current, head, (current == head)
    except Exception:
        return None, None, None


# Alembic reconciliation itself now lives in semi_intel.cli.upgrade_or_stamp_to_head
# (imported above) so semi_intel/web/app.py's create_app() can share the
# exact same logic without importing this CLI-facing module.


# --- install -----------------------------------------------------------------


@app.command("install")
def install(
    data_dir: Optional[str] = typer.Option(
        None,
        "--data-dir",
        help="Folder to store your database and backups in. Defaults to the current folder. "
        "Only needed the first time, or if you want to move your data somewhere new.",
    ),
) -> None:
    """First-time setup: choose where your data lives, create the database,
    and confirm everything works. Safe to run again later -- it never
    erases existing data."""
    existing = _load_config()

    if data_dir:
        chosen_dir = Path(data_dir).expanduser().resolve()
    elif existing.get("data_dir"):
        chosen_dir = Path(existing["data_dir"])
    else:
        chosen_dir = Path.cwd()

    try:
        chosen_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        typer.secho(f"Couldn't create or use {chosen_dir}: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1)

    db_path = chosen_dir / "semi_intel.db"
    db_url = f"sqlite:///{db_path}"
    os.environ["SEMI_INTEL_DB_URL"] = db_url

    typer.secho("Setting up your database...", fg=typer.colors.CYAN)
    try:
        upgrade_or_stamp_to_head()
    except Exception as exc:
        typer.secho(f"Could not set up the database: {exc}", fg=typer.colors.RED)
        typer.echo("Run `semintel doctor` for a more detailed check.")
        raise typer.Exit(1)

    (chosen_dir / "backups").mkdir(exist_ok=True)
    _save_config({"data_dir": str(chosen_dir), "db_url": db_url})

    typer.secho("Done.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Data folder:  {chosen_dir}")
    typer.echo(f"  Database:     {db_path}")
    typer.echo()
    typer.echo("Remember: run semintel commands from this same folder (or pass")
    typer.echo("--data-dir again) so it always finds the right database.")
    typer.echo()
    typer.echo("Next steps:")
    typer.echo("  semintel add-source     register somewhere to pull evidence from")
    typer.echo("  semintel run            fetch new evidence + look for matches")
    typer.echo("  semintel status         see what's in your database")


# --- run -----------------------------------------------------------------


@app.command("run")
def run(
    skip_pci_ids: bool = typer.Option(
        False, "--skip-pci-ids", help="Skip the pci.ids check this time (a public chip-ID database)."
    ),
) -> None:
    """Fetch new evidence from every source you've registered, then check
    whether any of it might relate to your existing claims. Safe to run as
    often as you like -- it never re-adds evidence it's already seen."""
    from semi_intel.pipeline.service import PipelineService

    session = _session()
    try:
        result = PipelineService(session).run_once(include_pci_ids=not skip_pci_ids)
    except Exception as exc:
        typer.secho(f"Something went wrong: {exc}", fg=typer.colors.RED)
        typer.echo("Run `semintel doctor` to check your setup.")
        raise typer.Exit(1)
    finally:
        session.close()

    if not result.ingestion_results and not result.failures:
        typer.echo("No sources registered yet. Add one with `semintel add-source`.")
    for r in result.ingestion_results:
        typer.secho(
            f"  ✓ {r.source_name}: {r.created} new, {r.skipped_duplicate} already seen",
            fg=typer.colors.GREEN,
        )
    for f in result.failures:
        typer.secho(f"  ✗ {f.source_name}: {f.error}", fg=typer.colors.RED)

    if result.suggestion_result:
        created = result.suggestion_result.suggestions_created
        if created:
            typer.secho(
                f"Found {created} new possible evidence-to-claim match(es). "
                "Review them with `semi-intel suggest list`.",
                fg=typer.colors.CYAN,
            )
        else:
            typer.echo("No new claim matches this time.")

    if result.failures:
        raise typer.Exit(1)


# --- gui -----------------------------------------------------------------


@app.command("gui")
def gui(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Print the address instead of opening it automatically."
    ),
) -> None:
    r"""Open the dashboard in your web browser -- browse and edit claims,
    evidence, sources, and suggestions by clicking instead of typing
    commands. It's the exact same database and the exact same rules as
    every other semintel command; this just gives you a page to look at
    instead of terminal output. Requires the 'web' extra -- if you built
    this from an .exe, that's already included; from source, run
    `pip install -e ".\[web]"` once first. Press Ctrl+C in this window to
    stop it."""
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
            "The web dashboard isn't installed. Run: pip install -e \".[web]\"",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    url = f"http://{host}:{port}"
    typer.secho(f"Opening the dashboard at {url}", fg=typer.colors.CYAN)
    typer.echo("Press Ctrl+C here to stop it.")

    if not no_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        uvicorn.run(create_app(), host=host, port=port)
    except (OSError, SystemExit):
        # uvicorn doesn't let a bind failure (e.g. the port is already
        # taken) reach us as a normal Python exception -- internally it
        # logs the error itself, then calls sys.exit() directly, which
        # raises SystemExit rather than the OSError you might expect. We
        # catch both here so either way, the person sees a plain-language
        # explanation instead of just uvicorn's raw "ERROR: [Errno ...]"
        # log line and an unexplained exit.
        typer.secho(f"Couldn't start the dashboard on {url}.", fg=typer.colors.RED)
        typer.echo(
            f"This usually means something's already using port {port} -- maybe the "
            f"dashboard is already open in another window (check for it at {url} before "
            "trying again). Or run this with a different port: semintel gui --port 8001"
        )
        raise typer.Exit(1)


# --- status -----------------------------------------------------------------


@app.command("status")
def status() -> None:
    """A quick snapshot of what's in your database right now."""
    session = _session()
    try:
        source_count = session.scalar(select(func.count()).select_from(Source))
        entity_count = session.scalar(select(func.count()).select_from(Entity))
        evidence_count = session.scalar(select(func.count()).select_from(Evidence))
        pending_suggestions = session.scalar(
            select(func.count()).select_from(ClaimLinkSuggestion).where(
                ClaimLinkSuggestion.status == SuggestionStatus.PENDING
            )
        )
        last_evidence = session.scalar(select(func.max(Evidence.collected_at)))
        claim_counts = {
            claim_status: session.scalar(
                select(func.count()).select_from(Claim).where(Claim.status == claim_status)
            )
            for claim_status in ClaimStatus
        }
    finally:
        session.close()

    current_rev, head_rev, up_to_date = _schema_status()

    typer.secho("Database", bold=True)
    typer.echo(f"  Location:        {_current_db_url()}")
    if up_to_date is True:
        typer.secho("  Schema:          up to date", fg=typer.colors.GREEN)
    elif up_to_date is False:
        typer.secho(
            f"  Schema:          OUT OF DATE (at {current_rev or 'none'}, latest is {head_rev}) "
            "-- run `semintel update`",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho("  Schema:          could not check (see `semintel doctor`)", fg=typer.colors.YELLOW)

    typer.echo()
    typer.secho("Contents", bold=True)
    typer.echo(f"  Sources:         {source_count}")
    typer.echo(f"  Entities:        {entity_count}")
    typer.echo(f"  Evidence:        {evidence_count}")
    typer.echo(
        f"  Claims:          {claim_counts[ClaimStatus.OPEN]} open, "
        f"{claim_counts[ClaimStatus.CONFIRMED]} confirmed, "
        f"{claim_counts[ClaimStatus.DEBUNKED]} debunked, "
        f"{claim_counts[ClaimStatus.RETRACTED]} retracted"
    )
    suggestion_note = " -- run `semi-intel suggest list`" if pending_suggestions else ""
    typer.echo(f"  Pending matches: {pending_suggestions}{suggestion_note}")
    typer.echo(f"  Last evidence:   {last_evidence or 'none yet'}")


# --- doctor -----------------------------------------------------------------


def _check(label: str, ok: bool, detail: str = "", warn: bool = False) -> bool:
    if ok:
        typer.secho(f"  [PASS] {label}", fg=typer.colors.GREEN)
    elif warn:
        typer.secho(f"  [WARN] {label}", fg=typer.colors.YELLOW)
        if detail:
            typer.echo(f"         {detail}")
    else:
        typer.secho(f"  [FAIL] {label}", fg=typer.colors.RED)
        if detail:
            typer.echo(f"         {detail}")
    return ok or warn


@app.command("doctor")
def doctor(
    skip_network: bool = typer.Option(
        False, "--skip-network", help="Skip checking that your registered sources are reachable."
    ),
) -> None:
    """Checks that everything is set up correctly and tells you exactly
    what's wrong, if anything. Run this first whenever something doesn't
    look right."""
    all_ok = True

    typer.secho("semintel doctor", bold=True)
    typer.echo()

    running_frozen = getattr(sys, "frozen", False)
    typer.echo(f"  Mode: {'standalone executable' if running_frozen else 'running from Python source'}")
    typer.echo()

    data_dir = _data_dir()
    all_ok &= _check(f"Data folder exists ({data_dir})", data_dir.exists())

    probe = data_dir / ".semintel_write_test"
    try:
        probe.write_text("ok")
        probe.unlink()
        all_ok &= _check("Data folder is writable", True)
    except OSError as exc:
        all_ok &= _check("Data folder is writable", False, str(exc))

    try:
        total, used, free = shutil.disk_usage(data_dir if data_dir.exists() else Path.cwd())
        free_mb = free / (1024 * 1024)
        ok = free_mb > 200
        all_ok &= _check(
            f"Disk space ({free_mb:.0f} MB free)", ok, "Less than 200 MB free -- consider freeing up space.", warn=not ok
        )
    except OSError as exc:
        all_ok &= _check("Disk space", False, str(exc))

    try:
        session = _session()
        session.execute(select(func.count()).select_from(Source))
        session.close()
        all_ok &= _check(f"Database is reachable ({_current_db_url()})", True)
    except Exception as exc:
        all_ok &= _check(f"Database is reachable ({_current_db_url()})", False, str(exc))
        typer.echo()
        typer.echo("  Can't continue checking without a working database. Try `semintel install`.")
        raise typer.Exit(1)

    current_rev, head_rev, up_to_date = _schema_status()
    if up_to_date is True:
        all_ok &= _check("Database schema is up to date", True)
    elif up_to_date is False:
        all_ok &= _check(
            "Database schema is up to date",
            False,
            f"At {current_rev or 'no migration history'}, latest is {head_rev}. Run `semintel update`.",
        )
    else:
        all_ok &= _check("Database schema is up to date", True, warn=True, detail="Could not determine -- non-fatal.")

    if not skip_network:
        session = _session()
        try:
            sources = list(session.scalars(select(Source).where(Source.url.is_not(None))))
        finally:
            session.close()

        if not sources:
            typer.echo("  (no registered sources with a URL to check)")
        for source in sources:
            ok, detail = _probe_url(source.url)
            all_ok &= _check(f"Source reachable: {source.name}", ok, detail, warn=not ok)

    typer.echo()
    if all_ok:
        typer.secho("Everything looks good.", fg=typer.colors.GREEN, bold=True)
    else:
        typer.secho("Something needs attention -- see FAIL lines above.", fg=typer.colors.RED, bold=True)
        raise typer.Exit(1)


def _probe_url(url: str, timeout: float = 5.0):
    import urllib.request

    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True, ""
    except Exception as exc:  # noqa: BLE001 - reachability probe, any failure is just a result
        return False, str(exc)


# --- update -----------------------------------------------------------------


@app.command("update")
def update() -> None:
    """Applies any pending database changes that shipped with this version.
    This does NOT download a newer version of the program itself -- there's
    no update server. To get a newer semintel, get the newer project files
    from whoever gave you this one and rebuild (see INSTALL.md)."""
    from semi_intel import __version__

    typer.echo(f"You're running semintel {__version__}.")

    current_rev, head_rev, up_to_date = _schema_status()
    if up_to_date is True:
        typer.secho("Your database schema is already up to date -- nothing to do.", fg=typer.colors.GREEN)
        return

    typer.secho("Applying database changes...", fg=typer.colors.CYAN)
    try:
        outcome = upgrade_or_stamp_to_head()
    except Exception as exc:
        typer.secho(f"Could not update the database: {exc}", fg=typer.colors.RED)
        typer.echo("Run `semintel doctor` for a more detailed check.")
        raise typer.Exit(1)

    if outcome == "stamped":
        typer.secho(
            "Your database already had the current structure -- marked it as up to date.",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho("Database is now up to date.", fg=typer.colors.GREEN)
    typer.echo()
    typer.echo("Reminder: this only updates your DATA. To update the PROGRAM itself,")
    typer.echo("replace this executable with a newer build -- see INSTALL.md.")


# --- add-source -----------------------------------------------------------------

_SOURCE_TYPE_HELP = "one of: " + ", ".join(t.value for t in SourceType)


@app.command("add-source")
def add_source(
    name: Optional[str] = typer.Option(None, "--name", help="What to call this source."),
    url: Optional[str] = typer.Option(None, "--url", help="Feed or website URL, if it has one."),
    type: Optional[str] = typer.Option(None, "--type", help=_SOURCE_TYPE_HELP),
    trust: float = typer.Option(0.6, "--trust", min=0.0, max=1.0, help="How much you trust this source, 0.0-1.0."),
) -> None:
    """Register somewhere to pull evidence from. Run with no options for a
    guided, step-by-step setup."""
    if name is None:
        name = typer.prompt("Name for this source (e.g. 'VideoCardz')")

    if url is None:
        url = typer.prompt(
            "Feed or website URL (press Enter to skip if this is a manual/offline source)",
            default="",
            show_default=False,
        )
        url = url or None

    if type is None:
        type = "rss" if url else "manual"

    try:
        source_type = SourceType(type.lower())
    except ValueError:
        typer.secho(f"'{type}' isn't a known source type. Valid options: {_SOURCE_TYPE_HELP}.", fg=typer.colors.RED)
        raise typer.Exit(1)

    session = _session()
    try:
        repo = SourceRepository(session)
        if repo.find_by_name(name):
            typer.secho(f"A source named '{name}' already exists.", fg=typer.colors.RED)
            raise typer.Exit(1)
        source = Source(name=name, type=source_type, url=url, trust_weight=trust)
        repo.add(source)
        session.commit()
    finally:
        session.close()

    typer.secho(f"Added source #{source.id}: {name}", fg=typer.colors.GREEN)
    if url:
        typer.echo(f"Test it with:  semintel test-source \"{name}\"")
    typer.echo("Fetch from it (and everything else) with:  semintel run")


# --- test-source -----------------------------------------------------------------


@app.command("test-source")
def test_source(
    name: Optional[str] = typer.Argument(None, help="Name of an already-registered source."),
    url: Optional[str] = typer.Option(None, "--url", help="Test a URL without registering it first."),
) -> None:
    """Checks that a source can actually be reached and read, WITHOUT
    saving anything. Use this before `semintel run` to catch a typo'd URL
    or a dead feed early."""
    if name is None and url is None:
        typer.secho("Give me either a registered source name, or --url to test something new.", fg=typer.colors.RED)
        raise typer.Exit(1)

    source_type = SourceType.RSS
    target_url = url
    label = url or ""

    if name:
        session = _session()
        try:
            source = SourceRepository(session).find_by_name(name)
        finally:
            session.close()
        if not source:
            typer.secho(f"No source named '{name}'.", fg=typer.colors.RED)
            typer.echo("Check the exact name with `semi-intel source list`, or register it with `semintel add-source`.")
            raise typer.Exit(1)
        if not source.url:
            typer.secho(f"'{name}' doesn't have a URL on file -- it's a manual source, nothing to test.", fg=typer.colors.YELLOW)
            raise typer.Exit(0)
        target_url = source.url
        source_type = source.type
        label = name

    if source_type not in (SourceType.RSS, SourceType.REGISTRY):
        typer.secho(
            f"'{label}' is type '{source_type.value}', which doesn't have an automatic fetcher yet.",
            fg=typer.colors.YELLOW,
        )
        typer.echo("Only rss and registry sources can be test-fetched right now.")
        typer.echo("You can still add evidence from it by hand with `semi-intel evidence add`.")
        raise typer.Exit(0)

    typer.echo(f"Fetching {target_url} ...")
    try:
        if source_type == SourceType.REGISTRY:
            plugin = PciIdsSourcePlugin(url=target_url)
        else:
            plugin = RSSSourcePlugin(name=label or "test", feed_url=target_url)
        items = list(plugin.fetch())
    except Exception as exc:
        typer.secho(f"Could not fetch or read this source: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.secho(f"Success -- found {len(items)} item(s). Nothing was saved.", fg=typer.colors.GREEN)
    for item in items[:5]:
        typer.echo(f"  - {item.title}")
    if len(items) > 5:
        typer.echo(f"  ...and {len(items) - 5} more")


# --- reindex -----------------------------------------------------------------


@app.command("reindex")
def reindex() -> None:
    """Re-checks everything you've already collected against your open
    claims, in case new evidence or a newly created claim produced matches
    that didn't exist before. Never changes a claim automatically -- new
    matches always need your review (`semi-intel suggest list`)."""
    from semi_intel.claim_engine.suggestion_service import SuggestionService
    from semi_intel.editorial.service import EditorialDiscoveryService

    session = _session()
    try:
        result = SuggestionService(session).run()
        editorial = EditorialDiscoveryService(session).backfill()
        session.commit()
    except Exception as exc:
        typer.secho(f"Something went wrong: {exc}", fg=typer.colors.RED)
        typer.echo("Run `semintel doctor` to check your setup.")
        raise typer.Exit(1)
    finally:
        session.close()

    typer.echo(f"Scanned {result.evidence_scanned} evidence item(s).")
    typer.echo(
        f"Editorial inbox: {editorial.stories_created} new story/stories, "
        f"{editorial.matches_created} topic match(es)."
    )
    if result.suggestions_created:
        typer.secho(
            f"Found {result.suggestions_created} new possible match(es) -- review with `semi-intel suggest list`.",
            fg=typer.colors.GREEN,
        )
    else:
        typer.echo("No new matches found.")


# --- backup -----------------------------------------------------------------


@app.command("backup")
def backup() -> None:
    """Compatibility alias for `semintel backups create`."""
    db_url = _current_db_url()
    if not db_url.startswith("sqlite:///"):
        typer.secho(
            "That database isn't a local SQLite file; local verified backups require one.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(1)
    db_path = Path(db_url[len("sqlite:///"):])
    if not db_path.exists():
        typer.secho(f"No database file found at {db_path} yet -- nothing to back up.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    session = _session()
    try:
        record = BackupService(session).create()
    except Exception as exc:
        typer.secho(f"Backup failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1)
    finally:
        session.close()
    typer.secho(f"Verified backup created: {record.path}", fg=typer.colors.GREEN)
    typer.echo(f"SHA-256: {record.sha256}")


# --- Phase 9 automation and recovery ---------------------------------------


@automation_app.command("status")
def automation_status(json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        payload = OperationalScheduler(session).status()
        session.commit()
    finally:
        session.close()
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(f"Automation: {'enabled' if payload['enabled'] else 'disabled'}")
    typer.echo(f"Timezone: {payload['timezone']}")
    typer.echo(f"Last heartbeat: {payload['last_heartbeat'] or 'none yet'}")
    typer.echo(f"Active jobs: {len(payload['active_leases'])}")
    for name, value in payload["next_runs"].items():
        typer.echo(f"Next {name.replace('_', ' ')}: {value}")


@automation_app.command("enable")
def automation_enable() -> None:
    session = _session()
    try:
        settings = OperationalScheduler(session).settings()
        settings.scheduler_enabled = True
        session.commit()
    finally:
        session.close()
    typer.secho("Automation enabled. Install or enable the Windows scheduled task to invoke cycles.", fg=typer.colors.GREEN)


@automation_app.command("disable")
def automation_disable() -> None:
    session = _session()
    try:
        settings = OperationalScheduler(session).settings()
        settings.scheduler_enabled = False
        session.commit()
    finally:
        session.close()
    typer.secho("Automation disabled. Manual commands still work.", fg=typer.colors.YELLOW)


@automation_app.command("run-now")
def automation_run_now(
    job_type: OperationalJobType = typer.Option(OperationalJobType.PIPELINE, "--job"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    session = _session()
    try:
        job = OperationalScheduler(session).run_job(
            job_type, trigger=OperationalTriggerType.MANUAL_CLI
        )
        payload = OperationalScheduler.job_dict(job)
    finally:
        session.close()
    typer.echo(json.dumps(payload, indent=2) if json_output else (
        f"{payload['job_type'].replace('_', ' ').title()}: {payload['status']} — {payload['summary']}"
    ))
    if payload["status"] == "failed":
        raise typer.Exit(1)


@automation_app.command("cycle")
def automation_cycle(json_output: bool = typer.Option(False, "--json")) -> None:
    """Run one bounded scheduler cycle and exit; intended for Task Scheduler."""
    session = _session()
    try:
        jobs = OperationalScheduler(session).cycle()
        payload = [OperationalScheduler.job_dict(job) for job in jobs]
    finally:
        session.close()
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    elif not payload:
        typer.echo("No jobs were due (or automation is disabled).")
    else:
        for job in payload:
            typer.echo(f"{job['job_type']}: {job['status']} — {job['summary']}")


@automation_app.command("jobs")
def automation_jobs(limit: int = typer.Option(30, min=1, max=500), json_output: bool = typer.Option(False, "--json")) -> None:
    from semi_intel.domain.models import OperationalJobRun
    session = _session()
    try:
        rows = list(session.scalars(select(OperationalJobRun).order_by(
            OperationalJobRun.started_at.desc()
        ).limit(limit)))
        payload = [OperationalScheduler.job_dict(row) for row in rows]
    finally:
        session.close()
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    elif not payload:
        typer.echo("No operational jobs have run yet.")
    else:
        for row in payload:
            typer.echo(f"#{row['id']} {row['job_type']} — {row['status']}: {row['summary']}")


@automation_app.command("retry")
def automation_retry(job_id: int) -> None:
    session = _session()
    try:
        try:
            job = OperationalScheduler(session).retry(job_id)
        except ValueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(1)
    finally:
        session.close()
    typer.echo(f"Retry #{job.id}: {job.status.value} — {job.summary}")


@automation_app.command("install-task")
def automation_install_task(
    apply: bool = typer.Option(False, "--apply", help="Actually create/update the Windows task."),
    yes: bool = typer.Option(False, "--yes", help="Confirm without an interactive prompt."),
) -> None:
    session = _session()
    try:
        interval = OperationalScheduler(session).settings().pipeline_interval_minutes
    finally:
        session.close()
    executable = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0])
    command = install_command(executable, Path.cwd(), interval_minutes=interval)
    typer.echo("Proposed Windows Task Scheduler command:")
    typer.echo(subprocess.list2cmdline(command))
    if not apply:
        typer.echo("Dry run only. Re-run with --apply to create or update the task.")
        return
    if not getattr(sys, "frozen", False):
        typer.secho("Task installation requires the packaged semintel.exe.", fg=typer.colors.RED)
        raise typer.Exit(1)
    if not yes and not typer.confirm("Create or update this Windows task?"):
        raise typer.Abort()
    result = execute_task_command(command)
    if result.returncode:
        typer.secho(result.stderr or result.stdout, fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.secho("Windows scheduled task installed.", fg=typer.colors.GREEN)


@automation_app.command("remove-task")
def automation_remove_task(
    apply: bool = typer.Option(False, "--apply"), yes: bool = typer.Option(False, "--yes"),
) -> None:
    command = remove_command()
    typer.echo("Proposed Windows Task Scheduler command:")
    typer.echo(subprocess.list2cmdline(command))
    if not apply:
        typer.echo("Dry run only. Re-run with --apply to remove the task.")
        return
    if not yes and not typer.confirm("Remove this Windows task?"):
        raise typer.Abort()
    result = execute_task_command(command)
    if result.returncode:
        typer.secho(result.stderr or result.stdout, fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.secho("Windows scheduled task removed.", fg=typer.colors.GREEN)


@app.command("health")
def operational_health(json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        report = HealthService(session).report()
        session.commit()
    finally:
        session.close()
    if json_output:
        typer.echo(json.dumps(report, indent=2))
        return
    typer.echo(f"Overall: {report['overall'].replace('_', ' ')}")
    typer.echo(report["summary"])
    for issue in report["issues"]:
        typer.echo(f"- {issue['explanation']} Next: {issue['recommended_action']}")


@backups_app.command("create")
def backups_create(json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        record = BackupService(session).create()
        payload = {"id": record.id, "path": record.path, "sha256": record.sha256,
                   "size_bytes": record.size_bytes, "revision": record.alembic_revision}
    finally:
        session.close()
    typer.echo(json.dumps(payload, indent=2) if json_output else (
        f"Verified backup: {record.path}\nSHA-256: {record.sha256}"
    ))


@backups_app.command("list")
def backups_list(json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        rows = BackupService(session).list()
        payload = [{"id": row.id, "filename": row.filename, "status": row.status.value,
                    "size_bytes": row.size_bytes, "sha256": row.sha256,
                    "created_at": row.created_at.isoformat()} for row in rows]
    finally:
        session.close()
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    elif not payload:
        typer.echo("No managed backups yet.")
    else:
        for row in payload:
            typer.echo(f"#{row['id']} {row['filename']} — {row['status']}, {row['size_bytes']} bytes")


@backups_app.command("verify")
def backups_verify(path: Path, json_output: bool = typer.Option(False, "--json")) -> None:
    session = _session()
    try:
        result = BackupService(session).verify(path)
    except Exception as exc:
        typer.secho(f"Backup verification failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1)
    finally:
        session.close()
    typer.echo(json.dumps(result, indent=2) if json_output else (
        f"Backup verified: integrity {result['integrity_result']}, revision {result['alembic_revision']}."
    ))


@backups_app.command("prune")
def backups_prune(
    apply: bool = typer.Option(False, "--apply"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    session = _session()
    try:
        service = BackupService(session)
        targets = service.prune(dry_run=True)
        if not targets:
            typer.echo("No managed backups are eligible for pruning.")
            return
        typer.echo("Managed backups eligible for pruning:")
        for path in targets:
            typer.echo(f"- {path}")
        if not apply:
            typer.echo("Dry run only. Re-run with --apply to prune these files.")
            return
        if not yes and not typer.confirm("Delete only the managed backups listed above?"):
            raise typer.Abort()
        service.prune(dry_run=False)
        typer.secho(f"Pruned {len(targets)} managed backup(s).", fg=typer.colors.GREEN)
    finally:
        session.close()


@backups_app.command("restore")
def backups_restore(
    path: Path, apply: bool = typer.Option(False, "--apply"),
    yes: bool = typer.Option(False, "--yes"), json_output: bool = typer.Option(False, "--json"),
) -> None:
    session = _session()
    try:
        service = BackupService(session)
        preview = service.restore(path, dry_run=True)
        if not apply:
            typer.echo(json.dumps(preview, indent=2) if json_output else (
                "Restore dry run passed. The active database was not modified."
            ))
            return
        if not yes and not typer.confirm(
            "Restore this verified backup? A safety backup will be created first."
        ):
            raise typer.Abort()
        result = service.restore(path, dry_run=False)
        typer.echo(json.dumps(result, indent=2) if json_output else "Restore completed and validated.")
    finally:
        session.close()


@backups_app.command("rehearse")
def backups_rehearse(path: Path, json_output: bool = typer.Option(False, "--json")) -> None:
    """Prove a verified backup would actually restore and load, without
    touching the live database. Copies the backup to a throwaway temp file,
    opens it with a real engine, and queries it through the application's
    own models -- catching schema drift that a raw integrity check misses."""
    session = _session()
    try:
        service = BackupService(session)
        report = service.rehearse(path)
    finally:
        session.close()
    if json_output:
        typer.echo(json.dumps(report, indent=2))
        return
    if report["passed"]:
        typer.secho(f"Rehearsal passed: {path}", fg=typer.colors.GREEN)
        typer.echo(f"Stamped revision: {report['schema_revision']}")
        if report["schema_up_to_date"] is False:
            typer.secho(
                f"This backup predates the installed app (expected head {report['expected_head']}). "
                "Run `semintel update` after restoring it.",
                fg=typer.colors.YELLOW,
            )
        for table, count in (report["orm_record_counts"] or {}).items():
            typer.echo(f"  {table}: {count}")
    else:
        typer.secho(f"Rehearsal failed: {report['error']}", fg=typer.colors.RED)
        raise typer.Exit(1)


from semi_intel.paths import get_diagnostics_dir

@diagnostics_app.command("create")
def diagnostics_create(
    output_directory: Optional[Path] = typer.Option(None, "--output"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    session = _session()
    try:
        out_dir = output_directory or get_diagnostics_dir()
        result = DiagnosticsService(session).create(out_dir)
    finally:
        session.close()
    typer.echo(json.dumps(result, indent=2) if json_output else (
        f"Privacy-bounded diagnostics created: {result['path']}\nSHA-256: {result['sha256']}"
    ))


if __name__ == "__main__":
    app()
