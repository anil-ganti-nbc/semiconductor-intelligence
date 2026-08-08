# UI Acceptance — 2026-08 (v0.9.2)

This is a browser-first acceptance record, not an exhaustive click-every-control
audit. Scope actually covered, and scope deliberately not covered, are both
stated explicitly below — see "Coverage honesty note."

This document has two dated batches. Batch 1 (below) covered Add Source,
Find Feed, and Automation. **Batch 2** (see that section further down)
covers the three source-creation workflows explicitly (Add tab, Signal
Radar Add source, Suggested Sources Add source) after the operator flagged
that these had been conflated, plus the executable rebuild/parity process.

## Environment

- Launch command (as reported by operator, reproduced exactly):
  `python -m semi_intel.cli web serve --port 8500`
- Python: 3.13, `.venv313` (editable install of `semi_intel`)
- Database: disposable, `sqlite:///C:/temp/v092_test.db`, via `SEMI_INTEL_DB_URL`
  (proven effective — `get_engine().url` printed and matched; API showed an
  empty `[]` source list before any test data was added)
- Port: 8500 (fresh, no prior listener), single PID confirmed via `netstat`
- Browser: in-app Browser pane (Chromium-based), fresh tab, hard-reloaded
  navigations (`force: true`) throughout
- Production DB (`semi_intel.db`) timestamp: `2026-08-06 17:48:07.004594100`,
  66,269,184 bytes — identical before and after this entire session
- Source file state at test time:
  - `semi_intel/web/app.py` — sha256 `df638bc947c2bc38...` (contains the
    v0.9.1 `_detect_provider` hostname fix)
  - `semi_intel/web/static/index.html` — sha256 `889694a7848b10d1...` (contains
    the v0.9.1 `fmtDate` fix, prior to the v0.9.2 health-check button fix)
  - Static assets are served directly from the source tree (editable
    install), not from a bundled/frozen copy — confirmed by editing the file
    and observing the change on next navigation, no server restart required
    for HTML/JS changes (only Python changes needed a server restart)

## Confirmed root causes (this session)

### 1. Find Feed returns no results for feeds that actually exist and work

- **Symptom:** clicking "Find feed" on a pending source suggestion always
  returned "Not detected," even for domains with real, working RSS feeds.
- **Affected controls:** "Find feed" button (Suggested Sources tab); indirectly
  blocks "Add source" for any suggestion where feed_url is empty (that button
  is `disabled` until a feed is detected).
- **Evidence:** live click on `chipsandcheese.com` → `POST
  /api/source-suggestions/1/discover-feed` → `200 OK` → `{"feeds":[],
  "selected":null}`. Direct backend test confirmed `_fetch('https://
  chipsandcheese.com/feed')` succeeds and `feedparser.parse()` extracts 13
  real entries, but `valid_feed()` rejected it solely because feedparser also
  set `bozo=1` (a truncated CDATA section from the library's 1MB read cap —
  a non-fatal quirk). `valid_feed()`'s check (`entries and not bozo`) is
  stricter than the equivalent, already-correct check in
  `semi_intel/signals/providers/rss.py`'s `validate()` (`not entries and
  bozo`), which correctly tolerates non-fatal bozo conditions.
- **Fix:** `semi_intel/editorial/feed_discovery.py`, `valid_feed()` — now
  accepts any feed feedparser extracted entries from, regardless of `bozo`,
  matching the existing precedent elsewhere in this codebase.

### 2. "Run health check" button does not run a health check

- **Symptom:** clicking "Run health check" on the Automation & Health tab
  visibly did nothing new — no new job appeared in job history, no request
  matching its sibling buttons.
- **Affected controls:** "Run health check" button only. Its three sibling
  buttons ("Run pipeline now," "Generate digest now," "Create backup now")
  were unaffected — each already correctly called `runOperationalJob(...)`.
- **Evidence:** `onclick` attribute was literally `loadOperations()` (a
  re-render of already-loaded data) instead of
  `runOperationalJob('health_check')`. Confirmed via
  `document.querySelector` inspection and by observing zero
  `/api/operations/run/*` network requests on click, versus a normal
  `POST /api/operations/run/health_check → 200` for the sibling buttons.
  Backend confirmed fully functional via direct API call before the fix.
- **Fix:** `semi_intel/web/static/index.html` — changed the button's
  `onclick` from `loadOperations()` to `runOperationalJob('health_check')`.
  Re-verified live: click now produces a real job row in "Recent job
  history" with correct timestamp and status.

No other instance of this "wrong handler" pattern was found — every other
`onclick` attribute in the file was audited via a full listing (`grep -oE
'onclick="[^"]*"' | sort | uniq -c`) and each is uniquely named and specific
to its control; the two remaining `loadOperations()` references are both
legitimately "retry loading this panel" buttons ("Retry status," and the
dynamically generated "Retry" button inside `operationPanelError()`).

## Coverage honesty note

This session verified, through real browser clicks (not just API calls):

- Add Source (Signal Radar tab) — full add/verify/DB-compare cycle
- Find Feed (Suggested Sources tab) — full discover → add cycle, before and
  after the fix
- Automation & Health — initial render, `Run health check` button before and
  after the fix, confirmed via job history and trends update
- Alerts & Digest — initial render, `Create test alert`, `Refresh now`
  (digest generation), confirmed via unread-count change and network capture
- Console-cleanliness sweep of all remaining top-level tabs (Editorial Inbox,
  Monitored Topics, Discovery Activity, Claims & Evidence, Source Rankings,
  Entities, Claim Matches, Add) — each loads with zero console errors

It did **not** perform an exhaustive per-control click-through of every
button, form, and row action across all twelve tabs (the v0.9.2 prompt's
Phases 8–11 in full: every Signal Radar/Candidate Review row action,
every Editorial control, every Entities workflow, every Operations/Settings
control individually). That would be well over 100 individual interactive
elements. Claiming full PASS status on all of them without actually clicking
each one would violate the standard this task itself sets ("a green backend
test suite does not prove a button works"). The table below marks untested
controls as **UNTESTED** rather than guessing PASS.

## Control matrix (controls actually exercised this session)

| Tab/Panel | Control | Selector | Expected behavior | Browser result | API result | Persistence | Console | Status | Notes |
|---|---|---|---|---|---|---|---|---|---|
| Signal Radar | Add source | `form[onsubmit=submitAddRadarSource]` | POST creates correctly-classified source, list refreshes | Phoronix added, `rss`/"Phoronix" shown | `201`, `{"provider":"rss","name":"Phoronix"}` | Confirmed via direct sqlite query, matches UI/API exactly | clean | **PASS** | v0.9.1 fix confirmed live under `web serve` |
| Suggested Sources | Find feed | `button[onclick^=discoverFeed]` | Detects a working feed, updates Feed column | Before fix: "Not detected." After fix: `https://chipsandcheese.com/feed` shown | Before: `{"feeds":[],"selected":null}`. After: real URL returned | Suggestion row `feed_url` updated in DB | clean | **PASS** (after fix) | Root cause #1 |
| Suggested Sources | Add source (from suggestion) | `button[onclick^=addSuggestedSource]` | Creates RSS source from discovered feed, suggestion moves to Added | Row disappeared from "To review" | — | Confirmed: `sources` table gained id=2 "Chips and Cheese"/RSS; suggestion status → `ADDED` | clean | **PASS** | |
| Automation & Health | Run health check | `button[onclick^=runOperationalJob('health_check')]` | Creates a `health_check` job row | Before fix: nothing happened. After fix: job appears in history | Before: no request fired. After: `POST /api/operations/run/health_check → 200` | Job row persisted (2 job rows visible after 2 clicks) | clean | **PASS** (after fix) | Root cause #2 |
| Automation & Health | Initial render (health/scheduler/jobs/backups/trends/windows-task) | `#operations` | All panels populate, no stuck "Loading…" | All 6 panels rendered with real data | All 6 endpoints `200` | n/a | clean | **PASS** | v0.9.1 `fmtDate` fix confirmed still effective under `web serve` |
| Alerts & Digest | Create test alert | `button[onclick=sendTestNotification]` | Creates a visible unread alert | Unread count: 0 → 1 | `POST /api/notifications/test → 200` | Confirmed via reload showing "1 unread alert" | clean | **PASS** | |
| Alerts & Digest | Refresh now (digest) | `button[onclick="generateDigest(false)"]` | Generates a digest without delivering | Request completed, no error | `POST /api/notifications/digest → 200` | not independently re-queried | clean | **PASS** | |
| Alerts & Digest | Initial render (all panels) | `#notifications` | No stuck loading state, external-delivery boundary explained clearly | Renders correctly, "Configuration missing" messaging shown | 9 GET endpoints all `200` on load | n/a | clean | **PASS** | External delivery itself untested — no webhook configured (by design, see below) |
| Editorial Inbox | Initial load | `#stories` | Loads without error | Renders (empty state, disposable DB) | `200` | n/a | clean | **PASS (render only)** | Row-level actions not exercised |
| Monitored Topics | Initial load | `#topics` | Loads without error | Renders | not captured individually | n/a | clean | **PASS (render only)** | Form actions not exercised |
| Discovery Activity | Initial load | `#discovery` | Loads without error | Renders | not captured individually | n/a | clean | **PASS (render only)** | Settings save not exercised |
| Claims & Evidence | Initial load | `#claims` | Loads without error | Renders | not captured individually | n/a | clean | **PASS (render only)** | Form actions not exercised |
| Source Rankings | Initial load | `#sources` | Loads without error | Renders | not captured individually | n/a | clean | **PASS (render only)** | |
| Entities | Initial load | `#entities` | Loads without error | Renders | not captured individually | n/a | clean | **PASS (render only)** | Search/create/merge not exercised |
| Claim Matches | Initial load | `#suggestions` | Loads without error | Renders | not captured individually | n/a | clean | **PASS (render only)** | Accept/reject not exercised |
| Add | New Source / New Entity / New Evidence / New Claim forms | `#add` | Forms present, no console error | Not submitted this session (Add Source tested via the Radar-specific form instead) | — | — | clean | **UNTESTED (submission)** | Render-only |
| Ignore / Block (source suggestions) | `reviewSource(id,'ignore'/'block')` | — | Operator-confirmed working prior to this session | Not re-clicked this session (already regression-tested in v0.9.1 pass) | — | — | — | **PASS (prior session)** | Not re-verified live in v0.9.2; no code path changed that would affect these |
| All other row-level actions (candidate review, editorial, entity merge, notification mute/rate, backup creation, Windows Task install, etc.) | various | various | — | — | — | — | — | **UNTESTED** | Out of scope for this session's time budget; see coverage note above |

## External dependencies not exercised

- Live Discord/webhook delivery — `SEMI_INTEL_WEBHOOK_URL` intentionally not
  set; UI correctly reports "Configuration missing" rather than crashing.
- Authenticated X (Twitter) collection.
- Windows Task Scheduler installation (`Install or repair task` was not
  clicked — it would create a real OS-level scheduled task).
- Any third-party feed/site beyond `phoronix.com` and `chipsandcheese.com`.

## Operator-visible limitations

- Backup creation, restore, Windows Task install, and most row-level actions
  across Signal Radar candidates, Editorial, and Entities were not exercised
  in this session (see UNTESTED rows above). Root-cause work in this session
  was scoped to the operator's explicitly reported failures (Add Source,
  Find Feed, Automation) plus a full-file audit for the same *class* of bug
  (wrong-handler wiring) that caused the health-check defect.
- Automatic candidate promotion throughput remains unverified (flagged in
  prior milestones, not re-tested here; automatic promotion thresholds were
  not touched per the task's constraints).

---

## Batch 2 — Add tab, Suggested Sources Add source, EXE parity

### Environment (Batch 2)

- Launch command: `python -m semi_intel.cli web serve --port 8700`
- Database: disposable, `sqlite:///C:/temp/v092b2_test.db` via
  `SEMI_INTEL_DB_URL` — printed effective engine URL and confirmed an empty
  `[]` source list before any test data
- Production DB (`semi_intel.db`) baseline recorded before testing: `2026-08-06
  19:17:13.270778700`, 69,951,488 bytes — unchanged after this batch's
  source-mode testing (the file continued to grow independently between
  batches from what appears to be separate, concurrent operator use of the
  live app — not from any test in this session; every server process this
  session used an isolated `SEMI_INTEL_DB_URL` with no exceptions)
- Browser: in-app Browser pane, hard-reloaded navigations

### Note on click delivery in this session

Early in this batch, the automation tool's coordinate-based mouse clicks
stopped reliably reaching the page in one browser tab (`nav button` clicks
and a dynamically-rendered row button both failed to fire their handlers).
This was diagnosed, not assumed: `document.elementFromPoint()` at the exact
click coordinates returned the correct target element with no overlay in
the way, and the identical element's `.click()` method (which dispatches
the same `click` `Event` a real mouse click would, processed by the same
`addEventListener`/`onclick` handlers) worked correctly and produced
correct application behavior. This was concluded to be a browser-automation
delivery artifact specific to that tab session, not an application defect,
and `.click()` was used for the remainder of this batch's interactive
testing as a faithful substitute — it exercises the identical code path a
real click would (same event, same handlers), differing only in not
simulating OS-level pointer coordinates, which was already proven
irrelevant here.

### Root cause: Suggested Sources → Add source fails silently on error

- **Symptom:** operator reports "Add source button still does not work
  correctly" on the Suggested Sources tab, despite the button's happy path
  (tested in Batch 1 and re-confirmed here) working correctly end to end.
- **Root cause:** `addSuggestedSource(id)` in
  `semi_intel/web/static/index.html` had no `try`/`catch` around its
  `postJSON()` call. Any backend rejection — most plausibly a 409 duplicate
  name conflict, given a populated production suggestion list — became an
  **unhandled promise rejection**, invisible anywhere in the UI. The button
  stayed enabled, the row stayed in place, nothing happened on screen. To
  an operator with no visibility into the browser console, this is
  indistinguishable from "the button does nothing."
- **Evidence:** seeded a second pending suggestion whose inferred name
  ("Chips and Cheese") collided with an already-added source. Clicking "Add
  source" produced `POST /api/source-suggestions/2/add → 409 Conflict` and
  a console error `Uncaught (in promise) Error: Source 'Chips and Cheese'
  already exists` — with zero visible UI feedback. The happy path (no
  conflict) was independently confirmed working: `POST
  /api/source-suggestions/1/add → 201 Created`, source persisted, row
  correctly removed from "To review," suggestion status transitioned to
  `ADDED`, all confirmed via direct SQLite query matching the API/UI
  exactly, and confirmed to survive a full server restart.
- **Fix:** wrapped the call in `try`/`catch`, surfacing `err.message` via
  `alert()` (this row has no dedicated `[data-msg]` element, unlike the Add
  tab's forms, so `alert()` is the minimal addition rather than introducing
  new DOM). Re-verified live: the same 409 scenario now produces a visible
  alert with the exact backend error message, and the row correctly remains
  in place rather than disappearing.
- **Files:** `semi_intel/web/static/index.html`

### Add tab — full acceptance

All four forms tested via real submit-button clicks, disposable DB:

| Control | Endpoint | Happy path | Error path (duplicate name) | Status |
|---|---|---|---|---|
| New Source | `POST /api/sources` | `201`, "Created #2." shown | `400`, "Source '...' already exists." shown inline via `[data-msg]` | **PASS** |
| New Entity | `POST /api/entities` | `201` | not separately tested (schema has no unique-name constraint at this layer) | **PASS (happy path)** |
| New Evidence | `POST /api/evidence` | `201` | not separately tested | **PASS (happy path)** |
| New Claim | `POST /api/claims` | `201` | not separately tested | **PASS (happy path)** |

**Finding:** the Add tab was never actually broken. `submitForm()` (the
shared handler for all four forms) already had a correct `try`/`catch`
with a visible `[data-msg]` error element — this is the same pattern the
Suggested Sources button was missing, and is exactly what that button's fix
now approximates. No shared frontend defect was found affecting the whole
tab; each form submitted, persisted, and displayed both success and error
states correctly. Console remained clean throughout.

### Signal Radar → Add source (regression control)

Re-tested against this batch's disposable DB: `POST /api/radar/sources`
with `https://www.phoronix.com/rss.php` → `201`, `provider: "rss"`,
`name: "Phoronix"`. No regression. `_detect_provider()` was not modified
this batch.

### Alerts & Digest / Automation & Health (regression controls)

Spot-checked via direct API against this batch's disposable DB rather than
re-clicking every control (no code shared with this batch's fixes was
touched): `GET /api/operations/health → 200`, `POST
/api/notifications/test → 200`. Full control-level re-verification from
Batch 1 stands; see that section above.

### Persistence

Confirmed after page reload and after a full server process restart
(same disposable DB file): all three sources created this batch (`Chips and
Cheese`, `Test Manual Source`, `Phoronix`) and the suggestion's `ADDED`
status all persisted correctly.

### Focused regression tests added this batch

| Test | File | Protects against |
|---|---|---|
| `test_valid_feed_accepts_entries_despite_non_fatal_bozo` | `tests/test_editorial_discovery.py` | Find Feed rejecting real feeds with non-fatal `bozo` |
| `test_source_suggestion_add_conflict_on_duplicate_name` | `tests/test_editorial_web.py` | Backend 409 contract on suggestion-add name collision; suggestion must remain pending on failure |
| `test_add_suggested_source_handler_surfaces_errors_to_the_operator` | `tests/test_editorial_web.py` | Silent-failure regression — asserts `try`/`catch`/`err.message` present in the handler |
| `test_run_health_check_button_calls_the_health_check_job_not_a_bare_reload` | `tests/test_automation_health_repair.py` | Wrong-handler regression on the health-check button |
| `test_add_rss_source_hostname_containing_x_dot_com_is_not_misdetected_as_x` | `tests/test_web_radar.py` | `_detect_provider()` hostname-substring regression (previously untested) |

### Executable rebuild and parity

See `docs/BUILD_AND_RELEASE.md` for the standing procedure this
established. Summary for this batch: built fresh `dist/semintel.exe` and
`dist/semi-intel.exe` from a throwaway `.build_venv` (`web`+`x` extras +
`pyinstaller`), staged (not overwriting the live root executables until
verified), launched the staged `semintel.exe` against a disposable DB on a
fresh port, and confirmed live in-browser: Add Source correctly classifies
`phoronix.com` as `rss`; Find Feed correctly detects
`chipsandcheese.com/feed`; Automation's "Run health check" fires the
correct request and logs a job. Only after all three passed were the root
`semintel.exe`/`semi-intel.exe` replaced with the staged builds.

### Untested this batch (unchanged from Batch 1's honesty note)

Row-level actions across Signal Radar candidates, Editorial, Entities, and
most Operations/Settings controls remain UNTESTED — this batch's scope was
the three source-creation workflows, their shared/adjacent controls, and
the executable release process.

---

## Batch 3 — Find Feed silent-zero-result defect (operator-reported, live)

The operator reported, with a screen recording, that "Add source" on the
Suggested Sources tab did nothing when clicked. Investigation (see below)
traced this to a *different*, separate root cause from the ones already
fixed — not a code regression, a previously-unfound defect surfaced by
real operator use.

### What actually happened

- The operator was on the Suggested Sources tab, where "Add source" is
  correctly `disabled` (by design) until "Find feed" finds a feed URL for
  that row.
- They had clicked "Find feed" first, and it gave **no feedback of any
  kind** — this was the actual complaint, once traced back one step.
- Root cause: `discoverFeed(id)` had no loading state and no result
  feedback in *either* outcome. A network request that fails, and a
  network request that succeeds but legitimately finds zero feeds (a site
  with no autodiscoverable feed, or one that blocks automated fetches —
  the same class of issue as the `videocardz.com` 403 found in Batch 1),
  looked visually identical to "the button did nothing": the row simply
  re-rendered with "Not detected" either way, no message, no spinner.
- This was independently reproduced live: seeded a suggestion for
  `videocardz.com` (known to 403 automated requests, confirmed in Batch 1),
  clicked "Find feed" — the request completed (`200 OK`,
  `{"feeds":[],"selected":null}`) but the UI gave zero indication anything
  had happened.
- **Fix:** `discoverFeed(id, btn)` in `semi_intel/web/static/index.html`
  now: shows "Searching…" on the button and disables it while the request
  is in flight; on completion, if no feed was found, shows a clear message
  ("No feed could be found automatically for this site. You can add the
  source manually with a known feed URL instead."); on a network/backend
  error, surfaces `err.message` the same way. The call site
  (`onclick="discoverFeed(${item.id}, this)"`) was updated to pass the
  button element so the handler can manage its own loading state.

### Verification

- Live, source mode, disposable DB: seeded `videocardz.com` (no feed) and
  `chipsandcheese.com` (real feed) as two pending suggestions.
- `videocardz.com`: button showed "Searching…" mid-request; after
  completion, a clear "No feed could be found automatically..." message
  appeared; button correctly re-enabled and reverted to "Find feed";
  "Add source" correctly remained disabled.
- `chipsandcheese.com`: no alert (correct — a feed was found); Feed column
  updated to `https://chipsandcheese.com/feed`; "Add source" button's
  `disabled` attribute correctly removed.
- No console errors from either case (stale console entries from an
  earlier unrelated `file://` tab load were present in the log but
  predate this test and were excluded from consideration).
- Focused regression test added:
  `test_discover_feed_handler_gives_feedback_when_no_feed_is_found` in
  `tests/test_editorial_web.py` — asserts the handler contains a loading
  state, checks `result.selected`, and has `try`/`catch`.

### Files modified this batch

- Production: `semi_intel/web/static/index.html`
- Tests: `tests/test_editorial_web.py`
- Documentation: this file

### Executable status

This fix was applied and verified in source mode. Per the standing release
procedure in `docs/BUILD_AND_RELEASE.md`, it is not yet operator-delivered
until the executables are rebuilt, parity-verified, and swapped into the
live project folder — see the final report for this batch's status on
that step.

## Suggested Sources provider-aware acceptance (v0.9.3)

### Why

Batch 3 fixed Find Feed's silent-zero-result defect for *website* suggestions.
The operator's production database (`semi_intel.db`, read via a disposable
copy, never opened for writing) showed the actual failure mode was broader:

```
source_suggestions total: 106
  kind=HANDLE platform=x  status=PENDING: 66
  kind=HANDLE platform=x  status=ADDED:   26
  kind=HANDLE platform=x  status=IGNORED: 14
  kind=DOMAIN (any platform):              0
```

Every single suggestion row in the real database is an X handle. "Find
feed" was the only action ever offered, and it always ran website RSS
discovery against `https://{suggestion.domain}` — for a handle row,
`domain` is a synthetic string like `legacy-handle:x:iancutress`, so the
fetch was doomed before Batch 3's fix even applied. This was not a feed-
parser defect; it was the UI asking the wrong question for the data it had.

### Root cause (confirmed by reading, not guessing)

- `SourceSuggestion.kind` (`domain`/`handle`), `.platform`, and
  `.provider_key` are real structured columns (`semi_intel/domain/models.py`)
  — the data was never ambiguous.
- The **old** `GET /api/source-suggestions` (used exclusively by the
  Suggested Sources tab) discarded `kind`/`platform`/`provider_key` from
  its response entirely (`semi_intel/web/app.py`). The frontend had no way
  to know a row was a handle even if it wanted to.
- `POST /api/source-suggestions/{id}/discover-feed` and
  `.../add` always assumed website/RSS, for any kind.
- A **second, already-built, provider-aware** API family already existed
  and was fully wired to a working service function
  (`accept_source_suggestion()` in `semi_intel/signals/suggestions.py`,
  exposed at `POST /api/radar/source-suggestions/{id}/review` with
  `action: "accept"`) — it correctly creates an `x`-provider `Source` for a
  handle suggestion, no feed required. **The Suggested Sources tab's
  JavaScript never called it.** This is the "where provider context is
  lost/ignored" answer required by Phase 2: nowhere in the data model —
  entirely in the frontend/old-endpoint pairing.

### Why the queue is 100% X handles (Phase 5)

Three suggestion-producing pathways exist:

1. `EditorialDiscoveryService._refresh_source_suggestions()` — domain/
   citation-based, kind=DOMAIN. Runs automatically every pipeline cycle.
   **Zero rows produced in the real database** — the `citations` table
   (its data source) has 0 rows despite 21,417 `evidence` rows. This
   suggests the operator's ingested evidence rarely/never contains the
   `<a href>` citation shape this miner looks for (e.g. RSS-sourced
   evidence bodies vs. the HTML-citation shape it was built for). This is
   a separate, deeper question from this task's scope (not an OEM Radar
   or scoring change) and is called out below as a confirmed limitation
   rather than fixed here.
2. `refresh_handle_suggestions()` — attribution-mined handles (`platform=
   "unknown"`, kind=HANDLE). **Never wired into any automated job** —
   reachable only via an explicit `POST /api/radar/source-suggestions/
   refresh` that no scheduler job or UI control ever called. Confirmed by
   reading every branch of `OperationsScheduler._execute()` and
   `PipelineService.run_once()`: neither called it before this fix.
3. `LegacyRadarImporter._plan_suggestions()` — one-time bulk import from a
   pre-merge Signal Radar SQLite database's `source_candidates` table,
   kind=HANDLE, platform normalized from the legacy `platform` column
   (`twitter`→`x`). **This is the source of all 106 real rows** — a single
   bulk import event, not an ongoing generator.

### Diversity bridge repaired (Phase 6)

The smallest safe fix: wire the already-existing, already-tested
`refresh_handle_suggestions()` into `PipelineService.run_once()` as a new
fault-isolated stage (`semi_intel/pipeline/service.py`), positioned next to
clustering/scoring — pure DB reasoning over already-collected `SignalItem`
text, same as those stages, safe to run every cycle. No new discovery
engine was built. The domain/citation pathway (#1 above) was left
untouched — it already runs automatically and is not disabled; it simply
has not had matching input in this operator's actual evidence yet.

Also added: a "Refresh handle suggestions" button in the Suggested Sources
toolbar (calls the pre-existing `/api/radar/source-suggestions/refresh`
endpoint directly) so an operator isn't purely dependent on waiting for the
next scheduled pipeline cycle.

### Provider-aware UI behavior (Phase 3)

`GET /api/source-suggestions` now returns `kind`, `platform`, and
`provider_key` per row (additive, non-breaking). The frontend derives a
`sourceSuggestionProviderGroup(item)` — `"website"` (kind=domain),
`"x"` (kind=handle, platform x/twitter), or `"unsupported"` (kind=handle,
any other/missing platform) — and renders per-group:

| Group | Primary action | Endpoint | Feed/Handle cell |
|---|---|---|---|
| Website (kind=domain) | Find feed → Add source (unchanged from Batch 3) | `/api/source-suggestions/{id}/discover-feed`, `/add` | feed URL or "Not detected" + Find feed |
| X handle (kind=handle, platform x/twitter) | **Add X source** | `POST /api/radar/source-suggestions/{id}/review {action:"accept"}` | `@handle`, no Find feed |
| Unsupported (kind=handle, platform unknown/other) | none — "Unsupported source type" label | n/a | "Not supported automatically" |

Ignore/Block/Restore are unchanged (`/api/source-suggestions/{id}/review`)
— they never depended on kind and work identically for every row type.

Backend guard rails added: `discover-feed` and `/add` now return `400` for
a `kind=handle` suggestion instead of silently attempting a doomed website
fetch, so even a stale client or direct API call fails loudly.

A provider filter (`All` / `X handles` / `Websites / domains` /
`Unsupported`) was added to the toolbar, client-side over the already-
fetched list (no new backend query needed for this dataset size).

### Browser acceptance (source mode, port 8901, disposable
`sqlite:///C:/temp/v093_test.db`, production DB confirmed unchanged
before/after: `2026-08-06 20:05:07.480632600`, 69,951,488 bytes)

Seeded 8 rows covering every required case (legacy-handle:x, direct-feed
domain, no-feed domain, unsupported/unknown-platform handle, duplicate-name
X handle, duplicate-URL RSS feed). All verified via `.click()` + `window
.alert` capture (the established reliable method — see prior batches'
notes on coordinate-click flakiness):

- **X suggestion (Ian Cutress)**: provider badge "X", no "Find feed"
  offered, "Add X source" fires → `Source` created with `provider="x"`,
  `provider_key="IanCutress"` (verified via a side DB session), suggestion
  moved to `added`, zero alerts, zero console errors, persisted after a
  hard page reload.
- **Duplicate X handle (Existing Handle, pre-existing `Source` with the
  same provider_key)**: "Add X source" resolves idempotently to the
  existing source (no duplicate created, no error) — matches
  `accept_source_suggestion()`'s documented idempotent-by-`(provider,
  provider_key)` behavior.
- **Website with a real feed (Chips and Cheese, feed_url pre-set)**: "Add
  source" enabled immediately, fires successfully, zero alerts.
- **Website with no discoverable feed (VideoCardz — real-world 403-
  blocking domain from the prior batch)**: "Find feed" shows "Searching…"
  then the same clear no-feed alert from Batch 3; suggestion remains
  reviewable.
- **Unsupported handle (John Doe, platform="unknown")**: renders "Unclear
  source type — needs manual review" and "Unsupported source type" instead
  of any clickable primary action; Ignore and Block both work and update
  the list correctly (verified in the Blocked state view with Restore
  present).
- **Provider filter**: switching to "X handles" while on Pending correctly
  shows zero rows once all X rows had been actioned; switching to "Added"
  + "X handles" together correctly shows exactly the two accepted/idempotent
  X rows.
- **Duplicate RSS by name** (existing behavior, unchanged by this batch):
  the legacy `/add` endpoint dedupes by `Source.name`, not by feed URL — a
  suggestion whose feed URL matches an existing source under a different
  name is not rejected. This is pre-existing behavior of the reused
  endpoint, out of this task's scope (Phase 4 said prefer reusing existing
  services), and is noted here as a known limitation rather than silently
  left undocumented.
- No console errors were observed across the entire session.

### Focused regression tests added

- `tests/test_editorial_web.py`: `test_source_suggestions_endpoint_exposes_provider_fields`,
  `test_find_feed_rejects_a_handle_suggestion`,
  `test_add_suggested_source_rejects_a_handle_suggestion`,
  `test_radar_review_accept_creates_an_x_source_from_a_handle_suggestion`,
  `test_source_suggestion_provider_grouping_is_kind_and_platform_aware`
  (semantic), `test_x_handle_row_does_not_offer_find_feed_as_primary_action`
  (semantic), `test_accept_handle_suggestion_calls_radar_review_accept_endpoint`
  (semantic).
- `tests/test_pipeline_service.py`:
  `test_run_once_mines_handle_suggestions_from_signal_text` — proves the
  diversity bridge actually runs every pipeline cycle.
- All prior regression tests (Batches 1–3, `_detect_provider`, non-fatal
  `bozo`) re-verified green in the same run.

### Files modified this batch

- Production: `semi_intel/web/app.py`, `semi_intel/web/static/index.html`,
  `semi_intel/pipeline/service.py`, `semi_intel/operations/scheduler.py`
- Tests: `tests/test_editorial_web.py`, `tests/test_pipeline_service.py`
- Documentation: this file, `docs/BUILD_AND_RELEASE.md` (no procedural
  change needed — the standard sequence was followed as-is)

### Executable status

Rebuilt and swapped. Build venv note: the first build attempt silently
used the ambient `python` (3.14.6, unsupported) instead of 3.13 because
`.build_venv` was created with the bare `python` command — caught only by
manually verifying `dist\` timestamps stayed unchanged after a build that
reported success, which turned out to be because a second, unrelated bug
(a lost path separator inside a piped shell command) had made PyInstaller
fail with "Spec file not found" while the wrapping shell pipeline still
reported exit 0. Both were caught by checking real evidence (timestamps,
log tails, explicit exit-code capture) instead of trusting a green exit
code, and the venv was rebuilt from the explicit
`...\Python313\python.exe` before proceeding. Final build:
`semintel.exe` — `2026-08-06 21:22:45`, 62,489,098 bytes,
sha256 `E2E49AF3A61CE79B08F7CE70FE95A84FDBFB1DF9452C2BF866EC2A8422AA67A3`;
`semi-intel.exe` — `2026-08-06 21:23:43`, 62,462,901 bytes,
sha256 `E940FD5C5A8F98D85719319DF355BD627810C3E0235463FD3ACE77A09AFF2DBD`.
Staged parity verified against `sqlite:///C:/temp/exe_verify_v093.db` on
port 8902: bundled static assets confirmed provider-aware (X handle row
renders "Add X source", website row renders "Find feed"), "Add X source"
click created a real `provider=x` `Source` end-to-end in the compiled exe,
`phoronix.com` still correctly classified `rss` (Batch 1 regression
guard), and the health-check job still runs. Production `semi_intel.db`
confirmed byte-identical (`2026-08-06 20:05:07.480632600`, 69,951,488
bytes) before, during, and after the entire rebuild.

## Multi-provider source discovery acceptance (v0.9.4)

Full design rationale, provider matrix, and generator details live in
`docs/SOURCE_SUGGESTION_ARCHITECTURE.md`. This section is the browser
acceptance record only.

### Environment

- Launch: `python -m semi_intel.cli web serve --port 8905`
- Disposable DB: `sqlite:///C:/temp/v094_test.db` (seeded with real-shaped
  `SignalItem` rows for domain/forum/subreddit/GitHub extraction, plus
  directly-seeded X handle and unsupported-handle rows)
- Production `semi_intel.db`: confirmed unchanged in content by every
  session-owned action; two timestamp/row-count changes observed during
  this batch were independently traced to the operator's own concurrent
  live use of the already-shipped v0.9.3 "Add X source" feature (sources
  83→89→98 across the session, `source_suggestions` ADDED count moved by
  the exact same delta each time) — not anything this session wrote.

### Bug found and fixed during acceptance

**`github.com`'s bare homepage was suggested twice** — once correctly as
`github:rocm/rocm` (the specific repository), and once uselessly as a
generic "Website" suggestion for `github.com` itself, because the generic
domain generator had no awareness that a specialized generator already
owned that domain. Fixed by adding `PLATFORM_HANDLED_DOMAINS =
{"github.com", "gist.github.com", "reddit.com", "old.reddit.com"}` to the
domain generator's exclusion set in `semi_intel/signals/source_discovery.py`,
with a regression test
(`test_github_and_reddit_homepages_are_not_also_suggested_as_plain_websites`)
and re-verified live: Website count dropped from 4 to 3 (the bogus row
gone), the real `rocm/rocm` GitHub suggestion unaffected.

### Results

- **Discover source suggestions button**: clicked live, ran all four
  generators against 17 seeded SignalItems, created 5 new suggestions
  (website ×2 already existed/updated, forum ×1, reddit ×1, github ×1),
  reported `overall_status: "SUCCESS"` with zero alerts on the happy path.
- **Provider filter + counts**: toolbar shows `X: 1 · Website: 4 · Forum:
  1 · Reddit: 1 · GitHub: 1 · Unsupported: 1` (pre-fix) → `X: 1 · Website:
  3 · Forum: 1 · Reddit: 1 · GitHub: 1 · Unsupported: 1` (post-fix,
  duplicate removed); each filter option correctly isolates its group.
- **Website (VideoCardz, no feed)**: renders "Not detected" + Find feed,
  unchanged from v0.9.3 behavior.
- **Website (Chips and Cheese, discovered via generator)**: reason text
  correctly shows "Cited by 5 signal item(s) across 1 independent
  source(s)".
- **Forum (community.example.com)**: badge "Forum", reason includes
  "(forum-shaped links observed)", Ignore fires and removes it from the
  pending list, zero alerts.
- **Reddit (r/hardware)**: badge "Reddit", title links to
  `https://www.reddit.com/r/hardware`, feed validated against the real
  internet during the live discovery run (`https://www.reddit.com/r/hardware/.rss`
  actually resolved, no 403 this time — Reddit's block behavior is
  intermittent, same class of outcome as VideoCardz's from Batch 3, and is
  handled the same honest way either way), "Add source" fired and created
  a real RSS-type `Source` end-to-end.
- **GitHub (rocm/rocm)**: badge "GitHub", title links to
  `https://github.com/rocm/rocm`, feed pre-validated to
  `https://github.com/rocm/rocm/releases.atom` during discovery, "Add
  source" fired and created a real `Source` end-to-end, zero alerts.
- **Unsupported (Jane Doe, attribution-mined, no platform)**: no
  clickable primary action, "Unsupported source type" label, Block fires
  correctly.
- **X (Ian Cutress)**: unchanged v0.9.3 behavior, still present and
  filterable, never suppressed by the new generators.
- **Persistence**: hard reload after all the above actions shows the
  exact same remaining pending set (`X: 1 · Website: 3`) — confirmed via
  `get_page_text`, not just an API call.
- **Console**: zero errors observed across the entire session
  (`read_console_messages` with `onlyErrors: true` returned nothing at
  every checkpoint).

### Focused regression tests added

- `tests/test_source_discovery.py` (new, 17 tests): domain/forum/subreddit/
  GitHub extraction and normalization, threshold enforcement, noise/
  shortener/registered-domain/platform-handled-domain exclusion, no-
  duplicate-on-rerun + status-never-reversed, fault isolation between
  generators (including the "one generator crashes, earlier generator's
  committed work survives" case), and the "never a false empty success
  when every generator fails" case.
- `tests/test_editorial_web.py` (+7): the `/discover` endpoint's
  structured per-generator report, Reddit's deterministic-feed-retry
  Find-Feed path, GitHub's reuse of the existing `/add` endpoint, and
  three semantic HTML tests for the new frontend badge/grouping/action
  logic.
