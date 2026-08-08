"""Deterministic story scoring.

  - novelty: how recently the claim was created, decaying linearly to 0
    over NOVELTY_WINDOW_DAYS. A two-week-old open claim isn't "new" anymore
    even if nothing else has changed.
  - corroboration: how many *distinct* sources have SUPPORTS evidence
    linked, capped at CORROBORATION_CAP for full credit -- reuses the same
    "distinct sources matter more than repeat posts from one leaker" idea as
    services/confidence.py's corroboration bonus.
  - momentum: how many claim events (new evidence links, confidence
    updates, contradiction checks, ...) happened in the last
    MOMENTUM_WINDOW_DAYS, capped at MOMENTUM_CAP for full credit -- a proxy
    for "is this actively developing right now."

This answers "what deserves investigation," not "what's important" -- it's a
triage signal, not an editorial verdict.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional

NOVELTY_WINDOW_DAYS = 14
CORROBORATION_CAP = 3
MOMENTUM_WINDOW_DAYS = 7
MOMENTUM_CAP = 5

NOVELTY_WEIGHT = 0.3
CORROBORATION_WEIGHT = 0.4
MOMENTUM_WEIGHT = 0.3


@dataclass(frozen=True)
class StoryScore:
    novelty: float
    corroboration: float
    momentum: float
    total: float
    reasons: List[str] = field(default_factory=list)


def score_story(
    created_at: dt.datetime,
    distinct_supporting_sources: int,
    recent_event_count: int,
    now: Optional[dt.datetime] = None,
) -> StoryScore:
    now = now or dt.datetime.utcnow()
    age_days = max((now - created_at).total_seconds() / 86400, 0.0)
    novelty = max(0.0, 1 - age_days / NOVELTY_WINDOW_DAYS)

    corroboration = min(distinct_supporting_sources / CORROBORATION_CAP, 1.0) if CORROBORATION_CAP else 0.0
    momentum = min(recent_event_count / MOMENTUM_CAP, 1.0) if MOMENTUM_CAP else 0.0

    total = round(
        novelty * NOVELTY_WEIGHT + corroboration * CORROBORATION_WEIGHT + momentum * MOMENTUM_WEIGHT, 4
    )

    reasons: List[str] = []
    if novelty > 0:
        reasons.append(f"novelty {novelty:.2f} ({age_days:.1f} days old)")
    if distinct_supporting_sources > 0:
        reasons.append(f"{distinct_supporting_sources} distinct supporting source(s)")
    if recent_event_count > 0:
        reasons.append(f"{recent_event_count} event(s) in the last {MOMENTUM_WINDOW_DAYS} days")

    return StoryScore(
        novelty=round(novelty, 4),
        corroboration=round(corroboration, 4),
        momentum=round(momentum, 4),
        total=total,
        reasons=reasons,
    )
