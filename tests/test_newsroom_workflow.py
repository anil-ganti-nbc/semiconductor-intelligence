"""Deterministic end-to-end acceptance for the operator's core newsroom loop.

The fixture feeds exercise both RSS paths without opening a network socket:
curated editorial RSS becomes Evidence/EditorialStory, while radar RSS becomes
SignalItem/SignalCandidate.  The test intentionally crosses API, service,
restart, notification, saved-view and backup boundaries instead of proving
those pieces in isolation again.
"""

from __future__ import annotations

import datetime as dt
import json
from email.utils import format_datetime
from pathlib import Path

import feedparser
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from semi_intel.db import get_engine, get_sessionmaker
from semi_intel.domain.enums import NotificationEventType
from semi_intel.domain.models import (
    EditorialStory,
    Evidence,
    MonitoredTopic,
    Notification,
    SignalCandidate,
    SourceSuggestion,
)
from semi_intel.notifications.service import NotificationService
from semi_intel.operations.backup import BackupService
from semi_intel.pipeline.service import PipelineService
from semi_intel.signals.collection import get_collection_settings


def _rss(items: list[dict[str, str]], published: dt.datetime) -> feedparser.FeedParserDict:
    rows = []
    for item in items:
        rows.append(
            "<item>"
            f"<guid>{item['id']}</guid>"
            f"<title><![CDATA[{item['title']}]]></title>"
            f"<link>{item['url']}</link>"
            f"<description><![CDATA[{item['description']}]]></description>"
            f"<pubDate>{format_datetime(published)}</pubDate>"
            "</item>"
        )
    return feedparser.parse(
        "<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel>"
        "<title>Local newsroom fixture</title>" + "".join(rows) + "</channel></rss>"
    )


def test_core_newsroom_workflow_survives_restart_and_backup(tmp_path, monkeypatch):
    database = tmp_path / "newsroom workflow.db"
    db_url = f"sqlite:///{database}"
    monkeypatch.setenv("SEMI_INTEL_DB_URL", db_url)
    monkeypatch.delenv("SEMI_INTEL_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SEMI_INTEL_WEBHOOK_TOKEN", raising=False)

    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    editorial_url = "https://fixture.local/editorial.xml"
    radar_origin_url = "https://fixture.local/radar-origin.xml"
    radar_corroboration_url = "https://fixture.local/radar-corroboration.xml"

    editorial_items = [
        {
            "id": "editorial-rtx-origin",
            "title": "RTX 50 Super 24GB memory configuration leaks",
            "url": "https://known-wire.test/rtx-origin",
            "description": (
                "The RTX 50 Super may use 24GB of memory. Original reporting: "
                '<a href="https://new-origin.example/report">New Origin</a>.'
            ),
        },
        {
            "id": "editorial-rtx-followup",
            "title": "RTX 50 Super 24GB memory configuration leaked",
            "url": "https://known-wire.test/rtx-followup",
            "description": "A follow-up report repeats the RTX 50 Super 24GB configuration.",
        },
        {
            "id": "editorial-zen-alias",
            "title": "Zen6 core roadmap leaks ahead of launch",
            "url": "https://known-wire.test/zen6",
            "description": "The Zen-6 CPU roadmap points to a revised core design.",
        },
        {
            "id": "editorial-noise",
            "title": "A quiet Zen garden design guide",
            "url": "https://known-wire.test/garden",
            "description": "Landscaping advice with stones, water and moss.",
        },
    ]
    radar_feeds = {
        radar_origin_url: [
            {
                "id": "radar-origin",
                "title": "RTX 50 Super leak shows 24GB VRAM configuration",
                "url": "https://radar-one.test/rtx",
                "description": "A leaked board description lists RTX 50 Super and 24GB VRAM.",
            },
            {
                "id": "radar-noise",
                "title": "A peaceful Zen garden photographed at sunrise",
                "url": "https://radar-one.test/garden",
                "description": "Travel photography unrelated to processors or semiconductors.",
            },
        ],
        radar_corroboration_url: [
            {
                "id": "radar-benchmark",
                "title": "Geekbench independently confirms RTX 50 Super branding",
                "url": "https://radar-two.test/benchmark",
                "description": "A separate Geekbench listing shows RTX 50 Super and 24GB VRAM.",
            }
        ],
    }

    import semi_intel.ingestion.plugins.rss_plugin as editorial_rss
    import semi_intel.signals.providers.rss as radar_rss

    monkeypatch.setattr(
        editorial_rss,
        "_default_fetch",
        lambda url: _rss(editorial_items, now) if url == editorial_url else feedparser.parse(""),
    )
    monkeypatch.setattr(
        radar_rss,
        "_default_fetch",
        lambda url: _rss(radar_feeds.get(url, []), now),
    )

    from semi_intel.web.app import create_app

    with TestClient(create_app(mutation_authorizer=lambda _value: True)) as api:
        topics = {row["name"]: row for row in api.get("/api/topics").json()}
        assert {"RDNA 5", "Zen 6", "RTX 60 Series", "RTX 50 Super"} <= set(topics)
        assert "RDNA5" in topics["RDNA 5"]["aliases"]
        assert "Zen6" in topics["Zen 6"]["aliases"]

        editorial_source = api.post(
            "/api/sources",
            json={
                "name": "Fixture Editorial Wire",
                "type": "rss",
                "url": editorial_url,
                "trust_weight": 0.8,
            },
        )
        assert editorial_source.status_code == 201, editorial_source.text
        radar_source = api.post(
            "/api/radar/sources",
            json={
                "handle_or_url": radar_origin_url,
                "display_name": "Fixture Radar Feed",
                "polling_enabled": True,
                "trust_weight": 0.75,
            },
        )
        assert radar_source.status_code == 201, radar_source.text

        engine = get_engine(db_url)
        session = get_sessionmaker(engine)()
        try:
            get_collection_settings(session).collection_enabled = True
            notification_settings = NotificationService(session).settings(
                now=now - dt.timedelta(days=1)
            )
            notification_settings.minimum_attention_score = 0.10
            notification_settings.required_independent_group_count = 2
            notification_settings.required_topic_match = True
            session.commit()

            first = PipelineService(session).run_once(include_pci_ids=False)
            assert not first.failures
            assert first.signal_collection is not None
            assert first.signal_clustering is not None
            assert first.signal_clustering.suppressed_no_topic_or_artifact == 1

            stories = list(session.scalars(select(EditorialStory).order_by(EditorialStory.id)))
            assert len(stories) == 2
            rtx_story = next(story for story in stories if "RTX 50 Super" in story.headline)
            assert rtx_story.coverage_count == 2
            assert any("Covered by 2 articles" in reason for reason in json.loads(rtx_story.score_reasons))
            assert not any("garden" in story.headline.casefold() for story in stories)
            assert session.scalar(select(func.count()).select_from(Evidence)) == 4

            suggestion = session.scalar(
                select(SourceSuggestion).where(SourceSuggestion.domain == "new-origin.example")
            )
            assert suggestion is not None
            assert suggestion.status.value == "pending"

            candidate = session.scalar(
                select(SignalCandidate).where(SignalCandidate.title.like("%RTX 50 Super%"))
            )
            assert candidate is not None
            assert candidate.item_count == 1
            assert candidate.independent_source_group_count == 1

            corroborating_source = api.post(
                "/api/radar/sources",
                json={
                    "handle_or_url": radar_corroboration_url,
                    "display_name": "Fixture Independent Benchmark",
                    "polling_enabled": True,
                    "trust_weight": 0.8,
                },
            )
            assert corroborating_source.status_code == 201, corroborating_source.text

            second = PipelineService(session).run_once(include_pci_ids=False)
            assert not second.failures
            session.refresh(candidate)
            assert candidate.item_count == 2
            assert candidate.independent_source_group_count == 2
            assert session.scalar(
                select(func.count()).select_from(SignalCandidate).where(
                    SignalCandidate.title.like("%RTX 50 Super%")
                )
            ) == 1

            notifications = list(session.scalars(
                select(Notification).where(Notification.candidate_id == candidate.id)
            ))
            event_types = {row.event_type for row in notifications}
            assert NotificationEventType.HIGH_ATTENTION in event_types
            assert NotificationEventType.INDEPENDENT_CORROBORATION in event_types
            notification_count = len(notifications)

            unchanged = PipelineService(session).run_once(include_pci_ids=False)
            assert not unchanged.failures
            assert unchanged.notifications_created == 0
            assert session.scalar(
                select(func.count()).select_from(Notification).where(
                    Notification.candidate_id == candidate.id
                )
            ) == notification_count
        finally:
            session.close()
            engine.dispose()

        unseen = api.get("/api/editorial/stories?state=unseen").json()
        assert any(row["id"] == rtx_story.id for row in unseen)
        assert api.post(
            "/api/editorial/stories/seen",
            json={"story_ids": [rtx_story.id], "seen": True},
        ).status_code == 200
        seen_before = api.get(f"/api/editorial/stories/{rtx_story.id}").json()["seen_at"]

        first_notification = api.get("/api/notifications?state=all").json()[0]
        assert api.post(
            "/api/notifications/read",
            json={"notification_ids": [first_notification["id"]], "read": True},
        ).status_code == 200
        assert api.post(f"/api/notifications/{first_notification['id']}/dismiss").status_code == 200
        assert api.get(f"/api/editorial/stories/{rtx_story.id}").json()["seen_at"] == seen_before

        view_body = {
            "name": "RTX corroboration desk",
            "state_filter": "all",
            "event_types": ["high_attention", "independent_corroboration"],
            "severities": ["notable", "important"],
            "topic_ids": [topics["RTX 50 Super"]["id"]],
            "relation_filters": {},
            "date_window_days": 7,
            "search_text": "RTX 50 Super",
            "sort_order": "severity",
        }
        saved_view = api.post("/api/notifications/saved-views", json=view_body)
        assert saved_view.status_code == 201, saved_view.text
        view_id = saved_view.json()["id"]
        applied = api.get(f"/api/notifications/saved-views/{view_id}/apply")
        assert applied.status_code == 200
        assert applied.json()["notifications"]

        editorial_items.insert(0, {
            "id": "editorial-rtx-new-coverage",
            "title": "RTX 50 Super 24GB memory configuration leak follow-up",
            "url": "https://known-wire.test/rtx-new-coverage",
            "description": "A genuinely new follow-up adds board-layout details.",
        })
        engine = get_engine(db_url)
        session = get_sessionmaker(engine)()
        try:
            update = PipelineService(session).run_once(include_pci_ids=False)
            assert not update.failures
        finally:
            session.close()
            engine.dispose()
        after_coverage = api.get(f"/api/editorial/stories/{rtx_story.id}").json()
        assert after_coverage["seen_at"] == seen_before
        assert after_coverage["new_coverage_count"] == 1

    # A new app and engine simulate a genuine restart against the same file.
    with TestClient(create_app(mutation_authorizer=lambda _value: True)) as restarted:
        persisted_story = restarted.get(f"/api/editorial/stories/{rtx_story.id}").json()
        assert persisted_story["seen"] is True
        assert persisted_story["new_coverage_count"] == 1
        assert restarted.get(f"/api/notifications/saved-views/{view_id}").json()["name"] == (
            "RTX corroboration desk"
        )
        assert restarted.get(f"/api/notifications/saved-views/{view_id}/apply").json()[
            "notifications"
        ]

    engine = get_engine(db_url)
    session = get_sessionmaker(engine)()
    try:
        backup = BackupService(session, backup_directory=Path(tmp_path / "backups")).create(now=now)
        rehearsal = BackupService(
            session, backup_directory=Path(tmp_path / "backups")
        ).rehearse(Path(backup.path))
        assert rehearsal["passed"] is True
        assert rehearsal["schema_up_to_date"] is True
        assert rehearsal["orm_record_counts"]["notifications"] >= 2
    finally:
        session.close()
        engine.dispose()
