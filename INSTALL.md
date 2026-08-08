# Installing the Semiconductor Intelligence Platform

This guide is for the project owner, not a developer. It assumes you don't
write Python and just want a working program. If you're comfortable with
Python and pip, you can skip straight to the very bottom ("Alternative:
installing with pip") -- everything above that is written for someone who
isn't.

There are two commands you'll end up with:

- **`semintel`** -- the one you'll use day to day. Nine simple commands:
  install, run, status, doctor, update, add-source, test-source, reindex,
  backup. This is the one covered by this guide and by QUICKSTART.md,
  OPERATOR_GUIDE.md, and TROUBLESHOOTING.md.
- **`semi-intel`** -- the full, detailed command set underneath it (adding
individual claims, linking evidence by hand, running the web dashboard,
and so on). You don't need it to get started -- see README.md when
you're ready to go deeper.

## Upgrading from 2.2 to the 3.1.0 Signal Radar merge checkpoint

**This is a checkpoint build, not the finished operations roadmap** -- see
`HANDOFF.md` for exactly what's included and what's explicitly deferred.
The preview-first Signal Radar database importer is included; unified
external notification delivery and media/OCR workers remain deferred. Local
in-app notifications and daily digests are included. Both Windows
executables are included and smoke-tested.

Back up your database first (`semintel backup`, or copy `semi_intel.db`
by hand), then:

```
semi-intel db upgrade
```

The migration preserves every existing source, evidence row, claim, and
graph edge untouched; every new column lands on a safe, collection-stays-off
default. Nothing starts collecting, analyzing, or auto-promoting on its own
after this upgrade -- open the new **Signal Radar** tab in the dashboard and
opt in explicitly:

- **Collection enabled** and **X collection enabled** are separate toggles,
  both off by default. X collection additionally requires the optional `x`
  extra (`pip install -e ".[x]"` then `playwright install chromium`) and an
  imported browser session -- RSS works with no extra install at all.
- **Automatic promotion enabled** is off by default; manual promotion
  (the "Promote to editorial story" button, or `semi-intel radar promote
  <id>`) always works regardless of this toggle.
- Add a source under Signal Radar > Sources by pasting a feed URL or an
  X handle -- the provider (RSS vs. X) is auto-detected. New sources default
  to *not* polling automatically; tick "Enable automatic collection" or
  leave it off and use "Collect now" / `semi-intel radar collect` manually.
- To carry forward an older Signal Radar installation, stop the old app,
  open **Signal Radar > Import an older Signal Radar database**, choose its
  `signal_radar.db`, preview the counts, and then import. Afterward click
  **Recluster & rescore now**. Old Radar stories/scores are intentionally
  skipped; the current safer pipeline reassesses the imported raw posts.

## Upgrading an existing installation to Editorial Discovery 2.1

Back up your database first using `semintel backup`. Install or replace the
program files, then run:

```
semi-intel db upgrade
semi-intel editorial backfill
```

The migration preserves existing sources, evidence, claims, and graph data.
The backfill is safe to repeat: it scans older evidence for monitored topics,
story clusters, citations, and source suggestions without duplicating rows.

Afterward, launch the GUI as usual. The default page is the unseen Editorial
Inbox. Use Monitored Topics to customize coverage and Suggested Sources to
review websites discovered in article citations.

## Upgrading from 2.1 to bounded discovery 2.2

Back up, replace the program files, and run:

```
semi-intel db upgrade
semi-intel discovery status
```

Open **Discovery Activity** in the GUI. Targeted discovery is enabled for
manual story searches but automatic scheduling starts off, so upgrading
cannot unexpectedly generate network traffic. Review the limits, then enable
“Run automatically after ingestion” if desired.

No API key is required by the initial Google News RSS provider. The feature
uses only bounded search feeds and does not fetch result articles.

## Step 1: Get the project files

You should have a folder (or a `.zip` file you've unzipped) called
something like `semi_intel_platform`. Put it somewhere permanent -- not
your Desktop temp downloads, not a USB drive you might lose. A good
choice: `C:\Users\<you>\semi_intel_platform` on Windows, or
`~/semi_intel_platform` on Mac/Linux.

Everything below assumes you're inside that folder.

## Step 2: Build the program

This project is delivered as source files, not a ready-made program, so
there's a one-time build step. You need Python installed for this step
only -- once it's built, running the program day to day does NOT need
Python at all.

**If you don't have Python:** download and install it from
[python.org](https://www.python.org/downloads/) (any version 3.10 or
newer). On Windows, tick "Add Python to PATH" during install.

**Then build:**

Windows (Command Prompt or PowerShell, from inside the project folder):

```
packaging\build_exe.bat
```

Mac or Linux (Terminal, from inside the project folder):

```
bash packaging/build_exe.sh
```

This takes a minute or two. It downloads what it needs automatically and
prints progress the whole time. When it's done, you'll have two new
programs in a `dist` folder inside the project:

```
dist\semintel.exe      (Windows)   or   dist/semintel      (Mac/Linux)
dist\semi-intel.exe                or   dist/semi-intel
```

If this step fails, see TROUBLESHOOTING.md's "Build fails" section --
don't skip ahead until it succeeds.

## Step 3: Put the program somewhere convenient (optional)

You can run `semintel` straight out of the `dist` folder. If you'd rather
have it somewhere tidier, copy `semintel.exe` (and `semi-intel.exe`, if you
want it too) to its own folder, e.g. `C:\Program Files\semintel\` or
`~/semintel/`. Wherever you put it, **always run it from that same folder**
-- see "Why does the folder matter?" below.

## Step 4: First-time setup

Open a terminal in the folder where `semintel.exe` (or `semintel`) lives,
and run:

```
semintel install
```

This creates your database and confirms everything works. You'll see
something like:

```
Setting up your database...
Done.
  Data folder:  C:\Users\you\semintel
  Database:     C:\Users\you\semintel\semi_intel.db
```

That's it -- installation is complete. Continue with **QUICKSTART.md** to
add your first source and start collecting evidence.

From now on, you don't need a terminal at all if you don't want one:
double-clicking `semintel.exe` in File Explorer opens the dashboard
directly in your browser (same as running `semintel gui`). The terminal
commands throughout the rest of these docs are there for automation and
for the handful of things the dashboard can't do yet -- not because
they're required for everyday use.

## Why does the folder matter?

`semintel` keeps a small file called `semintel.config.json` (and your
database, `semi_intel.db`) in whatever folder you run it from. If you run
`semintel status` from a *different* folder than where you ran `install`,
it won't find your data -- it'll look like you have nothing, because as
far as that folder is concerned, you do. This isn't a bug, it's just how
it remembers where things are.

**The fix is simple: always open your terminal in the same folder before
running any `semintel` command.** If you want to be extra safe, run
`semintel install --data-dir "C:\some\permanent\folder"` once to pin the
data location explicitly -- then it'll find that folder from anywhere.

## Updating later

When you get a newer copy of the project (a new `semi_intel_platform`
folder or zip from whoever maintains this for you):

1. Build it the same way (Step 2 above), in the new folder.
2. Point `semintel install --data-dir "<path to your OLD data folder>"` at
   your existing data folder so you don't lose anything, OR copy your old
   `semi_intel.db` and `semintel.config.json` into the new build's folder.
3. Run `semintel update` to apply any database changes that shipped with
   the new version.

See OPERATOR_GUIDE.md's "Updating" section for the full explanation of
what `semintel update` does and doesn't do.

## Alternative: installing with pip

If you're comfortable with Python, you don't need the `.exe` at all:

```bash
cd semi_intel_platform
pip install -e ".[dev]"
semintel install
```

This installs `semintel` and `semi-intel` as regular commands on your
PATH, backed by your normal Python install instead of a bundled one. See
README.md for the full developer-oriented documentation.
# Phase 9 Windows scheduling and secrets

After `semintel install` or `semintel update`, inspect `semintel automation
status`. Enabling the database setting does not create an OS task. Preview the
absolute executable and working-directory command with `semintel automation
install-task`; add `--apply` and confirm only when satisfied. Removal follows
the same dry-run/confirmation model.

For the optional generic webhook, provide `SEMI_INTEL_WEBHOOK_URL` and,
optionally, `SEMI_INTEL_WEBHOOK_TOKEN` and `SEMI_INTEL_WEBHOOK_TIMEOUT` in the
environment used by the scheduled task. The endpoint must be HTTPS except for
loopback development. Test from Alerts & Digest or
`semi-intel notifications delivery-test --yes`; enabling is a separate action.
