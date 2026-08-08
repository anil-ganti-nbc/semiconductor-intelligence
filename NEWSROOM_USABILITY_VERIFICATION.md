# Semiconductor Intelligence Platform 3.3.8 — Verification Report

## Scope

This was a bounded newsroom-usability pass. It repaired the operator path from
Signal Radar reports to claims, evidence, and the Editorial Inbox. Collection,
scoring, automatic-promotion thresholds, notifications, scheduling, and all
disabled-by-default safety settings were preserved. No migration was added;
Alembic remains at `a0b5d7e9f314`.

## Confirmed causes and repairs

- Candidate detail was rendered below a long list with no focus or scroll, so a
  click appeared inert. Candidate rows are now accessible controls and the full
  report detail opens in view with explicit loading, focus, close, and error
  states.
- Claims and Evidence were passive tables while creation controls lived in the
  generic Add tab. They are now one actionable workspace with manual creation,
  Signal Radar provenance, link editing, unlinking, filtering, and search.
- The Editorial Inbox was empty because the populated checkpoint had 350
  unpromoted historical candidates, automatic promotion was disabled, and the
  candidates did not satisfy the existing score/age rules. The inbox now shows
  a deterministic review shortlist and supports explicit manual promotion
  without changing automatic policy.

## Automated verification

- Focused newsroom tests plus dashboard JavaScript syntax: **17 passed**.
- Relevant Radar, editorial, claim/evidence, notification, persistence, and web
  regressions: **108 passed**.
- Complete suite, run once after focused gates: **496 passed, 0 failed**.
- Both Windows executables rebuilt successfully with PyInstaller 6.21.0.
- Both frozen command-line entry points passed their help smoke tests.

## Frozen GUI walkthrough

One bounded browser walkthrough was performed against a disposable copy of the
populated database through the rebuilt `semi-intel.exe` server:

1. Opened the Strix Halo candidate and verified all five contributing reports,
   excerpts, sources, topics, attachment reasons, and independence groups.
2. Created a human-authored sample claim from report #4443.
3. Converted that report to canonical evidence and linked it to the claim as
   supporting evidence. The workspace showed Radar candidate/report provenance.
4. Reopened the claim in the unified Claims & Evidence workspace.
5. Manually promoted candidate #288 after the GUI clearly explained why it was
   not eligible for automatic promotion.
6. Confirmed the Strix Halo story immediately appeared in Editorial Inbox.
7. Restarted the frozen application and confirmed the promoted story persisted.
8. Database verification after restart confirmed one claim, one link, five
   evidence records generated through the canonical promotion/conversion path,
   one editorial story, and zero notifications.

The walkthrough did not depend on a live external source. Original-report links
were verified as safe external links by automated GUI tests; their remote
availability was intentionally not tested because this pass prohibited network
activity.

## Database and safety checks

The private operator database was never used for acceptance writes. Before
packaging it remained:

- SQLite integrity: `ok`
- Alembic head: `a0b5d7e9f314`
- Sources: 80
- Signal items: 5,211
- Signal candidates: 350
- Claims, evidence, evidence links, editorial stories, notifications: 0

The disposable acceptance database passed `PRAGMA integrity_check` after the
frozen restart. Automatic collection, X collection, and automatic promotion
remained disabled. The acceptance flow created no notifications.

## Remaining limitations

- Signal extraction and candidate grouping remain deterministic heuristics and
  can produce broad or tangential attachments; the new detail makes those
  reasons inspectable but does not change scoring policy.
- Historical candidates remain subject to the documented age and score rules;
  the shortlist supports human judgment without silently overriding them.
- Existing `datetime.utcnow()` deprecation warnings remain intentionally out of
  scope.
