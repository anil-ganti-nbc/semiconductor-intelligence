# Reddit Pilot M0 — r/hardware inside Semiconductor Intelligence

This is a **source-local** experimental RSS pilot. It is not a fleet Reddit
platform, not a shared `clank-reddit` library, and not a new collection
provider. Reddit remains:

* `platform = reddit` (in `Source.provider_metadata`)
* collection `provider = rss`
* feed URL `https://www.reddit.com/r/hardware/.rss`

There is no `provider="reddit"`.

## Operator registration

The source must not appear in a database merely because this code was
deployed. Registration is an explicit operator action:

```text
semintel radar register-reddit-hardware
```

or the Python helper `register_reddit_hardware(session)`.

Exact existing identity `(provider=rss, provider_key=<r/hardware RSS URL>)`
is idempotent. A different source that only reuses the display name
`Reddit r/hardware` is a conflict: registration fails closed and does not
mutate or adopt that row.

Created state:

| Field | Value |
| --- | --- |
| name | Reddit r/hardware |
| type | RSS |
| provider | rss |
| provider_key / url | `https://www.reddit.com/r/hardware/.rss` |
| enabled | true |
| polling_enabled | **false** |
| muted | **true** (experimental; Discord blocked) |
| maturity | `experimental` in `provider_metadata` |
| delivery_admission_required | **true** (sticky; unmute is not admission) |

Registration performs **no network fetch**. Polling stays off until an
operator enables it separately. This helper is idempotent: a second call
returns the existing row and does not clobber a later promotion or polling
enable.

## Why `Source.muted` (no new lifecycle column)

`Source.muted` already existed from Signal Radar absorption. It was
serialized and imported, but it was **not** a collection gate and **not** a
notification gate. M0 reuses it as:

> muted = experimental / external editorial delivery blocked

`Notification.muted` and `muted_event_types` are unrelated per-alert
controls. Do not conflate them.

No Alembic migration is required. Baseline and admission watermarks live in
the existing `Source.provider_metadata` JSON:

```json
{
  "platform": "reddit",
  "subreddit": "hardware",
  "maturity": "experimental",
  "delivery_admission_required": true,
  "baseline_completed_at": "<ISO datetime of first successful collect>",
  "delivery_admitted_at": "<ISO datetime stamped only by admit_source_for_delivery>"
}
```

`delivery_admission_required` is sticky lifetime provenance. Unmute,
maturity changes, and feed-identity cursor resets do not clear it.

## Silent first populate (STD-DATA-COM-002)

The first successful `collect_source` for an **experimental / muted**
source whose `last_success_at` is still null is **BASELINE**.

Ordinary unmuted SemInt sources are unchanged: they do not receive this
silent first-populate stamp. Their novelty path remains the existing
global `NotificationSettings.activation_at` watermark.

* Raw `SignalItem` rows may be persisted.
* Those observations are tagged by `collected_at <= baseline_completed_at`.
* They do not enter the ordinary novelty / Discord path.
* The watermark is durable across process restart.

Sources that already have `last_success_at` are past first-populate. Adding
this feature does **not** reset them. Global `NotificationSettings.activation_at`
is not touched.

Reddit created time stays on `SignalItem.posted_at`. SemInt collected time
stays on `SignalItem.collected_at`. Observation identity stays
`(provider, external_id)`.

## Experimental delivery gate

While `Source.muted` is true, no member observation of that source is
delivery-admitted. Candidate notifications seed transition watermarks
without emitting:

* HIGH_ATTENTION / PROMOTION_READY boolean crossings are **not** consumed
  during experimental soak, so a later admitted member can still cross.
* SCORE_INCREASE / corroboration numeric watermarks **are** advanced
  without emitting, so soak-era movement cannot become a backlog when the
  gate later opens.

Those are the existing candidate-level transition rules. An experimental
echo that does not change score or independent-group count does not emit.
An admitted member that does change those fields may emit. The Reddit
observation remains non-admitted either way.

Admission for emit is **transition-aware**, not lifetime membership. An
older admitted observation already on the candidate does not authorise a
later HIGH_ATTENTION, SCORE_INCREASE, INDEPENDENT_CORROBORATION, or
PROMOTION_READY crossing caused only by new experimental material.
Notification watermarks record which `SignalItem` ids have been evaluated;
only newly attached members since that snapshot can grant authority. A
later genuinely admitted observation may still produce a legitimate
transition. Experimental numeric movement still advances internal
watermarks so soak-era jumps cannot flood after admission.

This is source-scoped. The global webhook enable flag is left alone. Other
SemInt sources can still Discord.

Provider-failure alerts originating from a muted source are emitted
`muted=True` so the existing webhook adapter skips them. Incidents and
`ProviderRun` health remain recorded.

## Promotion without backlog

```text
semintel radar admit-source SOURCE_ID
```

or `admit_source_for_delivery(source)`.

That unmutes the source and stamps `delivery_admitted_at`. It does **not**
enable polling.

An observation is delivery-admitted only when all of these hold:

1. the source is not muted
2. the observation is not in the first-populate baseline
3. if the source has sticky `delivery_admission_required` (set at r/hardware
   registration and kept for the source's lifetime), a **valid**
   `delivery_admitted_at` must exist — missing or malformed timestamps fail
   closed and never fall back to `baseline_completed_at`
4. `collected_at` is **after** `delivery_admitted_at`

Clearing `Source.muted` without `admit_source_for_delivery` does **not**
grant Discord authority.

Ordinary SemInt sources never placed under this contract keep prior
behaviour. Mixed-source candidates are not poisoned: an experimental
member cannot emit, and cannot block an admitted member's own transition.
Admission stays on observation/source provenance, not candidate maturity.

## Reddit RSS failure honesty

Scoped extra check: a **zero-entry reddit.com feed** is a suspicious empty
listing and is **not** HEALTHY. A genuinely empty non-Reddit RSS feed
remains a valid success.

Generic honesty (any RSS URL): HTTP 429 / 403 / other 4xx-5xx, and
bozo+no-entries malformed documents, fail the `ProviderRun` rather than
looking healthy. There is no HTML fallback, proxy, or anti-bot bypass.

## Identity

Unchanged: `(provider, external_id)`. For Reddit RSS, `external_id` is the
feed `id` (permalink) then `link`. Title is never used. `SignalItem.url`
keeps the Reddit permalink. Outbound aggregator URLs found in the entry
HTML are stored on `expanded_links` so existing `canonical_url()` /
independence grouping remain usable.

Ordinary non-Reddit RSS normalisation is unchanged from pre-M0: one
`summary or description` body, permalink-only `links`, and `author` as
feedparser exposed it. Richer HTML/outbound extraction is Reddit-only.

## Out of scope for M0

No Hetzner/NAS/deploy, no production DB registration, no live Reddit
fetch, no Discord test, no scheduler change, no shared Reddit package,
no other Clank.
