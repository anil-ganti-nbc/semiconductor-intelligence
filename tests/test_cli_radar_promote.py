"""CLI-level tests for `semi-intel radar promote/promote-eligible`."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from typer.testing import CliRunner

from semi_intel.cli import app
from semi_intel.db import get_engine, get_sessionmaker
from semi_intel.domain.enums import SourceType
from semi_intel.domain.models import SignalCandidate, SignalItem, Source
from semi_intel.editorial.service import TopicService
from semi_intel.signals.analysis import analyze_signal_item
from semi_intel.signals.clustering import cluster_unclustered_items
from semi_intel.signals.scoring import rescore_active_candidates

runner = CliRunner()
BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


def _seed_candidate(cli_env):
    engine = get_engine()
    session = get_sessionmaker(engine)()
    TopicService(session).seed()
    session.commit()
    source = Source(name="VideoCardz", type=SourceType.SOCIAL, provider="rss")
    session.add(source)
    session.commit()
    item = SignalItem(
        source_id=source.id, provider="rss", external_id="1", raw_payload="{}",
        normalized_text="RTX 50 Super leak: 24GB VRAM confirmed.", content_hash="h1", posted_at=BASE,
    )
    session.add(item)
    session.commit()
    analyze_signal_item(session, item)
    session.commit()
    cluster_unclustered_items(session)
    session.commit()
    rescore_active_candidates(session)
    candidate_id = session.scalars(select(SignalCandidate)).first().id
    session.close()
    engine.dispose()
    return candidate_id


def test_radar_promote_by_id(cli_env):
    runner.invoke(app, ["init-db"])
    candidate_id = _seed_candidate(cli_env)

    r = runner.invoke(app, ["radar", "promote", str(candidate_id)])

    assert r.exit_code == 0, r.output
    assert "EditorialStory" in r.output


def test_radar_promote_unknown_candidate_fails_cleanly(cli_env):
    runner.invoke(app, ["init-db"])
    r = runner.invoke(app, ["radar", "promote", "9999"])
    assert r.exit_code == 1
    assert "No candidate" in r.output


def test_radar_promote_eligible_dry_run_lists_reasons(cli_env):
    runner.invoke(app, ["init-db"])
    _seed_candidate(cli_env)

    r = runner.invoke(app, ["radar", "promote-eligible"])

    assert r.exit_code == 0, r.output
    assert "automatic promotion disabled" in r.output


def test_radar_promote_eligible_apply_respects_settings(cli_env):
    runner.invoke(app, ["init-db"])
    candidate_id = _seed_candidate(cli_env)

    engine = get_engine()
    session = get_sessionmaker(engine)()
    from semi_intel.signals.promotion import get_promotion_settings
    settings = get_promotion_settings(session)
    settings.automatic_promotion_enabled = True
    settings.minimum_attention_score = 0.01
    # The fixed BASE fixture date is now well in the past relative to real
    # wall-clock time (the CLI path always uses the real "now") -- raise
    # the age gate so this test isn't rejected purely for being old.
    settings.maximum_candidate_age_hours = 10_000_000
    session.commit()
    session.close()

    r = runner.invoke(app, ["radar", "promote-eligible", "--no-dry-run"])

    assert r.exit_code == 0, r.output
    assert f"Promoted 1: [{candidate_id}]" in r.output
