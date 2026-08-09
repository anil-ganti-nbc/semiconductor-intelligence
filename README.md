# Semiconductor Intelligence Platform 3.1 — Legacy import checkpoint

> Status: Staging / additional soak testing required

A claims-and-evidence intelligence platform for semiconductor/hardware
journalism: think "Bloomberg Terminal meets Palantir" scoped to GPUs, CPUs,
foundry nodes, and supply chains. The atomic unit is the **claim**, not the
article. The AI never owns the truth; the database does. Every milestone
below shipped as its own working, tested increment.

Version 3.0 added a persisted Signal Radar in front of the existing editorial
and claims layers: curated RSS/X providers produce raw Signal Items, which
are analyzed, conservatively clustered, independence-discounted, scored and
optionally promoted into canonical editorial stories. Collection, X access
and automatic promotion all default to off. See `HANDOFF.md` for checkpoint
scope and deferred operations work. Version 3.1 adds a preview-first,
transactional importer for the pre-merge Signal Radar database.

## Importing an older Signal Radar database

Open **Signal Radar** in the GUI and choose the old `signal_radar.db` under
**Import an older Signal Radar database**. Preview first, review the counts,
then apply. The importer accepts sources, raw posts, media metadata, provider
history and source suggestions. It is safe to repeat and imported sources
always start with automatic polling off.

The importer deliberately does not trust Radar's old stories, scores,
evidence, extracted entities or labels. Those derived tables are itemized in
the report but skipped. After importing, click **Recluster & rescore now**;
the current 3.1 analyzer evaluates the raw posts and creates conservative
Signal Candidates.

The equivalent CLI workflow is:

```bash
semi-intel radar import --database "C:\path\to\signal_radar.db"
semi-intel radar import --database "C:\path\to\signal_radar.db" --apply
semi-intel radar cluster
```

Stop the old Signal Radar before copying or selecting its database so SQLite
can checkpoint any `-wal` sidecar. Preview never changes either database, and
an apply failure rolls the destination transaction back.

## The milestones

- **M0** — core data model (claims, evidence, sources, knowledge-graph
  entities) plus a CLI for manual entry.
- **M1** — source plugins that fetch real evidence automatically: an
  RSS/Atom feed reader and a pci.ids vendor/device database reader.
- **M2** — a rules-based claim engine that *suggests* which open claims a
  piece of evidence might be relevant to. Proposals, not facts.
- **M3** — a rules-based contradiction engine, scoped to memory
  configuration validity (the brief's own 384-bit/16GB/16Gbit example).
- **M4** — source trust scoring: per-source and per-company accuracy,
  computed from resolved claims and the existing knowledge graph.
- **M5** — a graph query layer (BFS traversal + relation-type search) over
  the same relational `entities`/`relationships` tables — no graph database.
- **M6** — story scoring: which open claims deserve investigation right
  now, from novelty, corroboration, and momentum.
- **M7** — a web dashboard (optional extra) wrapping the same
  repositories and services the CLI uses -- browse and edit claims,
  evidence, sources, and suggestions from a page in the browser, not just
  read-only tables.

Nothing here is a black box. Every scoring/matching/checking module is a
short, documented, deterministic function you can read end to end — see
"Design notes" below for the full list.

## Editorial Discovery 2.1

The dashboard is now an automated daily inbox rather than a view over only
hand-authored claims. Every RSS ingestion pass also:

- matches evidence against an editor-managed set of monitored topics;
- conservatively groups similar coverage into editorial stories;
- gives each story an explainable interest score;
- keeps seen stories out of the default inbox;
- flags new coverage on an already-seen story without making it unseen;
- extracts editorial links and turns unknown domains into suggested sources.

Open the GUI and use **Editorial Inbox**, **Monitored Topics**, and
**Suggested Sources**. The seeded topic list covers current AMD, NVIDIA,
Intel, memory, packaging, foundry, and semiconductor-policy beats. All seed
topics are ordinary editable database rows: add, edit, disable, or delete
them in the browser.

The inbox defaults to unseen stories and supports topic, score, and sort
filters plus single/bulk seen actions. Opening a story shows its coverage
timeline, source links, matched terms, and citations.

For an existing database, upgrade and backfill once:

```bash
semi-intel db upgrade
semi-intel editorial backfill
```

The scheduled pipeline runs the same idempotent editorial discovery pass
automatically after ingestion.

### Editorial scoring

The interest score is deterministic and capped at 1.0:

- topic relevance and editorial priority: up to 0.45;
- recency, decaying over seven days: up to 0.25;
- additional coverage: up to 0.15;
- best contributing source trust: up to 0.10;
- detected editorial citations: up to 0.05.

The API and GUI retain human-readable reasons alongside the number. Matching
normalizes case, Unicode punctuation, whitespace, and letter/number joining
(`RDNA5`, `RDNA-5`, and `RDNA 5`) while enforcing word boundaries.

### Suggested sources

Links found in ingested article text are canonicalized and filtered against
an extensible noise-domain list. Unknown editorial domains are ranked by
reference count, distinct relevant stories, monitored-topic breadth, and
feed availability. From the GUI they can be ignored, blocked, restored,
checked for an RSS/Atom feed, and added with a conservative default trust
weight.

Feed discovery checks HTML alternate-feed declarations and common feed
paths, validates parsed entries, uses an eight-second timeout and a named
user agent, and only runs when requested by the editor.

Current limitation: the platform discovers domains present in content it
already ingests. It does not yet run a web-wide news search for publishers
linking to a known source, and feed summaries that omit outbound links cannot
yield citation leads without a future full-article fetcher.

## Bounded targeted discovery 2.2

Version 2.2 adds the practical middle ground: a small discovery ring around
stories the platform has already scored as interesting. It does **not** crawl
the internet, download result articles, follow result links, or recursively
search discovered sites.

The first provider is an isolated Google News RSS adapter. For each eligible
story the system creates no more than three deterministic searches using a
distinctive headline phrase, the primary monitored topic and originating
publication, and an explicit attribution phrase such as “according to
VideoCardz.” Provider metadata is normalized into separate discovery-result
records; it never becomes immutable evidence automatically.

Conservative defaults:

- minimum interest score: 0.55;
- maximum story age: 48 hours;
- cooldown: 6 hours;
- maximum cycles per story: 3;
- maximum queries per cycle: 3;
- 10 results per query and 30 per cycle;
- 5 cycles and 15 provider requests per rolling hour;
- cache lifetime: 6 hours;
- request timeout: 8 seconds;
- automatic discovery: off until enabled in the GUI.

The **Discovery Activity** page controls the ordinary settings and displays
persisted request/cycle budgets and recent runs. Story detail explains
eligibility, shows generated queries and accepted coverage, and provides a
manual “Search nearby coverage” action.

CLI equivalents:

```bash
semi-intel discovery status
semi-intel discovery run --story-id 123
semi-intel discovery run
semi-intel discovery backfill
```

The pipeline runs eligible searches only when automatic discovery has been
enabled. Provider timeouts and malformed responses are recorded as failed or
partial runs without aborting RSS ingestion.

Relevance is explainable and conservative: normalized headline similarity
(up to 0.40), specific monitored-topic overlap (up to 0.25), explicit
attribution to the registered origin (0.25), and publication-window proximity
(0.08). A result must score at least 0.45 and match a specific monitored topic.
Blocked domains, generic/listing URLs, out-of-window items, and already-stored
evidence are rejected with a reason.

Google News RSS is intentionally isolated behind `DiscoveryProvider`; its
metadata and availability are external dependencies and may change. The app
remains fully useful with discovery disabled.

## Not a developer? Start here instead

This README is the full developer-oriented reference. If you're the
project's owner rather than its maintainer -- you don't write Python and
just want to run the thing -- use the operator-friendly `semintel` CLI and
its docs instead:

- **INSTALL.md** — one-time setup, no Python knowledge assumed.
- **QUICKSTART.md** — add a source, fetch evidence, check status, in five steps.
- **OPERATOR_GUIDE.md** — the full reference for `semintel`'s ten commands.
- **TROUBLESHOOTING.md** — plain-language fixes for the errors you'll actually hit.

`semintel` (nine typing commands -- install, run, status, doctor, update,
add-source, test-source, reindex, backup -- plus one clicking command,
`gui`, which opens the same data in a browser) and `semi-intel` (everything
below) read and write the exact same database -- use whichever fits what
you're doing, freely mixed.

## Install

```bash
cd semi_intel_platform
pip install -e ".[dev]"          # CLI + tests (also pulls in web deps for testing)
# or, for just the CLI without web dashboard testing:
pip install -e .
# or, to run the web dashboard in production use:
pip install -e ".[web]"
```

## Quickstart — manual entry (M0)

```bash
semi-intel init-db

semi-intel source add "Golden Pig" --type social --trust-weight 0.7
semi-intel entity add "Nova Lake" --type product
semi-intel entity add "Intel" --type company
semi-intel entity relate 1 2 --type manufactured_by

semi-intel evidence add 1 --title "leak post" --content "Nova Lake uses 18A-P" --entity-id 1

semi-intel claim create "Nova Lake uses Intel 18A-P" --subject-entity-id 1
semi-intel claim link-evidence 1 1 --stance supports

semi-intel claim show 1
semi-intel claim timeline 1
semi-intel entity show 1

# Months later, once the launch confirms or kills the rumor:
semi-intel claim resolve 1 --status confirmed --note "confirmed at launch"
```

## Quickstart — automated ingestion (M1)

```bash
semi-intel ingest rss "VideoCardz" "https://videocardz.com/rss" --trust-weight 0.6
semi-intel ingest pci-ids
semi-intel evidence list
```

## Quickstart — claim engine suggestions (M2)

```bash
semi-intel evidence entities 1     # which known entities does this evidence mention?
semi-intel suggest run             # scan evidence against every OPEN claim
semi-intel suggest list
semi-intel suggest show 1
semi-intel suggest accept 1 --stance supports
semi-intel suggest reject 2 --note "coincidental keyword overlap"
```

## Quickstart — contradiction checks (M3)

```bash
semi-intel check memory-config --bus-width 384 --chip-density-gbit 16 --total-gb 16
# -> [CONTRADICTION] ... supports 24 GB (standard) or 48 GB (clamshell) -- not 16 GB.

semi-intel claim create-memory-spec "Leaked slides: 384-bit, 16GB, 16Gbit chips" \
  --bus-width 384 --chip-density-gbit 16 --total-gb 16
semi-intel claim memory-spec 1
```

## Quickstart — source trust scoring (M4)

```bash
semi-intel source stats 1     # accuracy + per-company breakdown for one source
semi-intel source rank        # every source ranked by accuracy
```

Needs `claim resolve` to have been used on claims that source's evidence was
linked to. With no resolved claims yet, it reports "no track record yet"
rather than a misleading number.

## Quickstart — knowledge graph queries (M5)

```bash
semi-intel graph related 1 --depth 2                                  # everything related to entity #1
semi-intel graph find --relation-type uses_memory --target "LPDDR5X"  # every product linked to LPDDR5X
```

## Quickstart — story scoring (M6)

```bash
semi-intel story rank --limit 10
```

## Quickstart — web dashboard (M7)

```bash
pip install -e ".[web]"
semi-intel web serve --port 8000
# or: semintel gui   (same server, opens your browser automatically)
# open http://127.0.0.1:8000
```

Tabs for Emerging Stories (M6), Claims (click a row for evidence + full
timeline, plus forms to link evidence and resolve the claim), Evidence,
Source Rankings (M4), Entities, Suggestions (accept/reject pending
claim-link suggestions), and Add (create a source, entity, piece of
evidence, or claim). Every write goes through the exact same repository
calls as the CLI -- see `semi_intel/web/app.py`'s module docstring for the
one-to-one mapping. Still CLI-only for now: memory-spec claims and
database migrations.

## Quickstart — standalone executable (no Python required)

Don't want to install Python or manage a venv? Build a single-file
`semi-intel` executable (`semi-intel.exe` on Windows) that bundles the
interpreter, every dependency, the Alembic migrations, and the web
dashboard's static assets:

```bash
# Windows (run FROM Windows -- see note below)
packaging\build_exe.bat

# Linux / Mac
bash packaging/build_exe.sh
```

Either script creates a throwaway build venv, installs the project plus
PyInstaller, and runs `pyinstaller packaging/semi_intel.spec`. Output lands
at `dist/semi-intel` (or `dist\semi-intel.exe`) — copy that one file
anywhere and run it without installing Python:

```bash
dist/semi-intel --help
dist/semi-intel init-db
dist/semi-intel db upgrade
dist/semi-intel pipeline run
dist/semi-intel web serve --port 8000
dist/semintel gui        # same dashboard, friendlier front door
```

`semintel` (unlike `semi-intel`) also treats zero arguments as a real,
meaningful invocation rather than falling back to a bare usage screen: its
Typer app callback sets `no_args_is_help=False` and checks
`ctx.invoked_subcommand is None`, running `install()` then `gui()` in that
case (see `semi_intel/operator.py`'s `_main()`). That's what makes
double-clicking `semintel.exe` in File Explorer -- which invokes it with
no arguments -- open straight to the dashboard instead of a console window
that flashes a usage message and closes.

**PyInstaller does not cross-compile.** Run `build_exe.bat` on a Windows
machine to get `semi-intel.exe`; there is no way to produce a Windows
executable from Linux or Mac (or vice versa) with PyInstaller alone. Build
on whichever OS you intend to run the result on.

Everything the frozen build needs (`alembic.ini`, `migrations/`,
`semi_intel/web/static/*.html`) is declared as a data file in
`packaging/semi_intel.spec`, and `semi_intel/cli.py`/`semi_intel/web/app.py`
resolve those paths relative to `sys._MEIPASS` when frozen (see
`_project_root()` and `_static_dir()`) instead of assuming a source checkout
is on disk next to the binary. `db upgrade`/`db downgrade`/`db current`/
`db stamp` call Alembic's Python API directly rather than shelling out to a
separate `alembic` executable, which is what makes the frozen build fully
self-contained — no second binary to bundle, no PATH dependency.

## Quickstart — schema migrations (Alembic)

`init-db` (used above) is fine for a throwaway/dev database: it just calls
`create_all()`, which only ever adds missing tables. It cannot alter an
existing table, so it's the wrong tool once a real database has data in it.
For that, use Alembic. Two equivalent ways to drive it:

```bash
# via the alembic executable (needs `alembic` installed -- pip install -e ".[dev]" already does)
alembic upgrade head        # create/update schema to the latest revision
alembic downgrade base      # drop everything Alembic created (dev/test only)
alembic revision --autogenerate -m "describe the schema change"

# via the semi-intel CLI itself -- no separate `alembic` install needed,
# and the only option available from the standalone executable (see
# "Quickstart -- standalone executable" below), since that bundle doesn't
# include a second `alembic` binary
semi-intel db upgrade
semi-intel db downgrade
semi-intel db current
```

Both paths read the same `SEMI_INTEL_DB_URL` env var as the app (see
`migrations/env.py`), so pointing at Postgres is just setting that variable
first. Don't mix `init-db` and Alembic against the same database — pick one.
If a database was already created with `init-db` and you want to switch it
over to Alembic-managed migrations, run `alembic stamp head` (or `semi-intel
db stamp head`) once to tell Alembic the schema is already current, without
re-running the migration. See `migrations/README` for details.

## Quickstart — scheduled pipeline

`pipeline run` is one idempotent pass: poll every registered RSS source
(anything added with `source add ... --type rss --url ...`), poll pci.ids,
then run the suggestion scanner — the same three things you'd otherwise run
by hand after M1/M2. Point any external scheduler at this one command:

```bash
# cron (every 15 minutes)
*/15 * * * * cd /path/to/semi_intel_platform && /path/to/venv/bin/semi-intel pipeline run >> pipeline.log 2>&1
```

```ini
# systemd timer -- semi-intel-pipeline.service
[Service]
Type=oneshot
WorkingDirectory=/path/to/semi_intel_platform
ExecStart=/path/to/venv/bin/semi-intel pipeline run

# semi-intel-pipeline.timer
[Timer]
OnCalendar=*:0/15
Persistent=true
```

No cron or systemd available (e.g. local testing, Windows without Task
Scheduler)? Use the stdlib-only fallback loop instead:

```bash
semi-intel pipeline loop --interval-minutes 15
```

`pipeline run`/`pipeline loop` are deliberately not a custom scheduler
daemon — cron and systemd timers already solve "run this periodically"
well; reimplementing that badly would be exactly the kind of unnecessary
framework the rest of this project has avoided.

By default everything writes to `./semi_intel.db` (SQLite). Point it
elsewhere, including a future Postgres instance, with `SEMI_INTEL_DB_URL`:

```bash
export SEMI_INTEL_DB_URL="postgresql://user:pass@host/dbname"
```

## Tests

```bash
pytest
```

175 tests cover every module above, including the operator CLI: the confidence formula, repository
behavior, source-plugin parsing against fixtures (no live network), the
claim engine (entity matching, keyword scoring, suggestion idempotency), the
contradiction engine (including the brief's own worked example), source
accuracy scoring (including the per-company graph walk), graph traversal
and relation search, story scoring, the web API reads and writes -- creating
sources/entities/evidence/claims, linking evidence, resolving claims,
running/accepting/rejecting suggestions, and every validation error path --
via FastAPI's TestClient, full CLI workflows for every command group, the
Alembic migration (schema parity with `create_all()`, plus a clean
upgrade/downgrade round trip), and the pipeline service/CLI (multi-source
ingestion + suggestion run in one pass). Also manually smoke-tested: a live
`semi-intel web serve` / `semintel gui` process hit with real HTTP requests
and clicked through in a browser.

## Architecture at a glance

```
semi_intel/
  domain/            Entity, Relationship, Source, Evidence, Claim,
                      ClaimEvidenceLink, ClaimEvent, ClaimLinkSuggestion,
                      MemorySpecClaim -- the whole schema, one file.
  repository/         Thin CRUD + a few domain methods per entity type.
                      ClaimRepository owns confidence recompute + event
                      logging; SuggestionRepository.accept() is the only
                      path from a suggestion to a real link.
  services/            confidence.py -- the claim confidence formula.
  ingestion/           SourcePlugin contract + IngestionService (M1).
                      plugins/rss_plugin.py, plugins/pci_ids_plugin.py.
  claim_engine/        entity_matcher.py, scoring.py, suggestion_service.py (M2).
  contradiction_engine/  memory_rules.py, service.py (M3).
  source_intelligence/  scoring.py, service.py (M4).
  graph/               queries.py -- BFS + relation search (M5).
  story_scoring/       scoring.py, service.py (M6).
  web/                 FastAPI app (reads + writes) + static dashboard,
                      optional extra (M7). schemas.py holds the request
                      validation models for the write endpoints.
  pipeline/            PipelineService.run_once() -- the one entry point an
                      external scheduler (cron/systemd) or `pipeline loop`
                      calls: poll every registered RSS source + pci.ids,
                      then run the suggestion scanner.
  cli.py               Typer CLI (`semi-intel`); every command is a thin
                      adapter over the repository/service layer above --
                      and so is the web API.
  operator.py           Typer CLI (`semintel`) -- the operator-friendly
                      front door: install/run/status/doctor/update/
                      add-source/test-source/reindex/backup. Thin wrapper
                      over the same repository/service layer, never new
                      business logic. See OPERATOR_GUIDE.md.
migrations/            Alembic migrations, wired to SEMI_INTEL_DB_URL --
                      see "Quickstart -- schema migrations" above.
alembic.ini
packaging/             PyInstaller entry points (run_cli.py,
                      run_operator_cli.py), specs (semi_intel.spec,
                      semintel.spec), and one-command build scripts
                      (build_exe.bat / build_exe.sh) that build both
                      executables -- see "Quickstart -- standalone
                      executable" above.
```

The CLI and the web dashboard are two adapters over the *same* repository
and service layer. Neither contains business logic of its own.

## Design notes (the philosophy, in one place)

- **Confidence is a documented formula, not a black box** (`services/confidence.py`):
  source-trust weighting, a corroboration bonus for distinct sources, a
  harsher penalty for contradictions than for weak evidence.
- **Claim-link suggestions are equally documented** (`claim_engine/scoring.py`):
  entity-mention match (weight 0.6) + keyword overlap (weight 0.4), favoring
  precision over recall on purpose.
- **Contradiction checks are equally documented** (`contradiction_engine/memory_rules.py`):
  checks both standard and clamshell GDDR population before flagging
  anything, and always explains what totals *would* be valid.
- **Source accuracy is raw counts, not a smoothed score** (`source_intelligence/scoring.py`):
  a source with 1/1 correct shows 100%, on purpose, with the sample size
  always shown alongside it.
- **Story scores are a triage signal, not an editorial verdict** (`story_scoring/scoring.py`):
  novelty + corroboration + momentum, each capped and weighted; soft
  dimensions like "SEO potential" are deliberately left to a human.
- **Nothing here owns the truth.** Claim-link suggestions start `pending`
  and need `suggest accept`. Contradiction checks are recorded as events and
  never change a claim's status or confidence by themselves. Source and
  story scores are read-only views computed on demand, never stored
  opinions. Every one of these surfaces a finding; a human decides what it
  means.
- **Evidence is immutable and deduplicated by content hash**, shared between
  manual entry and every ingestion plugin (`ingestion/hashing.py`). A source
  editing or deleting a post produces new evidence, never a mutated row.
- **Source plugins never touch the database** (`ingestion/service.py` owns
  find-or-create-source and dedup) — "add a source = write one class"
  actually holds.
- **Structured, checkable sub-claims get their own side table**
  (`MemorySpecClaim`) rather than growing `Claim` a domain at a time.
- **The knowledge graph is relational, not a dedicated graph database.**
  `entities` + `relationships`, queried with BFS and filtered joins
  (`graph/queries.py`) — a graph engine is a future migration if
  traversal-heavy queries ever become the actual bottleneck, not a
  day-one dependency.
- **The web dashboard is additive, not a second source of truth.** It
  imports fastapi/uvicorn lazily (inside `web serve` / `semintel gui`), so
  the base CLI never requires them. Every route -- reads and writes alike
  -- delegates to the exact repository/service classes the CLI uses, so a
  claim created from the browser follows the same rules as one created
  with `semi-intel claim create`.
- **Every claim has an append-only event log** (`claim timeline`) — the
  Timeline Engine from the original brief, present since M0.

## What's still deliberately not here

- Social/forum sources (Weibo, Chiphell, JD.com) — sequenced after
  structured sources on purpose; they need anti-bot handling, auth, and
  Chinese-language entity normalization structured sources don't.
- Free-text entity/claim *extraction* — the claim engine matches against
  entities and claims that already exist; discovering a brand-new product
  name from raw text is real NLP/NER, out of scope here.
- Contradiction rules beyond memory configuration (die size/node
  compatibility, power limits, launch timeline conflicts) — each would be
  its own rule module, proven out the same way memory_rules.py was.
- Lead-time tracking for source accuracy (how far ahead of launch a leak
  landed) — needs a structured launch-date claim type this schema doesn't
  have yet, the same pattern as `MemorySpecClaim`.
- Temporal graph queries ("what did the graph look like as of a date") —
  would need edge-level validity windows.
- Auth, alerting/notifications on the web dashboard, and GUI support for
  memory-spec claims and database migrations — the dashboard now covers
  the day-to-day create/edit/review workflow (see M7 above), but a few
  CLI-only corners remain deliberately unmigrated until there's a reason
  to prioritize them.
- A distributed/HA job scheduler — `pipeline run` is a single-process,
  single-machine command by design; cron/systemd already provide the
  periodicity, and a multi-worker queue (Celery, etc.) is a future
  migration if ingestion volume ever actually requires concurrent workers.
# Semiconductor Intelligence Platform 3.2

Phase 8 adds an autonomous alerting layer and daily intelligence digest on top
of the fused editorial platform and Signal Radar. Alerts are based on persisted
state transitions—not a repeated scan of whatever happens to score highly—so
scheduled runs are idempotent and newly upgraded databases do not flood the
operator with historical material.

Use the **Alerts & Digest** GUI tab for the everyday workflow, or
`semi-intel notifications --help` for the full command line interface.
External delivery is not configured in this checkpoint; all shipped behavior
is local and in-app.
# Phase 9 operational automation

Version 3.3 can run unattended through a bounded Windows Task Scheduler cycle
without keeping the GUI open. Automation remains off after upgrade. Enable the
persisted scheduler with `semintel automation enable`, preview the exact Windows
task with `semintel automation install-task`, and use `--apply` only when ready.
Every pipeline, digest, backup and maintenance run is recorded, and database
leases prevent two processes from running the same job at once.

The dashboard’s **Automation & Health** area explains current health, upcoming
runs, recent jobs and verified backups in plain language. **Alerts & Digest**
adds deterministic Quiet/Balanced/Breaking-news presets, useful/not-useful
feedback, saved views, and a safe external-delivery workflow.

External delivery is exactly one generic HTTPS webhook configured only through
`SEMI_INTEL_WEBHOOK_URL`, optional `SEMI_INTEL_WEBHOOK_TOKEN`, and optional
`SEMI_INTEL_WEBHOOK_TIMEOUT`. The URL/token are never stored in the database.
Preview is network-free; the synthetic test requires an explicit action; actual
delivery remains disabled by default.

Verified backups use SQLite’s backup API and live under the configured backup
directory with a manifest and SHA-256. Restore is CLI-only, begins with a dry
run, refuses active jobs, validates the input, and creates a safety backup first.
