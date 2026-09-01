"""Fail-closed persistent-state compatibility checks.

Normal application work must never use SQLAlchemy's ``create_all`` (or an
exception-string stamp fallback) as proof that the database is compatible.
The only schema authority for Semiconductor Intelligence is the checked-in
Alembic migration head.  This module performs a read-only comparison before a
supported runtime entry point opens a work session.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine


class SchemaCompatibilityError(RuntimeError):
    """Raised when persistent state cannot be proven compatible with code."""


@dataclass(frozen=True)
class SchemaStatus:
    """Read-only schema compatibility observation."""

    current_heads: tuple[str, ...]
    expected_head: str | None
    ready: bool
    reason: str | None = None

    @property
    def current_head(self) -> str | None:
        return self.current_heads[0] if len(self.current_heads) == 1 else None


def _project_root() -> Path:
    """Resolve the Alembic project root for source and frozen runtimes."""

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    override = os.environ.get("SEMINTEL_PROJECT_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent


def _alembic_config():
    from alembic.config import Config

    ini_path = _project_root() / "alembic.ini"
    if not ini_path.exists():
        raise SchemaCompatibilityError(f"authoritative Alembic config is missing: {ini_path}")
    cfg = Config(str(ini_path))
    cfg.attributes["configure_logging"] = False
    return cfg


def authoritative_head() -> str:
    """Return the single checked-in Alembic head, without touching the DB."""

    from alembic.script import ScriptDirectory

    try:
        head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    except SchemaCompatibilityError:
        raise
    except Exception as exc:  # noqa: BLE001 - turn config errors into gate failures
        raise SchemaCompatibilityError(f"authoritative Alembic head is unavailable: {exc}") from exc
    if not head:
        raise SchemaCompatibilityError("authoritative Alembic migration history has no head")
    return head


def _sqlite_path(engine: Engine) -> Path | None:
    if engine.url.get_backend_name() != "sqlite":
        return None
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    path = Path(database)
    return path if path.is_absolute() else Path.cwd() / path


def inspect_schema(engine: Engine) -> SchemaStatus:
    """Inspect current Alembic heads without creating or mutating state."""

    try:
        expected = authoritative_head()
    except SchemaCompatibilityError as exc:
        return SchemaStatus((), None, False, str(exc))

    db_path = _sqlite_path(engine)
    if db_path is not None and not db_path.exists():
        return SchemaStatus(
            (), expected, False,
            f"persistent database is missing at {db_path}; run the explicit Alembic upgrade first",
        )

    try:
        from alembic.runtime.migration import MigrationContext

        with engine.connect() as connection:
            current = tuple(MigrationContext.configure(connection).get_current_heads())
    except Exception as exc:  # noqa: BLE001 - all unknown state fails closed
        return SchemaStatus(
            (), expected, False,
            f"persistent schema compatibility could not be inspected: {exc}",
        )

    if current != (expected,):
        observed = ", ".join(current) if current else "none"
        return SchemaStatus(
            current, expected, False,
            f"persistent schema is incompatible: observed Alembic head {observed}; expected {expected}",
        )
    return SchemaStatus(current, expected, True)


def require_schema_head(engine: Engine) -> str:
    """Require an exact Alembic head before normal work can begin."""

    status = inspect_schema(engine)
    if not status.ready:
        raise SchemaCompatibilityError(status.reason or "persistent schema compatibility check failed")
    return status.expected_head  # type: ignore[return-value]
