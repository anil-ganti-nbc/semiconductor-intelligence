# Suggested Sources Architecture (v0.9.4)

This document is the honest capability map for the Suggested Sources
system: what Semi Intel can actually monitor today, what it can suggest
but not collect from yet, and why the implementation is shaped the way it
is. It supersedes informal notes in `docs/UI_ACCEPTANCE_2026-08.md`'s
earlier batch sections for anything that conflicts.

## Provider capability matrix (Phase 1)

| Provider/type | Discovery supported | Collection supported | Validation | Auth required | Notes |
|---|---|---|---|---|---|
| RSS/Atom | Yes (`feed_discovery.discover_feeds`, `RSSProvider.validate`) | Yes (`RSSProvider`, `RSSSourcePlugin`) | Yes (`feedparser`, non-fatal `bozo` tolerated) | No | The only genuinely general-purpose collector in the codebase |
| X/Twitter | N/A (handle-based, not discovered) | Yes (`XProvider`, Playwright) | Handle normalization only | Yes (browser session) | Optional `x` extra; absent by default |
| pci.ids registry | N/A | Yes (`PciIdsSourcePlugin`) | N/A | No | Single fixed source, not a general provider |
| Reddit (subreddit) | **New this release** — via its well-known `.rss` endpoint | **Via RSS**, not a dedicated collector | Yes, one bounded `RSSProvider.validate()` call per candidate | No | See "Why Reddit/GitHub aren't new providers" below |
| GitHub (repository releases) | **New this release** — via `releases.atom` | **Via RSS**, not a dedicated collector | Yes, same mechanism | No | Commits/tags/issues feeds NOT implemented this pass |
| Forum (generic) | **New this release** — structural detection + existing feed discovery | Via RSS, only if the forum happens to expose a working feed | Yes, reuses `discover_feeds()` | No | No Discourse/XenForo-specific API integration; native feed or nothing |
| Official newsroom | **Not implemented** | N/A | N/A | N/A | `Entity` has no website/homepage column (`semi_intel/domain/models.py`) — REQUIRES_PROVIDER: needs a schema addition before this can exist honestly |
| YouTube, Discord, Weibo, JSON API, sitemap, retailer/product pages | **Not implemented** | N/A | N/A | N/A | OUT_OF_SCOPE this release; no code anywhere in the repo understands these today (confirmed by exhaustive grep) |

**SUPPORTED_NOW**: RSS/Atom, X (handle-based), pci.ids.
**SUPPORTED_VIA_RSS**: Reddit (subreddit `.rss`), GitHub (repo `releases.atom`), generic forums with a discoverable native feed, generic websites.
**REVIEW_ONLY**: any domain/forum/subreddit/GitHub candidate whose deterministic or discovered feed check fails — kept in the queue with `feed_url=None`, never silently dropped, "Find feed" remains available for a retry.
**REQUIRES_PROVIDER**: official newsroom sections (schema gap), Reddit-user profiles, GitHub commits/tags/issues feeds.
**UNSAFE_OR_OUT_OF_SCOPE**: YouTube/Discord/Weibo/JSON API/sitemap/retailer collection, and generic web-wide crawling of any kind.

## Why Reddit/GitHub suggestions are `kind=DOMAIN`, not a new provider

`CollectionService._provider_for()` (`semi_intel/signals/collection.py`) is a
hardcoded dispatch recognizing exactly `{"rss", "replay"}` plus a
special-cased `"x"`. A `Source` created with `provider="reddit"` or
`provider="github"` would be accepted into the database but would fail
**every single poll cycle forever** with `ProviderUnavailable` — not
silently inert, actively erroring.

A subreddit's `.rss` URL and a GitHub repository's `releases.atom` URL are
both literally RSS/Atom feeds. So instead of inventing new provider
plumbing, Reddit and GitHub suggestions are represented exactly like a
website suggestion — `kind=SourceSuggestionKind.DOMAIN`, a synthetic
`domain` identity (`reddit:r/{name}`, `github:{owner}/{repo}`), and a
`feed_url` once validated — and flow through the *existing*, already-
tested `/api/source-suggestions/{id}/add` endpoint, which creates a real
`type=RSS` `Source` that the existing RSS polling path (`provider="manual"`)
picks up correctly with zero new collection code. `platform` is used
purely as a display/badge tag on these domain-kind rows.

This is the smallest architecture-consistent change: no migration, no new
enum values, no new provider registry entry, full reuse of the
already-battle-tested website Find-Feed/Add-source workflow.

## Suggestion input pathways (Phase 2)

Two previously-separate mining pathways existed before this release:

1. **Citation-based domains** (`EditorialDiscoveryService._refresh_source_suggestions()`)
   — reads the `citations` table, itself populated by regex-extracting
   `<a href>` tags out of `Evidence.raw_content` HTML. Runs automatically
   every pipeline cycle. In this operator's real database this pathway
   has produced **zero** suggestions (0 citation rows against 21,417
   evidence rows) — a pre-existing, separately-scoped gap (see
   `docs/UI_ACCEPTANCE_2026-08.md`'s v0.9.3 section), not something this
   release changes.
2. **Attribution-mined handles** (`refresh_handle_suggestions()`) — reads
   `SignalItem.normalized_text` for attribution phrases ("according to
   X", "via Y"). Wired into the pipeline in v0.9.3.

**New this release** (`semi_intel/signals/source_discovery.py`): a third,
previously entirely unmined input — `SignalItem.url`,
`SignalItem.expanded_links` (JSON list), and bare URLs embedded in
`SignalItem.normalized_text`. This is a genuinely separate corpus from
`Evidence`/`Citation` (SignalItems are raw provider output; most never
become Evidence at all). Against the real database this corpus contains
6,123 SignalItems, 3,541 with a direct `url`, and produced (before any
exclusion filtering) domains like `techpowerup.com` (1,190 mentions),
`notebookcheck.net` (1,071), `theregister.com` (414), `tomshardware.com`
(396) — legitimate, editorially relevant tech publications — plus 12
distinct GitHub repositories (`llvm/llvm-project`, `intel/perfmon`,
`amd/aocl-dlp`, ...) and one subreddit (`r/framework`).

## Generators (Phase 4)

All four generators live in `semi_intel/signals/source_discovery.py` and
are orchestrated by `run_source_discovery()`, invoked via
`POST /api/radar/source-suggestions/discover` (operator-triggered, bounded,
not tied into any automated job this release — see Phase 9/10 below).

| Generator | Input | Threshold | Network calls |
|---|---|---|---|
| `discover_domain_candidates` (website + forum) | `SignalItem.url`/`.expanded_links`/`.normalized_text` | `MIN_MENTIONS = 3` distinct items | None — pure text mining |
| `discover_subreddit_candidates` | same, regex `reddit\.com/r/(\w+)` | `MIN_MENTIONS = 3` | Bounded: up to 20 new candidates per run, one `RSSProvider.validate()` fetch each |
| `discover_github_candidates` | same, regex `github\.com/(owner)/(repo)` | `MIN_MENTIONS = 3` | Same bounded pattern, `releases.atom` |
| `refresh_handle_suggestions` (reused as-is from v0.9.3) | `SignalItem.normalized_text` attribution phrases | `MIN_APPEARANCES = 3` | None |

Website/forum are the same generator: a domain is tagged `platform="forum"`
purely from structural path markers (`/forum/`, `/forums/`, `/threads/`,
`/t/`, `/c/`, etc.) observed in its mentions — identity stays at the
registrable-domain level (per Phase 4's "prefer suggesting a category root
over one thread" guidance), with one example thread URL folded into the
`reasons` text for provenance.

### Quality filtering (Phase 5)

`_is_excluded_domain()` rejects:
- `NOISE_DOMAINS` (reused from `EditorialDiscoveryService` — Google/social/
  CDN/ad platforms, already includes `reddit.com`/`youtube.com`/etc.)
- `SHORTENER_DOMAINS` (`bit.ly`, `t.co`, `tinyurl.com`, ...) — not covered
  by the existing `NOISE_DOMAINS` set, added here since `t.co` alone
  accounted for 3,119 raw mentions in the real database
- `CDN_OR_ASSET_DOMAINS` (`imgur.com`, `githubusercontent.com`, ...)
- `PLATFORM_HANDLED_DOMAINS` (`github.com`, `reddit.com`) — a domain a
  dedicated generator already gives a specific identity to must not *also*
  get suggested as a generic website (caught during browser acceptance:
  `github.com` was briefly double-suggested once as `github:{owner}/{repo}`
  and once as a bare "Website" — fixed by excluding these domains from the
  generic domain generator)
- already-registered `Source.url` domains
- non-`http(s)` schemes

Existing suggestions are never re-created on a rerun — `_upsert_domain_suggestion()`
and the Reddit/GitHub generators match on the row's unique `domain` and
update `appearances`/`score`/`reasons`/`last_seen_at` only, **never**
`status` — an operator's Ignore/Block decision is never silently reversed
by a later discovery run.

### Confidence (Phase 6)

`score = min((mention_count + independent_origin_count) / 10.0, 1.0)` —
deterministic, not a reuse of Signal Candidate attention scoring (which
answers a different question: "how urgent is this story", not "how
trustworthy is this source candidate"). `independent_origin_count` is the
number of distinct `Source.id` values whose collected items linked to the
candidate — a genuinely new confidence signal this release starts
populating (the column already existed on `SourceSuggestion`, unused
before now). `reasons` is always a human-readable sentence naming the
mention count and independent-source count, e.g. *"Cited by 7 signal
item(s) across 3 independent source(s)"*.

## Diversity and queue presentation (Phase 7)

The Suggested Sources toolbar now has: All / X handles / Websites / Forums
/ Reddit / GitHub / Unsupported, plus a live per-group count summary
(`Website: 4 · Forum: 1 · Reddit: 1 · GitHub: 1`, etc.), computed
client-side from the already-fetched list. X suggestions are never
suppressed or deprioritized — the filter only changes what's *displayed*,
never what's stored. If a real database genuinely contains only X
suggestions (as this operator's did until the generators above ran), the
UI reports that honestly rather than fabricating diversity.

## Provider-specific actions (Phase 8)

Reddit, GitHub, Website, and Forum suggestions share one workflow because
they are mechanically identical once `kind=DOMAIN`+optional `feed_url` is
established: **Find feed → Add source → Ignore/Block**. The backend
`discover-feed` endpoint is platform-aware only in *which* URL it
validates — Reddit/GitHub retry their one deterministic feed URL instead
of running `feed_discovery.discover_feeds()` against a synthetic non-DNS
`domain` value (which would be meaningless for e.g. `reddit:r/hardware`).
X handles keep their existing v0.9.3 workflow (`Add X source` →
`accept_source_suggestion()`). Unsupported suggestions (attribution-mined
names with no identifiable platform) show no action beyond Ignore/Block —
"Unsupported source type", never a button that would silently do nothing.

## Operator-triggered discovery (Phase 9) and failure isolation (Phase 10)

`POST /api/radar/source-suggestions/discover` runs all four generators
sequentially, **committing after each one** — a later generator's failure
can only roll back its own uncommitted work, never an earlier generator's
already-persisted suggestions (verified by
`test_one_generator_failing_does_not_suppress_or_roll_back_the_others`).
The response reports per-generator `status` (`SUCCESS`/`PARTIAL`/`FAILED`),
`examined`/`created`/`updated`/`duplicates_skipped`/`rejected` counts, and
`errors`. `overall_status` is `FAILED` only when *every* generator failed —
never a false empty success.

This endpoint is **not** wired into the automatic pipeline this release
(unlike `refresh_handle_suggestions`, wired in v0.9.3) — it's reachable via
the "Discover source suggestions" button. Reason: the domain/Reddit/GitHub
generators scan the full `SignalItem` table each run (bounded by the
existing table size, not by an incremental cursor), which is appropriate
for an operator-triggered action but not yet soak-tested for every
15-minute pipeline cycle at scale — a deliberate, documented scope cut per
Phase 9's "avoid network-heavy crawling by default... do not tie it
silently to every normal collection run until soak-tested."

## Known limitations

- Official newsroom/company-source suggestions are not implemented — the
  `Entity` model has no website/homepage field to key them off honestly.
- GitHub monitoring covers releases only; commits/tags/issues feeds are
  not implemented.
- Reddit-user (non-subreddit) profiles are not extracted.
- Forum detection is purely structural (path markers); there is no
  Discourse/XenForo API integration — a forum with no native RSS/Atom
  feed and no discoverable `<link rel="alternate">` stays REVIEW_ONLY
  forever (correctly, not incorrectly).
- The domain/Reddit/GitHub generators scan the entire `SignalItem` table
  on every operator-triggered run — fine at the current data volume
  (~6,000 rows), but not incremental; a future pass could track a
  high-water-mark cursor if the table grows substantially.
