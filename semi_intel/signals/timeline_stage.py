"""Timeline Stage Classification (v1.0.0 Candidate Intelligence, Phase 11).

Deterministic decision tree over evidence already computed by other
Candidate Intelligence modules (independence groups, artifact strength,
official-documentation presence) plus the candidate's own state --
classifies the current evolution stage. "Corrected"/"Disproven" are only
assigned from an explicit dismissal reason keyword match, never inferred
from silence; a dismissal for unrelated reasons (spam, duplicate, out of
scope) does not retroactively relabel a story as "disproven".
"""

from __future__ import annotations

from dataclasses import dataclass

from semi_intel.domain.enums import SignalCandidateState
from semi_intel.domain.models import SignalCandidate

STAGES = (
    "rumor", "emerging", "corroborated", "pre_launch",
    "confirmed", "released", "corrected", "disproven",
)
_LAUNCH_ADJACENT_ARTIFACTS = ("pci_id", "benchmark", "retailer")
_DISPROVEN_KEYWORDS = ("false", "incorrect", "disproven", "debunked", "fake", "hoax")
_CORRECTED_KEYWORDS = ("corrected", "correction", "retracted", "revised", "walked back")


@dataclass
class TimelineStageResult:
    stage: str
    reason: str

    def to_dict(self) -> dict:
        return {"stage": self.stage, "reason": self.reason}


def classify_timeline_stage(candidate: SignalCandidate, official_documentation: bool) -> TimelineStageResult:
    if candidate.state == SignalCandidateState.DISMISSED and candidate.dismissed_reason:
        reason_low = candidate.dismissed_reason.lower()
        if any(kw in reason_low for kw in _DISPROVEN_KEYWORDS):
            return TimelineStageResult("disproven", f"Dismissed as false: {candidate.dismissed_reason}")
        if any(kw in reason_low for kw in _CORRECTED_KEYWORDS):
            return TimelineStageResult("corrected", f"Dismissed as corrected: {candidate.dismissed_reason}")

    if official_documentation and candidate.state == SignalCandidateState.PROMOTED:
        return TimelineStageResult("released", "Officially documented and published as a story")
    if official_documentation:
        return TimelineStageResult("confirmed", "Official documentation present")
    if candidate.strongest_artifact_type in _LAUNCH_ADJACENT_ARTIFACTS and candidate.primary_topic_id:
        return TimelineStageResult(
            "pre_launch",
            f"Launch-adjacent evidence ({candidate.strongest_artifact_type}) observed, not yet officially confirmed",
        )
    if candidate.independent_source_group_count >= 3:
        return TimelineStageResult(
            "corroborated", f"{candidate.independent_source_group_count} independent confirmation groups",
        )
    if candidate.independent_source_group_count == 2:
        return TimelineStageResult("emerging", "A second independent confirmation group has appeared")
    return TimelineStageResult(
        "rumor", f"Only {candidate.independent_source_group_count} independent confirmation group(s) so far",
    )
