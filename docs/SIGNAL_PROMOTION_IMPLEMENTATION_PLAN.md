# SIGNAL PROMOTION IMPLEMENTATION PLAN

## FINAL REPORT
SIGNAL PROMOTION VALIDATION

**Verdict:**
READY WITH CONDITIONS
(The missing entity bridge is factually present but decoupled from the manual promotion gateway UI, while *automatic* zero promotions are purely driven by default-deny thresholds).

**Observed funnel:**
- Signals: 5713
- Candidates: 402
- Eligible: 0
- Blocked by entity resolution: 0 (No candidates bypassed all other checks to arrive *strictly* at an entity resolution failure step)
- Blocked by other causes: 402
- Promotions: 0

**Root cause:**
- Primary: Safe default configurations blocking auto-promotions globally (`automatic_promotion_enabled = False` and strict minimum `attention_score=0.75`).
- Secondary: The "Candidate -> Entity" UI mapping is decoupled from the actual promotion page, requiring dual tabs/passes that operators naturally missed.
- Promotion function reached: YES (It is reachable by API, but Auto-Promotion cron exits early returning `eligible=False`).
- Entity resolution already exists: YES (Fully supported in `CanonicalEntityService` mapping APIs).
- Dashboard already exposes sufficient information: YES (All extraction payloads return via `radar_candidate_detail`).

**Database changes required:**
NONE

**Largest implementation risk:**
Coupling Entity Resolution to the explicit Promotion payload correctly, to ensure partial failures in creating an entity do not roll back a valid promotion.

## IMPLEMENTATION STAGES

### Stage 2.2 Manual entity assignment
* **Estimated files:** 2 (`app.py`, `semi_intel/web/static/index.html`)
* **Estimated migrations:** 0
* **Estimated tests:** 1 (`test_entities_api.py` or equivalent)
* **Risk:** Low (Mostly linking existing frontend logic to already-exposed `/api/entities/mention-proposals/resolve` endpoints). 

### Stage 2.3 Promotion bridge
* **Estimated files:** 3 (`schemas.py`, `app.py`, `promotion.py`)
* **Estimated tests:** 2 (`test_promotion.py`, validation edge cases)
* **Risk:** Moderate (Ensuring transaction isolation inside `promote_candidate` so an inline Entity payload resolves *before* or *during* storyline mapping properly).

### Stage 2.4 Dashboard integration
* **Estimated files:** 2 (`index.html`, `main.js`/`app.py`)
* **Estimated API changes:** Minimal (Changing `CandidatePromoteRequest` only)
* **Risk:** Low (The `timeline` JSON arrays already contain what we need; UI changes are isolated DOM elements).

### File Exclusions
* **Files expected to change:** `semi_intel/web/schemas.py`, `semi_intel/web/app.py`, `semi_intel/signals/promotion.py`, `semi_intel/web/static/index.html`
* **Files that must NOT change:** `semi_intel/domain/models.py`, `versions/*.py` (Alembic), `tests/*.py` (Unless updating test logic for new promotion payload), `oem_radar/*`

Stop. Wait for approval before implementing anything.
