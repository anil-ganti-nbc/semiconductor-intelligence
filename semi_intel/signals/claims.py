"""Atomic claim extraction, novelty comparison, and contradiction
detection (v1.0.0 Candidate Intelligence, Phases 3/4/7).

Deliberately narrow, per the project's established contradiction_engine
pattern (semi_intel/contradiction_engine/memory_rules.py: "each is a
separate rule module for a later milestone, not a generalization of this
one"): only three numeric claim types are extracted this pass -- core
count, memory size, clock speed -- via plain regex over already-collected
SignalItem text. No LLM, no generalized NLP claim extraction. Extending to
more claim types (price, launch date, SKU) means adding another regex
rule to `CLAIM_PATTERNS`, not redesigning this module.

Two genuinely different comparisons happen here, and the project already
has an internal-inconsistency example of confusing them (see
semi_intel/signals/scoring.py's own "novelty" component, which measures
something else entirely -- independent-group ratio within one candidate):

- **Novelty** (Phase 4): does this claim value differ from what *other,
  earlier* candidates on the same topic already said? Answers "have we
  seen this before".
- **Contradiction** (Phase 7): do items *within this candidate's own
  evidence* disagree on a claim's value right now? Answers "do our
  current sources agree with each other".
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.models import CandidateSignalItem, SignalCandidate, SignalItem, Source

CLAIM_PATTERNS: dict[str, re.Pattern] = {
    "core_count": re.compile(r"(\d{1,3})[\s-]*core", re.I),
    "memory_size_gb": re.compile(r"(\d{1,4})\s*GB\b(?!\s*/\s*s)", re.I),  # excludes GB/s bandwidth figures
    "clock_speed_ghz": re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*GHz\b", re.I),
}
CLAIM_UNITS = {"core_count": "cores", "memory_size_gb": "GB", "clock_speed_ghz": "GHz"}


@dataclass
class ClaimObservation:
    claim_type: str
    value: float
    unit: str
    signal_item_id: int
    source_id: int
    source_name: str
    posted_at: object
    snippet: str

    def to_dict(self) -> dict:
        return {
            "claim_type": self.claim_type, "value": self.value, "unit": self.unit,
            "signal_item_id": self.signal_item_id, "source_id": self.source_id,
            "source_name": self.source_name,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "snippet": self.snippet,
        }


def extract_numeric_claims_from_text(text: str) -> list[tuple[str, float]]:
    """Pure function, no DB -- one (claim_type, value) pair per regex
    match. A single item's text can assert more than one claim type."""
    found: list[tuple[str, float]] = []
    for claim_type, pattern in CLAIM_PATTERNS.items():
        for match in pattern.finditer(text or ""):
            try:
                found.append((claim_type, float(match.group(1))))
            except ValueError:
                continue
    return found


def _snippet(text: str, value: float) -> str:
    text = text or ""
    idx = text.find(str(int(value)) if value == int(value) else str(value))
    if idx < 0:
        return text[:80]
    start, end = max(0, idx - 30), min(len(text), idx + 30)
    return text[start:end].strip()


def extract_candidate_claims(session: Session, candidate: SignalCandidate) -> list[ClaimObservation]:
    rows = list(session.execute(
        select(SignalItem, Source.name)
        .join(CandidateSignalItem, CandidateSignalItem.signal_item_id == SignalItem.id)
        .join(Source, Source.id == SignalItem.source_id)
        .where(CandidateSignalItem.candidate_id == candidate.id)
    ))
    observations: list[ClaimObservation] = []
    for si, source_name in rows:
        text = f"{si.title or ''} {si.normalized_text or ''}"
        for claim_type, value in extract_numeric_claims_from_text(text):
            observations.append(ClaimObservation(
                claim_type=claim_type, value=value, unit=CLAIM_UNITS[claim_type],
                signal_item_id=si.id, source_id=si.source_id, source_name=source_name,
                posted_at=si.posted_at, snippet=_snippet(text, value),
            ))
    return observations


@dataclass
class Contradiction:
    claim_type: str
    unit: str
    values: dict  # value -> list[ClaimObservation]
    stronger_value: float | None
    reason: str

    def to_dict(self) -> dict:
        return {
            "claim_type": self.claim_type, "unit": self.unit,
            "values": {
                str(value): [obs.to_dict() for obs in observations]
                for value, observations in self.values.items()
            },
            "stronger_value": self.stronger_value, "reason": self.reason,
        }


def detect_contradictions(observations: list[ClaimObservation]) -> list[Contradiction]:
    """Same claim_type, different values, within the SAME candidate's
    current evidence. The 'stronger' value is whichever has more distinct
    contributing sources -- a simple, transparent, non-weighted count, not
    a confidence score (that's the confidence engine's job, which folds
    contradiction penalties back in separately)."""
    by_type: dict[str, dict[float, list[ClaimObservation]]] = defaultdict(lambda: defaultdict(list))
    for obs in observations:
        by_type[obs.claim_type][obs.value].append(obs)

    contradictions: list[Contradiction] = []
    for claim_type, by_value in by_type.items():
        if len(by_value) < 2:
            continue
        ranked = sorted(
            by_value.items(),
            key=lambda kv: len({o.source_id for o in kv[1]}),
            reverse=True,
        )
        stronger_value, stronger_obs = ranked[0]
        distinct_counts = {value: len({o.source_id for o in obs}) for value, obs in by_value.items()}
        tie = len({c for c in distinct_counts.values()}) == 1
        contradictions.append(Contradiction(
            claim_type=claim_type, unit=CLAIM_UNITS[claim_type], values=dict(by_value),
            stronger_value=None if tie else stronger_value,
            reason=(
                f"{len(by_value)} conflicting {claim_type.replace('_', ' ')} value(s) observed"
                + ("; evenly split, no stronger value yet" if tie else
                   f"; {stronger_value:g} {CLAIM_UNITS[claim_type]} has more independent source(s) "
                   f"({distinct_counts[stronger_value]} vs {max(c for v, c in distinct_counts.items() if v != stronger_value)})")
            ),
        ))
    return contradictions


@dataclass
class NoveltyFinding:
    claim_type: str
    status: str  # "first_appearance" | "repeated" | "updated"
    previous_value: float | None
    new_value: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "claim_type": self.claim_type, "status": self.status,
            "previous_value": self.previous_value, "new_value": self.new_value, "reason": self.reason,
        }


def compute_claim_novelty(
    session: Session, candidate: SignalCandidate, observations: list[ClaimObservation],
) -> list[NoveltyFinding]:
    """Compares this candidate's claims against OTHER candidates sharing
    the same primary_topic that were observed earlier -- deliberately
    excludes MERGED candidates (superseded, not history) but includes
    ACTIVE/PROMOTED/DISMISSED/SNOOZED/STALE ones, since a claim can be
    'seen before' regardless of what happened to that earlier candidate."""
    if not observations or not candidate.primary_topic_id:
        return []

    claim_types_present = {obs.claim_type for obs in observations}
    prior_candidates = list(session.scalars(
        select(SignalCandidate).where(
            SignalCandidate.primary_topic_id == candidate.primary_topic_id,
            SignalCandidate.id != candidate.id,
            SignalCandidate.first_observed_at < candidate.first_observed_at,
        )
    ))

    prior_values: dict[str, set] = defaultdict(set)
    for prior in prior_candidates:
        for obs in extract_candidate_claims(session, prior):
            if obs.claim_type in claim_types_present:
                prior_values[obs.claim_type].add(obs.value)

    findings: list[NoveltyFinding] = []
    seen_types: set = set()
    for obs in sorted(observations, key=lambda o: o.claim_type):
        if obs.claim_type in seen_types:
            continue
        seen_types.add(obs.claim_type)
        prior = prior_values.get(obs.claim_type, set())
        unit = obs.unit
        if not prior:
            findings.append(NoveltyFinding(
                claim_type=obs.claim_type, status="first_appearance",
                previous_value=None, new_value=obs.value,
                reason=f"First appearance of a {obs.claim_type.replace('_', ' ')} claim on this topic",
            ))
        elif obs.value in prior:
            findings.append(NoveltyFinding(
                claim_type=obs.claim_type, status="repeated",
                previous_value=obs.value, new_value=obs.value,
                reason=f"Same {obs.claim_type.replace('_', ' ')} ({obs.value:g} {unit}) as previously reported",
            ))
        else:
            previous = sorted(prior)[-1]
            findings.append(NoveltyFinding(
                claim_type=obs.claim_type, status="updated",
                previous_value=previous, new_value=obs.value,
                reason=(
                    f"{obs.claim_type.replace('_', ' ').title()} changed from "
                    f"{previous:g} {unit} to {obs.value:g} {unit}"
                ),
            ))
    return findings
