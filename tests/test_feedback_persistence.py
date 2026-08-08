"""Feedback persistence foundation: reviews, history, suggestions, migrations.

change_events.id is the canonical alert id. Absence of a review row means NEW.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from oem_radar.core.feedback import (
    FeedbackError,
    REASON_CODES,
    ReviewOutcome,
    normalize_reason_codes,
    validate_outcome,
)
from oem_radar.core.models import ChangeEvent, ChangeType, Severity
from oem_radar.providers.sqlite import SCHEMA_VERSION, SqliteStore


@pytest.fixture()
def store(tmp_path):
    s = SqliteStore(str(tmp_path / "radar.db"), str(tmp_path / "raw"))
    yield s
    s.close()


def _event(store: SqliteStore, key: str = "src:k12", ctype=ChangeType.NEW_PRODUCT) -> int:
    return store.record_event(
        ChangeEvent(
            product_key=key,
            change_type=ctype,
            severity=Severity.BREAKING,
        )
    )


# ---- validation helpers ----------------------------------------------------

def test_normalize_reason_codes_dedupes_and_orders():
    codes = normalize_reason_codes(
        ["OTHER", "TEMPORARY_404", "OTHER", "CDN_URL_CHURN"]
    )
    assert codes == ["CDN_URL_CHURN", "TEMPORARY_404", "OTHER"]
    # taxonomy order, not input order
    assert codes == [c for c in REASON_CODES if c in set(codes)]


def test_invalid_reason_code_rejected():
    with pytest.raises(FeedbackError, match="invalid reason code"):
        normalize_reason_codes(["NOT_A_REAL_CODE"])


def test_invalid_outcome_rejected():
    with pytest.raises(FeedbackError, match="invalid outcome"):
        validate_outcome("MAYBE")


# ---- create / read ---------------------------------------------------------

def test_create_review(store):
    eid = _event(store)
    assert store.get_review(eid) is None  # still NEW

    rev = store.upsert_review(
        eid,
        outcome="HIT",
        reason_codes=["VALID_CONFIRMATION_SIGNAL"],
        reviewer_note="scoop material",
        reviewer="anil",
    )
    assert rev["alert_id"] == eid
    assert rev["outcome"] == "HIT"
    assert rev["reason_codes"] == ["VALID_CONFIRMATION_SIGNAL"]
    assert rev["reviewer"] == "anil"
    assert rev["reviewer_note"] == "scoop material"
    assert rev["reviewed_at"]

    again = store.get_review(eid)
    assert again is not None and again["outcome"] == "HIT"


def test_reject_nonexistent_event(store):
    with pytest.raises(FeedbackError, match="no change_event"):
        store.upsert_review(999999, outcome="NOISE")


def test_reject_invalid_outcome_at_store(store):
    eid = _event(store)
    with pytest.raises(FeedbackError, match="invalid outcome"):
        store.upsert_review(eid, outcome="GARBAGE")


def test_reject_invalid_reason_at_store(store):
    eid = _event(store)
    with pytest.raises(FeedbackError, match="invalid reason code"):
        store.upsert_review(eid, outcome="NOISE", reason_codes=["FAKE_REASON"])


def test_note_and_reviewer_length_caps(store):
    eid = _event(store)
    with pytest.raises(FeedbackError, match="reviewer_note exceeds"):
        store.upsert_review(eid, outcome="NOISE", reviewer_note="x" * 2001)
    with pytest.raises(FeedbackError, match="reviewer exceeds"):
        store.upsert_review(eid, outcome="NOISE", reviewer="y" * 65)


# ---- update + history ------------------------------------------------------

def test_update_preserves_history(store):
    eid = _event(store)
    store.upsert_review(
        eid, outcome="INTERESTING", reason_codes=["VALID_BUT_TOO_EARLY"], reviewer="a"
    )
    store.upsert_review(
        eid,
        outcome="NOISE",
        reason_codes=["LOW_EDITORIAL_VALUE", "ROUTINE_IMAGE_CHANGE"],
        reviewer="b",
        change_note="reclassified after second look",
    )

    hist = store.list_review_history(eid)
    assert len(hist) == 2
    assert hist[0]["previous_outcome"] is None
    assert hist[0]["new_outcome"] == "INTERESTING"
    assert hist[0]["new_reason_codes"] == ["VALID_BUT_TOO_EARLY"]
    assert hist[1]["previous_outcome"] == "INTERESTING"
    assert hist[1]["new_outcome"] == "NOISE"
    assert hist[1]["previous_reason_codes"] == ["VALID_BUT_TOO_EARLY"]
    assert hist[1]["new_reason_codes"] == ["ROUTINE_IMAGE_CHANGE", "LOW_EDITORIAL_VALUE"]
    assert hist[1]["change_note"] == "reclassified after second look"
    assert hist[1]["changed_by"] == "b"

    current = store.get_review(eid)
    assert current["outcome"] == "NOISE"
    assert current["reason_codes"] == ["ROUTINE_IMAGE_CHANGE", "LOW_EDITORIAL_VALUE"]


def test_one_current_review_per_event(store):
    eid = _event(store)
    store.upsert_review(eid, outcome="HIT")
    store.upsert_review(eid, outcome="BUG", reason_codes=["PARSER_ERROR"])
    n = store.db.execute(
        "SELECT COUNT(*) c FROM alert_reviews WHERE alert_id=?", (eid,)
    ).fetchone()["c"]
    assert n == 1


def test_unreviewed_events_stay_untouched(store):
    e1 = _event(store, "src:a")
    e2 = _event(store, "src:b")
    store.upsert_review(e1, outcome="NOISE", reason_codes=["CDN_URL_CHURN"])
    assert store.get_review(e1) is not None
    assert store.get_review(e2) is None  # still NEW — no backfill
    total_reviews = store.db.execute("SELECT COUNT(*) c FROM alert_reviews").fetchone()["c"]
    assert total_reviews == 1


# ---- rule suggestions ------------------------------------------------------

def test_insert_and_list_suggestions(store):
    sug = store.insert_rule_suggestion(
        collector="gmktec-shopify",
        alert_type="images_changed",
        reason_code="CDN_URL_CHURN",
        suggested_rule="Normalize image URLs before comparison.",
        supporting_alert_count=18,
        estimated_noise_reduction=0.91,
        estimated_hit_loss=0.0,
    )
    assert sug["id"] is not None
    assert sug["status"] == "PROPOSED"
    assert sug["collector"] == "gmktec-shopify"
    assert sug["estimated_noise_reduction"] == 0.91

    listed = store.list_rule_suggestions(status="PROPOSED", collector="gmktec-shopify")
    assert len(listed) == 1 and listed[0]["id"] == sug["id"]


def test_update_suggestion_status(store):
    sug = store.insert_rule_suggestion(
        collector="dell-us-laptops",
        alert_type="price_changed",
        suggested_rule="Raise min magnitude to 15%.",
        supporting_alert_count=12,
    )
    updated = store.update_rule_suggestion_status(sug["id"], "ACCEPTED")
    assert updated["status"] == "ACCEPTED"

    with pytest.raises(FeedbackError, match="invalid rule suggestion status"):
        store.update_rule_suggestion_status(sug["id"], "AUTO_APPLIED")

    with pytest.raises(FeedbackError, match="no rule_suggestion"):
        store.update_rule_suggestion_status(99999, "REJECTED")


def test_suggestion_rejects_bad_reason_code(store):
    with pytest.raises(FeedbackError, match="invalid reason_code"):
        store.insert_rule_suggestion(
            collector="x",
            alert_type="y",
            suggested_rule="z",
            reason_code="NOT_REAL",
        )


# ---- migrations ------------------------------------------------------------

def test_clean_db_gets_v4_tables(tmp_path):
    store = SqliteStore(str(tmp_path / "clean.db"), str(tmp_path / "raw"))
    versions = [
        r["version"]
        for r in store.db.execute("SELECT version FROM schema_migrations ORDER BY version")
    ]
    assert versions == list(range(1, SCHEMA_VERSION + 1))
    assert SCHEMA_VERSION == 5
    tables = {
        r[0]
        for r in store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "alert_reviews" in tables
    assert "alert_review_history" in tables
    assert "rule_suggestions" in tables
    # indexes present
    idxs = {
        r[0]
        for r in store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "idx_review_history_alert" in idxs
    assert "idx_rule_suggestions_status" in idxs
    assert "idx_rule_suggestions_collector" in idxs
    store.close()


def test_v3_db_migrates_to_v4_preserving_events(tmp_path):
    """Populate a schema-v3 database, reopen with current code, events intact."""
    db = tmp_path / "v3.db"
    # Build a minimal v3 DB the same way older tests do.
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT '');
        INSERT INTO schema_migrations(version) VALUES (1);
        INSERT INTO schema_migrations(version) VALUES (2);
        INSERT INTO schema_migrations(version) VALUES (3);
        CREATE TABLE change_events (
            id INTEGER PRIMARY KEY,
            product_key TEXT NOT NULL,
            change_type TEXT NOT NULL,
            field TEXT,
            old_value_json TEXT,
            new_value_json TEXT,
            severity INTEGER NOT NULL,
            meta_json TEXT NOT NULL DEFAULT '{}',
            detected_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE stories (
            id INTEGER PRIMARY KEY, rule_id TEXT, story_key TEXT, dedup_key TEXT UNIQUE,
            title TEXT, score INTEGER, manufacturers_json TEXT, evidence_json TEXT,
            score_reasons_json TEXT, created_at TEXT
        );
        """
    )
    con.execute(
        "INSERT INTO change_events(product_key, change_type, severity) VALUES (?,?,?)",
        ("gmktec:k12", "new_product", 5),
    )
    con.commit()
    event_id = con.execute("SELECT id FROM change_events").fetchone()[0]
    con.close()

    store = SqliteStore(str(db), str(tmp_path / "raw"))  # migrate → v4
    versions = [
        r["version"]
        for r in store.db.execute("SELECT version FROM schema_migrations ORDER BY version")
    ]
    assert versions == [1, 2, 3, 4, 5]
    # historical event still there and still unreviewed
    row = store.db.execute(
        "SELECT id, change_type FROM change_events WHERE id=?", (event_id,)
    ).fetchone()
    assert row is not None and row["change_type"] == "new_product"
    assert store.get_review(event_id) is None
    # can now attach a review
    rev = store.upsert_review(event_id, outcome="HIT", reviewer="migrator")
    assert rev["outcome"] == "HIT"
    # idempotent reopen
    store.close()
    store2 = SqliteStore(str(db), str(tmp_path / "raw"))
    versions2 = [
        r["version"]
        for r in store2.db.execute("SELECT version FROM schema_migrations ORDER BY version")
    ]
    assert versions2 == [1, 2, 3, 4, 5]
    assert store2.get_review(event_id)["outcome"] == "HIT"
    store2.close()


def test_no_automatic_rule_activation(store):
    """Suggestions are data only — status stays PROPOSED until explicit update."""
    sug = store.insert_rule_suggestion(
        collector="minisforum-shopify",
        alert_type="product_removed",
        reason_code="TEMPORARY_404",
        suggested_rule="Require two consecutive misses over 24h.",
        supporting_alert_count=22,
        estimated_noise_reduction=0.82,
        estimated_hit_loss=0.04,
    )
    assert sug["status"] == "PROPOSED"
    # Nothing in the store mutates collectors or severity rules as a side effect.
    still = store.get_rule_suggestion(sug["id"])
    assert still["status"] == "PROPOSED"


def test_reason_codes_json_deterministic(store):
    eid = _event(store)
    store.upsert_review(
        eid,
        outcome="NOISE",
        reason_codes=["OTHER", "CDN_URL_CHURN", "CDN_URL_CHURN", "TEMPORARY_404"],
    )
    raw = store.db.execute(
        "SELECT reason_codes_json FROM alert_reviews WHERE alert_id=?", (eid,)
    ).fetchone()["reason_codes_json"]
    # stable order + no dups
    assert raw == '["CDN_URL_CHURN","TEMPORARY_404","OTHER"]'


def test_config_feedback_section_loads():
    from oem_radar.core.config import FeedbackConfig, RadarConfig, load_radar_config
    from pathlib import Path

    cfg = load_radar_config(Path("config/radar.yaml"))
    assert isinstance(cfg.feedback, FeedbackConfig)
    assert cfg.feedback.minimum_samples_for_suggestion == 10
    assert cfg.feedback.require_manual_rule_approval is True
    assert cfg.feedback.dashboard_base_url.startswith("http://")
