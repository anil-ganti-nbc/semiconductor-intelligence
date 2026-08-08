# DIFF_ENGINE.md

The diff engine answers one question deterministically: *given two immutable snapshots of the same product (or a snapshot and nothing), what changed, and how much should the user care?* It never sees HTML, never calls the network, never calls AI. Same inputs → same events, always — which is what makes it testable and what makes the AI layer safe (ADR-5).

## 1. Inputs and outputs

```
diff(before: NormalizedProduct | None, after: NormalizedProduct, ctx: DiffContext)
    -> list[ChangeEvent]
```

`DiffContext` carries what field comparison alone can't know: the known-hardware DB (is this CPU new to the world?), the product's listing set (is this a duplicate/variant?), and discovery metadata (was this URL hidden?). `before=None` means new product.

## 2. Change taxonomy

| change_type | trigger | notes |
|---|---|---|
| `new_product` | no resolvable prior product | subtype `hidden_listing` if found only via sitemap/JSON, not navigation |
| `component_changed` | cpu/gpu/npu/display canonical value differs | carries `unseen_component: bool` from known-hw DB |
| `spec_changed` | memory/storage/battery/wireless/ports/os differ | numeric fields carry direction + magnitude |
| `price_changed` | amount or currency differs | % magnitude; regional prices diff independently |
| `availability_changed` | in_stock/preorder/sold_out transitions | preorder→ new is often the earliest launch tell |
| `images_changed` | image set hash differs | added vs replaced distinguished |
| `description_changed` | marketing text differs materially | token-level, whitespace/formatting-insensitive |
| `product_renamed` | resolve linked listing under new title/handle | emits alias row |
| `regional_variant` | new listing resolved to existing product, different region | |
| `duplicate_listing` | new listing resolved to existing product, same region | |
| `product_removed` | listing absent for `removal_grace` full passes | see DATABASE.md |
| `support_artifact_added` | BIOS/driver/firmware source engines (M11+) | new-BIOS-before-launch is a classic leak |
| `source_degraded` | source yields ≪ expected products | operational, not product, signal |

Formatting-only changes (whitespace, HTML entity noise, key order) are eliminated *before* diffing by canonical serialization — they can't generate events at all, which is how ★☆☆☆☆ noise is handled: structurally, not by filtering.

## 3. Severity: rules as data (ADR-6)

Ordered rules in `radar.yaml`, first match wins; built-in defaults ship in code and config overrides them:

```yaml
severity_rules:
  - {match: {change_type: component_changed, unseen_component: true}, severity: 5}
  - {match: {change_type: new_product},                               severity: 5}
  - {match: {change_type: spec_changed, field: memory, direction: up}, severity: 4}
  - {match: {change_type: price_changed, magnitude_pct: ">10"},        severity: 3}
  - {match: {change_type: images_changed},                             severity: 3}
  - {match: {change_type: description_changed},                        severity: 2}
  - {match: {},                                                        severity: 2}

notify:
  discord:
    min_severity: 3
    digest_below: 3        # severities 1–2 roll into a daily digest instead of pings
```

Severity is stamped on the event at detection time (rules may change later; history shouldn't rewrite itself).

Rule matching supports comparison operators on numeric attributes (`">10"`, `">=5"`, `"<3"`) and direction matching (`direction: up|down`) on numeric spec fields — sizes are unit-aware, so `1 TB` > `512 GB`. Price events carry `magnitude_pct` and `direction` in meta; spec events carry `direction`. Products are diffed at both summary level and configuration level (per-variant, keyed by SKU); a config appearing or vanishing is a `spec_changed` on field `configurations`.

## 4. Known-hardware database — the star signal

`components` (DATABASE.md) is seeded from a curated list of CPUs/GPUs/NPUs and grows automatically. The normalization layer canonicalizes vendor spec strings ("AMD Ryzen™ AI MAX+ 395 (Strix Halo)" → `ryzen-ai-max-plus-395`) via tokenization + vendor pattern rules; canonicalization failures are *kept raw and flagged*, not guessed. On diff, any canonical component not in the DB triggers `unseen_component: true` → ★★★★★ by default rules, and the component is inserted with `source=discovered` and a pointer to the product that revealed it. That insert is the platform learning: the second product with a Ryzen AI Max+ 396 is *not* breaking news, and the DB now knows it.

False-positive control: unseen-component events where canonicalization confidence is low render with an explicit caveat ("string not recognized — may be a typo'd 395") instead of being suppressed. For your use case a false ping is cheap; a missed launch is expensive.

## 5. Interaction with entity resolution (ADR-3)

Resolution runs *before* diff and decides which prior snapshot `before` is. The diff engine trusts it but propagates its confidence: events on a `fuzzy`-resolved listing carry the link confidence, and below `resolve.review_threshold` the event renders as a question ("K12 Pro may be a rename of K12 — confirm?") with `listings.needs_review` set. `oem-radar review confirm|split <listing-id>` records the human answer into the alias table, permanently improving future resolution.

## 6. Testing

The suite is table-driven: `tests/diff_cases/*.yaml`, each case = before JSON + after JSON + expected events. Regression policy: every real-world mis-diff becomes a case file. Property tests assert invariants — diff(x, x) is empty; diff is deterministic; canonical-serialization idempotence; severity rules total (every event matches some rule).
