# MILESTONE 2: SIGNAL RADAR PROMOTION BRIDGE DESIGN

## 1. Problem Statement & Current State
The system successfully collects raw intelligence (5,713 items) and clusters them into candidates (402 clusters) via the Signal Radar lifecycle. However, 0 promotions have occurred because the system currently lacks a working, deterministic bridge in the editorial workflow to transition undefined "candidate mentions" into fully structured, canonical `Entities` within the knowledge graph. 

Currently, `semi_intel/signals/promotion.py` handles transitions to `EditorialStory` and `Evidence` tables, but it completely ignores the `SignalEntityMention` table where newly surfaced product/company names sit perpetually in a `CANDIDATE` state.

## 2. Authoritative Runtime & Pathway
Before proposing changes, the authoritative operational path is identified as follows:
- **API Entry Point**: `semi_intel/web/app.py` -> `@app.post("/api/radar/candidates/{candidate_id}/promote")`
- **Core Business Logic**: `semi_intel.signals.promotion.promote_candidate()`
- **Schema Responsibility**: `semi_intel.db` (Alembic mappings in `semi_intel/domain/models.py`)
  - Sources: `SignalItem`
  - Current Unresolved State: `SignalEntityMention` (status: `CANDIDATE`)
  - Target Resolved State: `Entity` & `CandidateEntity`

## 3. Proposed Deterministic Bridge Architecture

To resolve the 0% promotion rate without artificially lowering statistical thresholds, the promotion bridge must link the Signal Candidate review explicitly to Entity resolution. 

### A. The Schema Pathway
1. **Extraction**: Identify all `SignalEntityMention` rows associated with the `SignalItem`s of a given `SignalCandidate`.
2. **Review Gateway**: When an operator invokes `promote_candidate` (via the UI), they are presented with out-of-bag candidate mentions. 
3. **Resolution Actions**:
   - **Promote**: Transition a `SignalEntityMention` to a fully registered `Entity` targeting the `entities` table. Update the mention to `status = RESOLVED` and assign `resolved_entity_id`.
   - **Merge**: Map the mention to an existing canonical `Entity`.
   - **Reject**: Transition the mention's status to `REJECTED` to prune noise.

### B. Logical Guarantees & Constraints
- **Do not modify raw data**: `SignalItem.raw_payload` remains strictly immutable. 
- **Preserve Separation of Concerns**: OEM Radar logic remains disjointed. This is solely handled on the `semi_intel` application boundary.
- **Deduplication**: `Evidence` content hashes and idempotent pipeline promotion checks (`candidate.promoted_story_id is not None`) remain in effect. Re-promoting will safely bypass duplicate processing.
- **Threshold Integrity**: Automatic promotion rules (`attention_score`, etc.) are left completely intact. The lack of promotions is an architectural bridge leak, not a threshold ceiling issue.

## 4. Execution Strategy (Next Steps)
1. Augment the `CandidatePromoteRequest` schema in `web/schemas.py` to accept an optional array of `resolved_entities` actions.
2. Extend `semi_intel.signals.promotion.promote_candidate()` to parse these resolutions and safely apply them to the `entities` and `signal_entity_mentions` tables within the same transactional scope as the Story promotion.
3. Update the UI `index.html` to visualize `SignalEntityMention` objects during the promotion workflow, preventing them from being silently buried.
