"""Regression tests for a real bug hit in live use of the packaged 3.3.3
build: every get-or-create singleton settings row used
`session.get(Model, 1)` -> `None` -> insert `id=1` with no handling for a
concurrent request's session winning that same race, which raised
`sqlite3.IntegrityError: UNIQUE constraint failed` under the dashboard's
own concurrent page-load request bursts.

Each test reproduces the race deterministically (no real threading needed):
a "winner" session commits a real row 1 first; the "loser" session's very
next `.get()` call is forced to miss once (simulating its read having
raced ahead of the winner's commit), so it proceeds into the insert
branch and its flush() hits a genuine UNIQUE-constraint conflict against
the winner's already-committed row. The function under test must recover
by rolling back and re-fetching, not raise.
"""
from __future__ import annotations

from semi_intel.db import get_engine, get_sessionmaker, init_db
from semi_intel.discovery.service import DiscoverySettingsService
from semi_intel.domain.models import (
    AttentionScoringSettings, CandidatePromotionSettings, DeliveryAdapterStatus,
    DiscoverySettings, NotificationSettings, SchedulerSettings, SignalCollectionSettings,
)
from semi_intel.notifications.service import get_settings
from semi_intel.operations.scheduler import get_scheduler_settings
from semi_intel.operations.webhook import WebhookConfigurationService
from semi_intel.signals.collection import get_collection_settings
from semi_intel.signals.promotion import get_promotion_settings
from semi_intel.signals.scoring import get_scoring_settings


def _two_sessions(tmp_path, name):
    engine = get_engine(f"sqlite:///{tmp_path / name}")
    init_db(engine)
    factory = get_sessionmaker(engine)
    return factory(), factory()


def _miss_next_get_once(session):
    """Force this session's very next `.get()` call to return None, then
    behave normally -- simulates a read that raced ahead of a concurrent
    commit."""
    real_get = session.get
    state = {"missed": False}

    def get_that_misses_once(model, pk):
        if not state["missed"]:
            state["missed"] = True
            return None
        return real_get(model, pk)

    session.get = get_that_misses_once


def test_get_scheduler_settings_recovers_from_concurrent_insert_race(tmp_path):
    winner, loser = _two_sessions(tmp_path, "scheduler.db")
    winner.add(SchedulerSettings(id=1))
    winner.commit()
    winner.close()

    _miss_next_get_once(loser)
    settings = get_scheduler_settings(loser)
    assert settings.id == 1
    loser.close()


def test_get_notification_settings_recovers_from_concurrent_insert_race(tmp_path):
    winner, loser = _two_sessions(tmp_path, "notifications.db")
    winner.add(NotificationSettings(id=1))
    winner.commit()
    winner.close()

    _miss_next_get_once(loser)
    settings = get_settings(loser)
    assert settings.id == 1
    loser.close()


def test_get_collection_settings_recovers_from_concurrent_insert_race(tmp_path):
    winner, loser = _two_sessions(tmp_path, "collection.db")
    winner.add(SignalCollectionSettings(id=1))
    winner.commit()
    winner.close()

    _miss_next_get_once(loser)
    settings = get_collection_settings(loser)
    assert settings.id == 1
    loser.close()


def test_get_promotion_settings_recovers_from_concurrent_insert_race(tmp_path):
    winner, loser = _two_sessions(tmp_path, "promotion.db")
    winner.add(CandidatePromotionSettings(id=1))
    winner.commit()
    winner.close()

    _miss_next_get_once(loser)
    settings = get_promotion_settings(loser)
    assert settings.id == 1
    loser.close()


def test_get_scoring_settings_recovers_from_concurrent_insert_race(tmp_path):
    winner, loser = _two_sessions(tmp_path, "scoring.db")
    winner.add(AttentionScoringSettings(id=1))
    winner.commit()
    winner.close()

    _miss_next_get_once(loser)
    settings = get_scoring_settings(loser)
    assert settings.id == 1
    loser.close()


def test_discovery_settings_service_recovers_from_concurrent_insert_race(tmp_path):
    winner, loser = _two_sessions(tmp_path, "discovery.db")
    winner.add(DiscoverySettings(id=1))
    winner.commit()
    winner.close()

    _miss_next_get_once(loser)
    settings = DiscoverySettingsService(loser).get()
    assert settings.id == 1
    loser.close()


def test_webhook_status_row_recovers_from_concurrent_insert_race(tmp_path, monkeypatch):
    monkeypatch.delenv("SEMI_INTEL_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SEMI_INTEL_WEBHOOK_TOKEN", raising=False)
    winner, loser = _two_sessions(tmp_path, "webhook.db")
    winner.add(DeliveryAdapterStatus(id=1))
    winner.commit()
    winner.close()

    _miss_next_get_once(loser)
    row = WebhookConfigurationService(loser).status_row()
    assert row.id == 1
    # The post-recovery field updates from this call must still land on the
    # real, now-shared row -- not get silently dropped on an orphaned object.
    assert row.configuration_present is False
    loser.close()
