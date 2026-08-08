"""CLI-level tests for `semi-intel radar ...`. Uses the replay provider so
this suite never touches the network -- consistent with test_cli_pipeline.py
and test_cli_ingest.py."""

from __future__ import annotations

from typer.testing import CliRunner

from semi_intel.cli import app
from semi_intel.db import get_engine, get_sessionmaker
from semi_intel.domain.enums import SourceType
from semi_intel.domain.models import Source

runner = CliRunner()


def test_radar_status_on_clean_db_shows_safe_defaults(cli_env):
    runner.invoke(app, ["init-db"])

    r = runner.invoke(app, ["radar", "status"])

    assert r.exit_code == 0, r.output
    assert "Collection enabled: False" in r.output
    assert "X provider enabled: False" in r.output
    assert "No provider runs yet." in r.output


def test_radar_collect_with_no_polling_sources_is_a_safe_no_op(cli_env):
    runner.invoke(app, ["init-db"])

    r = runner.invoke(app, ["radar", "collect"])

    assert r.exit_code == 0, r.output
    assert "No due sources to collect." in r.output


def test_radar_collect_by_source_id_uses_replay_provider(cli_env, monkeypatch):
    runner.invoke(app, ["init-db"])
    engine = get_engine()
    session = get_sessionmaker(engine)()
    source = Source(
        name="Replay Source", type=SourceType.SOCIAL, provider="replay", provider_key="ian",
        enabled=True, polling_enabled=True,
    )
    session.add(source)
    session.commit()
    source_id = source.id
    session.close()

    import semi_intel.signals.collection as collection_module
    from semi_intel.signals.providers.replay import ReplayProvider

    fixture_registry = {"replay": ReplayProvider(name="replay", fixtures={
        "ian": [{"external_id": "1", "posted_at": "2026-01-01T00:00:00Z", "text": "leak", "author": "ian"}],
    })}
    monkeypatch.setattr(collection_module, "default_registry", lambda: fixture_registry)

    r = runner.invoke(app, ["radar", "collect", "--source-id", str(source_id)])

    assert r.exit_code == 0, r.output
    assert "ok" in r.output.lower()
    assert "1 item" in r.output


def test_radar_provider_health_reports_error_state(cli_env):
    runner.invoke(app, ["init-db"])
    engine = get_engine()
    session = get_sessionmaker(engine)()
    source = Source(
        name="Broken Source", type=SourceType.SOCIAL, provider="nonexistent", provider_key="x",
        enabled=True, polling_enabled=True,
    )
    session.add(source)
    session.commit()
    session.close()

    runner.invoke(app, ["radar", "collect"])  # attempts every due source, records the failure
    r = runner.invoke(app, ["radar", "provider-health"])

    assert r.exit_code == 0, r.output
    assert "Broken Source" in r.output
    assert "ERROR" in r.output
