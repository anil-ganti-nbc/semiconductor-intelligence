"""StoryScoringService against a real (temp-file sqlite) database: gathers
distinct-source and recent-event counts correctly and ranks by total score."""

from __future__ import annotations

import datetime as dt

from semi_intel.domain.enums import ClaimEventType, ClaimStatus, EvidenceStance, SourceType
from semi_intel.domain.models import Evidence, Source
from semi_intel.repository.repositories import ClaimRepository, EvidenceRepository, SourceRepository
from semi_intel.story_scoring.service import StoryScoringService


def test_rank_only_includes_open_claims(db_session):
    claim_repo = ClaimRepository(db_session)
    open_claim = claim_repo.create("An open rumor")
    resolved_claim = claim_repo.create("A resolved rumor")
    claim_repo.resolve(resolved_claim, ClaimStatus.CONFIRMED)
    db_session.commit()

    ranked = StoryScoringService(db_session).rank()
    ranked_ids = {r.claim.id for r in ranked}
    assert open_claim.id in ranked_ids
    assert resolved_claim.id not in ranked_ids


def test_rank_orders_by_total_score_and_counts_distinct_sources(db_session):
    claim_repo = ClaimRepository(db_session)
    ev_repo = EvidenceRepository(db_session)
    src_repo = SourceRepository(db_session)

    strong_claim = claim_repo.create("Strongly corroborated claim")
    weak_claim = claim_repo.create("Weakly corroborated claim")
    db_session.commit()

    src_a = src_repo.add(Source(name="Source A", type=SourceType.RSS))
    src_b = src_repo.add(Source(name="Source B", type=SourceType.FORUM))
    db_session.commit()

    ev_a = ev_repo.add(Evidence(source_id=src_a.id, title="a", raw_content="a", content_hash="ha"))
    ev_b = ev_repo.add(Evidence(source_id=src_b.id, title="b", raw_content="b", content_hash="hb"))
    db_session.commit()

    claim_repo.link_evidence(strong_claim, ev_a, EvidenceStance.SUPPORTS)
    claim_repo.link_evidence(strong_claim, ev_b, EvidenceStance.SUPPORTS)
    db_session.commit()

    ranked = StoryScoringService(db_session).rank()
    ranked_by_id = {r.claim.id: r for r in ranked}

    assert ranked_by_id[strong_claim.id].score.total > ranked_by_id[weak_claim.id].score.total
    assert ranked[0].claim.id == strong_claim.id


def test_rank_respects_limit(db_session):
    claim_repo = ClaimRepository(db_session)
    for i in range(5):
        claim_repo.create(f"Claim {i}")
    db_session.commit()

    ranked = StoryScoringService(db_session).rank(limit=2)
    assert len(ranked) == 2


def test_recent_events_increase_momentum(db_session):
    claim_repo = ClaimRepository(db_session)
    claim = claim_repo.create("A claim with lots of activity")
    db_session.commit()

    # baseline: only the CREATED event exists so far
    before = StoryScoringService(db_session).rank()
    before_momentum = before[0].score.momentum

    # append a few more events directly -- each one should push momentum up
    for _ in range(3):
        claim_repo.add_event(claim, ClaimEventType.CONFIDENCE_UPDATED, note="test event")
    db_session.commit()

    after = StoryScoringService(db_session).rank()
    after_momentum = after[0].score.momentum

    assert after_momentum > before_momentum
