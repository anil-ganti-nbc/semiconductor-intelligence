"""CollectionService: the one place a Provider's raw output becomes
persisted SignalItem rows.

Mirrors semi_intel/ingestion/service.py's philosophy exactly: every provider
routes through the same find-or-create-nothing (SignalItems are never
find-or-create by name -- identity is (provider, external_id)), the same
dedup rule, and the same fault isolation, so adding a new provider can never
accidentally add a new dedup or error-handling strategy too.

Fault isolation (brief section 16/17): a single dead/misbehaving/
unauthenticated source must not take down the rest of a collection cycle.
Every failure -- provider unavailable, network error, malformed payload --
is caught per-source and recorded as a failed ProviderRun; the caller
(PipelineService, Phase 2 integration) continues to the next source.

Cursor safety: `source.cursor` is only advanced after the batch of
SignalItems from this cycle has been added to the session and the
transaction commits -- a crash between provider.collect() and commit leaves
the cursor untouched, so the next run safely re-collects the same items
(caught by the (provider, external_id) unique constraint, not lost or
duplicated).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semi_intel.domain.enums import ProviderRunStatus
from semi_intel.domain.models import ProviderRun, SignalCollectionSettings, Source, SignalItem, SignalMedia
from semi_intel.ingestion.hashing import hash_content
from semi_intel.signals.providers import Cursor, Provider, ProviderUnavailable
from semi_intel.signals.providers.replay import ReplayProvider
from semi_intel.signals.providers.rss import RSSProvider


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


# Higher priority = polled more often. Values are deliberately coarse (this
# is a "don't hammer a low-priority feed every cycle" policy, not a
# real-time SLA) and apply only to the new signal-collection path -- the
# legacy direct-to-Evidence RSS plugin path is unaffected. Source.priority
# outside 1..5 (shouldn't happen; the column defaults to 3) falls back to
# the 3-minute-equivalent middle tier.
POLL_INTERVAL_MINUTES_BY_PRIORITY = {5: 5, 4: 10, 3: 20, 2: 40, 1: 60}
DEFAULT_POLL_INTERVAL_MINUTES = 20

# Startup staggering (brief section 16 "per-platform startup staggering"):
# spreads a long-running scheduler's very first poll of each never-before-
# collected source over this window, per provider, so e.g. 40 freshly
# imported X sources don't all fire in the same second. One-shot CLI
# invocations (`pipeline run` called by cron/Task Scheduler) don't pass
# `loop_started_at` and are therefore never staggered -- there is no "many
# sources fire simultaneously at boot" problem for a process that runs once
# and exits.
DEFAULT_STARTUP_STAGGER_SECONDS = 45


def stagger_delay_seconds(source_id: int, provider: str, window_seconds: int) -> int:
    """Deterministic per-source-per-provider jitter within [0, window). Same
    source always gets the same delay, so behavior is reproducible and
    explainable rather than randomized every run."""
    if window_seconds <= 0:
        return 0
    digest = hashlib.sha256(f"{provider}:{source_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % window_seconds


def _is_due(source: Source, *, now: dt.datetime, loop_started_at: Optional[dt.datetime],
           stagger_window_seconds: int) -> bool:
    if source.last_success_at is None:
        if loop_started_at is None:
            return True  # one-shot invocation: always eligible for first collection
        delay = stagger_delay_seconds(source.id, source.provider, stagger_window_seconds)
        return (now - loop_started_at).total_seconds() >= delay
    interval_minutes = POLL_INTERVAL_MINUTES_BY_PRIORITY.get(source.priority, DEFAULT_POLL_INTERVAL_MINUTES)
    return now - source.last_success_at >= dt.timedelta(minutes=interval_minutes)


def default_registry() -> dict[str, Provider]:
    """Providers safe to construct unconditionally (no optional runtime
    dependency). `x` is deliberately NOT included here -- it is constructed
    on demand in `_provider_for` so a missing Playwright install never
    affects RSS/replay collection or anything else in the app."""
    return {"rss": RSSProvider(), "replay": ReplayProvider(name="replay")}


def get_collection_settings(session: Session) -> SignalCollectionSettings:
    """Get-or-create the single settings row, same pattern as
    DiscoverySettingsService.get() in semi_intel/discovery/service.py.
    Defaults (collection_enabled=False, x_provider_enabled=False) mean a
    freshly migrated database performs zero network activity until an
    operator explicitly opts in (brief sections 17, 18, 26)."""
    settings = session.get(SignalCollectionSettings, 1)
    if not settings:
        settings = SignalCollectionSettings(id=1)
        session.add(settings)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            settings = session.get(SignalCollectionSettings, 1)
    return settings


@dataclass
class CollectionSummary:
    runs: List[ProviderRun] = field(default_factory=list)

    @property
    def items_collected(self) -> int:
        return sum(r.items_collected for r in self.runs)

    @property
    def failures(self) -> List[ProviderRun]:
        return [r for r in self.runs if r.status == ProviderRunStatus.FAILED]

    def __str__(self) -> str:  # pragma: no cover - display only
        lines = []
        for r in self.runs:
            if r.status == ProviderRunStatus.OK:
                lines.append(
                    f"{r.provider}/source={r.source_id}: {r.items_collected} new, "
                    f"{r.duplicates_skipped} duplicate(s)"
                )
            else:
                lines.append(f"{r.provider}/source={r.source_id}: FAILED -- {r.error}")
        return "\n".join(lines)


class CollectionService:
    def __init__(self, session: Session, registry: Optional[dict[str, Provider]] = None):
        self.session = session
        self.registry = registry if registry is not None else default_registry()

    def _provider_for(self, name: str) -> Provider:
        if name == "x":
            settings = get_collection_settings(self.session)
            if not settings.x_provider_enabled:
                raise ProviderUnavailable(
                    "X collection is disabled (signal_collection_settings.x_provider_enabled=False). "
                    "This applies to manual and automatic collection alike -- enable it explicitly first."
                )
            from semi_intel.signals.providers.x import XProvider

            return XProvider()  # raises ProviderUnavailable if not otherwise usable right now
        if name not in self.registry:
            raise ProviderUnavailable(f"no provider registered for {name!r}")
        return self.registry[name]

    def collect_due_sources(
        self,
        provider_name: Optional[str] = None,
        *,
        automatic: bool = False,
        loop_started_at: Optional[dt.datetime] = None,
        stagger_window_seconds: int = DEFAULT_STARTUP_STAGGER_SECONDS,
    ) -> CollectionSummary:
        """The method PipelineService calls each cycle (`automatic=True`).
        Selects sources that are enabled, polling_enabled, due per their
        priority-derived poll interval, and (optionally) restricted to one
        provider -- never sources on the legacy direct-to-Evidence plugin
        path, since those default to provider="manual"/polling_enabled=False
        and are handled by the existing PipelineService RSS step instead.

        `automatic=True` additionally requires
        `SignalCollectionSettings.collection_enabled` -- mirrors
        DiscoveryService.run_eligible()'s `automatic` flag exactly (brief
        section 18: "leave collection ... disabled unless already explicitly
        configured"). The explicit `radar collect` CLI command passes
        `automatic=False` (the default) so an operator's deliberate one-off
        collection always works regardless of the scheduler toggle -- same
        precedent as Discovery's "disabled-provider manual run stays safe"
        behavior. X specifically is gated in `_provider_for` regardless of
        this flag: it requires its own explicit opt-in either way.

        `loop_started_at` is only passed by the long-running scheduler
        (`pipeline loop`); a one-shot `pipeline run` leaves it unset and
        every never-before-collected source is immediately eligible (see
        `_is_due`)."""
        if automatic and not get_collection_settings(self.session).collection_enabled:
            return CollectionSummary()

        now = _now()
        stmt = select(Source).where(Source.enabled.is_(True), Source.polling_enabled.is_(True))
        if provider_name:
            stmt = stmt.where(Source.provider == provider_name)
        summary = CollectionSummary()
        for source in self.session.scalars(stmt):
            if not _is_due(source, now=now, loop_started_at=loop_started_at,
                          stagger_window_seconds=stagger_window_seconds):
                continue
            summary.runs.append(self.collect_source(source))
        return summary

    def collect_source(self, source: Source) -> ProviderRun:
        run = ProviderRun(
            provider=source.provider,
            source_id=source.id,
            started_at=_now(),
            status=ProviderRunStatus.RUNNING,
            cursor_before=source.cursor,
        )
        self.session.add(run)
        self.session.flush()

        try:
            provider = self._provider_for(source.provider)
            handle = source.provider_key or source.url
            if not handle:
                raise ProviderUnavailable(f"source {source.name!r} has no provider_key or url to collect from")
            cursor = Cursor(source.cursor) if source.cursor else None
            result = provider.collect(handle, cursor)
        except ProviderUnavailable as exc:
            return self._fail(run, source, str(exc))
        except Exception as exc:  # noqa: BLE001 - isolate one bad source from the rest of the cycle
            self.session.rollback()
            self.session.add(run)
            return self._fail(run, source, str(exc))

        created = 0
        duplicates = 0
        for raw in result.items:
            try:
                normalized = provider.normalize(raw)
            except Exception as exc:  # noqa: BLE001 - one bad item shouldn't kill the batch
                duplicates += 0
                run.error = f"{run.error or ''}\nnormalize failed for {raw.external_id}: {exc}".strip()
                continue

            existing = self.session.execute(
                select(SignalItem.id).where(
                    SignalItem.provider == source.provider,
                    SignalItem.external_id == normalized.external_id,
                )
            ).first()
            if existing:
                duplicates += 1
                continue

            content_hash = hash_content(normalized.text or normalized.title or normalized.external_id)
            item = SignalItem(
                source_id=source.id,
                provider=source.provider,
                external_id=normalized.external_id,
                raw_payload=json.dumps(raw.payload, default=str),
                normalized_text=normalized.text,
                title=normalized.title,
                url=normalized.url,
                expanded_links=json.dumps(normalized.links),
                author_handle=normalized.author_handle,
                author_display_name=normalized.author_display_name,
                posted_at=normalized.posted_at,
                collected_at=_now(),
                language=normalized.language,
                fidelity=normalized.fidelity,
                quoted_external_id=normalized.quoted_external_id,
                reply_to_external_id=normalized.reply_to_external_id,
                content_hash=content_hash,
            )
            self.session.add(item)
            self.session.flush()  # need item.id for SignalMedia rows below
            created += 1

            for m in normalized.media:
                url = m.get("url")
                if not url:
                    continue
                self.session.add(
                    SignalMedia(
                        signal_item_id=item.id,
                        media_type=m.get("kind", "image"),
                        remote_url=url,
                        alt_text=m.get("alt_text"),
                        from_quoted=bool(m.get("from_quoted", False)),
                    )
                )

        source.cursor = result.next_cursor.value if result.next_cursor else source.cursor
        source.last_success_at = _now()
        if created:
            source.last_observed_item_at = _now()
        source.error_state = None

        run.items_collected = created
        run.duplicates_skipped = duplicates
        run.cursor_after = source.cursor
        run.status = ProviderRunStatus.OK
        run.finished_at = _now()
        self.session.commit()
        return run

    def _fail(self, run: ProviderRun, source: Source, error: str) -> ProviderRun:
        run.status = ProviderRunStatus.FAILED
        run.error = error
        run.finished_at = _now()
        source.error_state = error
        self.session.commit()
        return run
