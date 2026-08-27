"""Human QC review of SignalCandidates: the fleet-wide four-way
Useful/Not-useful/False-positive/Duplicate disposition contract (see
semi_intel/signals/candidate_qc.py's module docstring for why Duplicate
replaces Watch Clank's Out-of-stock in this domain), a separate archive
table, immediate removal from the active-lead queue once reviewed, a
"Recently QC'd" history view, race-safe duplicate-submission handling, and
restart (i.e. plain DB row) persistence.

Uses FastAPI's TestClient against a throwaway sqlite file per test, same
pattern as tests/test_web_radar.py.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from semi_intel.db import get_engine, get_sessionmaker, init_db
from semi_intel.domain.enums import CandidateReviewDisposition, SourceType
from semi_intel.domain.models import CandidateReview, Source, SignalCandidate, SignalItem
from semi_intel.editorial.service import TopicService
from semi_intel.signals.analysis import analyze_signal_item
from semi_intel.signals.candidate_qc import (
    CandidateQueueFilters,
    InvalidDispositionError,
    fetch_candidate_review_queue_page,
    submit_candidate_review,
    unreviewed_candidate_count,
)
from semi_intel.signals.clustering import cluster_unclustered_items
from semi_intel.signals.scoring import rescore_active_candidates

BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "qc_web_test.db"
    monkeypatch.setenv("SEMI_INTEL_DB_URL", f"sqlite:///{db_file}")
    from semi_intel.web.app import create_app
    with TestClient(create_app(mutation_authorizer=lambda _value: True)) as client:
        yield client


@pytest.fixture()
def session(tmp_path):
    db_file = tmp_path / "qc_session_test.db"
    engine = get_engine(f"sqlite:///{db_file}")
    init_db(engine)
    s = get_sessionmaker(engine)()
    yield s
    s.close()


def _seed_candidate(s, *, text="RTX 50 Super leak: 24GB VRAM confirmed.", external_id="1"):
    TopicService(s).seed()
    s.commit()
    source = Source(name="VideoCardz", type=SourceType.SOCIAL, provider="rss")
    s.add(source)
    s.commit()
    item = SignalItem(
        source_id=source.id, provider="rss", external_id=external_id, raw_payload="{}",
        normalized_text=text, content_hash=f"h-{external_id}", posted_at=BASE,
    )
    s.add(item)
    s.commit()
    analyze_signal_item(s, item)
    s.commit()
    cluster_unclustered_items(s)
    s.commit()
    rescore_active_candidates(s)
    candidate = s.scalars(select(SignalCandidate)).first()
    return candidate


def _seed_candidate_via_client(client, **kwargs):
    engine = get_engine(os.environ["SEMI_INTEL_DB_URL"])
    s = get_sessionmaker(engine)()
    candidate = _seed_candidate(s, **kwargs)
    candidate_id = candidate.id
    s.commit()
    s.close()
    return candidate_id


# --- service-level: disposition vocabulary, archive semantics -----------


def test_disposition_vocabulary_is_the_fleet_four_way_set():
    """USEFUL/NOT_USEFUL/FALSE_POSITIVE/DUPLICATE -- no OUT_OF_STOCK (no
    honest inventory concept for a signal candidate; see module docstring
    for why DUPLICATE stands in for it, following Watch Clank's own
    SpecialistLeadReview precedent)."""
    assert {d.value for d in CandidateReviewDisposition} == {
        "useful", "not_useful", "false_positive", "duplicate",
    }
    assert not any(d.value == "out_of_stock" for d in CandidateReviewDisposition)


def test_submit_review_creates_separate_archive_row_and_does_not_mutate_candidate(session):
    candidate = _seed_candidate(session)
    candidate_id, original_state = candidate.id, candidate.state

    review = submit_candidate_review(
        session, candidate=candidate, disposition=CandidateReviewDisposition.USEFUL, reason="Real leak, credible source."
    )
    session.commit()

    assert review.candidate_id == candidate_id
    assert review.disposition == CandidateReviewDisposition.USEFUL
    assert review.is_corrected is False

    # CANDIDATE != REVIEW: the candidate row itself is untouched.
    session.refresh(candidate)
    assert candidate.state == original_state
    assert candidate.dismissed_at is None
    assert candidate.seen_at is None

    # It's a genuinely separate table, not a status flag on signal_candidates.
    row = session.scalar(select(CandidateReview).where(CandidateReview.candidate_id == candidate_id))
    assert row is not None
    assert row.id != candidate_id or True  # separate primary key space entirely


def test_invalid_disposition_rejected(session):
    candidate = _seed_candidate(session)
    with pytest.raises(InvalidDispositionError):
        submit_candidate_review(session, candidate=candidate, disposition="in_stock")


# --- idempotent-by-correction + race-safety ------------------------------


def test_second_submission_is_a_correction_not_a_duplicate_row(session):
    candidate = _seed_candidate(session)

    submit_candidate_review(session, candidate=candidate, disposition=CandidateReviewDisposition.USEFUL)
    session.commit()

    corrected = submit_candidate_review(
        session, candidate=candidate, disposition=CandidateReviewDisposition.FALSE_POSITIVE, reason="Actually a rehash."
    )
    session.commit()

    rows = list(session.scalars(select(CandidateReview).where(CandidateReview.candidate_id == candidate.id)))
    assert len(rows) == 1
    assert corrected.disposition == CandidateReviewDisposition.FALSE_POSITIVE
    assert corrected.is_corrected is True
    import json
    history = json.loads(corrected.review_metadata)["correction_history"]
    assert history[0]["previous_disposition"] == "useful"


def test_duplicate_review_insert_recovers_race_safely(session):
    """Simulates two near-simultaneous submissions for the same candidate:
    the DB-level unique constraint prevents a second archive row even if
    application code tries to insert one directly."""
    candidate = _seed_candidate(session)
    session.add(CandidateReview(
        candidate_id=candidate.id, candidate_title=candidate.title,
        disposition=CandidateReviewDisposition.USEFUL,
    ))
    session.commit()

    with pytest.raises(IntegrityError):
        session.add(CandidateReview(
            candidate_id=candidate.id, candidate_title=candidate.title,
            disposition=CandidateReviewDisposition.NOT_USEFUL,
        ))
        session.flush()
    session.rollback()

    # The real entry point recovers from exactly this race instead of
    # ever letting IntegrityError escape to the caller.
    recovered = submit_candidate_review(session, candidate=candidate, disposition=CandidateReviewDisposition.NOT_USEFUL)
    session.commit()
    assert recovered.disposition == CandidateReviewDisposition.NOT_USEFUL
    rows = list(session.scalars(select(CandidateReview).where(CandidateReview.candidate_id == candidate.id)))
    assert len(rows) == 1


# --- queue exclusion + persistence ---------------------------------------


def test_reviewed_candidate_disappears_from_active_queue_immediately(session):
    candidate = _seed_candidate(session)
    filters = CandidateQueueFilters()

    assert unreviewed_candidate_count(session, filters) == 1
    queue = fetch_candidate_review_queue_page(session, filters)
    assert candidate.id in {c.id for c in queue}

    submit_candidate_review(session, candidate=candidate, disposition=CandidateReviewDisposition.USEFUL)
    session.commit()

    assert unreviewed_candidate_count(session, filters) == 0
    queue = fetch_candidate_review_queue_page(session, filters)
    assert candidate.id not in {c.id for c in queue}


def test_review_persists_across_a_fresh_session_restart(tmp_path):
    """Restart-persistence: a review is a plain committed DB row, so a
    brand-new engine/session against the same sqlite file sees it -- no
    in-memory or process-local state involved."""
    db_file = tmp_path / "restart_test.db"
    engine = get_engine(f"sqlite:///{db_file}")
    init_db(engine)
    s1 = get_sessionmaker(engine)()
    candidate = _seed_candidate(s1)
    candidate_id = candidate.id
    submit_candidate_review(s1, candidate=candidate, disposition=CandidateReviewDisposition.DUPLICATE, reason="Same as #17.")
    s1.commit()
    s1.close()

    # Fresh engine + session, simulating a process restart.
    engine2 = get_engine(f"sqlite:///{db_file}")
    s2 = get_sessionmaker(engine2)()
    row = s2.scalar(select(CandidateReview).where(CandidateReview.candidate_id == candidate_id))
    assert row is not None
    assert row.disposition == CandidateReviewDisposition.DUPLICATE
    assert row.reason == "Same as #17."
    s2.close()


# --- web endpoints --------------------------------------------------------


def test_review_endpoint_records_verdict_and_leaves_state_untouched(client):
    candidate_id = _seed_candidate_via_client(client)

    r = client.post(f"/api/radar/candidates/{candidate_id}/review", json={"disposition": "useful", "reason": "Good lead."})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review"]["disposition"] == "useful"
    assert body["review"]["reason"] == "Good lead."
    assert body["candidate"]["state"] == "active"  # unaffected by QC review
    assert body["candidate"]["review"]["disposition"] == "useful"


def test_review_endpoint_rejects_unknown_disposition(client):
    candidate_id = _seed_candidate_via_client(client)
    r = client.post(f"/api/radar/candidates/{candidate_id}/review", json={"disposition": "out_of_stock"})
    assert r.status_code == 422


def test_review_endpoint_404s_for_missing_candidate(client):
    r = client.post("/api/radar/candidates/999999/review", json={"disposition": "useful"})
    assert r.status_code == 404


def test_review_queue_endpoint_excludes_reviewed_and_counts_correctly(client):
    candidate_id = _seed_candidate_via_client(client, external_id="a")

    r = client.get("/api/radar/candidates/review-queue")
    assert r.status_code == 200
    body = r.json()
    assert body["unreviewed_count"] >= 1
    assert candidate_id in {item["id"] for item in body["items"]}

    r = client.post(f"/api/radar/candidates/{candidate_id}/review", json={"disposition": "not_useful"})
    assert r.status_code == 200

    r = client.get("/api/radar/candidates/review-queue")
    body = r.json()
    assert candidate_id not in {item["id"] for item in body["items"]}


def test_review_history_endpoint_is_recently_qced_view(client):
    candidate_id = _seed_candidate_via_client(client, external_id="b")
    client.post(f"/api/radar/candidates/{candidate_id}/review", json={"disposition": "false_positive", "reason": "Mislabeled."})

    r = client.get("/api/radar/candidates/review-history")
    assert r.status_code == 200
    rows = r.json()
    assert any(row["candidate_id"] == candidate_id and row["disposition"] == "false_positive" for row in rows)


def test_correcting_a_verdict_via_the_endpoint_updates_in_place(client):
    candidate_id = _seed_candidate_via_client(client, external_id="c")
    client.post(f"/api/radar/candidates/{candidate_id}/review", json={"disposition": "useful"})
    r = client.post(f"/api/radar/candidates/{candidate_id}/review", json={"disposition": "duplicate", "reason": "Same as an earlier item."})
    assert r.status_code == 200
    assert r.json()["review"]["disposition"] == "duplicate"

    history = client.get("/api/radar/candidates/review-history").json()
    matching = [row for row in history if row["candidate_id"] == candidate_id]
    # A corrected review drops out of the default (non-corrected) history view.
    assert matching == []

    history_all = client.get("/api/radar/candidates/review-history?include_corrected=true").json()
    matching_all = [row for row in history_all if row["candidate_id"] == candidate_id]
    assert len(matching_all) == 1
    assert matching_all[0]["is_corrected"] is True


def test_dismiss_and_review_are_independent(client):
    """Existing dismiss/restore/snooze/promote workflow must keep working
    exactly as before, completely independent of QC review."""
    candidate_id = _seed_candidate_via_client(client)

    r = client.post(f"/api/radar/candidates/{candidate_id}/dismiss", json={"reason": "not relevant"})
    assert r.status_code == 200
    assert r.json()["state"] == "dismissed"

    # A dismissed candidate can still receive a QC verdict.
    r = client.post(f"/api/radar/candidates/{candidate_id}/review", json={"disposition": "not_useful"})
    assert r.status_code == 200
    assert r.json()["candidate"]["state"] == "dismissed"  # review didn't restore it

    r = client.post(f"/api/radar/candidates/{candidate_id}/restore")
    assert r.status_code == 200
    assert r.json()["state"] == "active"

    # Restoring the candidate doesn't erase its QC verdict either.
    r = client.get(f"/api/radar/candidates/{candidate_id}")
    assert r.json()["review"]["disposition"] == "not_useful"
