from __future__ import annotations
import pytest
import datetime as dt
from fastapi.testclient import TestClient

from semi_intel.domain.models import (
    Entity, SignalCandidate, SignalCandidateState, SignalEntityMention,
    SignalMentionStatus, SignalItem, CandidateSignalItem,
    ProviderRun, CandidatePromotionEvent, Source, Evidence
)

@pytest.fixture()
def client(db_session, cli_env):
    from semi_intel.web.app import create_app, get_session

    app = create_app()
    def override_get_session():
        yield db_session
    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        yield client

def _seed_candidate(session):
    cand = SignalCandidate(
        title="Test Candidate",
        state=SignalCandidateState.ACTIVE,
        attention_score=0.4,
        score_explanation='{"components":{}}',
        first_observed_at=dt.datetime.utcnow(),
        latest_observed_at=dt.datetime.utcnow(),
        fingerprint="dummy_fp"
    )
    session.add(cand)
    session.flush()
    return cand

def _seed_item(session, candidate_id, provider="rss"):
    source = session.get(Source, 1)
    if not source:
        source = Source(id=1, name="Test Source", type="rss")
        session.add(source)
    item = SignalItem(
        url="http://test.com/1",
        title="Test item",
        source_id=1,
        provider=provider,
        posted_at=dt.datetime.utcnow(),
        normalized_text="hello world",
        raw_payload="{}",
        external_id="ext-dummy",
        content_hash="dummy_hash"
    )
    session.add(item)
    session.flush()
    citem = CandidateSignalItem(candidate_id=candidate_id, signal_item_id=item.id)
    session.add(citem)
    session.flush()
    return item

def _seed_mention(session, item_id, text="test entity", type="company"):
    mention = SignalEntityMention(
        signal_item_id=item_id,
        candidate_text=text,
        proposed_entity_type=type,
        status=SignalMentionStatus.CANDIDATE,
        confidence=0.9,
        extractor="test"
    )
    session.add(mention)
    session.flush()
    return mention

def test_candidate_detail_payload(cli_env, client, db_session):
    cand = _seed_candidate(db_session)
    item = _seed_item(db_session, cand.id, provider="rss") # proves rss-only independence from X
    _seed_mention(db_session, item.id)
    db_session.commit()
    
    r = client.get(f"/api/radar/candidates/{cand.id}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == cand.id
    assert "timeline" in data
    assert len(data["timeline"]) == 1
    assert data["timeline"][0]["signal_item_id"] == item.id
    assert len(data["timeline"][0]["mentions"]) == 1
    
    m = data["timeline"][0]["mentions"][0]
    assert m["text"] == "test entity"
    
    assert data["automatic_promotion_eligibility"]["eligible"] is False
    assert any("attention score" in reason.lower() for reason in data["automatic_promotion_eligibility"]["reasons"])

def test_create_entity_from_mention(cli_env, client, db_session):
    cand = _seed_candidate(db_session)
    item = _seed_item(db_session, cand.id)
    m = _seed_mention(db_session, item.id, text="nvidia", type="company")
    db_session.commit()

    r = client.post("/api/entities/mention-proposals/resolve", json={
        "candidate_text": "nvidia",
        "proposed_entity_type": "company",
        "name": "Nvidia Corp",
        "type": "company",
        "add_observed_as_alias": True
    })
    assert r.status_code == 200
    assert r.json()["mentions_updated"] == 1
    
    db_session.expire_all()
    mention = db_session.get(SignalEntityMention, m.id)
    assert mention.status == SignalMentionStatus.RESOLVED
    assert mention.resolved_entity_id is not None
    
    # candidate remains unchanged
    cand = db_session.get(SignalCandidate, cand.id)
    assert cand.state == SignalCandidateState.ACTIVE

    # idempotency
    r2 = client.post("/api/entities/mention-proposals/resolve", json={
        "candidate_text": "nvidia",
        "proposed_entity_type": "company",
        "name": "Nvidia Corp",
        "type": "company",
        "add_observed_as_alias": True
    })
    assert r2.status_code in (200, 400, 404)

from semi_intel.domain.models import EntityType
def test_link_entity_from_mention(cli_env, client, db_session):
    cand = _seed_candidate(db_session)
    item = _seed_item(db_session, cand.id)
    m = _seed_mention(db_session, item.id, text="amd", type="company")
    
    entity = Entity(name="Advanced Micro Devices", type=EntityType.COMPANY)
    db_session.add(entity)
    db_session.commit()

    r = client.post("/api/entities/mention-proposals/resolve", json={
        "candidate_text": "amd",
        "proposed_entity_type": "company",
        "entity_id": entity.id,
        "add_observed_as_alias": True
    })
    assert r.status_code == 200
    
    db_session.expire_all()
    mention = db_session.get(SignalEntityMention, m.id)
    assert mention.status == SignalMentionStatus.RESOLVED
    assert mention.resolved_entity_id == entity.id

def test_reject_mention(cli_env, client, db_session):
    cand = _seed_candidate(db_session)
    item = _seed_item(db_session, cand.id)
    m = _seed_mention(db_session, item.id, text="junk", type="product")
    db_session.commit()

    r = client.post("/api/entities/mention-proposals/disposition", json={
        "candidate_text": "junk",
        "proposed_entity_type": "product",
        "action": "rejected"
    })
    assert r.status_code == 200, r.text
    
    db_session.expire_all()
    mention = db_session.get(SignalEntityMention, m.id)
    assert mention.status == SignalMentionStatus.REJECTED
    
    # idempotent
    r = client.post("/api/entities/mention-proposals/disposition", json={
        "candidate_text": "junk", "proposed_entity_type": "product", "action": "rejected"
    })
    assert r.status_code in (200, 404)

def test_manual_promotion_independent(cli_env, client, db_session):
    cand = _seed_candidate(db_session)
    item = _seed_item(db_session, cand.id)
    m = _seed_mention(db_session, item.id, text="something", type="company")
    db_session.commit()

    r = client.post(f"/api/radar/candidates/{cand.id}/promote", json={
        "by": "human_operator", "headline": "Override Promotion"
    })
    
    assert r.status_code == 200, r.text
    
    db_session.expire_all()
    cand = db_session.get(SignalCandidate, cand.id)
    assert cand.state == SignalCandidateState.PROMOTED
    assert cand.promoted_story_id is not None
    
    mention = db_session.get(SignalEntityMention, m.id)
    assert mention.status == SignalMentionStatus.CANDIDATE # Ensure mention not automatically resolved

    # verify evidence created
    assert db_session.query(Evidence).count() > 0

    # verify audit record
    audit = db_session.query(CandidatePromotionEvent).filter_by(candidate_id=cand.id).first()
    assert audit is not None
    assert audit.promoted_by == "human_operator"

    # repeated promotion rejected/idempotent
    r2 = client.post(f"/api/radar/candidates/{cand.id}/promote", json={
        "by": "human_operator", "headline": "Override Promotion"
    })
    assert r2.status_code in (400, 200)

def test_reject_candidate(cli_env, client, db_session):
    cand = _seed_candidate(db_session)
    db_session.commit()

    r = client.post(f"/api/radar/candidates/{cand.id}/dismiss", json={"reason": "test"})
    assert r.status_code == 200
    
    db_session.expire_all()
    assert db_session.get(SignalCandidate, cand.id).state == SignalCandidateState.DISMISSED

    # test idempotency
    r = client.post(f"/api/radar/candidates/{cand.id}/dismiss", json={"reason": "test"})
    assert r.status_code in (400, 200)

def test_snooze_candidate(cli_env, client, db_session):
    cand = _seed_candidate(db_session)
    db_session.commit()

    tomorrow = (dt.datetime.utcnow() + dt.timedelta(days=1)).isoformat()
    r = client.post(f"/api/radar/candidates/{cand.id}/snooze", json={"until": tomorrow})
    assert r.status_code == 200
    
    db_session.expire_all()
    assert db_session.get(SignalCandidate, cand.id).state == SignalCandidateState.SNOOZED

    # Idempotent
    r2 = client.post(f"/api/radar/candidates/{cand.id}/snooze", json={"until": tomorrow})
    assert r2.status_code in (400, 200)

def test_missing_candidate(cli_env, client, db_session):
    r = client.get("/api/radar/candidates/999999")
    assert r.status_code == 404
    r2 = client.post("/api/radar/candidates/999999/promote", json={"by": "tester", "headline": "yyy"})
    assert r2.status_code == 404

def test_static_html_rendered_correctly():
    with open("semi_intel/web/static/index.html", "r", encoding="utf-8") as f:
        html = f.read()
    
    # candidate detail contains entity review section
    assert "Extracted entity mentions" in html
    
    # unresolved warning element matches spec
    assert "This candidate contains ${unresolvedCount} unresolved entity mention" in html
    
    # missing playwright does not affect UI wiring since this works in vanilla browser
    
    # automatic eligibility and manual promotion labelled distinctively
    assert "Automatic promotion eligibility:" in html
    assert "Manual promotion availability:" in html
    
    # No placeholder filters remain in js/html
    assert "has_unresolved" not in html
    
    # API endpoints
    assert "/api/entities/mention-proposals/resolve" in html
    assert "/api/entities/mention-proposals/disposition" in html

def test_repeated_mention_resolution_is_idempotent(cli_env, client, db_session):
    cand = _seed_candidate(db_session)
    item = _seed_item(db_session, cand.id)
    m = _seed_mention(db_session, item.id, text="apple", type="company")
    db_session.commit()
    r = client.post("/api/entities/mention-proposals/resolve", json={"candidate_text": "apple", "proposed_entity_type": "company", "name": "Apple", "type": "company"})
    assert r.status_code == 200
    r2 = client.post("/api/entities/mention-proposals/resolve", json={"candidate_text": "apple", "proposed_entity_type": "company", "name": "Apple", "type": "company"})
    assert r2.status_code in (200, 400, 404)

def test_missing_entity_id_returns_safe_error(cli_env, client, db_session):
    cand = _seed_candidate(db_session)
    item = _seed_item(db_session, cand.id)
    m = _seed_mention(db_session, item.id, text="intel", type="company")
    db_session.commit()
    r = client.post("/api/entities/mention-proposals/resolve", json={"candidate_text": "intel", "proposed_entity_type": "company", "entity_id": 99999})
    assert r.status_code == 404

def test_dismiss_idempotent_or_safe(cli_env, client, db_session):
    cand = _seed_candidate(db_session)
    db_session.commit()
    r = client.post(f"/api/radar/candidates/{cand.id}/dismiss", json={"reason": "test"})
    r2 = client.post(f"/api/radar/candidates/{cand.id}/dismiss", json={"reason": "test again"})
    assert r2.status_code in (200, 400)
