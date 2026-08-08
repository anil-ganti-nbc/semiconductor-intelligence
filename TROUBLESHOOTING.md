# Troubleshooting

Start here whenever something isn't working. Most problems fall into one
of these -- and if not, `semintel doctor` will usually point at the exact
issue.

## First move: run the doctor

```
semintel doctor
```

This checks your data folder, database, schema version, and every
registered source, and prints exactly what's wrong (if anything) in plain
language. Most of the sections below are just "what to do about a
particular doctor result."

## Double-clicking `semintel.exe` just does nothing / opens a browser I didn't expect

That's expected now, not a bug -- double-clicking `semintel.exe` (or
running it with no command at all) sets up your database if needed and
opens the dashboard in your browser, the same as typing `semintel gui`.
It replaced an older, more confusing behavior (see the next section) on
purpose: a console window with a bare usage message wasn't useful to
click on. If the browser tab doesn't appear within a few seconds, check
the console window it opened for an error -- most commonly "already using
port 8000" if another copy is already running (see the `semintel gui`
sections above).

## "Missing command" / "Usage: semi-intel.exe [OPTIONS] COMMAND [ARGS]..."

This is `semi-intel.exe` specifically (the full, detailed CLI) -- unlike
`semintel.exe`, it still needs an explicit command every time, since it's
aimed at someone comfortable typing exact commands rather than clicking.
Try:

```
semi-intel.exe --help
```

to see everything available, or jump straight to a real command, e.g.
`semi-intel.exe entity list`.

If you double-clicked `semi-intel.exe` in File Explorer instead of typing
a command in a terminal, this is also what you'll see -- and the window
will likely close immediately after, since there's nowhere to type a
command. Always run `semi-intel` from Command Prompt, PowerShell, or
Terminal (or use `semintel.exe`/`semintel gui` instead if you just want to
look at your data by clicking).

## "No such command 'X'. Did you mean 'Y'?"

You typed a command name with a typo, or split something into two words
that should be one, e.g. `semintel "Test Entity"` instead of `semi-intel
entity add "Test Entity"`. Run `semintel --help` (or `semi-intel --help`)
to see the exact command names and spelling.

## "No such option: --foo"

You're mixing up which command a flag belongs to. Flags belong to a
specific command, not the tool overall -- e.g. `--type` is valid on
`semi-intel entity add ...` but not on `semi-intel entity list`. Run
`<command> --help` (e.g. `semi-intel entity add --help`) to see exactly
which flags that specific command accepts.

## `semintel status` shows nothing, but I know I added data

Almost always: you're running the command from a different folder than
where you ran `semintel install` (or where your `.db` file actually is).
`semintel` finds your data based on the folder you run it from -- see
OPERATOR_GUIDE.md's "How semintel finds your data."

Fix: `cd` back into the folder you installed in before running commands,
or run `semintel install --data-dir "<the correct folder>"` once to pin
it explicitly so it stops depending on which folder you happen to be in.

## A command seems to hang / never returns

If you ran `semintel add-source` (or any command) with only *some* of the
flags filled in, it may be silently waiting for you to type an answer to
a question it asked (e.g. "Feed or website URL:") -- scroll up, the
prompt is probably there waiting. If you're running it from a script or
another program with no way to type a reply, always pass every flag
explicitly (`--name`, `--url`, `--type`) so it never needs to ask.

If it's `semintel run` or `semintel test-source` that seems stuck, it's
probably a slow or unreachable source -- it will give up on its own after
about 15 seconds per source rather than hanging forever. If it's taking
much longer than that consistently, check your internet connection, or
run `semintel doctor` to test reachability of each source individually.

## `semintel doctor` reports a FAIL on "Database schema is up to date"

Run:

```
semi-intel db upgrade
```

(or `semintel update`, which does the same thing plus prints your
version). This applies any database structure changes that shipped with
your current version but haven't been applied to your actual database
yet. Safe to run any time.

## `semintel doctor` reports a FAIL on "Source reachable"

The source's URL is unreachable right now -- could be your internet
connection, the site being down, or the URL having changed/gone stale.
Run `semintel test-source "<name>"` for more detail on the specific
error. If a source is permanently gone, there's currently no
`semintel remove-source` -- use the full command,
`semi-intel source list` to find its ID, and treat it as retired (stop
relying on its evidence; removal support may come later).

## `semintel backup` says "isn't a local file"

You've pointed `SEMI_INTEL_DB_URL` (or your config) at a real database
server (like Postgres) instead of the default local file. `semintel
backup` only knows how to copy local `.db` files -- use your database
server's own backup tool (e.g. `pg_dump`) instead.

## `semintel gui` says "The web dashboard isn't installed"

You're running from source (not the `.exe`) and haven't installed the
optional web pieces yet. Run this once, from the project folder:

```
pip install -e ".[web]"
```

then try `semintel gui` again. If you're using `semintel.exe`, this
shouldn't happen -- the `.exe` already has everything it needs built in;
if you see this message from the `.exe`, the build may be out of date --
ask whoever built it for a newer one.

## `semintel gui` fails with something about the address already being in use

Something else on your machine is already using port 8000 (maybe another
copy of `semintel gui` you forgot was running). Either close that, or run
this one on a different port:

```
semintel gui --port 8001
```

## The browser opens but the page never loads / "can't connect"

Give it a couple of seconds -- the browser tab can open slightly before
the server inside `semintel gui` has finished starting. If it's still
broken after that, check the Command Prompt/Terminal window you ran
`semintel gui` from for an error message; that's where problems show up,
not in the browser tab itself.

## I clicked something in `semintel gui` and nothing happened / it shows a red error message

The red message under the form is the actual reason -- it's the same
plain-language error you'd get from the equivalent `semi-intel` command
(e.g. "Source 'X' already exists", "Duplicate evidence"). Fix whatever it
says and submit again; nothing is saved until it succeeds. If the page
looks frozen with no message at all, check the browser's address bar --
if `semintel gui` was stopped (Ctrl+C in its terminal window), the page
stays open but can no longer save anything until you start it again.

## The build window flashes and closes instantly, no error visible

You double-clicked `build_exe.bat` in File Explorer instead of running it
from an already-open Command Prompt. Windows opens a brand new window just
for the script and closes that window the moment the script ends --
whether it succeeded or failed -- so you never get to read the output.

Fix: open Command Prompt yourself first (Start → type `cmd` → Enter), `cd`
into the project folder, then run `packaging\build_exe.bat` from inside
that window. Now the window is yours, not the script's, so it stays open
and you can read exactly what happened -- including scrolling up if it's a
wall of text.

## Build fails (`build_exe.bat` / `build_exe.sh`)

Common causes, in order of likelihood:

- **Python not found.** Install Python 3.10+ from python.org (Windows:
  tick "Add Python to PATH" during install), then try again.
- **"does not appear to be a Python project: neither 'setup.py' nor
  'pyproject.toml' found."** Fixed as of this version -- the build
  scripts now always operate from the project root regardless of which
  folder was current when you ran them. If you still see this, you're
  running an older copy of `build_exe.bat`/`build_exe.sh` than the rest of
  the project; get a matching, current copy of the whole folder.
- **No internet access during the build.** The build step downloads
  dependencies the first time -- it needs a working connection.
- **Antivirus/corporate policy blocking the build.** PyInstaller-built
  executables sometimes get flagged by antivirus software as a false
  positive, especially the first time. Check your antivirus's quarantine
  or blocked-items list if the build seems to succeed but the resulting
  `.exe` won't run or immediately disappears.

Paste the exact error text you see into whatever channel you use to reach
your project's maintainer -- the specific error at the bottom of the
output is almost always the useful part.

## I really can't figure it out

Run this and save the output -- it's the single most useful thing to hand
to whoever set this project up for you:

```
semintel doctor > doctor-output.txt
semintel status >> doctor-output.txt
```

Include what command you ran, what you expected, and what actually
happened, along with that file.
# Alerts and digest troubleshooting

## Old imported candidates are not generating alerts

This is intentional. Phase 8 establishes an activation watermark and seeds
transition state for older candidates. Only material changes observed after
activation alert by default. Use
`semi-intel notifications settings --reactivate-now` only when you deliberately
want a new “from now” baseline.

## The digest appears empty

The digest covers the most recently completed 24-hour window at the configured
local digest time. Confirm the timezone and clock with
`semi-intel notifications settings --json`. An empty day produces a concise
“nothing material” digest and is not an error.

## Alerts stopped appearing

Check `semi-intel notifications status`, then review muted event types and topic
IDs with `semi-intel notifications settings --json`. The GUI lists muted
choices under Alert settings and provides an Unmute button.

## External delivery does nothing

Expected in Phase 8. Only the local in-app adapter ships. External channels are
disabled by default and no email, webhook, Slack, or other network adapter is
configured.

## Timezone is rejected on Windows

Use an IANA name such as `Asia/Kolkata`, `Europe/London`, or
`America/Los_Angeles`. The packaged executables include `tzdata`. Source
installations must install the dependencies declared by `pyproject.toml`.
# Phase 9 operational troubleshooting

- **Automation enabled but nothing runs:** the persisted switch is only one
  half of unattended operation. Preview/install the Windows task, then inspect
  `semintel automation status` and `semintel automation jobs`.
- **A run was skipped:** another unexpired database lease owns that job. Do not
  delete it manually. If its process crashed, expiry recovery is automatic and
  recorded as abandoned.
- **Webhook says configured but disabled:** run the synthetic test successfully,
  then explicitly enable delivery. Preview never sends. Check that the scheduled
  process receives the environment variables.
- **Backup verification fails:** retain the failed artifact for diagnosis, do
  not restore it, create a fresh backup, and inspect `semintel health`.
- **Restore is refused:** wait for active jobs to finish. Use the dry run first;
  restore never runs automatically.
- **Health reports revision mismatch:** run `semintel update`, then health again.
