# PLUGIN_GUIDE.md

Two extension paths, in order of how often you'll use them.

## Path 1: Add an OEM (YAML only — the common case)

If the OEM runs Shopify or WooCommerce, you write no code. Create `config/oems/<name>.yaml`:

```yaml
manufacturer:
  name: GMKtec
  aliases: [GMK, "极摩客"]
  country: CN

sources:
  - id: gmktec-shopify
    engine: shopify                # registered engine name
    base_url: https://www.gmktec.com
    min_interval: 6h               # ADR-1: run skips this source if crawled more recently
    discovery: [products_json, sitemap]   # strategies, union of results
    currency_default: USD
    category_map:                  # vendor collection → normalized category
      mini-pcs: mini_pc
      handhelds: handheld
    spec_hints:                    # optional regex/JSON-path nudges for messy fields
      cpu_from_title: true
```

Run `oem-radar validate` — it checks the descriptor against the engine's declared config schema without touching the network. Then `oem-radar run --source gmktec-shopify --dry-run` to see what would be stored/notified. That's the whole procedure; core, DB, notifier, and every other OEM are untouched.

How do you know which platform a store runs? `oem-radar probe <url>` (M2) fetches the homepage once and fingerprints it (Shopify: `/products.json` responds, `cdn.shopify.com` assets; Woo: `wp-json/wc/store` or `woocommerce` body classes).

## Path 2: Add an engine (code — for platforms and oddballs)

An engine implements the `SourceEngine` protocol (`core/interfaces.py`):

```python
from oem_radar.core.interfaces import SourceEngine, Fetcher, ProductRef, FetchedDocument
from oem_radar.core.models import NormalizedProduct, RawProduct, ValidationIssue
from oem_radar.core.registry import engines

@engines.register("topton")
class ToptonEngine:
    config_schema = ToptonConfig          # pydantic model; validates the YAML source block

    def discover(self, fetcher: Fetcher) -> Iterable[ProductRef]: ...
    def parse(self, doc: FetchedDocument) -> RawProduct: ...
    def normalize(self, raw: RawProduct) -> NormalizedProduct: ...
    def validate(self, product: NormalizedProduct) -> list[ValidationIssue]: ...
```

Rules that keep the architecture honest:

- **No I/O except through the injected `Fetcher`.** The fetcher gives you politeness, caching, conditional GETs, and — in tests — canned fixtures for free. An engine that imports `requests` fails review.
- **No database, no Discord, no AI.** Return values only.
- **`normalize` maps into the shared model; put everything else in `raw_data`.** Never invent values: unknown stays `None`, and `confidence` should reflect how much of the listing you actually understood.
- **`validate` reports, it doesn't reject.** Issues lower confidence; the core decides what to do (a listing failing validation because of an unrecognized CPU string is high-value, not garbage — see DIFF_ENGINE.md on known-hardware flags).
- **Discovery strategies are separate registered classes** (`discovery.register("sitemap")`) so engines share them; an engine declares which strategies it supports.

### Engine tests (required per engine)

Fixtures live in `tests/fixtures/<engine>/` as captured real responses (JSON/HTML), goldens in `tests/goldens/<engine>/`. Minimum suite: discovery finds the expected refs from fixture responses; parse+normalize matches stored golden JSON (`assert_goldens` — a change in engine output fails loudly; accept deliberately with `UPDATE_GOLDENS=1 pytest`); validate flags the deliberately broken fixture (`assert_validate_flags`); config schema rejects a malformed block (`assert_config_rejected`). The shared harness in `tests/engine_harness.py` provides all four given routed fixture responses, so a new engine's test file is ~20 lines — see `tests/test_review_now_list.py` for usage.

## Path 3 (rare): new provider

Storage, notification, and AI backends implement `SnapshotStore`, `Notifier`, `Summarizer` respectively, register under a name, and get selected in `radar.yaml` (`notifier: discord`, `store: sqlite`, `summarizer: anthropic`). Same rules: config-schema declared, no cross-imports.

## Source support status (required)

Every descriptor must be classified. Allowed values:

`LIVE_VALIDATED` · `LIVE_PARTIAL` · `CANARY` · `NEEDS_OWNER_PROBE` · `BLOCKED_JS` · `BLOCKED_BOT` · `BROKEN` · `DISABLED_LOW_VALUE`

Do not set `enabled: true` without live proof (or real sanitized fixtures + tests) and a status other than the blocked/broken set.

## Fixture rules

- Capture real responses (`/products.json`, catalog HTML, etc.).
- Sanitize: truncate long HTML, strip query noise where safe, no credentials/PII.
- Store under `tests/fixtures/` with source + date in comments or docs.
- Never invent catalog JSON to green a test.

## Acceptance criteria (enablement)

1. Discovery returns relevant products from fixture and live probe.
2. Unexpected zero-product runs are **failed**, not success.
3. Stable identifiers across fixture re-runs.
4. Accessories filtered via non-product denylist.
5. Malformed product does not abort the whole source.
6. Source failure does not abort other collectors.
7. Baseline quiet on first crawl.
8. Removal grace unchanged.
9. Real fixtures + tests present.
10. Documented available/missing signals in `docs/OEM_COVERAGE.md`.
11. Change events carry evidence metadata when available (`collector_engine`, `catalog_count`, …).
12. Health reflects degraded/failed on catalog collapse.
13. No fake confidence from “parse returned an object.”

## Collector health

Configured under `collector_health` in `radar.yaml`. Catalog collapse is **not** a mass `product_removed` event.

## Canary promotion

New sources may ship as `CANARY`: collect + persist; avoid flooding the primary Discord channel when a canary channel/config exists. Promotion to `LIVE_VALIDATED` is **manual** after multiple successful runs.

## Feedback suggestions

Stage 3 may *propose* rules. They must **not** be applied automatically to collectors. Implementation is a separate human change.

## Collector health runtime wiring

`collector_health` in `radar.yaml` is loaded into `RadarConfig.collector_health` and
passed by `run_all()` into every `run_source(..., health_cfg=...)`. Direct unit-test
calls to `run_source` without `health_cfg` use safe defaults.

Thresholds must satisfy `0 <= min < warn <= 1`. Failed runs never become the
last-good catalog baseline and never emit mass `product_removed` events.
