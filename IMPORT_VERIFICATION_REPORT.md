# Import verification report

## Outcome

Semiconductor Intelligence Platform 3.3.7 was initialized from a fresh
database, its 71 monitored topics were seeded through the normal dashboard
startup path, and the supplied legacy Signal Radar database was imported. No
application source code was changed and the version remains 3.3.7.

The original legacy database remained read-only throughout this work and
continued to pass `PRAGMA integrity_check`.

## Import results

| Category | Previewed | Imported | Duplicate on repeat | Invalid |
|---|---:|---:|---:|---:|
| Sources | 80 | 80 | 80 | 0 |
| Posts | 5,211 | 5,211 | 5,211 | 0 |
| Media | 2,056 | 2,056 | 2,056 | 0 |
| Provider runs | 2,020 | 2,020 | 2,020 | 0 |
| Source suggestions | 106 | 106 | 106 | 0 |
| **Total** | **9,473** | **9,473** | **9,473** | **0** |

The repeated apply imported zero new rows, demonstrating transactional
idempotency.

Unsupported legacy-derived material was intentionally skipped: 1,409 stories,
3,610 story-entity links, 14,940 story scores, 2,490 evidence records, 3,763
entities, 8,567 post-entity links, 5,867 post labels, 6,092 relationships,
3,168 review rows, 3,130 notifications, six score weights, and 58 reliability
records. Current rules rebuilt applicable state from raw posts instead.

## Reconstruction results

The canonical Radar cluster action analyzed all 5,211 imported posts:

- 329 posts attached to existing candidates during the pass
- 350 new candidates created
- 4,532 posts suppressed by current relevance/clustering rules
- 350 candidates rescored

Final derived-state counts:

- 71 monitored topics
- 695 signal-topic matches
- 11,150 signal entity mentions
- 5,867 signal labels
- 350 active Signal Radar candidates
- 335 candidates with a primary monitored topic
- 679 candidate-to-post links
- 415 independence groups
- 0 automatically promoted candidates
- 0 generated notifications

Candidate detail API verification showed plain-language score components,
topic-match reasons, source attribution, publication gaps, independence groups,
and timelines. For example, the leading Strix Halo candidate explained its
topic relevance, four independent groups, source diversity, artifact strength,
and source quality.

## Safety and settings

- 33 RSS sources and 47 X sources were imported.
- Polling remained disabled for all 80 sources.
- General collection and X access remained disabled.
- Automatic promotion remained disabled.
- Scheduling, digests, scheduled backup, and maintenance remained disabled.
- External webhook delivery and Windows desktop notifications remained disabled.
- Media download and OCR remained disabled.
- Historical import generated no notification flood.

## Application verification

- Database integrity: `ok`
- Alembic revision: `a0b5d7e9f314`
- Expected application version: 3.3.7
- Frozen dashboard root: HTTP 200
- Dashboard/API counts before restart: 80 sources, 350 candidates, 71 topics,
  and 70 pending source suggestions
- Counts after a complete server stop and restart: unchanged
- Source, candidate, topic, source-suggestion, notification, health, scheduler,
  and settings endpoints returned successfully
- Backup creation succeeded and produced a verified artifact
- Backup rehearsal passed against schema head `a0b5d7e9f314`, loading 80 sources,
  350 candidates, zero notifications, and 2,020 provider runs from the rehearsal copy

Focused legacy-import, interface, and dashboard JavaScript tests: **17 passed**.
The first test invocation used a bare machine Python and stopped during test
collection because optional project dependencies were absent; the same focused
set was then run in the recovered project environment and passed. No full-suite
rerun or executable rebuild was warranted because no source defect or source
change was made. The supplied executables are the already verified 3.3.7 builds.

## Packaging and privacy

The package configuration points to `semi_intel.db` using portable relative
paths. The temporary backup-verification record and its machine-local paths
were removed from the packaged database after the rehearsal; this does not
remove any imported operator data. Runtime logs, test caches, and temporary
backup files are excluded from the archive.

This archive includes private historical source and post data. It is **not a
sanitized archive** and should not be shared publicly.

## Remaining limitations

- Unsupported legacy stories, scores, review state, and notifications are not
  preserved verbatim; current state is reconstructed from supported raw data.
- Imported sources remain off until individually or globally enabled.
- X collection was not exercised and requires its separate explicit opt-in and
  working provider prerequisites.
- No live RSS collection, X access, webhook, desktop alert, scheduler task,
  automatic promotion, media download, OCR, LLM, or internet-wide discovery was run.
- Current deterministic extraction can produce broad candidate entity mentions,
  and topic-based clustering can occasionally associate a tangential report;
  operators should review candidate timelines before promotion.
- Existing `datetime.utcnow()` deprecation warnings remain outside this task.

Final SHA-256 hashes are recorded in `SHA256SUMS.txt` beside this report.
