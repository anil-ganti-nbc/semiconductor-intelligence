# Operator Guide

The full reference for running this project day to day. If you just want
to get moving, read QUICKSTART.md instead -- come back here when you want
to understand what's actually happening, or need something QUICKSTART.md
didn't cover.

## Bringing in an older Signal Radar database

Stop the old Signal Radar first so SQLite can fold any pending `-wal` data
into `signal_radar.db`. In the dashboard, open **Signal Radar**, find
**Import an older Signal Radar database**, choose the file and click
**Preview import**. Nothing is changed during preview.

Review the ready, duplicate, invalid and deliberately skipped counts. Click
**Import reviewed items** only when they look right, then click **Recluster &
rescore now**. Imported source polling remains off until you enable it.

For the detailed CLI, the same flow is:

```
semi-intel radar import --database "C:\path\to\signal_radar.db"
semi-intel radar import --database "C:\path\to\signal_radar.db" --apply
semi-intel radar cluster
```

The apply command is safe to repeat. It does not import browser sessions,
cookies, local media paths, or the old Radar's derived editorial stories and
scores.

## Reviewing Radar reports, claims, and editorial stories

### Managing and collecting Radar sources

The Radar source table is the operational source workspace. Use **Edit** to
correct a source name, feed URL or X handle, priority, trust, enabled state, or
automatic-polling state. A source marked disabled cannot be collected manually
until you deliberately enable it. Health labels distinguish an untested source
from a healthy source, a timeout, authentication/rate-limit problem, HTTP error,
invalid feed, or other sanitized failure.

Use the row checkboxes with **Collect selected**, or choose **Collect all**.
Batches run one source at a time and show progress; RSS sources run before X
accounts. If X accounts are included, the dashboard lists their count and asks
for confirmation. X collection still requires the platform's separate X opt-in
and a valid imported session. Cancel stops before the next source. If X reports
authentication expiry, a challenge, or a rate limit, the remaining X accounts
are skipped to avoid needless requests. Completed RSS work is retained.

Source suggestions are reviewed in the dedicated **Suggested Sources** tab, not
inside Radar. Accepting a suggestion continues to create a source with automatic
polling off for deliberate review.

Open **Signal Radar** and select any candidate card or its **View reports**
action. The detail panel shows every report that formed the candidate, why it
was attached, its source/topic/independence context, and the candidate's score
explanation. Use **Create claim from this report** to write a falsifiable claim
and attach that report as evidence, or **Use as evidence** to save it for later.
Repeated conversion of the same report reuses the existing Evidence record.

Open **Claims & Evidence** to review both sides together. You can create a claim
or evidence manually, attach existing evidence, change a link between supports,
weakens/context, and contradicts, or remove a mistaken link without deleting
the evidence. Evidence created from Radar includes a route back to its candidate.
Adding evidence changes confidence according to existing rules but never
confirms or debunks a claim automatically.

Editorial Inbox also shows **Radar candidates awaiting editorial review**.
These are ranked suggestions, not promoted stories, and include candidates
below automatic thresholds. **Promote to Editorial Inbox** is an explicit human
decision with an editable headline and warning summary. It does not enable
automatic promotion.

### Keeping older Radar coverage out of the way

Signal Radar and the Editorial Inbox fallback shortlist open on **Current**
coverage from the last seven days. Use the adjacent controls to choose:

- **Current** — meaningful independent reporting inside the selected window.
- **Older** — no meaningful activity inside that window.
- **All ages** — the complete historical candidate view.

The window can be 3, 7, 14, or 30 days. This is a view filter only: older
candidates are not deleted, dismissed, rescored, or removed from claims and
evidence research.

The activity clock uses a report's publication/observation time. Collection
time is used only when publication time is unavailable, and the candidate card
says when that fallback occurred. Later dependent copies in the same citation
or syndication group do not make an old candidate current. A genuinely new
independent group can resurface it; the Resurfaced badge appears only when the
stored group history proves there was a gap longer than the selected window.

## Canonical entities and claim matches

Radar deliberately stores extracted names as mention proposals rather than
silently turning every capitalized phrase, version number, or retailer into a
knowledge-graph entity. Open **Entities** to review the highest-frequency
unresolved phrases. Resolve an exact group to a new or existing canonical
entity, optionally retain its spelling as an alias, or explicitly ignore or
reject it. Entity creation and resolution are always human actions.

When writing a claim in **Claims & Evidence** or from a Radar report, choose an
optional subject from the searchable canonical-entity selector. Candidate-
relevant resolved entities appear first in Radar, but none is preselected.

After canonical evidence and open claims exist, open **Claim Matches**. The
readiness cards explain any missing prerequisite. **Scan for new claim
matches** deterministically compares evidence with open claims and proposes
only currently unlinked pairs. Inspect the claim, evidence excerpt, source,
score reasons, and Radar provenance before accepting a stance or rejecting the
proposal. Acceptance uses the same confidence and claim-timeline path as a
manual evidence link; the scanner never chooses a stance or resolves a claim.

## The two commands

- **`semintel`** -- nine typing commands plus one optional clicking
  command (`gui`), this guide. Plain-English output, never a raw error
  message, safe to run repeatedly.
- **`semi-intel`** -- the full command set (creating claims by hand,
  linking evidence, the web dashboard, database migrations, and more).
  `semintel` is a friendlier front door to the same data -- both read and
  write the exact same database, so you can freely mix the two. Full
  reference: README.md.

You'll live in `semintel` for routine work. `semintel gui` covers most of
what you'd otherwise drop into `semi-intel` for -- creating and resolving
claims, linking evidence, reviewing suggestions -- from a page in your
browser instead of typing. Anything the GUI doesn't cover yet (mainly
memory-spec claims and database migrations) still needs `semi-intel`.

## The nine core commands

### `semintel install`

First-time setup. Creates (or re-checks) your database in the current
folder, and writes `semintel.config.json` so future commands remember
where to look. Safe to run again -- it never deletes existing data. Use
`--data-dir "<path>"` to put your data somewhere specific instead of the
current folder.

### `semintel run`

Fetches new evidence from every source you've registered, then checks
whether any of it might be relevant to claims you've already created.
This is the command to automate (see "Automating `run`" below). Add
`--skip-pci-ids` to skip the built-in pci.ids check (a public database of
chip IDs, useful for catching unreleased hardware) if you don't need it.

### `semintel status`

A snapshot: how many sources, entities, pieces of evidence, and claims (by
status) you have, whether your database schema is current, and when
evidence was last collected. Good first command to run each time you sit
down.

### `semintel doctor`

Checks that everything is actually working: your data folder exists and
is writable, the database is reachable, the schema is current, and (by
default) that every registered source with a URL is actually reachable.
Prints `[PASS]`, `[WARN]`, or `[FAIL]` per check. Run this whenever
something seems off, or add `--skip-network` to skip the reachability
checks if you're offline or in a hurry.

### `semintel update`

Applies any pending database changes. This does **not** download a newer
version of the program -- there's no update server. "Updating the
program" means replacing this executable with a newer build (see
INSTALL.md's "Updating later"); `semintel update` only updates your
*database schema* to match whatever version you're currently running.

### `semintel add-source`

Registers somewhere to pull evidence from. Run it with no flags for a
guided setup (it'll ask you for a name and a URL), or pass everything at
once:

```
semintel add-source --name "VideoCardz" --url "https://videocardz.com/rss" --trust 0.6
```

`--trust` is how much you trust this source, from 0 to 1 -- it feeds into
how much weight evidence from this source carries later. 0.6 is a
reasonable default for an established outlet; lower it for a source
that's often wrong, raise it for one that's usually right.

`--type` defaults to `rss` if you give a URL, or `manual` if you don't.
Other types (`forum`, `social`, `retail`, `regulatory`, `kernel`,
`registry`, `patent`, `other`) exist for bookkeeping but don't have an
automatic fetcher yet -- you'd add evidence from those by hand with
`semi-intel evidence add`.

### `semintel test-source`

Checks that a source can actually be fetched and read, **without saving
anything**. Use this right after adding a source, or any time
`semintel run` reports a failure for one:

```
semintel test-source "VideoCardz"
semintel test-source --url "https://example.com/rss"   (test before registering)
```

### `semintel reindex`

Re-checks everything you've already collected against your currently open
claims. Useful after you create a new claim -- old evidence that might be
relevant to it won't get checked again automatically until you run this
(or `semintel run`, which does the same check as its last step). Never
changes a claim on its own; new matches show up as pending suggestions you
review with `semi-intel suggest list`.

### `semintel backup`

Makes a timestamped copy of your database into a `backups` folder next to
it. Only works for the default local database file -- if you've pointed
this at a real database server instead, use that server's own backup
tool. Run this before anything risky (a big `semi-intel claim resolve`
session, an update, experimenting with `semi-intel db downgrade`, etc.).

## The optional tenth command: `semintel gui`

Everything above is typing. `semintel gui` opens the same data in your
web browser instead -- tables you can click through, and forms for the
things you'd otherwise reach for `semi-intel` to do: creating a source,
entity, piece of evidence, or claim; linking evidence to a claim; resolving
a claim; and reviewing pending suggestions (accept or reject, with a
dropdown for the stance). It's not a separate copy of your data -- it's
the same database file, read and written through the exact same rules as
every `semintel`/`semi-intel` command, so you can freely switch between
clicking and typing.

```
semintel gui
```

opens `http://127.0.0.1:8000` in your default browser automatically and
keeps running until you press Ctrl+C in that window. Add `--no-browser` if
you'd rather open the address yourself (useful if you're running this on
a machine you're accessing remotely), or `--port` if 8000 is already in
use for something else.

**You don't actually have to type this.** Just double-click `semintel.exe`
in File Explorer (or run `semintel` with no command at all from a
terminal) -- it sets your database up first if this is the first time
(same as `install`, and just as safe to do again), then does exactly what
`semintel gui` does. This is the intended everyday way to open the
program if you don't want to think about the command line at all.

If you built this from source rather than the `.exe`, `gui` needs one
extra install step the first time: `pip install -e ".[web]"` (the `.exe`
already includes it). `semintel gui` will tell you if this step is
missing rather than crashing.

What it can't do (still `semi-intel`-only, for now): memory-spec claims
(the ones with the built-in GDDR consistency check) and database
migrations.

## Automating `run`

Running `semintel run` by hand works, but the point of automated sources
is not having to remember. Two options:

**Windows Task Scheduler:**

1. Open Task Scheduler → Create Basic Task.
2. Name it "semintel run", trigger: Daily, recur every 15 minutes (use
   "Repeat task every" in the trigger's advanced settings).
3. Action: Start a Program. Program: the full path to `semintel.exe`.
   Arguments: `run`. **Start in:** the folder containing your data (this
   is the field that makes it find the right database -- don't skip it).
4. Finish, then right-click the task → Run, once, to confirm it works.

**cron (Mac/Linux):**

```
*/15 * * * * cd /path/to/your/data && /path/to/semintel run >> semintel.log 2>&1
```

`cd` into the data folder first, same reason as "Start in" above.

Either way, check in periodically with `semintel status` or `semintel
doctor` to make sure it's actually still working -- automation that fails
silently is worse than no automation.

## How `semintel` finds your data

On `install`, it writes `semintel.config.json` in the folder you ran it
from (or `--data-dir`, if you gave one), recording where your database
lives. Every other command reads that same file to find it again -- which
only works if you run the command from that same folder (or the folder
`--data-dir` pointed at).

If you ever want to check where a command thinks your data is, `semintel
status` and `semintel doctor` both print the exact location at the top of
their output.

You can also override this entirely with an environment variable,
`SEMI_INTEL_DB_URL`, which always wins over the config file -- useful if
you want to point at a shared database on a server instead of a local
file. See README.md for the details on that.

## Backups and rolling back

`semintel backup` copies the current database file with a timestamp. To
restore one: stop anything that might be writing to the database, then
copy the backup file over your live `semi_intel.db` (rename it back to
`semi_intel.db` first). There's currently no `semintel restore` command --
it's a plain file copy, which is why backups are plain files.

## What `semintel` will never do

- Create or resolve a claim for you. Only a human decides something is
  worth tracking, or that a rumor turned out true or false.
- Auto-link evidence to a claim. Matches are always suggestions --
  `semi-intel suggest accept`/`reject` is the only way one becomes real.
- Delete anything. There's no `semintel` command that removes data.
- Reach out to the internet except to fetch the specific sources you
  registered (plus, optionally, pci.ids).
# Alerts & Digest (3.2)

Open the GUI and choose **Alerts & Digest**. The page starts in the unread
view and explains why each alert fired. You can mark alerts read or unread,
dismiss and restore them, jump to the related Signal Radar candidate, inspect
provider incidents, and generate the current daily digest.

The first activation is intentionally quiet about old candidates: the platform
records their current state as a baseline, then alerts only when fresh activity
crosses a threshold or changes state. Re-running alert generation is safe and
does not duplicate the same transition.

Settings are deliberately conservative. In-app alerts are on, while the daily
digest, webhook delivery, and Windows desktop notifications are off. On
Windows, use **Send test notification** first, then select **Show Windows
desktop notifications** and save. Only important and urgent eligible alerts
are sent to the desktop; mutes, quiet hours, the hourly cap, and bounded retries
apply. Desktop delivery is local and independent of webhook delivery. Windows
Focus Assist, Do Not Disturb, or system notification permissions can suppress
the visible banner even after Windows accepts it. Choose your timezone and
digest time before enabling the digest. These delivery controls do not hide
local in-app alerts.

Useful advanced commands:

```
semi-intel notifications status
semi-intel notifications generate
semi-intel notifications list
semi-intel notifications test
semi-intel notifications digest
semi-intel notifications settings --timezone Asia/Kolkata --enable-digest
```

`notifications settings --reactivate-now` establishes a new “from now”
watermark and clears transition baselines. Existing alerts remain as audit
history.

**Generate now** refreshes today's digest from the current notification state,
so a digest generated earlier in the day is not permanently frozen. An empty
digest explains whether no eligible notifications exist rather than pretending
delivery occurred. External delivery remains off until explicitly enabled and
configured. The dashboard shows only required environment-variable names and
safe delivery status—never webhook URLs, tokens, or secret values. Repeated
delivery does not resend an already delivered digest.

# Phase 9 daily operations

Start with **Automation & Health** or `semintel health`. A non-healthy item
includes both an explanation and a recommended action. Job history records the
trigger, times, result counts, safe error and retry state. A skipped run usually
means another process owns the same unexpired lease; an abandoned record means
an expired lease was recovered and audited.

Choose Quiet for fewer interruptions, Balanced for the recommended newsroom
default, or Breaking news while actively monitoring. Preview before applying.
Presets preserve mutes and external-delivery state. Feedback is advisory only:
rating an alert never changes thresholds or trains a model.

Use `semintel backups create`, `list`, and `verify`. `prune` previews by default
and targets only managed files inside the backup directory. `restore` validates
first, refuses active leases, previews by default, and creates a verified safety
backup before an explicitly confirmed replacement.

`semintel backups rehearse <path>` goes a step further than `verify`: it copies
the backup to a throwaway temp file, opens it with a real database connection,
and reads it through the application itself -- proving the backup would
actually work if restored, not just that the file isn't corrupt. It also warns
if the backup was made on an older version of the program and would need
`semintel update` right after a restore. It never touches your real database.

Diagnostics created with `semintel diagnostics create` include safe operational
metadata and redacted summaries—not the database, article bodies, cookies,
sessions, tokens, webhook URLs, authorization headers or environment secrets.

The Automation & Health page distinguishes the in-app automation setting from
the real Windows scheduled task. Check the displayed task state, executable
path, last/next task times, last result, and heartbeat together. If the task is
missing or points at an older executable, review the shown command before using
the explicit install/repair action. No task is installed merely by opening the
page. If an interrupted process left a job marked running, **Reconcile stale
runs** changes only expired, unleased records to Abandoned and preserves their
audit history; it will not take over an active lease.
