"""Tests for `semintel` -- the operator-friendly CLI (semi_intel/operator.py).

Unlike the other CLI tests, these commands are deliberately sensitive to
the current working directory (that's the whole "remembers where your data
is" feature -- see OPERATOR_GUIDE.md). Every test here runs inside its own
scratch directory (`isolated_cwd`) so nothing leaks into the real repo or
between tests. No live network -- RSS/pci.ids fetches are monkeypatched
the same way test_pipeline_service.py and test_cli_ingest.py do it.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import feedparser
import pytest
import uvicorn
from typer.testing import CliRunner

import semi_intel.ingestion.plugins.pci_ids_plugin as pci_ids_plugin_module
import semi_intel.ingestion.plugins.rss_plugin as rss_plugin_module
from semi_intel.operator import app

runner = CliRunner()
FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"


@pytest.fixture()
def isolated_cwd(tmp_path, monkeypatch):
    """A scratch directory with no SEMI_INTEL_DB_URL inherited from
    anywhere -- simulates a person opening a brand new terminal in a fresh
    folder, which is exactly the scenario semintel install is for."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEMI_INTEL_DB_URL", raising=False)
    return tmp_path


def _patch_fetchers(monkeypatch):
    xml = (FIXTURES / "sample_feed.xml").read_text()
    pci_text = (FIXTURES / "sample_pci_ids.txt").read_text()
    monkeypatch.setattr(rss_plugin_module, "_default_fetch", lambda url: feedparser.parse(xml))
    monkeypatch.setattr(pci_ids_plugin_module, "_default_fetch", lambda url: pci_text)


# --- install -----------------------------------------------------------------


def test_install_creates_database_and_config(isolated_cwd):
    r = runner.invoke(app, ["install"])

    assert r.exit_code == 0, r.output
    assert (isolated_cwd / "semi_intel.db").exists()
    assert (isolated_cwd / "semintel.config.json").exists()
    assert (isolated_cwd / "backups").is_dir()

    config = json.loads((isolated_cwd / "semintel.config.json").read_text())
    assert config["data_dir"] == str(isolated_cwd)
    assert "sqlite:///" in config["db_url"]


def test_install_with_data_dir_flag(isolated_cwd):
    target = isolated_cwd / "elsewhere"
    r = runner.invoke(app, ["install", "--data-dir", str(target)])

    assert r.exit_code == 0, r.output
    assert (target / "semi_intel.db").exists()
    # config.json still lands in the CURRENT folder (that's what future
    # commands run from here will read) even though the DATA lives at --data-dir
    config = json.loads((isolated_cwd / "semintel.config.json").read_text())
    assert config["data_dir"] == str(target)


def test_install_is_idempotent(isolated_cwd):
    first = runner.invoke(app, ["install"])
    second = runner.invoke(app, ["install"])

    assert first.exit_code == 0
    assert second.exit_code == 0, second.output
    # still exactly one config file, still pointed at the same place
    config = json.loads((isolated_cwd / "semintel.config.json").read_text())
    assert config["data_dir"] == str(isolated_cwd)


# --- status / config persistence across "process boundaries" ---------------


def test_status_reads_db_url_from_config_file_across_invocations(isolated_cwd, monkeypatch):
    """The whole point of semintel.config.json: a SECOND, separate
    invocation with no SEMI_INTEL_DB_URL set (simulating a brand new
    terminal/process) should still find the right database, purely from
    the config file `install` wrote."""
    runner.invoke(app, ["install"])
    monkeypatch.delenv("SEMI_INTEL_DB_URL", raising=False)

    r = runner.invoke(app, ["status"])

    assert r.exit_code == 0, r.output
    assert "Sources:" in r.output
    assert str(isolated_cwd) in r.output  # the db location line


def test_status_shows_counts(isolated_cwd, monkeypatch):
    _patch_fetchers(monkeypatch)
    runner.invoke(app, ["install"])
    runner.invoke(app, ["add-source", "--name", "VideoCardz", "--url", "https://example.com/rss"])
    runner.invoke(app, ["run", "--skip-pci-ids"])

    r = runner.invoke(app, ["status"])

    assert r.exit_code == 0, r.output
    assert "Sources:         1" in r.output
    assert "Evidence:        2" in r.output  # sample_feed.xml has 2 entries


# --- doctor -----------------------------------------------------------------


def test_doctor_all_pass_after_install(isolated_cwd):
    runner.invoke(app, ["install"])

    r = runner.invoke(app, ["doctor", "--skip-network"])

    assert r.exit_code == 0, r.output
    assert "[FAIL]" not in r.output
    assert "Everything looks good." in r.output


def test_doctor_flags_schema_before_migrations_applied(isolated_cwd, monkeypatch):
    """Without ever running `install` (or `db upgrade`), the database gets
    created lazily via create_all() the first time any command opens a
    session -- but that bypasses Alembic entirely, so there's no migration
    history. doctor should catch and explain this, not just say PASS."""
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{isolated_cwd / 'semi_intel.db'}")

    r = runner.invoke(app, ["doctor", "--skip-network"])

    assert "schema is up to date" in r.output.lower()
    assert "[FAIL]" in r.output
    assert r.exit_code == 1


# --- add-source -----------------------------------------------------------------


def test_add_source_with_flags(isolated_cwd):
    runner.invoke(app, ["install"])

    r = runner.invoke(
        app,
        ["add-source", "--name", "VideoCardz", "--url", "https://example.com/rss", "--type", "rss", "--trust", "0.7"],
    )

    assert r.exit_code == 0, r.output
    assert "Added source #1: VideoCardz" in r.output


def test_add_source_interactive_prompts(isolated_cwd):
    runner.invoke(app, ["install"])

    r = runner.invoke(app, ["add-source"], input="Manual Tip\n\n\n")

    assert r.exit_code == 0, r.output
    assert "Added source #1: Manual Tip" in r.output


def test_add_source_rejects_duplicate_name(isolated_cwd):
    runner.invoke(app, ["install"])
    runner.invoke(app, ["add-source", "--name", "VideoCardz", "--url", "https://example.com/rss"])

    r = runner.invoke(app, ["add-source", "--name", "VideoCardz", "--url", "https://example.com/other"])

    assert r.exit_code == 1
    assert "already exists" in r.output


def test_add_source_rejects_unknown_type(isolated_cwd):
    runner.invoke(app, ["install"])

    r = runner.invoke(
        app, ["add-source", "--name", "Bad", "--url", "https://example.com/rss", "--type", "not-a-type"]
    )

    assert r.exit_code == 1
    assert "isn't a known source type" in r.output


# --- test-source -----------------------------------------------------------------


def test_test_source_manual_source_has_no_url(isolated_cwd):
    runner.invoke(app, ["install"])
    # --url "" (not omitted) so this stays non-interactive -- omitting --url
    # entirely triggers add-source's guided prompt, which would block
    # waiting on stdin in a non-interactive test run.
    runner.invoke(app, ["add-source", "--name", "Manual Tip", "--url", "", "--type", "manual"])

    r = runner.invoke(app, ["test-source", "Manual Tip"])

    assert r.exit_code == 0, r.output
    assert "nothing to test" in r.output


def test_test_source_unregistered_name(isolated_cwd):
    runner.invoke(app, ["install"])

    r = runner.invoke(app, ["test-source", "Nope"])

    assert r.exit_code == 1
    assert "No source named" in r.output


def test_test_source_fetches_registered_rss_source(isolated_cwd, monkeypatch):
    _patch_fetchers(monkeypatch)
    runner.invoke(app, ["install"])
    runner.invoke(app, ["add-source", "--name", "VideoCardz", "--url", "https://example.com/rss"])

    r = runner.invoke(app, ["test-source", "VideoCardz"])

    assert r.exit_code == 0, r.output
    assert "found 2 item(s)" in r.output
    assert "Nothing was saved" in r.output


def test_test_source_via_url_flag_without_registering(isolated_cwd, monkeypatch):
    _patch_fetchers(monkeypatch)
    runner.invoke(app, ["install"])

    r = runner.invoke(app, ["test-source", "--url", "https://example.com/rss"])

    assert r.exit_code == 0, r.output
    assert "found 2 item(s)" in r.output


def test_test_source_reports_fetch_errors_clearly(isolated_cwd, monkeypatch):
    def _boom(url):
        raise TimeoutError("simulated network timeout")

    monkeypatch.setattr(rss_plugin_module, "_default_fetch", _boom)
    runner.invoke(app, ["install"])
    runner.invoke(app, ["add-source", "--name", "Dead Feed", "--url", "https://example.com/dead"])

    r = runner.invoke(app, ["test-source", "Dead Feed"])

    assert r.exit_code == 1
    assert "Could not fetch or read" in r.output
    assert "simulated network timeout" in r.output


def test_test_source_skips_types_without_a_fetcher(isolated_cwd):
    runner.invoke(app, ["install"])
    runner.invoke(app, ["add-source", "--name", "Golden Pig", "--url", "https://example.com/profile", "--type", "social"])

    r = runner.invoke(app, ["test-source", "Golden Pig"])

    assert r.exit_code == 0, r.output
    assert "doesn't have an automatic fetcher" in r.output


# --- run -----------------------------------------------------------------


def test_run_with_no_sources_registered(isolated_cwd):
    runner.invoke(app, ["install"])

    r = runner.invoke(app, ["run", "--skip-pci-ids"])

    assert r.exit_code == 0, r.output
    assert "No sources registered" in r.output


def test_run_fetches_and_reports_per_source_results(isolated_cwd, monkeypatch):
    _patch_fetchers(monkeypatch)
    runner.invoke(app, ["install"])
    runner.invoke(app, ["add-source", "--name", "VideoCardz", "--url", "https://example.com/rss"])

    r = runner.invoke(app, ["run", "--skip-pci-ids"])

    assert r.exit_code == 0, r.output
    assert "VideoCardz: 2 new" in r.output


def test_run_reports_failures_without_aborting(isolated_cwd, monkeypatch):
    _patch_fetchers(monkeypatch)

    def _boom(url):
        raise TimeoutError("simulated network timeout")

    runner.invoke(app, ["install"])
    runner.invoke(app, ["add-source", "--name", "Dead Feed", "--url", "https://example.com/dead"])
    runner.invoke(app, ["add-source", "--name", "VideoCardz", "--url", "https://example.com/rss"])

    def selective_fetch(url):
        if url == "https://example.com/dead":
            raise TimeoutError("simulated network timeout")
        return feedparser.parse((FIXTURES / "sample_feed.xml").read_text())

    monkeypatch.setattr(rss_plugin_module, "_default_fetch", selective_fetch)

    r = runner.invoke(app, ["run", "--skip-pci-ids"])

    assert r.exit_code == 1  # failures present -> non-zero, but...
    assert "✗ Dead Feed" in r.output
    assert "✓ VideoCardz: 2 new" in r.output  # ...the healthy source still ran


# --- reindex -----------------------------------------------------------------


def test_reindex_with_no_evidence(isolated_cwd):
    runner.invoke(app, ["install"])

    r = runner.invoke(app, ["reindex"])

    assert r.exit_code == 0, r.output
    assert "Scanned 0 evidence item(s)" in r.output
    assert "No new matches" in r.output


def test_reindex_reports_new_matches(isolated_cwd, monkeypatch):
    from semi_intel.domain.enums import EntityType
    from semi_intel.domain.models import Entity
    from semi_intel.repository.repositories import ClaimRepository

    _patch_fetchers(monkeypatch)
    runner.invoke(app, ["install"])
    runner.invoke(app, ["add-source", "--name", "VideoCardz", "--url", "https://example.com/rss"])
    runner.invoke(app, ["run", "--skip-pci-ids"])

    import os

    from semi_intel.db import get_engine, get_sessionmaker

    engine = get_engine(os.environ["SEMI_INTEL_DB_URL"])
    session = get_sessionmaker(engine)()
    entity = Entity(type=EntityType.PRODUCT, name="RTX 4090", aliases="[]", attributes="{}")
    session.add(entity)
    session.commit()
    ClaimRepository(session).create(statement="RTX 4090 rumor claim", subject_entity_id=entity.id)
    session.close()

    r = runner.invoke(app, ["reindex"])

    assert r.exit_code == 0, r.output
    assert "Scanned 2 evidence item(s)" in r.output


# --- backup -----------------------------------------------------------------


def test_backup_creates_distinct_files_for_rapid_successive_calls(isolated_cwd):
    """Regression test: backups used to be named with second-precision
    timestamps, so two backups run within the same second silently
    overwrote each other while both still reported success."""
    runner.invoke(app, ["install"])

    first = runner.invoke(app, ["backup"])
    second = runner.invoke(app, ["backup"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output

    backups = list((isolated_cwd / "backups").glob("semi-intel-backup-*.sqlite3"))
    assert len(backups) == 2, f"expected 2 distinct backup files, got {[b.name for b in backups]}"


def test_backup_fails_gracefully_with_no_database_yet(isolated_cwd, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{isolated_cwd / 'nope.db'}")

    r = runner.invoke(app, ["backup"])

    assert r.exit_code == 1
    assert "nothing to back up" in r.output


def test_backup_fails_gracefully_for_non_sqlite_url(isolated_cwd, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_DB_URL", "postgresql://user:pass@host/db")

    r = runner.invoke(app, ["backup"])

    assert r.exit_code == 1
    assert "isn't a local file" in r.output or "isn't a local" in r.output


def test_backups_rehearse_passes_and_reports_schema_currency(isolated_cwd):
    """`backups rehearse` must go further than integrity_check: it opens the
    backup through a real engine and the application's own models, proving
    it would actually load if restored -- not just that sqlite3 can see it."""
    runner.invoke(app, ["install"])
    runner.invoke(app, ["backup"])
    backup_path = next((isolated_cwd / "backups").glob("semi-intel-backup-*.sqlite3"))

    r = runner.invoke(app, ["backups", "rehearse", str(backup_path), "--json"])

    assert r.exit_code == 0, r.output
    report = json.loads(r.output)
    assert report["passed"] is True
    assert report["schema_up_to_date"] is True
    assert report["orm_record_counts"]["sources"] == 0
    assert report["error"] is None


def test_backups_rehearse_fails_gracefully_on_a_corrupt_file(isolated_cwd):
    runner.invoke(app, ["install"])
    runner.invoke(app, ["backup"])
    backup_path = next((isolated_cwd / "backups").glob("semi-intel-backup-*.sqlite3"))
    backup_path.write_bytes(b"not a real sqlite file")

    r = runner.invoke(app, ["backups", "rehearse", str(backup_path)])

    assert r.exit_code == 1
    assert "Rehearsal failed" in r.output


def test_backup_commands_follow_database_directory_across_working_directories(tmp_path, monkeypatch):
    """Dashboard/CLI entry points must agree on where a relative `backups`
    setting lives.  The database directory is stable; process cwd is not."""
    data_dir = tmp_path / "data"
    first_cwd = tmp_path / "first invocation"
    second_cwd = tmp_path / "second invocation"
    data_dir.mkdir()
    first_cwd.mkdir()
    second_cwd.mkdir()
    database = data_dir / "semi_intel.db"
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{database}")

    monkeypatch.chdir(first_cwd)
    assert runner.invoke(app, ["update"]).exit_code == 0
    created = runner.invoke(app, ["backups", "create", "--json"])
    assert created.exit_code == 0, created.output
    backup_path = Path(json.loads(created.output)["path"])
    assert backup_path.parent == (data_dir / "backups").resolve()

    monkeypatch.chdir(second_cwd)
    rehearsed = runner.invoke(app, ["backups", "rehearse", str(backup_path), "--json"])
    assert rehearsed.exit_code == 0, rehearsed.output
    assert json.loads(rehearsed.output)["passed"] is True


# --- update -----------------------------------------------------------------


def test_update_reports_up_to_date_after_install(isolated_cwd):
    runner.invoke(app, ["install"])

    r = runner.invoke(app, ["update"])

    assert r.exit_code == 0, r.output
    assert "already up to date" in r.output


def test_update_applies_pending_schema_changes(isolated_cwd, monkeypatch):
    # Simulate a database that exists (via create_all, same as any lazily
    # opened session) but was never actually migrated with Alembic --
    # `update` should notice and fix it, same as `semintel doctor` flags it.
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{isolated_cwd / 'semi_intel.db'}")
    runner.invoke(app, ["status"])  # touches the DB, lazily creates tables via create_all

    r = runner.invoke(app, ["update"])

    assert r.exit_code == 0, r.output
    # This exact scenario (create_all ran first, no Alembic history yet)
    # takes the stamp fallback, not a real upgrade -- see
    # _upgrade_or_stamp_to_head()'s docstring in operator.py.
    assert "marked it as up to date" in r.output


# --- gui -----------------------------------------------------------------
# Not the actual server startup (uvicorn.run blocks forever, same reason
# tests/test_cli_web.py doesn't exercise `semi-intel web serve` either) --
# just confirms the command is registered, its help text renders (and
# isn't mangled by rich swallowing the "[web]" in the extras name -- see
# the fix in this same commit), and that --no-browser/--host/--port exist.


def test_gui_is_registered_with_working_help(isolated_cwd):
    r = runner.invoke(app, ["gui", "--help"])
    assert r.exit_code == 0, r.output
    assert "--host" in r.output
    assert "--port" in r.output
    assert "--no-browser" in r.output
    assert ".[web]" in r.output  # regression check: rich markup must not eat the brackets


# --- bare invocation (double-clicking semintel.exe) -------------------------
# Double-clicking the .exe in File Explorer runs it with zero arguments --
# there's no terminal to type a subcommand into. These confirm that case
# sets up the database (same as `install`, idempotent) and opens the
# dashboard (same as `gui`) instead of printing a usage screen that would
# flash and close, without ever actually starting a real server or opening
# a real browser tab during the test run: uvicorn.run and threading.Timer
# are replaced with fakes, the same trick used to avoid a real network
# fetch in the RSS/pci.ids tests above.


@pytest.fixture()
def fake_server(monkeypatch):
    """Replaces uvicorn.run with a recorder and threading.Timer with a
    no-op, so a bare `semintel` invocation exercises its real setup logic
    (install) without blocking forever in a real server or scheduling a
    real webbrowser.open() call a second after the test has already ended."""
    calls = []

    def _fake_run(app_obj, host, port):
        calls.append({"host": host, "port": port})

    class _NoTimer:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(uvicorn, "run", _fake_run)
    monkeypatch.setattr(threading, "Timer", _NoTimer)
    return calls


def test_bare_invocation_sets_up_database_and_opens_dashboard(isolated_cwd, fake_server):
    r = runner.invoke(app, [])

    assert r.exit_code == 0, r.output
    assert (isolated_cwd / "semi_intel.db").exists()
    assert (isolated_cwd / "semintel.config.json").exists()
    assert fake_server == [{"host": "127.0.0.1", "port": 8000}]
    assert "Opening the dashboard" in r.output


def test_bare_invocation_is_idempotent_when_already_installed(isolated_cwd, fake_server):
    runner.invoke(app, ["install"])

    r = runner.invoke(app, [])

    assert r.exit_code == 0, r.output
    assert fake_server == [{"host": "127.0.0.1", "port": 8000}]


@pytest.mark.parametrize(
    "raised",
    [
        OSError("[Errno 98] Address already in use"),
        # This is what a real bind failure actually looks like: uvicorn's
        # own Server.startup() catches the OSError itself, logs it, and
        # calls sys.exit() directly rather than letting the OSError
        # propagate -- see semi_intel/operator.py's gui() for why both
        # exception types are caught, confirmed against a real double
        # launch of the frozen .exe, not just guessed at.
        SystemExit(3),
    ],
)
def test_bare_invocation_reports_port_in_use_without_a_traceback(isolated_cwd, monkeypatch, raised):
    def _raise(app_obj, host, port):
        raise raised

    class _NoTimer:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(uvicorn, "run", _raise)
    monkeypatch.setattr(threading, "Timer", _NoTimer)

    r = runner.invoke(app, [])

    assert r.exit_code == 1
    assert r.exception is None or isinstance(r.exception, SystemExit)  # no raw traceback
    assert "already using port" in r.output


def test_help_still_lists_every_command_and_does_not_launch_the_server(isolated_cwd, monkeypatch):
    """--help must keep working exactly as before -- Click handles it ahead
    of our no-args-means-gui callback, so this should never touch uvicorn
    at all."""
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append(1))

    r = runner.invoke(app, ["--help"])

    assert r.exit_code == 0, r.output
    for command in ["install", "run", "status", "doctor", "update", "add-source", "test-source", "reindex", "backup", "gui"]:
        assert command in r.output
    assert calls == []
