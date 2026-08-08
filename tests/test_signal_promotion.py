"""Editorial promotion tests (brief section 24 "Promotion tests")."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from semi_intel.domain.enums import SignalCandidateState, SourceType
from semi_intel.domain.models import (
    CandidatePromotionEvent,
    CandidatePromotionSettings,
    Evidence,
    Source,
    SignalCandidate,
    SignalItem,
    StoryEvidence,
    TopicMatch,
)
from semi_intel.editorial.service import TopicService
from semi_intel.signals.analysis import analyze_signal_item
from semi_intel.signals.clustering import cluster_unclustered_items
from semi_intel.signals.promotion import (
    check_automatic_eligibility,
    get_promotion_settings,
    merge_candidate_into_story,
    promote_candidate,
    run_automatic_promotion,
)
from semi_intel.signals.scoring import rescore_active_candidates

BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


def _seed(session):
    TopicService(session).seed()
    session.commit()


def _candidate_with_items(session, n=2, *, trust_weight=0.7):
    """Each item gets genuinely distinct wording -- real-world reports of
    the same fact are never byte-identical, and Evidence dedup is
    content-hash based (matching ingestion's own dedup rule), so identical
    text across items would collapse into one Evidence row by design."""
    source = Source(name="VideoCardz", type=SourceType.SOCIAL, provider="rss", trust_weight=trust_weight)
    session.add(source)
    session.commit()
    for i in range(n):
        item = SignalItem(
            source_id=source.id, provider="rss", external_id=str(i), raw_payload="{}",
            normalized_text=f"RTX 50 Super leak report #{i}: 24GB VRAM confirmed on a 256-bit bus.",
            content_hash=f"h{i}", posted_at=BASE + dt.timedelta(minutes=i),
            url=f"https://videocardz.com/x{i}", title=f"RTX 50 Super report {i}",
        )
        session.add(item)
        session.commit()
        analyze_signal_item(session, item)
        session.commit()
    cluster_unclustered_items(session)
    session.commit()
    rescore_active_candidates(session)
    return session.scalars(select(SignalCandidate)).first()


def test_manual_promotion_creates_story_and_evidence(db_session):
    _seed(db_session)
    candidate = _candidate_with_items(db_session, n=2)

    story = promote_candidate(db_session, candidate, by="human:tester")

    assert candidate.promoted_story_id == story.id
    assert candidate.state == SignalCandidateState.PROMOTED
    evidence = list(db_session.scalars(select(Evidence)))
    assert len(evidence) == 2
    for e in evidence:
        assert e.origin_signal_item_id is not None
        assert e.url is not None and e.url.startswith("https://videocardz.com/")

    links = list(db_session.scalars(select(StoryEvidence).where(StoryEvidence.story_id == story.id)))
    assert len(links) == 2


def test_promotion_preserves_original_url_external_id_timestamps(db_session):
    _seed(db_session)
    candidate = _candidate_with_items(db_session, n=1)
    item = db_session.scalars(select(SignalItem)).first()

    promote_candidate(db_session, candidate, by="human:tester")

    evidence = db_session.scalars(select(Evidence).where(Evidence.origin_signal_item_id == item.id)).one()
    assert evidence.url == item.url
    assert evidence.external_id == item.external_id
    assert evidence.observed_at == item.posted_at


def test_promotion_creates_topic_matches_with_reasons(db_session):
    _seed(db_session)
    candidate = _candidate_with_items(db_session, n=1)

    story = promote_candidate(db_session, candidate, by="human:tester")

    matches = list(db_session.scalars(select(TopicMatch).where(TopicMatch.story_id == story.id)))
    assert matches
    assert matches[0].matched_text  # a real matched term, not blank


def test_promotion_is_idempotent(db_session):
    _seed(db_session)
    candidate = _candidate_with_items(db_session, n=2)

    story1 = promote_candidate(db_session, candidate, by="human:tester")
    evidence_count_1 = len(list(db_session.scalars(select(Evidence))))

    story2 = promote_candidate(db_session, candidate, by="human:tester-again")
    evidence_count_2 = len(list(db_session.scalars(select(Evidence))))

    assert story1.id == story2.id
    assert evidence_count_1 == evidence_count_2  # no duplicate Evidence rows


def test_promotion_records_audit_event(db_session):
    _seed(db_session)
    candidate = _candidate_with_items(db_session, n=1)

    story = promote_candidate(db_session, candidate, by="human:alice", reason="looks solid")

    event = db_session.scalars(
        select(CandidatePromotionEvent).where(CandidatePromotionEvent.candidate_id == candidate.id)
    ).one()
    assert event.story_id == story.id
    assert event.promoted_by == "human:alice"
    assert event.automatic is False
    assert event.reason == "looks solid"


def test_merge_into_existing_story(db_session):
    _seed(db_session)
    first_candidate = _candidate_with_items(db_session, n=1)
    existing_story = promote_candidate(db_session, first_candidate, by="human:setup")

    # A second, separately-clustered candidate about the same topic that an
    # operator decides belongs in the SAME story.
    source2 = Source(name="Aggregator", type=SourceType.SOCIAL, provider="rss")
    db_session.add(source2)
    db_session.commit()
    item2 = SignalItem(
        source_id=source2.id, provider="rss", external_id="separate", raw_payload="{}",
        normalized_text="RTX 50 Super memory config reconfirmed by an aggregator weeks later.",
        content_hash="h-separate", posted_at=BASE + dt.timedelta(days=10),  # outside attach window
        url="https://aggregator.example/rtx-50-super",
    )
    db_session.add(item2)
    db_session.commit()
    analyze_signal_item(db_session, item2)
    db_session.commit()
    cluster_unclustered_items(db_session)
    db_session.commit()
    second_candidate = db_session.scalars(
        select(SignalCandidate).where(SignalCandidate.id != first_candidate.id)
    ).first()
    assert second_candidate is not None

    merged_story = merge_candidate_into_story(db_session, second_candidate, existing_story, by="human:operator")

    assert merged_story.id == existing_story.id
    assert second_candidate.promoted_story_id == existing_story.id
    links = list(db_session.scalars(select(StoryEvidence).where(StoryEvidence.story_id == existing_story.id)))
    assert len(links) == 2  # both candidates' evidence in the same story


# --- automatic promotion ------------------------------------------------

def test_automatic_promotion_off_by_default(db_session):
    _seed(db_session)
    candidate = _candidate_with_items(db_session, n=3, trust_weight=0.9)

    summary = run_automatic_promotion(db_session, now=BASE + dt.timedelta(hours=1))

    assert summary.promoted == []
    assert candidate.state == SignalCandidateState.ACTIVE
    assert candidate.promoted_story_id is None


def test_automatic_promotion_when_enabled_and_eligible(db_session):
    _seed(db_session)
    candidate = _candidate_with_items(db_session, n=3, trust_weight=0.9)

    settings = get_promotion_settings(db_session)
    settings.automatic_promotion_enabled = True
    settings.minimum_attention_score = 0.05
    db_session.commit()

    summary = run_automatic_promotion(db_session, now=BASE + dt.timedelta(hours=1))

    assert candidate.id in summary.promoted
    assert candidate.state == SignalCandidateState.PROMOTED


def test_automatic_promotion_respects_hourly_budget(db_session):
    _seed(db_session)
    settings = get_promotion_settings(db_session)
    settings.automatic_promotion_enabled = True
    settings.minimum_attention_score = 0.01
    settings.hourly_promotion_budget = 1
    db_session.commit()

    # Two genuinely distinct monitored topics -> two distinct candidates,
    # not one merged candidate (which would defeat the point of this test).
    distinct_texts = [
        "RTX 50 Super leak with 24GB VRAM confirmed.",
        "Zen 6 core design previewed at the AMD chip roadmap event.",
    ]
    for n, text in enumerate(distinct_texts):
        source = Source(name=f"Source {n}", type=SourceType.SOCIAL, provider="rss", trust_weight=0.9)
        db_session.add(source)
        db_session.commit()
        item = SignalItem(
            source_id=source.id, provider="rss", external_id=f"item-{n}", raw_payload="{}",
            normalized_text=text, content_hash=f"budget-{n}", posted_at=BASE + dt.timedelta(minutes=n),
            url=f"https://example.com/{n}",
        )
        db_session.add(item)
        db_session.commit()
        analyze_signal_item(db_session, item)
        db_session.commit()
    cluster_unclustered_items(db_session)
    db_session.commit()
    rescore_active_candidates(db_session)

    candidates = list(db_session.scalars(select(SignalCandidate)))
    assert len(candidates) == 2  # two separate topics/candidates

    summary = run_automatic_promotion(db_session, now=BASE + dt.timedelta(hours=1))

    assert len(summary.promoted) == 1
    assert summary.budget_exhausted is True


def test_ineligible_candidate_lists_specific_reasons(db_session):
    _seed(db_session)
    candidate = _candidate_with_items(db_session, n=1)
    settings = get_promotion_settings(db_session)
    settings.automatic_promotion_enabled = False
    settings.minimum_attention_score = 0.99
    db_session.commit()

    result = check_automatic_eligibility(db_session, candidate, settings, now=BASE + dt.timedelta(hours=1))

    assert result.eligible is False
    assert any("disabled" in r for r in result.reasons)
    assert any("attention score" in r for r in result.reasons)


def test_dismissed_candidate_never_auto_promoted(db_session):
    from semi_intel.signals.candidate_state import dismiss

    _seed(db_session)
    candidate = _candidate_with_items(db_session, n=3, trust_weight=0.9)
    dismiss(candidate, reason="not relevant")
    db_session.commit()

    settings = get_promotion_settings(db_session)
    settings.automatic_promotion_enabled = True
    settings.minimum_attention_score = 0.01
    db_session.commit()

    summary = run_automatic_promotion(db_session, now=BASE + dt.timedelta(hours=1))

    assert summary.promoted == []
    assert candidate.state == SignalCandidateState.DISMISSED  # unchanged


def test_promotion_does_not_create_claims(db_session):
    """Promotion runs claim-link suggestions but never creates a Claim
    itself (brief section 12, point 7)."""
    from semi_intel.domain.models import Claim

    _seed(db_session)
    candidate = _candidate_with_items(db_session, n=1)

    promote_candidate(db_session, candidate, by="human:tester")

    assert list(db_session.scalars(select(Claim))) == []
