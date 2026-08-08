"""Deterministic claim-confidence scoring.

This is intentionally NOT a Bayesian posterior. It is a transparent, inspectable
formula so a journalist (or a future contributor) can answer "why is this claim
at 70%?" in one sentence, by reading this file. Swap it for a calibrated
probabilistic model once enough resolved claims exist to calibrate against
(see roadmap milestone M4, source trust scoring).

Rules:
  - Start at a neutral baseline.
  - Each piece of supporting evidence pushes confidence up, weighted by how
    much the source is trusted.
  - Weakening evidence pulls confidence down modestly; contradicting evidence
    pulls it down hard, since engineering contradictions matter more than
    soft doubt.
  - Corroboration from multiple *distinct* sources earns a small, capped
    bonus, so five posts from one leaker can't outscore two independent ones.
  - The result is always clamped away from 0 and 1 -- nothing is ever fully
    certain or fully impossible from evidence alone.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from semi_intel.domain.enums import EvidenceStance

SUPPORT_WEIGHT = 0.15
WEAKEN_WEIGHT = 0.10
CONTRADICT_WEIGHT = 0.25
CORROBORATION_BONUS_PER_SOURCE = 0.05
CORROBORATION_BONUS_CAP = 0.20
BASELINE = 0.5
MIN_CONFIDENCE = 0.02
MAX_CONFIDENCE = 0.98

# (stance, source_id, source_trust_weight)
StanceRecord = Tuple[EvidenceStance, Optional[int], float]


def compute_confidence(stances: Sequence[StanceRecord]) -> float:
    if not stances:
        return BASELINE

    score = BASELINE
    supporting_sources: set[Optional[int]] = set()

    for stance, source_id, trust in stances:
        trust = max(0.0, min(1.0, trust))
        if stance == EvidenceStance.SUPPORTS:
            score += SUPPORT_WEIGHT * trust
            supporting_sources.add(source_id)
        elif stance == EvidenceStance.WEAKENS:
            score -= WEAKEN_WEIGHT * trust
        elif stance == EvidenceStance.CONTRADICTS:
            score -= CONTRADICT_WEIGHT * trust

    if len(supporting_sources) > 1:
        bonus = min(
            CORROBORATION_BONUS_PER_SOURCE * (len(supporting_sources) - 1),
            CORROBORATION_BONUS_CAP,
        )
        score += bonus

    return round(max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, score)), 4)
