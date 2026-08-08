"""Read-only web dashboard (optional extra: `pip install -e ".[web]"`).

Every route is a thin adapter over the same repository/service classes the
CLI uses -- no business logic lives here. Deliberately read-only: mutations
(creating claims, accepting suggestions, resolving claims, ...) stay CLI
actions for now. A web form that lets someone accidentally fat-finger a
claim resolution is a worse failure mode than "you have to use the CLI for
that," and the CLI already exists end to end.

`fastapi`/`uvicorn` are imported lazily by the CLI's `web serve` command,
never at module import time for the base package -- the core CLI must keep
working for users who never installed the `web` extra.
"""
