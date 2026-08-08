# OEM Coverage Matrix

Last audit: **2026-08-02**. Status codes:

`LIVE_VALIDATED` · `LIVE_PARTIAL` · `CANARY` · `NEEDS_OWNER_PROBE` · `BLOCKED_JS` · `BLOCKED_BOT` · `BROKEN` · `DISABLED_LOW_VALUE`

## Enabled sources

| OEM | Source ID | Region | URL | Platform | Engine | Status | ~Catalog | Discovery | Signals | Missing | Fixture | Notes |
|-----|-----------|--------|-----|----------|--------|--------|----------|-----------|---------|---------|---------|-------|
| Dell | dell-us-laptops | US | dell.com | static HTML | dell | LIVE_VALIDATED | model list | catalog HTML | model, CPU, price | GPU sparse | existing | Big-brand engine |
| MINISFORUM | minisforum-shopify | US | store.minisforum.com | Shopify | shopify | LIVE_VALIDATED | — | products_json+sitemap | full Shopify set | — | existing | |
| GMKtec | gmktec-shopify | US | gmktec.com | Shopify | shopify | LIVE_VALIDATED | — | products_json+sitemap | full | — | gmktec_products.json | |
| Beelink | beelink-shopify | US | bee-link.com | Shopify | shopify | LIVE_VALIDATED | — | products_json+sitemap | full | — | existing | |
| AOOSTAR | aoostar-shopify | US | aoostar.com | Shopify | shopify | LIVE_VALIDATED | — | products_json+sitemap | full | — | existing | |
| Chuwi | chuwi-shopify | US | us.chuwi.com | Shopify | shopify | LIVE_VALIDATED | — | products_json+sitemap | full | — | existing | us.chuwi.com only |
| **Bosgame** | bosgame-shopify | US | bosgame.com | Shopify | shopify | **LIVE_VALIDATED** | ~37 | products_json+sitemap | title,CPU,RAM,SSD,price,images | region sparse | bosgame_products_p1.json | Enabled 2026-08-02 |
| **NiPoGi** | nipogi-shopify | US | nipogi.com | Shopify | shopify | **LIVE_VALIDATED** | ~15 | products_json+sitemap | full mini-PC | small catalog | nipogi_products_p1.json | Enabled 2026-08-02 |
| **ACEMAGIC** | acemagic-shopify | US | acemagic.com | Shopify | shopify | **LIVE_VALIDATED** | ~46 | products_json+sitemap | AI/mini-PC | accessories present | acemagic_products_p1.json | **New OEM** 2026-08-02 |
| **KAMRUI** | kamrui-shopify | US | kamrui.com | Shopify | shopify | **LIVE_VALIDATED** | ~60 | products_json+sitemap | mini-PC | accessories possible | kamrui_products_p1.json | Enabled Stage 4.1 2026-08-02 |

## Disabled / deferred (audited)

| OEM | Source ID | URL tried | Status | Evidence | Next action |
|-----|-----------|-----------|--------|----------|-------------|
| GEEKOM | geekom-shopify | geekompc.com, geekom.com | BROKEN | HTML 404 / lander redirect — not Shopify | Owner probe alternate storefront |
| Trigkey | trigkey-shopify | trigkey.com | BROKEN | `{"errors":"Unavailable Shop"}` | Re-probe later; shop may have moved |
| GPD | gpd-shopify | gpd.hk | NEEDS_OWNER_PROBE | Connection timeout | Owner: `oem-radar probe https://gpd.hk` |
| Morefine | morefine-shopify | store.morefine.com | BROKEN | DNS failure | Find current storefront URL |
| Peladn | peladn-shopify | peladn.com | BROKEN | 404 → JS redirect `/` | Find Shopify subdomain if any |
| Firebat | firebat-shopify | firebat.com | BROKEN | products.json 404 | Re-probe |
| Kingnovy | kingnovy-shopify | kingnovy.com | BLOCKED_BOT | Lander HTML redirect | Avoid; low value if cloaked |
| AYANEO | ayaneo-shopify | ayaneo.com | NEEDS_OWNER_PROBE | Not Shopify (custom HTML) | Probe for Woo/API; high editorial value |

## Fixture-ready, not yet enabled

_(none — KAMRUI promoted in Stage 4.1)_

## Health expectations

Enabled Shopify collectors must not report `ok` with **zero** products when a prior successful run had products, or when zero is unexpected.

Config (`collector_health` in `radar.yaml`):

- `unexpected_zero_is_failure: true`
- `minimum_fraction_of_previous_catalog: 0.35` → below this = **failed** (not mass removals)
- `warn_fraction_of_previous_catalog: 0.70` → **degraded**

## Recommended next batch

1. **KAMRUI** (fixture ready, Shopify live)
2. AYANEO — only after stable non-Shopify discovery surface is identified
3. GPD — owner probe for real storefront
4. High-value non-Shopify: Framework / System76 / Tuxedo — needs **one** reusable engine (JSON-LD or Woo Store API); **not** in this stage without that engine

## Browser automation

**Not justified yet.** Shopify products.json still unlocks the majority of boutique mini-PC OEMs. GEEKOM/AYANEO blockers are platform/URL issues, not “need Playwright for everything.”


## Runtime health path (Stage 4.1)

```
config/radar.yaml → CollectorHealthConfig (Pydantic)
  → RadarConfig.collector_health
  → run_all(radar_cfg, ...)
  → run_source(..., health_cfg=radar_cfg.collector_health)
  → SourceRunStats.health / health_reason
  → store.run_finished(status='failed' if health=='failed' else 'ok')
```

**Last-good baseline** = most recent `crawler_runs` row with `status='ok'`.
Failed health runs are stored as `status='failed'` and **do not** replace the baseline.
Degraded runs remain `status='ok'` (catalog still processed) but set `health=degraded`.

Reason codes: `HEALTHY_CATALOG`, `CATALOG_WARN_THRESHOLD`, `CATALOG_FAILURE_THRESHOLD`,
`UNEXPECTED_ZERO`, `NO_PREVIOUS_BASELINE`, `RECOVERED`.

Failed collapses return before product processing → **no mass removal events**.
