"""CollectionService tests (brief section 24 "Provider tests"): cursor
advancement, cursor NOT advanced after failed persistence, duplicate
external ID handling, one provider/source failure not stopping others."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from semi_intel.domain.enums import ProviderRunStatus, SourceType
from semi_intel.domain.models import ProviderRun, SignalCollectionSettings, SignalItem, SignalMedia, Source
from semi_intel.signals.collection import CollectionService, stagger_delay_seconds
from semi_intel.signals.providers import ProviderUnavailable
from semi_intel.signals.providers.replay import ReplayProvider


def _make_source(session, *, name="Test Source", provider="replay", provider_key="ian", polling_enabled=True):
    src = Source(
        name=name, type=SourceType.SOCIAL, provider=provider, provider_key=provider_key,
        enabled=True, polling_enabled=polling_enabled,
    )
    session.add(src)
    session.commit()
    return src


def test_collect_source_persists_signal_items_and_advances_cursor(db_session):
    source = _make_source(db_session)
    registry = {"replay": ReplayProvider(name="replay", fixtures={
        "ian": [
            {"external_id": "1", "posted_at": "2026-01-01T00:00:00Z", "text": "RTX 50 Super leak", "author": "ian"},
            {"external_id": "2", "posted_at": "2026-01-02T00:00:00Z", "text": "follow up", "author": "ian"},
        ]
    })}
    service = CollectionService(db_session, registry=registry)

    run = service.collect_source(source)

    assert run.status == ProviderRunStatus.OK
    assert run.items_collected == 2
    assert run.duplicates_skipped == 0
    assert source.cursor == "2"
    assert source.error_state is None
    assert source.last_success_at is not None
    assert source.last_observed_item_at is not None

    items = db_session.scalars(select(SignalItem)).all()
    assert {i.external_id for i in items} == {"1", "2"}
    assert all(i.provider == "replay" for i in items)
    assert all(i.source_id == source.id for i in items)


def test_recollecting_is_idempotent_no_duplicates(db_session):
    source = _make_source(db_session)
    fixtures = {"ian": [
        {"external_id": "1", "posted_at": "2026-01-01T00:00:00Z", "text": "a", "author": "ian"},
    ]}
    registry = {"replay": ReplayProvider(name="replay", fixtures=fixtures)}
    service = CollectionService(db_session, registry=registry)

    first = service.collect_source(source)
    assert first.items_collected == 1

    # Same underlying fixture (as if the source were polled again while
    # nothing new was posted) -- cursor already at the newest id, so a real
    # provider would return zero items; simulate that directly too.
    second = service.collect_source(source)
    assert second.items_collected == 0
    assert second.duplicates_skipped == 0  # provider itself returned nothing past the cursor

    assert db_session.scalars(select(SignalItem)).all().__len__() == 1


def test_duplicate_external_id_across_runs_is_skipped_not_duplicated(db_session):
    """If a provider ever re-returns an already-collected external_id (e.g.
    cursor semantics differ slightly from a real deployment), the
    (provider, external_id) uniqueness must win -- no duplicate SignalItem,
    counted as a skipped duplicate instead of silently ignored."""
    source = _make_source(db_session)
    fixtures = {"ian": [
        {"external_id": "1", "posted_at": "2026-01-01T00:00:00Z", "text": "a", "author": "ian"},
    ]}
    registry = {"replay": ReplayProvider(name="replay", fixtures=fixtures)}
    service = CollectionService(db_session, registry=registry)
    service.collect_source(source)

    # Force a re-collect from scratch (as if the cursor were reset) to prove
    # the DB-level dedup guard, not just the provider's own cursor logic.
    source.cursor = None
    run = service.collect_source(source)

    assert run.items_collected == 0
    assert run.duplicates_skipped == 1
    assert db_session.scalars(select(SignalItem)).all().__len__() == 1


def test_media_is_persisted_with_pending_download_state(db_session):
    source = _make_source(db_session)
    registry = {"replay": ReplayProvider(name="replay", fixtures={
        "ian": [{
            "external_id": "1", "posted_at": "2026-01-01T00:00:00Z", "text": "a", "author": "ian",
            "media": [{"kind": "image", "url": "https://example.com/a.jpg"}],
        }]
    })}
    service = CollectionService(db_session, registry=registry)
    service.collect_source(source)

    media = db_session.scalars(select(SignalMedia)).all()
    assert len(media) == 1
    assert media[0].remote_url == "https://example.com/a.jpg"
    assert media[0].download_state.value == "pending"


def test_unregistered_provider_fails_gracefully_without_crashing(db_session):
    source = _make_source(db_session, provider="nonexistent", provider_key="whatever")
    service = CollectionService(db_session, registry={})

    run = service.collect_source(source)

    assert run.status == ProviderRunStatus.FAILED
    assert "nonexistent" in run.error
    assert source.error_state is not None
    # cursor untouched by a failed run
    assert source.cursor is None


def test_one_source_failure_does_not_stop_collect_due_sources(db_session):
    good = _make_source(db_session, name="Good", provider="replay", provider_key="ian")
    bad = _make_source(db_session, name="Bad", provider="nonexistent", provider_key="x")

    registry = {"replay": ReplayProvider(name="replay", fixtures={
        "ian": [{"external_id": "1", "posted_at": "2026-01-01T00:00:00Z", "text": "a", "author": "ian"}],
    })}
    service = CollectionService(db_session, registry=registry)

    summary = service.collect_due_sources()

    assert len(summary.runs) == 2
    statuses = {r.source_id: r.status for r in summary.runs}
    assert statuses[good.id] == ProviderRunStatus.OK
    assert statuses[bad.id] == ProviderRunStatus.FAILED
    assert summary.items_collected == 1
    assert len(summary.failures) == 1


def test_disabled_and_non_polling_sources_are_not_collected(db_session):
    _make_source(db_session, name="Disabled", provider="replay", provider_key="a", polling_enabled=False)
    manual = Source(name="Manual legacy", type=SourceType.RSS, provider="manual", enabled=True, polling_enabled=False)
    db_session.add(manual)
    db_session.commit()

    service = CollectionService(db_session, registry={"replay": ReplayProvider(name="replay")})
    summary = service.collect_due_sources()

    assert summary.runs == []


def test_cursor_not_advanced_when_normalize_all_fail_but_provider_errors(db_session):
    """A provider.collect() raising mid-flight must leave the cursor exactly
    as it was before this run -- the next run retries from the same point
    rather than silently skipping unprocessed items."""
    source = _make_source(db_session)
    source.cursor = "5"
    db_session.commit()

    class BrokenProvider:
        name = "replay"

        def collect(self, handle, cursor):
            raise RuntimeError("network exploded")

        def normalize(self, raw):  # pragma: no cover - unused
            raise NotImplementedError

        def validate(self, handle):  # pragma: no cover - unused
            raise NotImplementedError

    service = CollectionService(db_session, registry={"replay": BrokenProvider()})
    run = service.collect_source(source)

    assert run.status == ProviderRunStatus.FAILED
    assert source.cursor == "5"  # untouched
    assert db_session.scalars(select(ProviderRun)).all().__len__() == 1


def test_automatic_collection_disabled_by_default_no_network_activity(db_session):
    """Brief sections 17/18/26: a freshly migrated database must perform
    zero collection until an operator explicitly enables it. The pipeline's
    automatic path must respect that; a manual command must not."""
    source = _make_source(db_session)
    registry = {"replay": ReplayProvider(name="replay", fixtures={
        "ian": [{"external_id": "1", "posted_at": "2026-01-01T00:00:00Z", "text": "a", "author": "ian"}],
    })}
    service = CollectionService(db_session, registry=registry)

    automatic_summary = service.collect_due_sources(automatic=True)
    assert automatic_summary.runs == []
    assert db_session.scalars(select(SignalItem)).all() == []

    manual_summary = service.collect_due_sources(automatic=False)
    assert len(manual_summary.runs) == 1
    assert manual_summary.items_collected == 1


def test_automatic_collection_runs_once_enabled(db_session):
    source = _make_source(db_session)
    db_session.add(SignalCollectionSettings(id=1, collection_enabled=True))
    db_session.commit()
    registry = {"replay": ReplayProvider(name="replay", fixtures={
        "ian": [{"external_id": "1", "posted_at": "2026-01-01T00:00:00Z", "text": "a", "author": "ian"}],
    })}
    service = CollectionService(db_session, registry=registry)

    summary = service.collect_due_sources(automatic=True)

    assert len(summary.runs) == 1
    assert summary.items_collected == 1


def test_x_provider_requires_explicit_opt_in_regardless_of_automatic_flag(db_session):
    source = _make_source(db_session, provider="x", provider_key="handle")
    service = CollectionService(db_session, registry={})

    run_manual = service.collect_source(source)
    assert run_manual.status == ProviderRunStatus.FAILED
    assert "X collection is disabled" in run_manual.error

    # get_collection_settings() get-or-created the row during the call
    # above; update it in place rather than inserting a second id=1 row.
    settings = db_session.get(SignalCollectionSettings, 1)
    settings.collection_enabled = True
    settings.x_provider_enabled = True
    db_session.commit()
    source.cursor = None  # reset after the failed attempt above
    run_again = service.collect_source(source)
    # Still fails -- Playwright isn't installed in this environment -- but
    # for a DIFFERENT reason now (not the settings gate), proving the gate
    # itself was lifted correctly.
    assert run_again.status == ProviderRunStatus.FAILED
    assert "X collection is disabled" not in run_again.error


def test_priority_derived_poll_interval_skips_recently_collected_source(db_session):
    source = _make_source(db_session)
    source.priority = 3  # 20-minute interval
    source.last_success_at = dt.datetime.utcnow() - dt.timedelta(minutes=5)
    db_session.commit()

    service = CollectionService(db_session, registry={"replay": ReplayProvider(name="replay")})
    summary = service.collect_due_sources(automatic=False)

    assert summary.runs == []  # not due yet


def test_priority_derived_poll_interval_collects_when_overdue(db_session):
    source = _make_source(db_session)
    source.priority = 3  # 20-minute interval
    source.last_success_at = dt.datetime.utcnow() - dt.timedelta(minutes=25)
    db_session.commit()

    service = CollectionService(db_session, registry={"replay": ReplayProvider(name="replay", fixtures={"ian": []})})
    summary = service.collect_due_sources(automatic=False)

    assert len(summary.runs) == 1


def test_startup_stagger_delays_first_ever_collection(db_session):
    source = _make_source(db_session)
    loop_start = dt.datetime.utcnow()
    delay = stagger_delay_seconds(source.id, source.provider, 45)

    service = CollectionService(db_session, registry={"replay": ReplayProvider(name="replay", fixtures={"ian": []})})

    # Immediately at loop start: not yet past this source's deterministic
    # stagger offset (unless the hash happened to land on 0 -- guard below).
    if delay > 0:
        summary_early = service.collect_due_sources(loop_started_at=loop_start, stagger_window_seconds=45)
        assert summary_early.runs == []

    # Once "enough time" has passed relative to loop start, it becomes due.
    later_reference = loop_start - dt.timedelta(seconds=delay + 1)
    summary_later = service.collect_due_sources(loop_started_at=later_reference, stagger_window_seconds=45)
    assert len(summary_later.runs) == 1


def test_stagger_delay_is_deterministic_and_bounded():
    d1 = stagger_delay_seconds(source_id=42, provider="x", window_seconds=45)
    d2 = stagger_delay_seconds(source_id=42, provider="x", window_seconds=45)
    assert d1 == d2
    assert 0 <= d1 < 45
