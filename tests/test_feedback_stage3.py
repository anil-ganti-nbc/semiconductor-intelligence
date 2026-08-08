"""Stage 3: analytics, analyzer, simulation, CLI-level persistence."""

from __future__ import annotations

import json

import pytest

from oem_radar.core.feedback import FeedbackError
from oem_radar.core.feedback_analytics import (
    build_metrics_payload,
    compute_summary,
)
from oem_radar.core.feedback_analyze import analyze_reviews, persist_candidates
from oem_radar.core.feedback_simulate import simulate_rule
from oem_radar.core.models import ChangeEvent, ChangeType, Severity
from oem_radar.providers.sqlite import SCHEMA_VERSION, SqliteStore


@pytest.fixture()
def store(tmp_path):
    s = SqliteStore(str(tmp_path / "s.db"), str(tmp_path / "raw"))
    yield s
    s.close()


def _add(store, key, ctype, outcome=None, reasons=None, meta=None, old=None, new=None):
    eid = store.record_event(ChangeEvent(
        product_key=key, change_type=ctype, severity=Severity.NOTABLE,
        meta=meta or {}, old_value=old, new_value=new,
    ))
    if outcome:
        store.upsert_review(eid, outcome=outcome, reason_codes=reasons or [])
    return eid


def _fixture_patterns(store):
    # Safe temporary 404 pattern: 12 NOISE, 0 signal
    for i in range(12):
        _add(store, f"msi-shopify:p{i}", ChangeType.PRODUCT_REMOVED, "NOISE",
             ["TEMPORARY_404"])
    # Risky pattern: looks noisy but includes HITs
    for i in range(8):
        _add(store, f"beelink-shopify:img{i}", ChangeType.IMAGES_CHANGED, "NOISE",
             ["CDN_URL_CHURN"],
             old=[f"https://cdn.example/a.jpg?v={i}"],
             new=[f"https://cdn.example/a.jpg?v={i+1}"])
    for i in range(3):
        _add(store, f"beelink-shopify:img-hit{i}", ChangeType.IMAGES_CHANGED, "HIT",
             ["OTHER"],
             old=["https://cdn.example/real.jpg"],
             new=["https://cdn.example/new-product.jpg"])
    # Below min sample
    for i in range(3):
        _add(store, f"chuwi-shopify:x{i}", ChangeType.PRODUCT_REMOVED, "NOISE",
             ["TEMPORARY_404"])
    # Duplicate noise
    for i in range(10):
        _add(store, "gmktec-shopify:dup", ChangeType.DESCRIPTION_CHANGED, "NOISE",
             ["DUPLICATE_ALERT"])
    # Document with hash evidence
    for i in range(10):
        _add(store, f"minisforum-shopify:doc{i}", ChangeType.DESCRIPTION_CHANGED, "NOISE",
             ["UNCHANGED_DOCUMENT"],
             meta={"identical_content_hash": True, "content_hash_before": "abc",
                   "content_hash_after": "abc"})
    # Unreviewed backlog
    for i in range(5):
        _add(store, f"aoostar-shopify:u{i}", ChangeType.NEW_PRODUCT)


def test_schema_v5(store):
    assert SCHEMA_VERSION == 5
    cols = {r[1] for r in store.db.execute("PRAGMA table_info(rule_suggestions)")}
    assert "fingerprint" in cols and "rule_json" in cols


def test_summary_and_snr(store):
    _fixture_patterns(store)
    summary = compute_summary(store.db)
    assert summary["total_alerts"] > 0
    assert summary["reviewed_alerts"] > 0
    assert summary["signal_count"] == summary["hit_count"] + summary["interesting_count"]
    assert summary["bug_count"] == 0
    # SNR defined when noise > 0
    assert summary["noise_count"] > 0
    assert summary["signal_to_noise_ratio"] is not None
    assert summary["signal_to_noise_infinite"] is False


def test_snr_infinite_edge_case(store):
    _add(store, "s:a", ChangeType.NEW_PRODUCT, "HIT", ["VALID_CONFIRMATION_SIGNAL"])
    summary = compute_summary(store.db)
    assert summary["noise_count"] == 0
    assert summary["signal_count"] == 1
    assert summary["signal_to_noise_ratio"] is None
    assert summary["signal_to_noise_infinite"] is True


def test_zero_reviewed(store):
    _add(store, "s:a", ChangeType.NEW_PRODUCT)
    summary = compute_summary(store.db)
    assert summary["reviewed_alerts"] == 0
    assert summary["signal_rate"] is None


def test_metrics_payload_groupings(store):
    _fixture_patterns(store)
    payload = build_metrics_payload(store.db, group_by="collector", min_sample=3)
    assert payload["summary"]["total_alerts"] > 0
    assert payload["breakdown"]
    assert "noisiest_collectors" in payload["rankings"]
    with pytest.raises(FeedbackError):
        build_metrics_payload(store.db, group_by="not_a_dim")


def test_analyzer_safe_and_risky(store):
    _fixture_patterns(store)
    cands = analyze_reviews(store.db, min_samples=10, min_noise_ratio=0.75,
                            max_signal_loss_ratio=0.05)
    assert cands
    fps = {c.fingerprint() for c in cands}
    assert len(fps) == len(cands)
    # MSI temporary 404 should be low signal loss
    safeish = [c for c in cands if c.collector == "msi-shopify"]
    assert safeish
    assert safeish[0].estimated_signal_loss <= 0.05
    # image churn with hits may appear
    img = [c for c in cands if c.rule_type == "normalize_image_url"]
    if img:
        # if emitted, signal loss may be high
        assert img[0].supporting_alert_count >= 10


def test_below_min_sample_skipped(store):
    for i in range(3):
        _add(store, f"chuwi-shopify:x{i}", ChangeType.PRODUCT_REMOVED, "NOISE",
             ["TEMPORARY_404"])
    cands = analyze_reviews(store.db, min_samples=10, min_noise_ratio=0.75,
                            max_signal_loss_ratio=0.05)
    assert not any(c.collector == "chuwi-shopify" for c in cands)


def test_persist_fingerprint_no_dup(store):
    _fixture_patterns(store)
    cands = analyze_reviews(store.db, min_samples=10, min_noise_ratio=0.75,
                            max_signal_loss_ratio=0.05)
    rows1 = persist_candidates(store, cands)
    rows2 = persist_candidates(store, cands)
    n = store.db.execute("SELECT COUNT(*) c FROM rule_suggestions").fetchone()["c"]
    assert n == len(rows1)
    assert len(rows2) == len(rows1)


def test_rejected_not_revived(store):
    _fixture_patterns(store)
    cands = analyze_reviews(store.db, min_samples=10, min_noise_ratio=0.75,
                            max_signal_loss_ratio=0.05)
    rows = persist_candidates(store, cands)
    assert rows
    rid = rows[0]["id"]
    store.update_rule_suggestion_status(rid, "REJECTED")
    persist_candidates(store, cands)
    again = store.get_rule_suggestion(rid)
    assert again["status"] == "REJECTED"


def test_illegal_transition(store):
    _fixture_patterns(store)
    cands = analyze_reviews(store.db, min_samples=10, min_noise_ratio=0.75,
                            max_signal_loss_ratio=0.05)
    rid = persist_candidates(store, cands)[0]["id"]
    with pytest.raises(FeedbackError, match="illegal status transition"):
        store.update_rule_suggestion_status(rid, "IMPLEMENTED")  # PROPOSED -> IMPLEMENTED illegal


def test_simulation_safe_risky_insufficient(store):
    _fixture_patterns(store)
    cands = analyze_reviews(store.db, min_samples=10, min_noise_ratio=0.75,
                            max_signal_loss_ratio=0.05)
    rows = persist_candidates(store, cands)
    # Find MSI 404 suggestion
    msi = next(r for r in rows if r["collector"] == "msi-shopify")
    sim = simulate_rule(store.db, rule_id=msi["id"], min_samples=10,
                        max_signal_loss_ratio=0.05)
    assert sim["assessment"] in ("SAFE_CANDIDATE", "RISKY", "INSUFFICIENT_EVIDENCE")
    assert sim["noise_affected"] >= 1
    assert sim["hit_affected"] == 0

    # Image suggestion if present — may be RISKY due to hits
    img = [r for r in rows if r["alert_type"] == "images_changed"]
    if img:
        sim2 = simulate_rule(store.db, rule_id=img[0]["id"], min_samples=5,
                             max_signal_loss_ratio=0.05)
        assert sim2["assessment"] in ("SAFE_CANDIDATE", "RISKY", "INSUFFICIENT_EVIDENCE")
        if sim2["hit_affected"] > 0:
            assert sim2["assessment"] in ("RISKY", "INSUFFICIENT_EVIDENCE")


def test_simulate_missing_id(store):
    with pytest.raises(FeedbackError, match="no rule_suggestion"):
        simulate_rule(store.db, rule_id=99999)


def test_document_detector_requires_hash_evidence(store):
    # Without hash meta — should not suggest compare_content_hash
    for i in range(12):
        _add(store, f"x-shopify:d{i}", ChangeType.DESCRIPTION_CHANGED, "NOISE",
             ["UNCHANGED_DOCUMENT"])
    cands = analyze_reviews(store.db, min_samples=10, min_noise_ratio=0.75,
                            max_signal_loss_ratio=0.05)
    assert not any(c.rule_type == "compare_content_hash" for c in cands)
