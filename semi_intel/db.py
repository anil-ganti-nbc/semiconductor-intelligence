"""Engine/session plumbing. SQLite by default (zero-ops for M0), but every
model uses standard column types so swapping SEMI_INTEL_DB_URL to a Postgres
DSN later is a config change, not a rewrite.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from semi_intel.domain.models import Base

from semi_intel.paths import get_db_path

DEFAULT_DB_URL = f"sqlite:///{get_db_path()}"


def get_engine(db_url: Optional[str] = None) -> Engine:
    url = db_url or os.environ.get("SEMI_INTEL_DB_URL", f"sqlite:///{get_db_path()}")
    # `timeout` is how long sqlite3 waits on a locked database before raising
    # OperationalError, not a journal-mode change -- rollback-journal mode
    # stays the default so BackupService's direct file copies remain safe.
    connect_args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def get_sessionmaker(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Convenience context manager for scripts; the CLI manages sessions itself
    since each command is a short-lived process."""
    factory = get_sessionmaker(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
