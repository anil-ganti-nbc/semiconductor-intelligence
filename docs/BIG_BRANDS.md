# BIG_BRANDS.md — monitoring Lenovo, ASUS, HP, MSI, etc.

The boutique OEMs (GMKtec, Minisforum, Beelink…) run Shopify and hand you a
clean `/products.json`. The big brands do not. This doc records what the
2026-07-22 feasibility probe found and how to add them without guessing.

## Probe results (2026-07-22)

| Vendor | Catalog surface | Verdict | Engine |
|---|---|---|---|
| **Dell** | `/en-us/shop/.../sr/laptops` | ✅ static HTML with JSON-LD ItemList | **built** (`engines/dell`) |
| **ASUS** | `/us/laptops/.../filter?...` | ❌ redirects to homepage; product grid is client-side JS | needs Playwright |
| **Lenovo** | `/us/en/c/laptops/` | ❌ empty body; JS-rendered or bot-gated | needs Playwright |

The rule this confirmed: **probe before writing a parser.** Two of three
flagship brands can't be scraped statically. Do not write an ASUS or Lenovo
parser against assumed HTML — it will be fiction.

## Dell engine (the working reference)

`engines/dell/__init__.py`. Static fetch of category pages; extracts a JSON-LD
`ItemList` of `Product` nodes (primary) with a text-anchor fallback (`Model
<code>`, `Starting at $<price>`) if Dell drops the structured data. Identity
is the **Dell model code** (DX13260, PC16250…) carried in `vendor_sku`.

Signal scope (owner decision): the catalog page gives model code + price +
display + *vague* silicon family ("Core Ultra Processors", "RTX 50 Series") —
NOT the exact chip, which lives on each config page. So Dell's catalog signal
is **new model codes appearing**, which is genuinely "new product before it's
news". Exact-silicon detection needs the deep-crawl step below.

### Deep-crawl (BUILT, still static): per-model spec pages

Confirmed 2026-07-22: Dell `/spd/<code>` spec pages are static HTML with
labelled spec sections containing exact CPU/GPU (gaming models) and exact
memory/storage/display config lists (all models). The Dell engine's opt-in
`deep_crawl` flag fetches each model's spec page in discovery and merges the
exact values over the vague catalog data — turning "new Dell model" into "new
Dell model with an unannounced Core Ultra 9 275HX + RTX 5080". Costs one extra
polite request per model, capped by `deep_crawl_limit`; OFF by default. The
top (largest) RAM/storage config becomes the snapshot's memory/storage; the
full option lists are kept in `raw_data`. Tested in `test_dell.py`
(`test_deep_crawl_enriches_exact_silicon`).

Caveat observed: some non-gaming pages (e.g. a plain XPS 13) still show CPU
only as a family ("Core Ultra Processors") even on the spec page — deep-crawl
gives exact RAM/storage/display there but not always exact CPU. Gaming/
workstation models are the richest silicon signal.

## Adding a Playwright-rendered brand (ASUS, Lenovo, …)

The Fetcher is an injected interface (ADR: engines never do their own I/O), so
a browser fetcher is a drop-in — no engine or core change.

1. **Add `PlaywrightFetcher`** implementing the `Fetcher` protocol in
   `core/fetch.py` (or `core/fetch_playwright.py`):
   ```python
   class PlaywrightFetcher:  # same .get(url) -> FetchedDocument contract
       # lazy-launch chromium once; navigate; wait for the product grid
       # selector; return page.content() as the body. Reuse the same
       # politeness/caching wrappers as HttpFetcher where possible.
   ```
   Gate it so it only launches when a source needs it (`render: playwright`
   in the source YAML). Never launch a browser for a static source.
   Dependency: `pip install playwright && playwright install chromium`
   (~500MB). Keep it an OPTIONAL extra in pyproject, not a base dependency.

2. **Write the engine against CAPTURED rendered HTML.** Run the target page
   through Playwright ONCE on your machine, save `page.content()` as a fixture
   in `tests/fixtures/<vendor>/`, and build the parser against that real DOM —
   same fixtures-first rule as every other engine. Prefer any embedded JSON
   (`__NEXT_DATA__`, `window.__INITIAL_STATE__`, JSON-LD) over CSS selectors.

3. **Descriptor** in `config/oems/<vendor>.yaml` sets `engine: <vendor>`,
   `render: playwright`, region, category paths — identical shape to dell.yaml.

4. **Tests** via `tests/engine_harness.py` on the captured fixture: discovery
   count, golden normalize, validate flags, config-schema reject.

## Template: a new big-brand engine

Copy `engines/dell/__init__.py` and adapt. The four methods and their
contracts (discover→ProductRefs with inline_payload, parse→RawProduct,
normalize→NormalizedProduct, validate→issues) are identical regardless of
brand. Only the extraction internals change. Keep identity on the vendor's
own model/SKU code — it's the one field that survives config churn and
regional variants.

## Remaining brands from the original 30-vendor list

Boutique/Shopify (likely YAML-only, probe to confirm): GMKtec✓, Geekom,
Framework, Chuwi, Teclast. Big-brand (need per-vendor engine, probably
Playwright): Lenovo, ASUS, Acer, MSI, HP, Gigabyte, Samsung, LG, Huawei,
Honor, Xiaomi, Microsoft, Zotac, Colorful, Thunderobot, Mechrevo, Machenike,
Hasee, Clevo, Tongfang. **Do not scaffold these as empty parsers.** Add one
at a time, probe-first, fixture-first. Three real engines (Shopify, Dell, and
the next Playwright one) beat thirty `pass` statements.
