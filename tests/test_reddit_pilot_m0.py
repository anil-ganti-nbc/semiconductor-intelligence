"""M0 r/hardware Reddit RSS pilot: silent experimental source, no live network."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import feedparser
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semi_intel.db import get_engine, get_sessionmaker, init_db
from semi_intel.domain.enums import (
    NotificationEventType,
    ProviderRunStatus,
    SignalCandidateState,
    SourceType,
)
from semi_intel.domain.models import (
    CandidateSignalItem,
    MonitoredTopic,
    Notification,
    SignalCandidate,
    SignalItem,
    Source,
)
from semi_intel.editorial.service import canonical_url
from semi_intel.notifications.service import NotificationService
from semi_intel.operations.webhook import ExternalDeliveryService, WebhookAdapter, WebhookConfigurationService
from semi_intel.signals.analysis import analyze_signal_item
from semi_intel.signals.clustering import cluster_unclustered_items
from semi_intel.signals.collection import CollectionService
from semi_intel.signals.independence import _Item, group_items
from semi_intel.signals.providers import ProviderUnavailable
from semi_intel.signals.providers.replay import ReplayProvider
from semi_intel.signals.providers.rss import RSSProvider
from semi_intel.signals.source_lifecycle import (
    HARDWARE_FEED_URL,
    HARDWARE_SOURCE_NAME,
    admit_source_for_delivery,
    candidate_has_admitted_novelty,
    item_is_baseline,
    item_is_delivery_admitted,
    register_reddit_hardware,
    source_baseline_completed_at,
    source_delivery_admitted_at,
    source_lifecycle_view,
    source_maturity,
)
from tests.test_operations_webhook import Opener

FIXTURES = Path("tests/fixtures")
LISTING = FIXTURES / "reddit_hardware_listing.xml"
LISTING_NEW = FIXTURES / "reddit_hardware_listing_new_post.xml"
LISTING_ADMIT = FIXTURES / "reddit_hardware_listing_after_admit.xml"
DUPLICATE_ID = FIXTURES / "reddit_hardware_duplicate_id.xml"
EMPTY_REDDIT = FIXTURES / "reddit_hardware_empty.xml"
MALFORMED = FIXTURES / "reddit_hardware_malformed.html"
EMPTY_OTHER = FIXTURES / "non_reddit_empty.xml"
SAMPLE = FIXTURES / "sample_feed.xml"

BASE = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)
VIDEOCARDZ_URL = "https://www.videocardz.com/newz/nvidia-geforce-rtx-50-super-specifications"
TOMS_URL = "https://www.tomshardware.com/pc-components/gpus/rtx-50-super-leak"
POST_A = "https://www.reddit.com/r/hardware/comments/abc111/intel_18a_yield_chatter/"
POST_NEW = "https://www.reddit.com/r/hardware/comments/abc444/rtx_50_super_memory_config_confirmed_after_baseline/"
POST_ADMIT = "https://www.reddit.com/r/hardware/comments/abc555/rtx_50_super_independent_bench_after_admission/"
DUP_ID = "https://www.reddit.com/r/hardware/comments/dup001/same_post/"


def _fetch_from(path: Path, status: int | None = None):
    content = path.read_bytes()

    def fetch_fn(_url: str):
        parsed = feedparser.parse(content)
        if status is not None:
            parsed.status = status
        return parsed

    return fetch_fn


def _status_only(status: int, body: bytes = b""):
    def fetch_fn(_url: str):
        parsed = feedparser.parse(body)
        parsed.status = status
        return parsed

    return fetch_fn


def _rss_service(session: Session, fetch_fn) -> CollectionService:
    return CollectionService(session, registry={"rss": RSSProvider(fetch_fn=fetch_fn)})


def _collect(session: Session, source: Source, path: Path) -> None:
    _rss_service(session, _fetch_from(path)).collect_source(source)


def _item_by_external_id(session: Session, external_id: str) -> SignalItem:
    item = session.scalar(select(SignalItem).where(SignalItem.external_id == external_id))
    assert item is not None, f"missing SignalItem {external_id}"
    return item


def _attach(session: Session, candidate: SignalCandidate, item: SignalItem) -> None:
    session.add(CandidateSignalItem(candidate_id=candidate.id, signal_item_id=item.id))
    session.flush()


def _enable_webhook(session: Session, monkeypatch, opener: Opener) -> WebhookAdapter:
    monkeypatch.setenv("SEMI_INTEL_WEBHOOK_URL", "https://example.com/hook")
    adapter = WebhookAdapter(url="https://example.com/hook", opener=opener)
    configuration = WebhookConfigurationService(session)
    assert configuration.test(adapter=adapter).delivered
    assert configuration.set_enabled(True)["enabled"] is True
    settings = NotificationService(session).settings()
    settings.external_delivery_enabled = True
    settings.quiet_hours_start = settings.quiet_hours_end = "00:00"
    session.flush()
    return adapter


# --- A. Registration is safe -------------------------------------------------

def test_register_reddit_hardware_is_safe_and_offline(db_session, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network contacted during registration")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.setattr("socket.create_connection", boom)

    source, created = register_reddit_hardware(db_session)
    again, created_again = register_reddit_hardware(db_session)

    assert created is True
    assert created_again is False
    assert again.id == source.id
    assert source.name == HARDWARE_SOURCE_NAME
    assert source.type == SourceType.RSS
    assert source.provider == "rss"
    assert source.provider_key == HARDWARE_FEED_URL
    assert source.url == HARDWARE_FEED_URL
    assert source.enabled is True
    assert source.polling_enabled is False
    assert source.muted is True
    assert source_maturity(source) == "experimental"
    view = source_lifecycle_view(source)
    assert view["delivery_blocked"] is True
    assert view["platform"] == "reddit"
    assert db_session.scalar(select(func.count()).select_from(SignalItem)) == 0


def test_register_cli_does_not_enable_polling(cli_env, monkeypatch):
    from typer.testing import CliRunner
    from semi_intel.cli import app
    from semi_intel.db import get_engine, get_sessionmaker

    def boom(*_args, **_kwargs):
        raise AssertionError("network contacted during CLI registration")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    runner = CliRunner()
    runner.invoke(app, ["init-db"])
    first = runner.invoke(app, ["radar", "register-reddit-hardware"])
    second = runner.invoke(app, ["radar", "register-reddit-hardware"])
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "No network fetch was performed" in first.output
    assert "Already registered" in second.output

    session = get_sessionmaker(get_engine())()
    source = session.scalar(select(Source).where(Source.name == HARDWARE_SOURCE_NAME))
    assert source is not None
    assert source.polling_enabled is False
    assert source.muted is True
    assert source.provider == "rss"
    session.close()


# --- B/C. First populate is silent and durable --------------------------------

def test_first_successful_populate_is_silent_baseline(db_session):
    source, _ = register_reddit_hardware(db_session)
    run = _rss_service(db_session, _fetch_from(LISTING)).collect_source(source)

    assert run.status == ProviderRunStatus.OK
    items = list(db_session.scalars(select(SignalItem)))
    assert len(items) == 3
    assert source_baseline_completed_at(source) is not None
    assert all(item_is_baseline(item, source) for item in items)
    assert all(not item_is_delivery_admitted(item, source) for item in items)

    notifications = NotificationService(db_session)
    notifications.settings(now=BASE)
    generated = notifications.generate(now=BASE + dt.timedelta(hours=1))
    assert generated.created_count == 0
    assert db_session.scalar(select(func.count()).select_from(Notification)) == 0


def test_baseline_survives_new_session(tmp_path):
    db_file = tmp_path / "reddit_restart.db"
    engine = get_engine(f"sqlite:///{db_file}")
    init_db(engine)
    SessionFactory = get_sessionmaker(engine)
    first = SessionFactory()
    source, _ = register_reddit_hardware(first)
    source_id = source.id
    _collect(first, source, LISTING)
    first.close()

    second = SessionFactory()
    reloaded = second.get(Source, source_id)
    assert reloaded is not None
    assert source_baseline_completed_at(reloaded) is not None
    items = list(second.scalars(select(SignalItem)))
    assert len(items) == 3
    assert all(item_is_baseline(item, reloaded) for item in items)
    second.close()
    engine.dispose()


# --- D/E. New post after baseline; resight; identity --------------------------

def test_new_post_after_baseline_is_not_baseline_and_resight_does_not_duplicate(db_session):
    source, _ = register_reddit_hardware(db_session)
    _collect(db_session, source, LISTING)
    baseline_ids = {row.external_id for row in db_session.scalars(select(SignalItem))}
    assert POST_A in baseline_ids

    _collect(db_session, source, LISTING_NEW)
    items = list(db_session.scalars(select(SignalItem)))
    assert len(items) == 4
    new_item = _item_by_external_id(db_session, POST_NEW)
    assert item_is_baseline(new_item, source) is False
    assert new_item.url == POST_NEW
    assert new_item.posted_at is not None
    assert new_item.collected_at is not None

    # Unchanged resight of the post-baseline listing.
    _collect(db_session, source, LISTING_NEW)
    assert db_session.scalar(select(func.count()).select_from(SignalItem)) == 4

    from semi_intel.editorial.service import TopicService
    TopicService(db_session).seed()
    db_session.commit()
    analyze_signal_item(db_session, new_item)
    db_session.commit()
    summary = cluster_unclustered_items(db_session)
    db_session.commit()
    assert item_is_baseline(new_item, source) is False
    # Clustering may or may not seed a candidate depending on topic match;
    # the observation itself is past the baseline boundary either way.
    assert summary.items_processed >= 1


def test_reddit_identity_is_stable_id_not_title(db_session):
    source, _ = register_reddit_hardware(db_session)
    provider = RSSProvider(fetch_fn=_fetch_from(DUPLICATE_ID))
    first = provider.collect(HARDWARE_FEED_URL, cursor=None)
    assert [item.external_id for item in first.items] == [DUP_ID]
    assert DUP_ID != first.items[0].payload.get("title")

    run = CollectionService(db_session, registry={"rss": provider}).collect_source(source)
    assert run.items_collected == 1
    assert run.duplicates_skipped == 0
    source.cursor = None
    again = CollectionService(db_session, registry={"rss": provider}).collect_source(source)
    assert again.items_collected == 0
    assert again.duplicates_skipped == 1
    assert db_session.scalar(select(func.count()).select_from(SignalItem)) == 1


def test_outbound_aggregator_urls_keep_existing_canonical_and_independence(db_session):
    source, _ = register_reddit_hardware(db_session)
    _collect(db_session, source, LISTING)
    reddit_item = _item_by_external_id(
        db_session,
        "https://www.reddit.com/r/hardware/comments/abc333/nvidia_rtx_50_super_24gb_board_slides/",
    )
    assert reddit_item.url.startswith("https://www.reddit.com/r/hardware/comments/")
    links = json.loads(reddit_item.expanded_links)
    assert VIDEOCARDZ_URL in links
    assert TOMS_URL in links
    assert canonical_url(VIDEOCARDZ_URL) == canonical_url(
        "http://www.videocardz.com/newz/nvidia-geforce-rtx-50-super-specifications?utm_source=reddit"
    )

    origin = Source(name="VideoCardz", type=SourceType.RSS, provider="rss", provider_key="https://www.videocardz.com/feed")
    db_session.add(origin)
    db_session.flush()
    echo = SignalItem(
        source_id=origin.id, provider="rss", external_id="vc-1", raw_payload="{}",
        normalized_text="RTX 50 Super specifications", content_hash="vc-1",
        url=VIDEOCARDZ_URL, collected_at=dt.datetime.utcnow(),
    )
    db_session.add(echo)
    db_session.flush()
    grouped, reasons = group_items([
        _Item(
            id=echo.id, source_id=origin.id, author_handle=None, url=echo.url,
            quoted_signal_item_id=None, reply_to_signal_item_id=None,
            normalized_text=echo.normalized_text, source_name=origin.name, posted_at=None,
        ),
        _Item(
            id=reddit_item.id, source_id=source.id, author_handle=None, url=VIDEOCARDZ_URL,
            quoted_signal_item_id=None, reply_to_signal_item_id=None,
            normalized_text=reddit_item.normalized_text or "", source_name=source.name, posted_at=None,
        ),
    ])
    assert grouped[echo.id] == grouped[reddit_item.id]
    assert "same_url" in reasons.values()


# --- F/G. Experimental delivery gate and promotion backlog --------------------

def _ensure_topic(session: Session) -> MonitoredTopic:
    topic = session.scalar(select(MonitoredTopic).where(MonitoredTopic.normalized_name == "rtx 50 super"))
    if topic is not None:
        return topic
    topic = MonitoredTopic(
        name="RTX 50 Super", normalized_name="rtx 50 super", keyword="RTX 50 Super",
        aliases="[]", category="gpu", priority=0.9, enabled=True,
    )
    session.add(topic)
    session.flush()
    return topic


def _high_candidate(db_session, *, fingerprint: str, latest: dt.datetime, score: float = 0.92):
    topic = _ensure_topic(db_session)
    candidate = SignalCandidate(
        fingerprint=fingerprint,
        title="RTX 50 Super specifications",
        state=SignalCandidateState.ACTIVE,
        attention_score=score,
        score_explanation=json.dumps({
            "components": {
                "topic_relevance": {"contribution": 0.4, "detail": "high-priority RTX 50 Super topic"},
                "source_diversity": {"contribution": 0.2, "detail": "3 independent groups"},
            }
        }),
        first_observed_at=latest,
        latest_observed_at=latest,
        item_count=1,
        distinct_source_count=1,
        independent_source_group_count=3,
        primary_topic_id=topic.id,
    )
    db_session.add(candidate)
    db_session.flush()
    return topic, candidate


def test_experimental_source_gate_blocks_external_delivery_while_webhook_enabled(db_session, monkeypatch):
    source, _ = register_reddit_hardware(db_session)
    _collect(db_session, source, LISTING)
    _collect(db_session, source, LISTING_NEW)
    new_item = _item_by_external_id(db_session, POST_NEW)
    assert source.muted is True
    assert item_is_delivery_admitted(new_item, source) is False

    now = BASE + dt.timedelta(days=2)
    NotificationService(db_session).settings(now=BASE)
    _topic, experimental = _high_candidate(db_session, fingerprint="reddit-exp", latest=now)
    _attach(db_session, experimental, new_item)
    assert candidate_has_admitted_novelty(db_session, experimental) is False

    _topic2, control = _high_candidate(db_session, fingerprint="control-open", latest=now)

    opener = Opener()
    adapter = _enable_webhook(db_session, monkeypatch, opener)
    service = NotificationService(db_session)
    generated = service.generate(now=now)
    assert generated.created_count >= 1
    experimental_notes = list(db_session.scalars(
        select(Notification).where(Notification.candidate_id == experimental.id)
    ))
    control_notes = list(db_session.scalars(
        select(Notification).where(Notification.candidate_id == control.id)
    ))
    assert experimental_notes == []
    assert control_notes
    assert all(row.muted is False for row in control_notes)
    assert service.settings().external_delivery_enabled is True

    delivered = ExternalDeliveryService(db_session, adapter=adapter).deliver_pending(now=now)
    assert delivered["disabled"] is False
    assert delivered["notifications"] >= 1
    payloads = []
    for request, _timeout in opener.calls:
        payloads.append(request.data.decode("utf-8") if isinstance(request.data, bytes) else str(request.data))
    assert any("independent group" in body for body in payloads)


def test_promotion_does_not_flush_historical_observations_to_discord(db_session, monkeypatch):
    source, _ = register_reddit_hardware(db_session)
    _collect(db_session, source, LISTING)
    _collect(db_session, source, LISTING_NEW)
    soak_item = _item_by_external_id(db_session, POST_NEW)
    now = BASE + dt.timedelta(days=3)
    _topic, soak_candidate = _high_candidate(db_session, fingerprint="reddit-soak", latest=now)
    _attach(db_session, soak_candidate, soak_item)

    notifications = NotificationService(db_session)
    notifications.settings(now=BASE)
    first = notifications.generate(now=now)
    assert first.created_count == 0

    admit_at = dt.datetime.utcnow()
    admit_source_for_delivery(source, now=admit_at)
    db_session.commit()
    assert source.muted is False
    assert source_delivery_admitted_at(source) is not None
    assert item_is_delivery_admitted(soak_item, source) is False
    assert candidate_has_admitted_novelty(db_session, soak_candidate) is False

    opener = Opener()
    adapter = _enable_webhook(db_session, monkeypatch, opener)
    second = notifications.generate(now=now + dt.timedelta(minutes=5))
    soak_notes = list(db_session.scalars(
        select(Notification).where(Notification.candidate_id == soak_candidate.id)
    ))
    assert second.created_count == 0
    assert soak_notes == []

    _collect(db_session, source, LISTING_ADMIT)
    admitted_item = _item_by_external_id(db_session, POST_ADMIT)
    admitted_item.collected_at = admit_at + dt.timedelta(minutes=10)
    db_session.flush()
    assert item_is_baseline(admitted_item, source) is False
    assert item_is_delivery_admitted(admitted_item, source) is True

    _topic2, fresh = _high_candidate(
        db_session, fingerprint="reddit-fresh", latest=now + dt.timedelta(hours=2),
    )
    _attach(db_session, fresh, admitted_item)
    third = notifications.generate(now=now + dt.timedelta(hours=2))
    fresh_notes = list(db_session.scalars(
        select(Notification).where(Notification.candidate_id == fresh.id, Notification.muted.is_(False))
    ))
    assert third.created_count >= 1
    assert fresh_notes
    delivered = ExternalDeliveryService(db_session, adapter=adapter).deliver_pending(
        now=now + dt.timedelta(hours=2)
    )
    assert delivered["notifications"] >= 1
    assert db_session.scalar(
        select(func.count()).select_from(Notification).where(Notification.candidate_id == soak_candidate.id)
    ) == 0


# --- H. Provider health honesty ----------------------------------------------

@pytest.mark.parametrize(
    "fetch_fn, fragment",
    [
        (_status_only(429), "429"),
        (_status_only(403, Path("tests/fixtures/reddit_hardware_malformed.html").read_bytes()), "403"),
        (_fetch_from(MALFORMED), "malformed"),
        (_fetch_from(EMPTY_REDDIT), "suspicious empty Reddit"),
    ],
)
def test_reddit_failure_classes_are_not_healthy(db_session, fetch_fn, fragment):
    source, _ = register_reddit_hardware(db_session)
    run = _rss_service(db_session, fetch_fn).collect_source(source)
    assert run.status == ProviderRunStatus.FAILED
    assert fragment.lower() in (run.error or "").lower()
    assert source.last_success_at is None
    assert source_baseline_completed_at(source) is None
    assert source.error_state


def test_reddit_provider_raises_on_rate_limit_without_collection_service():
    provider = RSSProvider(fetch_fn=_status_only(429))
    with pytest.raises(ProviderUnavailable, match="429"):
        provider.collect(HARDWARE_FEED_URL, cursor=None)


# --- I/J. Existing sources and non-Reddit RSS --------------------------------

def test_existing_successful_source_is_not_rebaselined(db_session):
    established = Source(
        name="Existing Replay",
        type=SourceType.SOCIAL,
        provider="replay",
        provider_key="ian",
        enabled=True,
        polling_enabled=True,
        muted=False,
        last_success_at=BASE.replace(tzinfo=None) - dt.timedelta(days=10),
    )
    db_session.add(established)
    db_session.commit()
    registry = {"replay": ReplayProvider(name="replay", fixtures={
        "ian": [{"external_id": "1", "posted_at": "2026-01-01T00:00:00Z", "text": "RTX 50 Super", "author": "ian"}],
    })}
    run = CollectionService(db_session, registry=registry).collect_source(established)
    assert run.status == ProviderRunStatus.OK
    assert source_baseline_completed_at(established) is None
    item = db_session.scalar(select(SignalItem))
    assert item_is_baseline(item, established) is False
    assert item_is_delivery_admitted(item, established) is True

    now = BASE + dt.timedelta(days=1)
    NotificationService(db_session).settings(now=BASE)
    _topic, candidate = _high_candidate(db_session, fingerprint="existing-open", latest=now)
    _attach(db_session, candidate, item)
    generated = NotificationService(db_session).generate(now=now)
    assert generated.created_count >= 1


def test_non_reddit_empty_rss_remains_healthy_and_sample_feed_still_parses(db_session):
    empty_source = Source(
        name="Empty Example",
        type=SourceType.RSS,
        provider="rss",
        provider_key="https://example.com/empty.xml",
        url="https://example.com/empty.xml",
        enabled=True,
        polling_enabled=False,
    )
    sample_source = Source(
        name="Sample Hardware News",
        type=SourceType.RSS,
        provider="rss",
        provider_key="https://example.com/feed",
        url="https://example.com/feed",
        enabled=True,
        polling_enabled=False,
        last_success_at=BASE.replace(tzinfo=None),
    )
    db_session.add_all([empty_source, sample_source])
    db_session.commit()

    empty_run = _rss_service(db_session, _fetch_from(EMPTY_OTHER)).collect_source(empty_source)
    assert empty_run.status == ProviderRunStatus.OK
    assert empty_run.items_collected == 0
    assert source_baseline_completed_at(empty_source) is None

    sample_run = _rss_service(db_session, _fetch_from(SAMPLE)).collect_source(sample_source)
    assert sample_run.status == ProviderRunStatus.OK
    assert sample_run.items_collected == 2
    assert source_baseline_completed_at(sample_source) is None

    provider = RSSProvider(fetch_fn=_fetch_from(SAMPLE))
    result = provider.collect("https://example.com/feed", cursor=None)
    assert len(result.items) == 2
