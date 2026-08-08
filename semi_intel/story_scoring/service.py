"""Ranks OPEN claims by story score. Gathers the raw signals (distinct
supporting sources, recent event count) from the database and hands them to
scoring.score_story -- no logic lives here beyond assembling those inputs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import ClaimStatus, EvidenceStance
from semi_intel.domain.models import Claim, ClaimEvent, ClaimEvidenceLink, Evidence
from semi_intel.story_scoring.scoring import MOMENTUM_WINDOW_DAYS, StoryScore, score_story


@dataclass(frozen=True)
class RankedClaim:
    claim: Claim
    score: StoryScore


class StoryScoringService:
    def __init__(self, session: Session):
        self.session = session

    def rank(self, limit: Optional[int] = None, now: Optional[dt.datetime] = None) -> List[RankedClaim]:
        now = now or dt.datetime.utcnow()
        claims = list(self.session.scalars(select(Claim).where(Claim.status == ClaimStatus.OPEN)))

        ranked = []
        for claim in claims:
            distinct_sources = self._distinct_supporting_sources(claim.id)
            recent_events = self._recent_event_count(claim.id, now)
            score = score_story(claim.created_at, distinct_sources, recent_events, now=now)
            ranked.append(RankedClaim(claim=claim, score=score))

        ranked.sort(key=lambda r: r.score.total, reverse=True)
        return ranked[:limit] if limit is not None else ranked

    def _distinct_supporting_sources(self, claim_id: int) -> int:
        stmt = (
            select(Evidence.source_id)
            .join(ClaimEvidenceLink, ClaimEvidenceLink.evidence_id == Evidence.id)
            .where(ClaimEvidenceLink.claim_id == claim_id, ClaimEvidenceLink.stance == EvidenceStance.SUPPORTS)
            .distinct()
        )
        return len(list(self.session.scalars(stmt)))

    def _recent_event_count(self, claim_id: int, now: dt.datetime) -> int:
        cutoff = now - dt.timedelta(days=MOMENTUM_WINDOW_DAYS)
        stmt = (
            select(func.count())
            .select_from(ClaimEvent)
            .where(ClaimEvent.claim_id == claim_id, ClaimEvent.created_at >= cutoff)
        )
        return self.session.scalar(stmt) or 0
