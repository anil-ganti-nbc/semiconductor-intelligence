# Semiconductor Intelligence Platform 3.3.9 — Verification Report

## Scope and safety

This bounded pass repaired the canonical Entities and deterministic Claim
Matches workflows. It added no table or migration and did not change collection,
clustering, scoring weights, the match threshold, automatic promotion,
notifications, scheduling, or disabled-by-default settings. Alembic remains
`a0b5d7e9f314`.

The supplied populated operator database was treated as read-only. Development
tests used fresh fixture databases, and the frozen acceptance walkthrough used
a disposable database copy.

## Confirmed pre-pass state

- Canonical entities: 0
- Signal entity mentions: 11,150
- Unresolved candidate mentions: 7,618
- Rejected mentions: 3,532
- Claims, canonical evidence, evidence links, and claim-link suggestions: 0

The legacy entity and suggestion engines existed, but the GUI exposed no usable
bridge from Radar mention proposals to canonical entities or entity-backed
claims. The scanner therefore lacked its primary deterministic entity-match
signal and provided little explanation when it had no eligible work.

## Delivered behavior

- Explicit canonical entity creation, filtering, detail, aliases, attributes,
  usage counts, relationships, and Radar provenance.
- Bounded unresolved-mention aggregation with exact normalized resolve,
  create, optional alias, reject, and ignore actions.
- Candidate-entity synchronization after an operator resolution; no automatic
  canonical creation while listing or scanning.
- Searchable optional subject selectors for manual and Radar claim creation.
- Claim Match readiness counts, actionable prerequisites, scan diagnostics,
  enriched review context, status/history filters, and Radar provenance.
- Existing real claim/evidence links are excluded from proposals, and stale
  proposal acceptance returns a readable conflict.

## Automated verification

- Focused workflow plus dashboard JavaScript gate: **20 passed**.
- Relevant entity, graph, claim engine, Radar, newsroom, web, and persistence
  gate: **128 passed**.
- Complete suite: **507 passed, 0 failed**.
- Existing `datetime.utcnow()` deprecation warnings remain intentionally out of
  scope.

## Frozen executable walkthrough

A single bounded walkthrough was completed with the rebuilt `semi-intel.exe`
against a disposable copy of the populated operator database. It confirmed:

- The initially empty Entities workspace explained that canonical entities are
  curated and exposed all 7,618 unresolved Radar mentions for review.
- Explicitly resolving AMD, Intel, and Strix Halo created three canonical
  entities, resolved 863 exact normalized mentions, and synchronized the
  affected Radar candidates. Intel 14A was also created deliberately with a
  controlled foundry-node type, alias, and vendor attribute.
- Two Intel 14A claims were created with a subject entity. TechPowerUp Radar
  report #4646 from candidate #288 was converted to canonical evidence without
  creating an evidence link automatically.
- The deterministic scan evaluated two eligible pairs and created two proposals.
  Each proposal displayed the claim, subject, source, report title/excerpt,
  Radar provenance, score, and plain-language reasons.
- One proposal was accepted as supporting evidence and one was rejected. The
  accepted proposal created exactly one claim/evidence link; the rejected one
  created none. The claim detail showed the link, editable stance/note, Radar
  provenance, and an `evidence_linked` timeline event.
- After stopping and restarting the frozen executable, all four entities, two
  claims, one evidence record, one accepted proposal, one rejected proposal,
  and exactly one evidence link remained intact.

The source operator database hash remained unchanged throughout the walkthrough.

## Remaining limitations

- Canonical entity review is intentionally human-curated. The platform does not
  perform ontology construction, fuzzy matching, embeddings, or automatic NER
  promotion.
- Extractor types such as codename, version, retailer, and PCI ID remain proposal
  metadata; the operator selects an existing controlled canonical type.
- Relationship editing and semantic search remain outside this phase.
