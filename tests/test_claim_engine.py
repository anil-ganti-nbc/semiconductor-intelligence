"""Pure unit tests for entity matching and claim scoring -- no database.

Entity/Claim ORM objects are constructed directly (never added to a session)
since these modules only ever read plain attributes off them.
"""

from __future__ import annotations

import json

from semi_intel.claim_engine.entity_matcher import find_entity_mentions
from semi_intel.claim_engine.scoring import keyword_overlap, score_claim
from semi_intel.domain.enums import EntityType
from semi_intel.domain.models import Entity


def _entity(id_: int, name: str, aliases=None) -> Entity:
    return Entity(id=id_, type=EntityType.PRODUCT, name=name, aliases=json.dumps(aliases or []))


def test_find_entity_mentions_matches_name_as_whole_word():
    nova_lake = _entity(1, "Nova Lake")
    mentions = find_entity_mentions("Leaked slides show Nova Lake using a new node.", [nova_lake])
    assert len(mentions) == 1
    assert mentions[0].entity_id == 1
    assert mentions[0].matched_term == "Nova Lake"


def test_find_entity_mentions_does_not_match_substring_inside_another_word():
    # "AI" should not match inside "again" or "Wait"
    ai_co = _entity(1, "AI")
    mentions = find_entity_mentions("Wait, this happened again.", [ai_co])
    assert mentions == []


def test_find_entity_mentions_matches_via_alias():
    rtx = _entity(2, "RTX 5080 Super", aliases=["5080 Super", "RTX5080S"])
    mentions = find_entity_mentions("Board partners are prepping the 5080 Super launch.", [rtx])
    assert len(mentions) == 1
    assert mentions[0].matched_term == "5080 Super"


def test_find_entity_mentions_skips_terms_shorter_than_minimum():
    short = _entity(3, "N2")  # 2 chars, below MIN_TERM_LENGTH
    mentions = find_entity_mentions("The N2 node is coming along.", [short])
    assert mentions == []


def test_keyword_overlap_is_zero_for_disjoint_text():
    assert keyword_overlap("Nova Lake uses 18A-P", "Completely unrelated football news today") == 0.0


def test_keyword_overlap_rewards_shared_significant_words():
    overlap = keyword_overlap(
        "Nova Lake uses Intel 18A-P process node",
        "New leak: Nova Lake spotted using Intel's 18A-P node in engineering samples",
    )
    assert overlap > 0


def test_score_claim_combines_entity_and_keyword_signals():
    outcome = score_claim(
        claim_statement="Nova Lake uses Intel 18A-P",
        claim_subject_entity_id=1,
        evidence_text="Nova Lake spotted using Intel's 18A-P node",
        mentioned_entity_ids={1},
    )
    assert outcome.score > 0.6  # entity match weight alone is 0.6
    assert any("subject entity" in r for r in outcome.reasons)
    assert any("keyword overlap" in r for r in outcome.reasons)


def test_score_claim_is_low_without_entity_or_keyword_match():
    outcome = score_claim(
        claim_statement="Nova Lake uses Intel 18A-P",
        claim_subject_entity_id=1,
        evidence_text="Completely unrelated football news today",
        mentioned_entity_ids=set(),
    )
    assert outcome.score == 0.0
    assert outcome.reasons == []
