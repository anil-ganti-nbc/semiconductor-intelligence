# Candidate Intelligence (v1.0.0)

A deterministic editorial-intelligence layer that explains *why* a
SignalCandidate matters, sitting between clustering and editorial review:

```
Sources -> Collectors -> SignalItems -> Candidate clustering
        -> Candidate Intelligence -> Editorial Review -> [LLM Brief, deferred] -> Story
```

Every score is explainable (a `{total, components: {name: {raw_value,
weight, contribution, detail}}, penalties}` structure, the same shape
`signals/scoring.py`'s attention score already uses). Nothing here calls
an LLM. Phase 9 (an optional LLM editorial brief) is explicitly deferred
per the milestone's own instruction — it may only be built once Phases
1-8 are validated, and this release stops short of it.

## Why this doesn't duplicate existing infrastructure

Before writing any code, the existing scoring/independence/timeline
infrastructure was audited in full (see the session's research notes).
Key findings that shaped this design:

- **Attention score** (`signals/scoring.py`) already answers "should a
  human look at this at all" — topic relevance, novelty (independent-group
  ratio), momentum, source diversity, artifact strength, source quality.
  Confidence and editorial value are *deliberately different questions*
  and reuse none of its component names.
- **Independence/echo-chamber grouping** (`signals/independence.py`)
  already exists and is the real origin-detection mechanism — four
  concrete rules (same_url, same_author, lineage, citation), union-find,
  `SignalIndependenceGroup.origin_signal_item_id`. The Origin Graph
  (Phase 2) is a thin, honest presentation layer over this data, not a
  reimplementation. One real bug was found and fixed in the audited code:
  origin selection picked the group's earliest-*inserted* item (by SQL
  row id) instead of the earliest-*posted* one, contradicting its own
  docstring — fixed with a regression test
  (`test_origin_is_the_earliest_posted_item_not_the_lowest_id`).
- **Claim confidence** (`services/confidence.py`) is a separate,
  pre-existing concept scoped to human-authored citation `Claim`s, not
  SignalCandidates. Untouched.
- **Contradiction detection** (`contradiction_engine/`) already exists
  as one narrow, deterministic rule module (GDDR memory-config
  arithmetic) with an explicit stated philosophy: "each is a separate
  rule module for a later milestone, not a generalization of this one."
  This release's contradiction detection (`signals/claims.py`) follows
  the same philosophy at the candidate level: three numeric claim types
  this pass (core count, memory size, clock speed), extensible one regex
  rule at a time — not a general NLP contradiction engine.
- **"Novelty"** already has two unrelated existing meanings (attention
  score's independent-group ratio; `story_scoring`'s claim-triage novelty
  decay). This release's claim-level novelty (Phase 4) is a *third*,
  clearly distinct meaning — "have we seen this exact claim value on this
  topic before" — and is named/scoped to avoid colliding with either.

## Modules

| Module | Phase | Answers |
|---|---|---|
| `signals/reputation.py` | 1 | How trustworthy has this source been, by the numbers? |
| `signals/origin_graph.py` | 2 | Where did this originate, and what's an echo vs. an independent confirmation? |
| `signals/claims.py` | 3, 4, 7 | What does the evidence actually claim, is it new, and does it agree with itself? |
| `signals/confidence_engine.py` | 5 | How likely is this to be true? |
| `signals/editorial_value_engine.py` | 6 | Should an editor care? |
| `signals/verification.py` | 8 | What should a human check next? |
| `signals/timeline_stage.py` | 11 | What stage has this story reached? |
| `signals/candidate_intelligence.py` | orchestrator | Ties all of the above together for one candidate |

## Phase 1 — Source Reputation

New table `source_reputations`, one row per `Source` (1:1,
`get_or_create_reputation`). Every field is a deterministic counter or
ratio, recomputed from scratch each time (`recompute_source_reputation`,
idempotent):

- `originality` = independence groups this source *originated* /
  independence groups it *appeared in* — did it break the story, or just
  confirm someone else's?
- `editorial_yield` = candidates it contributed to that were later
  promoted / total candidates it contributed to
- `noise_rate` = candidates it contributed to that were later dismissed /
  total candidates it contributed to
- `verification_count` / `false_positive_count` = candidates it
  *originated* that were later promoted / dismissed, respectively (a
  narrower, origination-specific counterpart to yield/noise)
- `lead_time_hours` = average hours between this source's origin item and
  the next independent confirmation in the same group
- `authority` = `0.4*originality + 0.4*editorial_yield + 0.2*(1-noise_rate)`,
  clamped to [0,1], defaulting to 0.5 (neutral, not "bad") for a source
  with no candidate history yet
- `authority_override` — an operator-pinned value (`PUT
  /api/radar/source-reputations/{id}/override`) that always wins over the
  computed value (`effective_authority()`), without ever deleting or
  hiding the computed one

No machine learning anywhere in this module — every number can be
recomputed from scratch and will reproduce identically.

## Phase 2 — Origin Graph

`build_origin_graph()` structures existing `SignalIndependenceGroup`/
`SignalIndependenceGroupMember` data as an explicit graph: one origin node
per group, edges from origin to every echo (labeled with the group's
`reason` — same_url/same_author/lineage/citation), plus explicit
quote/reply lineage edges even across group boundaries. Never infers
independence from timestamps alone — every edge traces to one of
independence.py's four concrete rules or an explicit reply/quote
reference. Reports "N independent confirmations, M echoes" rather than a
bare source count, directly preventing the failure mode the spec named:
"never report 5 sources when there are really 1 origin, 4 echoes."

## Phase 3/4/7 — Claims, Novelty, Contradictions (`signals/claims.py`)

**Extraction** (Phase 3): three deterministic regex patterns —
`core_count` (`\d+[\s-]*core`), `memory_size_gb` (`\d+\s*GB`, explicitly
excluding `GB/s` bandwidth figures), `clock_speed_ghz` (`\d+(\.\d+)?\s*GHz`).
No LLM. Extending to more claim types (price, launch date, SKU) means
adding another entry to `CLAIM_PATTERNS`, not redesigning anything.

**Novelty** (Phase 4): compares this candidate's claims against *other,
earlier* candidates sharing the same `primary_topic_id`. Three outcomes:
`first_appearance` (never seen this claim type on this topic before),
`repeated` (same value as before), `updated` (different value — the
"Launch Q1 → Launch Q2" example from the spec, realized with the claim
types this pass actually implements).

**Contradictions** (Phase 7): same claim type, different values, *within
one candidate's current evidence* — a different question from novelty
(which compares across time/candidates). The "stronger" value is whichever
has more distinct contributing sources, a transparent count, not a hidden
score; a tie is reported as a tie, never silently resolved.

## Phase 5 — Confidence Engine

Six weighted components (weights sum to 1.0): `source_authority` (0.25,
from Phase 1), `independent_confirmations` (0.25, reuses
`candidate.independent_source_group_count`), `official_documentation`
(0.15, the "Official Announcement" `SignalLabel`), `structured_identifiers`
(0.15, reuses `ARTIFACT_STRENGTH_RANK` from `signals/scoring.py`),
`historical_source_accuracy` (0.10, from `SourceReputation`, neutral 0.5
when unknown), `time_consistency` (0.10, a genuine deterministic check:
does any item's `posted_at` precede what it quotes/replies to — an
impossible ordering). Penalty: -0.15 per detected contradiction, clamped
so confidence never goes negative.

## Phase 6 — Editorial Value Engine

Six weighted components: `product_importance` (0.25, `MonitoredTopic.priority`),
`novelty` (0.20, best Phase-4 finding for this candidate), `officiality`
(0.15), `exclusivity` (0.15, inverse of how many other candidates on the
same topic were promoted in the last 30 days), `freshness` (0.15, linear
decay over 72 hours), `verification_effort` (0.10, reuses the structured-
identifier rank — already-verified evidence needs less editor work).
Never takes confidence as an input (`compute_editorial_value`'s signature
has no confidence parameter, enforced by a test) — a candidate can be
low-confidence and high editorial value simultaneously, and this score
alone never auto-publishes or auto-promotes anything.

## Phase 8 — Verification Checklist

Deterministic rule table (`signals/verification.py`), additive — a
candidate can trigger any number of rules: missing official documentation
→ "search OEM sites"; `strongest_artifact_type` in a known set → the
matching check (pci_id → registry cross-check, benchmark → Geekbench
search, codename/product/version → prior-SKU comparison, retailer →
catalog check); each detected contradiction → its own resolution step;
fewer than 2 independent confirmation groups → "wait for/search for
confirmation"; no resolved entity → "identify and resolve the subject
entity". If nothing applies, that itself is reported explicitly (never an
ambiguous empty list).

## Phase 11 — Timeline Stage

`classify_timeline_stage()` is a pure decision tree (no DB access) over
already-computed evidence: `rumor` → `emerging` (2 confirmation groups) →
`corroborated` (3+) → `pre_launch` (launch-adjacent artifact + topic
match, no official doc yet) → `confirmed` (official documentation present)
→ `released` (official + promoted). `corrected`/`disproven` are assigned
*only* from an explicit keyword match in a dismissal reason — a dismissal
for an unrelated reason (spam, duplicate, out of scope) never gets
silently relabeled as "the story turned out to be false" (regression test:
`test_generic_dismissal_does_not_get_mislabeled_as_disproven`).

## API (Phase 13)

One consolidated, structured endpoint rather than seven thin wrappers
around the same underlying computation:

```
GET  /api/radar/candidates/{id}/intelligence
```
Returns `{origin_graph, claims, novelty, contradictions, confidence,
editorial_value, verification_checklist, timeline_stage}` — every section
is its own named, structured object, never an opaque blob. Also writes
`confidence_score`/`editorial_value_score`/`timeline_stage` back onto the
candidate row (now included in the existing candidate list/detail
payloads) so list views can sort on them without recomputing every
candidate on every request.

```
GET  /api/radar/source-reputations
POST /api/radar/source-reputations/recompute
PUT  /api/radar/source-reputations/{source_id}/override
```

**Design decision on endpoint count**: the spec's Phase 13 lists seven
separate endpoints (intelligence, timeline, origin graph, claims,
contradictions, verification checklist, editorial brief). All but the
editorial brief (deferred) are already substructures of one candidate's
intelligence computation and are always computed together in one pass —
splitting them into separate HTTP round-trips would mean either
recomputing shared inputs per endpoint or introducing a cache layer for
no functional benefit. One endpoint with clearly named, structured
sections satisfies "no endpoint should return opaque blobs" without the
redundant plumbing.

## UI (Phase 10)

A "Candidate Intelligence" section added to the existing candidate detail
card (`showRadarCandidate()` in `index.html`), loaded on demand via a
"Load candidate intelligence" button (kept separate from the main detail
load since the intelligence computation does real claim-extraction/DB
work, matching the project's existing pattern of expensive actions being
explicitly triggered rather than bundled into every page load). Renders,
in order: timeline stage badge, origin & confirmation counts, expandable
confidence/editorial-value component breakdowns (`<details>` elements —
"everything expandable"), extracted claims, novelty findings,
contradictions (auto-expanded when present), verification checklist, and
finally a one-line free-text summary — facts first, summary last, per the
spec's explicit UI requirement.

## Phase 12 — Operator Feedback

`SourceReputation.verification_count`/`false_positive_count` are
transparent counters incremented purely by recomputation from real
promote/dismiss outcomes (Phase 1) — no machine learning, no hidden
weighting. A candidate's own promote/dismiss/snooze actions already exist
and are unchanged by this release; this release's contribution is making
those outcomes feed back into source-level statistics, not adding new
operator actions.

## Known limitations

- Claim extraction covers three numeric types (core count, memory size,
  clock speed) — price, launch date, SKU, and other claim types from the
  spec's examples are not implemented, per the project's own
  one-rule-module-at-a-time contradiction-engine philosophy.
- Contradiction detection is claim-type-scoped (same type, different
  value within one candidate) — cross-claim-type reasoning (e.g. "16-core
  implies at least X memory bandwidth") is not implemented.
- Novelty comparison is topic-scoped, not entity-scoped — two different
  products sharing a topic could theoretically cross-contaminate novelty
  findings; narrowing to `CandidateEntity`-based comparison is a
  reasonable follow-up once entity resolution is more consistently
  populated.
- `/intelligence` recomputes on every call rather than caching -- fine at
  current data volumes, not yet incremental.
- Phase 9 (LLM editorial brief) is explicitly not implemented this
  release, per the milestone's own instruction to defer it until Phases
  1-8 are validated.
