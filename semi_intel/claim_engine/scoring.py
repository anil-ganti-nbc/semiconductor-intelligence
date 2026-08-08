"""Deterministic claim-relevance scoring.

Like services/confidence.py, this is a documented formula, not a model: the
score and the reasons behind it should be readable at a glance in
`suggest show`. Two rules-based signals, combined:

  - the claim's subject entity (if any) is mentioned in the evidence text
  - the claim's statement and the evidence text share meaningful keywords

This says nothing about *stance* (supports/weakens/contradicts) -- that
requires actually understanding the claim, which this deliberately does not
attempt. A human always chooses the stance when accepting a suggestion.

Because the entity-match signal dominates the weighting (0.6 of 1.0) and
keyword overlap alone rarely clears the acceptance threshold on realistic
text (short claim statements vs. longer evidence bodies shrink Jaccard
overlap), this approach favors precision over recall: it will suggest fewer
links than a real NLP system would, but the ones it suggests should make
sense on inspection. That trade-off is deliberate for M2 -- see the roadmap
for what a smarter claim engine would need.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Set

ENTITY_MATCH_WEIGHT = 0.6
KEYWORD_WEIGHT = 0.4
MIN_WORD_LENGTH = 4

STOPWORDS = {
    "this", "that", "with", "from", "have", "will", "uses", "used", "using",
    "into", "over", "than", "then", "them", "they", "there", "which", "about",
    "were", "been", "your", "just", "also", "some", "more", "most", "such",
    "only", "very", "when", "what", "does", "each", "both", "same", "here",
}


def _tokenize(text: str) -> Set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) >= MIN_WORD_LENGTH and w not in STOPWORDS}


def keyword_overlap(claim_statement: str, evidence_text: str) -> float:
    """Jaccard similarity over significant words. 0 if either side has none."""
    claim_words = _tokenize(claim_statement)
    evidence_words = _tokenize(evidence_text)
    if not claim_words or not evidence_words:
        return 0.0
    intersection = claim_words & evidence_words
    union = claim_words | evidence_words
    return len(intersection) / len(union) if union else 0.0


@dataclass(frozen=True)
class ClaimScore:
    score: float
    reasons: List[str] = field(default_factory=list)


def score_claim(
    claim_statement: str,
    claim_subject_entity_id: Optional[int],
    evidence_text: str,
    mentioned_entity_ids: Set[int],
) -> ClaimScore:
    score = 0.0
    reasons: List[str] = []

    if claim_subject_entity_id is not None and claim_subject_entity_id in mentioned_entity_ids:
        score += ENTITY_MATCH_WEIGHT
        reasons.append("evidence mentions the claim's subject entity")

    overlap = keyword_overlap(claim_statement, evidence_text)
    if overlap > 0:
        score += overlap * KEYWORD_WEIGHT
        reasons.append(f"keyword overlap {overlap:.2f}")

    return ClaimScore(score=round(min(score, 1.0), 4), reasons=reasons)
