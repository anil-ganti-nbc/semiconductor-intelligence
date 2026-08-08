"""Alembic environment.

Wired to read the database URL from SEMI_INTEL_DB_URL -- the exact same
env var semi_intel/db.py uses -- so `alembic upgrade head` and
`semi-intel init-db` always point at the same database when you mean them
to. Target metadata is semi_intel.domain.models.Base, so
`alembic revision --autogenerate` picks up schema changes automatically.

Do NOT run both `semi-intel init-db` (Base.metadata.create_all) and
`alembic upgrade head` against the same fresh database -- pick one. init-db
is for quick local trials with no upgrade history; Alembic is for anything
you intend to evolve over time (see migrations/README.md in the repo root
for the full explanation).
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make sure `semi_intel` is importable regardless of where alembic is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from semi_intel.db import DEFAULT_DB_URL  # noqa: E402
from semi_intel.domain.models import Base  # noqa: E402

config = context.config

# `semi-intel db upgrade/downgrade/current/stamp` (semi_intel/cli.py) drive
# Alembic in-process via alembic.command, rather than shelling out to a
# separate `alembic` executable -- that's what lets a frozen/standalone
# build work without also bundling a second CLI. Reconfiguring Python's
# global logging via fileConfig() on every one of those calls is not just
# unnecessary noise, it's actively broken across repeated in-process calls
# within one interpreter session (e.g. Typer's CliRunner in tests, or any
# future code that calls more than one `db` command back to back): each
# fileConfig() call binds a fresh StreamHandler to whatever sys.stderr is
# *at that moment*, and if that stream later gets closed (as test runners
# routinely do), the next log call raises "I/O operation on closed file"
# instead of just... logging. `_alembic_config()` in cli.py sets
# `configure_logging=False` for exactly this reason; plain `alembic upgrade
# head` from a real terminal is unaffected and still gets its usual INFO
# logging, since config.attributes is empty in that path and this defaults
# to True.
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    return os.environ.get("SEMI_INTEL_DB_URL", DEFAULT_DB_URL)


def run_migrations_offline() -> None:
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
