# DATABASE.md

SQLite, WAL mode, one writer (the run process — ADR-1 guarantees single-writer). All product data is append-only; the only mutable columns are observation bookkeeping (`last_seen_at`) and workflow state (outbox status, resolution review flags). Postgres migration stays trivial because access goes through the `SnapshotStore` protocol and the schema avoids SQLite-isms.

## Entity model

The crucial distinction (ADR-3): a **listing** is what a source shows at a URL; a **product** is the canonical thing a listing refers to. Renames, regional variants, and duplicates are *listing-level* phenomena around one product.

```
manufacturers 1─* listings *─1 products 1─* snapshots
                     │                        │
                  sources                 change_events ─→ notifications(outbox)
products *─* aliases      products 1─* prices(observations)
components(known hardware) *─* snapshot_components
crawler_runs 1─* run_errors
```

## Tables

```sql
manufacturers(id, name UNIQUE, country, aliases_json, created_at)

sources(id, manufacturer_id →manufacturers, engine, base_url,
        config_json, min_interval_s, enabled, created_at)

-- canonical product identity
products(id, manufacturer_id →manufacturers, canonical_model, series, category,
         status,             -- active | removed | pre_release | uncertain
         first_seen_at, created_at)

-- what a source shows at a URL; the resolve stage links it to a product
listings(id, source_id →sources, product_id →products NULL,
         url UNIQUE, vendor_handle, vendor_sku,
         resolution_method,  -- url | sku | alias | fuzzy | manual | unresolved
         resolution_confidence REAL,
         needs_review,       -- low-confidence link awaiting confirmation
         first_seen_at, last_seen_at)   -- last_seen_at: the ONE hot mutable column (ADR-4)

-- immutable, deduplicated (ADR-4)
snapshots(id, listing_id →listings, content_hash, normalized_json,
          confidence REAL, validation_issues_json, raw_ref,  -- raw payload on disk, hash-named
          captured_at,
          UNIQUE(listing_id, content_hash))

change_events(id, product_id →products, snapshot_before →snapshots NULL,
              snapshot_after →snapshots,
              change_type, field, old_value_json, new_value_json,
              severity INT,        -- 1..5, from rules at detection time
              detected_at)

-- notification outbox (ADR-1): written in the source transaction, drained separately
notifications(id, change_event_id →change_events, provider, dedup_key UNIQUE,
              payload_json, status,   -- pending | sent | failed | suppressed
              attempts, last_error, sent_at)

aliases(id, product_id →products, alias, kind,   -- rename | regional | marketing | sku
        source_of_truth, created_at, UNIQUE(product_id, alias))

-- price is an observation stream, not a product field: regional/currency history for free
prices(id, listing_id →listings, amount, currency, region, availability, observed_at)

-- known-hardware DB (DIFF_ENGINE.md §4)
components(id, kind,               -- cpu | gpu | npu | display | memory_cfg | family
           canonical_name UNIQUE, vendor, attrs_json,
           first_seen_at, first_seen_product_id →products NULL,
           source)                 -- seeded | discovered
snapshot_components(snapshot_id →snapshots, component_id →components, raw_text,
                    PRIMARY KEY(snapshot_id, component_id))

crawler_runs(id, started_at, finished_at, trigger,   -- manual | scheduled
             sources_json,        -- per-source stats: fetched, cache_hits, products, snapshots, events
             status)
run_errors(id, run_id →crawler_runs, source_id NULL, stage, url, error_class,
           message, traceback_ref, occurred_at)

schema_migrations(version PRIMARY KEY, applied_at)
```

Indexes: `listings(source_id, last_seen_at)`, `snapshots(listing_id, captured_at)`, `change_events(product_id, detected_at)`, `change_events(severity, detected_at)`, `notifications(status)`, `prices(listing_id, observed_at)`, `components(kind, canonical_name)`.

## Semantics worth spelling out

**Immutability + dedup (ADR-4).** A crawl computes the canonical JSON of the normalized product and its hash. Hash unchanged → touch `listings.last_seen_at` only. Hash changed → append a snapshot; the diff engine compares it to the previous one. The live state at time T is reconstructable: latest snapshot with `captured_at ≤ T` for each listing observed at T.

**Removal detection.** A listing whose source completed a *successful, full* discovery pass without it gets no new snapshot; after `removal_grace` (config, default 2 full passes — one miss is often a CDN hiccup) the core emits `product_removed`. Failed or partial runs never trigger removals.

**Raw payloads** are stored on disk under `data/raw/<sha256>.{json,html}` and referenced by `snapshots.raw_ref` — reparseable forever after engine bugfixes (`oem-radar backfill --source X` re-runs parse/normalize over stored raw data and appends corrected snapshots), without bloating the DB.

**Migrations** are numbered statements applied at store startup; `schema_migrations` tracks state. No ORM migrations magic — the schema is an asset you read.

**Schema v2** (DESIGN_REVIEW now-list): snapshots store zlib-compressed JSON in `normalized_zjson` (the read path transparently falls back to `normalized_json` for v1 rows — old history needs no rewrite); listings gained `vendor_sku` and `region`. The normalized model gained `configurations[]` (per-variant memory/storage/price/SKU/region/availability — variants are no longer flattened). The v1→v2 transition changes content hashes, so the first post-upgrade crawl writes one new snapshot per product; the diff engine treats empty-configurations as a migration boundary and emits **zero events** for that wave.
