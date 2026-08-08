"""The plugin contract every source adapter implements.

Deliberately small: a plugin's only job is to produce a stream of RawItem.
It does not touch the database, does not know about Source/Evidence rows,
and does not decide what's a duplicate -- that's IngestionService's job.
This keeps plugins trivially unit-testable (pure fetch-and-parse) and keeps
dedup/persistence logic in exactly one place instead of duplicated per source.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from semi_intel.domain.enums import SourceType


@dataclass(frozen=True)
class RawItem:
    """One observation from a source, before it becomes an Evidence row."""

    title: str
    content: str
    external_id: Optional[str] = None
    url: Optional[str] = None
    observed_at: Optional[dt.datetime] = None
    raw: dict[str, Any] = field(default_factory=dict)


class SourcePlugin(ABC):
    """Base class for every source adapter.

    `name` must be stable across runs -- it's used to find-or-create the
    matching Source row, so renaming a plugin's `name` effectively creates a
    brand-new source with no history.
    """

    name: str
    source_type: SourceType = SourceType.OTHER
    default_trust_weight: float = 0.5

    @abstractmethod
    def fetch(self) -> Iterable[RawItem]:
        """Return the current set of observations from this source. Plugins
        are free to fetch everything on every run -- IngestionService dedups
        by content hash, so re-fetching unchanged items is a cheap no-op."""
        raise NotImplementedError
