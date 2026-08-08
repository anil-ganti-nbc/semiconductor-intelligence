# Feedback System

Human review of `change_events` (alerts), with immutable history and a foundation for later deterministic rule suggestions.

This is **not** an AI ranking engine. Reviews are source data. Suggested rules are never applied automatically.

## Purpose

OEM Radar initially produces many low-value alerts. Reviewers classify each event so noise patterns can be measured and, later, turned into *proposed* tuning rules that require explicit human approval.

## Alert lifecycle

| State | Meaning |
|-------|---------|
| **NEW** | No row in `alert_reviews` for this `change_events.id` |
| **REVIEWED** | A current review exists |

Outcomes (stable string enums):

| Outcome | Definition |
|---------|------------|
| **HIT** | Directly useful for an article, scoop, or actionable investigation |
| **INTERESTING** | Valid signal worth retaining, not immediately actionable |
| **NOISE** | Generated as designed, but not editorially useful |
| **BUG** | Parser failure, incorrect extraction, broken matching, invalid data, or other software defect |

**NOISE and BUG are distinct.** Do not conflate them.

## Reason taxonomy

Stored identifiers are stable machine codes (e.g. `TEMPORARY_404`). Human labels and display groups are metadata for the UI / API only.

See `GET /api/feedback/reasons` for the full list with `code`, `label`, and `group`.

## Review workflow

1. Open the dashboard (`oem-radar dashboard`, default `http://127.0.0.1:8787`).
2. On **All changes**, each event shows a review badge (`UNREVIEWED` / outcome) and a link `#id` → `/alerts/{id}`.
3. Filter with **Unreviewed only** (or by outcome).
4. On the review page, select outcome (shortcuts `1`–`4`), optional reason codes, reviewer, notes → **Save review**.

Keyboard shortcuts select the outcome only; they do **not** auto-submit. They are ignored while focus is in an `input`, `textarea`, `select`, or `contenteditable` element.

Updating a review always appends a row to `alert_review_history` (previous/new outcome and reason codes, change note, actor).

### Seen vs review

- **Seen / mark-seen** curates the *known-hardware* feed (components).
- **Review** classifies a *change event* for editorial value.

These are independent. Reviewing an alert does not mark a component seen, and vice versa.

## Discord

When feedback is enabled, each Discord embed footer includes:

```text
OEM Radar · Alert ID: 1842 · Review: http://127.0.0.1:8787/alerts/1842
```

Also added as fields: Collector, Alert type, Confidence.

Configure base URL:

```yaml
feedback:
  enabled: true
  dashboard_base_url: "http://127.0.0.1:8787"
  max_review_request_bytes: 16384
```

Trailing slashes on the base URL are normalized. If `feedback.enabled` is false, the Review link is omitted (Alert ID remains).

## API

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/alerts/{id}/review` | Alert summary, current review, history, taxonomy, CSRF token |
| POST | `/api/alerts/{id}/review` | Create/update review (CSRF required) |
| GET | `/api/feedback/reasons` | Outcomes + reason taxonomy |

POST body example:

```json
{
  "outcome": "NOISE",
  "reason_codes": ["TEMPORARY_404"],
  "reviewer_note": "Returned on next crawl.",
  "reviewer": "anil",
  "change_note": "Reclassified after next observation.",
  "csrf_token": "…"
}
```

Errors:

```json
{ "error": { "code": "invalid_reason_code", "message": "…" } }
```

CSRF: send `X-OEM-Radar-CSRF` header and/or `csrf_token` field matching the per-process token embedded in the review page. This is a **localhost** hardening measure, not multi-user authentication.

Request limits: content-type must be JSON; body size capped (`max_review_request_bytes`); unknown fields rejected; validation runs at the store boundary.

## Database

Schema v4 tables: `alert_reviews` (one row per event), `alert_review_history` (append-only), `rule_suggestions` (suggestions only; unused until later stages).

Absence of a review row means **NEW**. Historical events are never backfilled.

## Security model (localhost)

- Server binds to `127.0.0.1` by default.
- Review writes require CSRF token.
- Bodies size-limited; HTML escaped on the review page.
- Parameterized SQL only; numeric IDs validated.
- Not a multi-tenant auth system.

## Current limitations

- No analytics dashboard yet.
- No rule suggestion generation / simulation CLI yet.
- No automatic suppression.
- Related events matching is conservative (same `product_key` only in this pass).
- CSRF is per-process; restarting the dashboard rotates the token.

## Manual approval process (future)

Rule suggestions will be stored with status `PROPOSED` until an operator sets `ACCEPTED` / `REJECTED` / `IMPLEMENTED` / `REVERTED`. Nothing in the collectors or severity engine reads these rows automatically.

## Analytics

**Signal** = HIT + INTERESTING. **BUG is never noise.**

**Signal-to-noise ratio** = `signal_count / noise_count` when `noise_count > 0`.
If `noise_count == 0` and `signal_count > 0`, `signal_to_noise_ratio` is `null` and `signal_to_noise_infinite` is `true`.
If both are zero, both fields are null/false.

Confidence scores on snapshots are treated as 0–1 floats; buckets: 0.00–0.19 … 0.80–1.00 and `unknown`.

Date filters: `start` inclusive, `end` exclusive (ISO-8601). Timestamps compared as stored strings (UTC ISO from the crawler).

API: `GET /api/feedback/metrics?start=&end=&group_by=&limit=`
Allowlisted `group_by`: oem, collector, alert_type, reason_code, day, week, confidence.

Dashboard: `GET /feedback`

## Rule suggestions

Detectors (deterministic):

| Detector | Rule type | Notes |
|----------|-----------|--------|
| Temporary 404 / removals | `require_consecutive_missing` | Needs reviewed removal/availability noise |
| Duplicate alerts | `suppress_exact_duplicate` | DUPLICATE_ALERT reason preferred |
| Image CDN/query churn | `normalize_image_url` | images_changed |
| Unchanged document | `compare_content_hash` | **Requires** hash evidence in `meta` |
| Minor price | `minimum_price_change_percent` | Requires `meta.magnitude_pct` |

**Fingerprint** = SHA-256 of `{version, rule_type, collector, alert_type, reason_code, parameters}` (32 hex chars).
Re-running analyze refreshes metrics for PROPOSED/ACCEPTED/IMPLEMENTED; does **not** revive REJECTED/REVERTED.

Structured rule lives in `rule_json` (schema v5). Human one-liner remains in `suggested_rule`.

**ACCEPTED ≠ activated.** **IMPLEMENTED is manual recordkeeping only** — collectors are never modified by this system.

## Simulation

`oem-radar feedback simulate --rule-id N`

Assessment: `SAFE_CANDIDATE` | `RISKY` | `INSUFFICIENT_EVIDENCE` based on sample size, review coverage, and `maximum_signal_loss_ratio` (default 0.05). Does not change suggestion status.

## CLI

```bash
oem-radar feedback analyze [--dry-run] [--json] [--minimum-samples 10] ...
oem-radar feedback simulate --rule-id 14 [--json]
```

## Status transitions

PROPOSED→ACCEPTED|REJECTED; ACCEPTED→IMPLEMENTED|REJECTED; IMPLEMENTED→REVERTED; REVERTED→ACCEPTED.
