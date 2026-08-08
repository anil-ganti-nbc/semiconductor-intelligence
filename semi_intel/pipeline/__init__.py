"""The scheduled pipeline: one idempotent command that polls every
registered RSS source, polls pci.ids, and runs the claim-suggestion scanner
-- meant to be called by cron, a systemd timer, Windows Task Scheduler, or
`semi-intel pipeline loop` for people without access to any of those.

Deliberately not a custom scheduler daemon: cron (or your platform's
equivalent) already solves "run this periodically" well, and reimplementing
that badly would be exactly the kind of unnecessary framework the rest of
this project has avoided. `pipeline run` is the one thing an external
scheduler needs to call; `pipeline loop` is a zero-dependency stdlib
fallback for environments without one.
"""
