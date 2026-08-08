# SIGNAL CANDIDATE REVIEW - MANUAL SMOKE TEST

This guide walks an operator through manually validating the Signal Radar candidate review and entity resolution integration in the local environment, without modifying the authoritative production DB.

## Requirements
- Python 3.10+
- The `semi_intel` environment.

## Execution Steps

### 1. Copy the Database
Create a disposable test copy of the database to ensure `semi_intel.db` is protected:
```powershell
Copy-Item "$env:LOCALAPPDATA\SemiIntel\data\semi_intel.db" -Destination "temp_smoke_test.db"
```
*(If you do not have an existing database, run `python -m semi_intel.cli init-db` against the temp one instead).*

### 2. Point Application to the Copy
```powershell
$env:SEMI_INTEL_DB_URL="sqlite:///temp_smoke_test.db"
```

### 3. Start the Dashboard
Launch the web UI using the test database:
```powershell
python -m semi_intel.cli web --port 8080
```
Open your browser to `http://127.0.0.1:8080`.

### 4. Open a Candidate
- Click the **Signal Radar** tab.
- Click a candidate row to expand the detail view.
- **Expected Outcome:** You should see the Candidate Summary, Automatic Eligibility strings distinct from Manual promotion, and an "Extracted entity mentions" section if mentions exist.

### 5. Link One Mention
- Find a raw entity mention and click `Resolve...`
- Select an existing Canonical Entity from the dropdown and `Submit`.
- **Expected Outcome:** The modal closes and the Candidate Detail immediately refreshes, showing the mention as `Resolved` with an `entity_id`.

### 6. Create One Entity
- Find another unresolved mention, click `Resolve...`
- Switch the modal to "Create New" by filling in Name and Type, then `Submit`.
- **Expected Outcome:** Fast reload confirms the new canonical entity was created and instantly linked to the Candidate mention.

### 7. Reject One Mention
- Click `Reject` on a noisy mention.
- **Expected Outcome:** Fast reload confirms the word is scrubbed and assigned `rejected`.

### 8. Leave One Unresolved
- Validate the UI warning showing exactly `This candidate contains N unresolved entity mentions.` above the Promotion buttons.

### 9. Refresh and Verify Persistence
- Reload the entire page `(F5)` and click the candidate again.
- **Expected Outcome:** All states (Resolved links, rejected strings, candidate states, unresolutions) are 100% historically preserved.

### 10. Manually Promote the Candidate
- Click `Promote to Editorial Inbox`. Give it a headline.
- **Expected Outcome:** The candidate changes state to `Promoted`. The button becomes `Open editorial story`. Your unresolved mentions remain untouched.

### 11. Confirm Audit Records
- Switch to the Editorial tab and find your promoted story.
- *Under the hood verification*: `CandidatePromotionEvent` now exists mapped to the target.

### 12. Stop Application & Clean Up
```powershell
# CTRL+C to kill the process
Remove-Item "temp_smoke_test.db"
```
