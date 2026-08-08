from __future__ import annotations

import json
import sqlite3

import pytest
from sqlalchemy import func, select

from semi_intel.domain.models import ProviderRun, SignalItem, SignalMedia, Source, SourceSuggestion
from semi_intel.legacy_import import LegacyRadarImporter


def make_legacy_db(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE sources (
          id INTEGER PRIMARY KEY, platform TEXT, handle TEXT, display_name TEXT,
          priority INTEGER, reliability REAL, languages TEXT, expertise TEXT,
          signal_types TEXT, enabled INTEGER, muted INTEGER, notes TEXT,
          created_at TEXT, last_seen_at TEXT, cursor TEXT, meta TEXT
        );
        CREATE TABLE posts (
          id INTEGER PRIMARY KEY, source_id INTEGER, platform TEXT, external_id TEXT,
          posted_at TEXT, collected_at TEXT, text TEXT, language TEXT, links TEXT,
          quoted_external_id TEXT, reply_to_external_id TEXT, raw TEXT,
          fidelity TEXT, seen_deleted_at TEXT, processed_at TEXT
        );
        CREATE TABLE media (
          id INTEGER PRIMARY KEY, post_id INTEGER, kind TEXT, url TEXT, alt_text TEXT,
          from_quoted INTEGER, local_path TEXT, downloaded_at TEXT, meta TEXT
        );
        CREATE TABLE provider_runs (
          id INTEGER PRIMARY KEY, provider TEXT, source_id INTEGER, started_at TEXT,
          finished_at TEXT, items_collected INTEGER, duplicates_skipped INTEGER,
          cursor_before TEXT, cursor_after TEXT, collection_path TEXT, status TEXT, error TEXT
        );
        CREATE TABLE source_candidates (
          id INTEGER PRIMARY KEY, platform TEXT, handle TEXT, reason TEXT,
          est_reliability REAL, supporting_post_ids TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE stories (id INTEGER PRIMARY KEY, title TEXT);
        CREATE TABLE evidence (id INTEGER PRIMARY KEY, story_id INTEGER);
        """
    )
    connection.execute(
        "INSERT INTO sources VALUES (1,'rss','https://example.com/feed','Example',2,.8,"
        "'[\"en\"]','[\"gpu\"]','[\"news\"]',1,0,'legacy notes',"
        "'2025-01-01T00:00:00','2025-01-02T00:00:00','cursor-1','{}')"
    )
    connection.execute(
        "INSERT INTO posts VALUES (10,1,'rss','story-1','2025-01-02T00:00:00',"
        "'2025-01-02T00:01:00','RTX 50 Super leak','en','[\"https://example.com/story\"]',"
        "NULL,NULL,?, 'full',NULL,'2025-01-02T00:02:00')",
        (json.dumps({"title": "RTX 50 Super", "auth_token": "must-not-import"}),),
    )
    connection.execute(
        "INSERT INTO media VALUES (20,10,'image','https://example.com/image.jpg','chart',0,"
        "'C:/private/image.jpg','2025-01-02T00:03:00','{}')"
    )
    connection.execute(
        "INSERT INTO provider_runs VALUES (30,'rss',1,'2025-01-02T00:00:00',"
        "'2025-01-02T00:02:00',1,0,NULL,'cursor-1','api','ok',NULL)"
    )
    connection.execute(
        "INSERT INTO source_candidates VALUES (40,'x','VideoCardz','credited repeatedly',"
        ".7,'[10,11,12]','pending','2025-01-02T00:00:00')"
    )
    connection.execute("INSERT INTO stories VALUES (1,'Do not trust me')")
    connection.execute("INSERT INTO evidence VALUES (1,1)")
    connection.commit()
    connection.close()
    return path


def test_preview_is_read_only_and_reports_unsupported(tmp_path, db_session):
    path = make_legacy_db(tmp_path / "radar.db")
    report = LegacyRadarImporter(db_session, path).preview()

    assert report.categories["sources"].importable == 1
    assert report.categories["posts"].importable == 1
    assert report.unsupported["stories"] == 1
    assert report.unsupported["evidence"] == 1
    assert db_session.scalar(select(func.count()).select_from(Source)) == 0
    assert db_session.scalar(select(func.count()).select_from(SignalItem)) == 0


def test_apply_imports_safe_raw_layer_and_is_idempotent(tmp_path, db_session):
    path = make_legacy_db(tmp_path / "radar.db")
    first = LegacyRadarImporter(db_session, path).apply()
    db_session.commit()

    assert first.categories["sources"].imported == 1
    assert first.categories["posts"].imported == 1
    assert first.categories["media"].imported == 1
    assert first.categories["provider_runs"].imported == 1
    assert first.categories["source_suggestions"].imported == 1
    source = db_session.scalar(select(Source))
    assert source.polling_enabled is False
    item = db_session.scalar(select(SignalItem))
    assert item.processing_state.value == "pending"
    assert "must-not-import" not in item.raw_payload
    media = db_session.scalar(select(SignalMedia))
    assert media.local_path is None

    second = LegacyRadarImporter(db_session, path).apply()
    db_session.commit()
    assert second.categories["sources"].duplicate == 1
    assert second.categories["posts"].duplicate == 1
    assert db_session.scalar(select(func.count()).select_from(Source)) == 1
    assert db_session.scalar(select(func.count()).select_from(SignalItem)) == 1
    assert db_session.scalar(select(func.count()).select_from(SignalMedia)) == 1
    assert db_session.scalar(select(func.count()).select_from(ProviderRun)) == 1
    assert db_session.scalar(select(func.count()).select_from(SourceSuggestion)) == 1


def test_failure_rolls_back_partial_import(tmp_path, db_session, monkeypatch):
    path = make_legacy_db(tmp_path / "radar.db")
    importer = LegacyRadarImporter(db_session, path)

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(importer, "_plan_runs", fail)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        importer.apply()

    assert db_session.scalar(select(func.count()).select_from(Source)) == 0
    assert db_session.scalar(select(func.count()).select_from(SignalItem)) == 0


def test_rejects_non_radar_database(tmp_path, db_session):
    path = tmp_path / "not-radar.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE unrelated (id INTEGER)")
    connection.close()

    with pytest.raises(ValueError, match="Unsupported database format"):
        LegacyRadarImporter(db_session, path).preview()
