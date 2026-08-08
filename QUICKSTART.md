# Quickstart

Already installed (see INSTALL.md)? Here's the fastest path to something
useful. Every command below is `semintel`, run from the same folder you
installed in.

**Rather click than type?** Just double-click `semintel.exe` -- it sets
everything up and opens the dashboard in your browser. Steps 1, 6, and 7
below can all be done there instead. Keep reading if you'd rather
follow along on the command line, or want the automation in step 6.

## 1. Add a source

A "source" is somewhere you want to automatically pull evidence from --
usually an RSS feed.

If you already used the older Signal Radar, open the dashboard's **Signal
Radar** tab instead. Its import card lets you choose `signal_radar.db`,
preview exactly what will be carried over, apply the import, and then analyze
the raw posts with **Recluster & rescore now**.

```
semintel add-source
```

It'll ask you a couple of questions:

```
Name for this source (e.g. 'VideoCardz'): VideoCardz
Feed or website URL (press Enter to skip if this is a manual/offline source): https://videocardz.com/rss
```

(You can also skip the questions by passing everything as flags:
`semintel add-source --name "VideoCardz" --url "https://videocardz.com/rss"`.)

## 2. Check it actually works

Before waiting on a schedule, confirm the source is reachable:

```
semintel test-source "VideoCardz"
```

You should see something like:

```
Fetching https://videocardz.com/rss ...
Success -- found 12 item(s). Nothing was saved.
  - Some headline
  - Another headline
  ...and 7 more
```

If that fails, it'll tell you why in plain language -- fix it before
moving on (see TROUBLESHOOTING.md if you're stuck).

## 3. Pull in evidence

```
semintel run
```

This fetches everything new from every source you've registered. Run it
again anytime -- it never re-adds something it's already seen.

## 4. See what you've got

```
semintel status
```

```
Database
  Location:        sqlite:///.../semi_intel.db
  Schema:          up to date

Contents
  Sources:         1
  Entities:        0
  Evidence:        12
  Claims:          0 open, 0 confirmed, 0 debunked, 0 retracted
  Pending matches: 0
  Last evidence:   2026-07-18 14:02:11
```

## 5. Prefer clicking to typing?

```
semintel gui
```

(or just double-click `semintel.exe` -- same thing) opens the same data
in your browser: tables for everything above, plus forms for creating
sources/entities/evidence/claims, linking evidence to a claim, resolving
a claim, and reviewing suggestions. Steps 6 and 7 below ("create claims",
"review suggestions") can both be done there instead of typing
`semi-intel` commands, if you'd rather click. See OPERATOR_GUIDE.md for
what it can and can't do yet.

## 6. Keep it running automatically (optional)

`semintel run` on its own only fetches once. To have it check
periodically without you remembering:

- **Windows:** use Task Scheduler to run `semintel.exe run` every 15-30
  minutes. (Task Scheduler → Create Basic Task → point the action at your
  `semintel.exe`, with `run` as the argument, and set it to start "in" your
  data folder so it finds the right database.)
- **Mac/Linux:** add a cron entry, e.g. `*/15 * * * * cd
  /path/to/your/data && /path/to/semintel run >> semintel.log 2>&1`.

See OPERATOR_GUIDE.md's "Automating `run`" section for step-by-step
screenshots-in-words for Task Scheduler.

## 7. What claims and evidence actually are

`semintel run` only ever collects raw **evidence** (a post, a feed entry,
a database line). It does not decide anything is true, and it does not
create **claims** on its own -- that's still up to you, because deciding
something is a rumor worth tracking is a judgment call, not something a
rule can safely automate. Once you have some evidence, use the fuller
`semi-intel` command to create claims and link evidence to them:

```
semi-intel claim create "Nova Lake uses Intel 18A-P" --subject-entity-id 1
semi-intel claim link-evidence <claim-id> <evidence-id> --stance supports
```

See README.md for the full walkthrough of claims, entities, and evidence
using `semi-intel`. `semintel reindex` will also periodically re-scan your
evidence against whatever claims you've created and flag possible matches
for you to review (`semi-intel suggest list`, or the Suggestions tab in
`semintel gui`) -- it never links anything automatically.
# Alerts and daily digest

Open the GUI and choose **Alerts & Digest**. In-app alerts are enabled after
upgrade, but the activation watermark prevents old imported candidates from
flooding the unread view. External delivery and the daily digest remain off
until you enable them.

To inspect the defaults or generate alerts manually:

```powershell
.\semi-intel.exe notifications status
.\semi-intel.exe notifications generate
.\semi-intel.exe notifications list
.\semi-intel.exe notifications digest
```

Choose your timezone before enabling the scheduled digest:

```powershell
.\semi-intel.exe notifications settings --timezone Asia/Kolkata --enable-digest
```
# Phase 9 quick start

```text
semintel health
semintel automation status
semintel automation enable
semintel automation install-task
semintel backups create
semi-intel notifications preset-preview balanced
semi-intel notifications preset-apply balanced
semi-intel notifications delivery-preview
```

`install-task` is a dry run unless `--apply` is supplied. Scheduler, collection,
X, automatic promotion and external delivery keep their existing disabled
defaults. Set webhook values in the process environment; never paste a token
into the GUI, database, diagnostics, or a support archive.
