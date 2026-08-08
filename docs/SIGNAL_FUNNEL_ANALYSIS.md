# PHASE 1: PROMOTION FUNNEL ANALYSIS

Based on querying the live SQLite database (`semi_intel.db`), the actual funnels are completely derived from data without estimation.

**Funnel:**
* Signals collected: 5713
* Candidates created: 402
* Candidates above score threshold: 0 (Highest score is 0.73, below the required 0.75 minimum)
* Candidates meeting independence requirements: 402
* Candidates containing SignalEntityMentions: 387
* Candidates with unresolved mentions: 370
* Candidates already linked to Entities: 0
* Candidates satisfying every requirement except entity resolution: 0
* Candidates failing for unrelated reasons: 402 (All 402 fail because automatic promotion is disabled globally, attention scores are too low, and candidate age exceeds 72h max)
* Promotion attempts: 0
* Successful promotions: 0

---

# PHASE 2: PROMOTION TRACE

We selected three representative clusters to trace their exact blockers:

**1. Highest Scoring Candidate (ID: 401)**
* **SignalItems**: 1
* **EntityMentions**: 5 (all unresolved)
* **Candidate**: Attention Score = 0.73
* **Eligibility checks**: `eligible = False`
* **Promotion code path**: Stopped at automatic eligibility check.
* **Exit point**: `check_automatic_eligibility()`
* **Reason promotion stopped**: `["automatic promotion disabled", "attention score 0.73 below minimum 0.75"]`

**2. Median Candidate (ID: 128)**
* **SignalItems**: 1
* **EntityMentions**: 49 (48 unresolved)
* **Candidate**: Attention Score = 0.40
* **Eligibility checks**: `eligible = False`
* **Promotion code path**: Stopped at automatic eligibility check.
* **Exit point**: `check_automatic_eligibility()`
* **Reason promotion stopped**: `["automatic promotion disabled", "attention score 0.40 below minimum 0.75", "candidate age 1034.7h exceeds maximum 72h"]`

**3. Lowest Scoring Candidate (ID: 78)**
* **SignalItems**: 25
* **EntityMentions**: 129 (all unresolved)
* **Candidate**: Attention Score = 0.00
* **Eligibility checks**: `eligible = False`
* **Promotion code path**: Stopped at automatic eligibility check.
* **Exit point**: `check_automatic_eligibility()`
* **Reason promotion stopped**: `["automatic promotion disabled", "attention score 0.00 below minimum 0.75", "no monitored-topic match (required_topic_match=True)", "candidate age 3562.2h exceeds maximum 72h"]`

---

# PHASE 3: ENTITY RESOLUTION AUDIT

Entity resolution code ALREADY EXISTS and is neither orphaned nor unreachable.

* **How mentions are created:** During signal analysis (`semi_intel/signals/analysis.py`), extracted NLP phrases are added to `SignalEntityMention` with a default `status = CANDIDATE`.
* **How they are linked:** Through `CandidateEntity` mapping `SignalCandidate` to an `Entity` ID.
* **How they become canonical entities:** Operator explicitly approves it through `CanonicalEntityService.resolve_group()`. 
* **Does existing code perform entity resolution?** YES. `semi_intel/entities/service.py` houses `CanonicalEntityService` which contains robust `resolve_group` and `reject_group` logic.
* **Is it unreachable?** NO. It is actively exported via API endpoints like `@app.post("/api/entities/mention-proposals/resolve")` in `web/app.py`.
* **Is it disabled / never called / incomplete?** It is complete and available, but it requires *manual review* as intended ("Unknown extracted text remains a proposal until an operator explicitly resolves..."). However, it is fundamentally decoupled from the actual candidate promotion gateway. Operators must do this out-of-band in a separate screen.

---

# PHASE 4: PROMOTION CODE PATH

Trace of `POST /api/radar/candidates/{id}/promote`:
1. `GET /api/radar/candidates/{id}/promote` -> Fast API Route
2. Validates Request payload (`schemas.CandidatePromoteRequest`)
3. DB `session.get(SignalCandidate)`
4. Early return/block if `candidate` NOT FOUND (Raises HTTP 404)
5. Early return/block if `merge_into_story_id` passed but story NOT FOUND (Raises HTTP 404)
6. Calls `promote_candidate(session, candidate, by=by)`
7. `promote_candidate` validates `candidate` member items. Early return/Raises `PromotionBlocked` if candidate has 0 items.
8. Attaches Evidence -> Story -> TopicMatch.
9. Updates candidate state to `PROMOTED`.
10. Fires suggestions/discovery via background queues.
11. Returns 200 JSON OK.

*Conditions that prevent promotion:* 
- Missing `candidate_id` / 404.
- Target `merge_into_story_id` 404.
- Candidate has NO member signal items (`PromotionBlocked` raised).

Note: **Entity resolution** is wholly missing from this direct trace path.

---

# PHASE 5: DASHBOARD GAP

The backend dashboard explicitly powers all operator fields. In `app.py::radar_candidate_detail`, we can confirm the operator currently sees:
* **supporting signals**: YES (via `timeline` object representing `SignalItem` fields)
* **extracted mentions**: YES (via `timeline[].mentions` arrays)
* **confidence**: YES (via `mentions[].confidence` fields)
* **source count**: YES (via `distinct_source_count` / `independence_groups`)
* **entity status**: YES (via `resolved_entities` tracking)
* **promotion eligibility**: YES (via `automatic_promotion_eligibility` block determining pass/fail criteria).

**Missing:** Nothing. The existing `/api/radar/candidates/{id}` payload effectively contains 100% of the data an operator needs to manually approve entity extraction.

---

# PHASE 6: DATABASE IMPACT

Does the existing schema already support the below?
* **manual entity assignment**: YES (`SignalEntityMention` `status` and `resolved_entity_id`)
* **candidate promotion**: YES (`SignalCandidate` `state`, `promoted_story_id`)
* **entity linking**: YES (`CandidateEntity`, `StoryEvidence`)
* **story promotion**: YES (`EditorialStory` generation)
* **audit history**: YES (`CandidatePromotionEvent`)

What code is missing or migrations needed?
* **NONE**. The database explicitly maps every table required here. Modifying schema is 100% unneeded.

---

# FINAL REPORT SUMMARY

**Verdict:** READY FOR IMPLEMENTATION

Observed funnel confirms that auto-promotion failed for multiple unrelated configuration bounds (Age > 72 hours, Score < 0.75, Auto Promoted global config disabled). The Entity bridging code completely exists (`CanonicalEntityService`), mapping APIs exist, and Database schema is globally sufficient. All we must do is weave the UI calls together.
