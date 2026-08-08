"""Unified suggested-sources mining, the Signal Radar half (brief section
14). The domain/citation half already exists and is unchanged:
`semi_intel.editorial.service.EditorialDiscoveryService._refresh_source_suggestions()`
mines unregistered domains from Evidence HTML. This module adds the other
half: platform accounts (X handles, etc.) repeatedly *explicitly credited*
in collected SignalItem text -- not every `@mention`, only the stronger
attribution patterns (via/according to/reported by/citing/source/spotted
by/hat tip/originally published by), reusing the exact same detector
`semi_intel.signals.independence.extract_attributed_names` already applies
for echo-chamber grouping. One credit-pattern rule, not two.

Both halves write into the SAME `SourceSuggestion` table (`kind=domain` vs
`kind=handle`), so there is one review workflow, one accept/dismiss/block
state machine, and one path into the canonical Source registry --
`accept_source_suggestion()` below reuses `(provider, provider_key)`
identity so accepting an already-registered handle is idempotent rather
than creating a duplicate Source.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import SourceSuggestionKind, SourceSuggestionStatus, SourceType
from semi_intel.domain.models import Source, SourceSuggestion, SignalItem
from semi_intel.signals.independence import extract_attributed_names

MIN_APPEARANCES = 3  # brief section 14: repeatedly credited/quoted, not a one-off


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def refresh_handle_suggestions(session: Session, *, min_appearances: int = MIN_APPEARANCES) -> int:
    """Scan SignalItem text for explicit-attribution mentions of a name
    that does NOT already match a registered Source's display name/handle,
    and surface it once it has been credited at least `min_appearances`
    times across distinct items. Idempotent: updates the existing
    suggestion row (appearances, last_seen_at) rather than duplicating it.
    """
    registered_names = {
        (row[0] or "").strip().lower()
        for row in session.execute(select(Source.name))
    }
    registered_keys = {
        (row[0] or "").strip().lower()
        for row in session.execute(select(Source.provider_key)) if row[0]
    }

    counts: dict[str, int] = defaultdict(int)
    first_seen: dict[str, dt.datetime] = {}
    last_seen: dict[str, dt.datetime] = {}

    for item in session.scalars(select(SignalItem).where(SignalItem.normalized_text.is_not(None))):
        names = extract_attributed_names(item.normalized_text or "")
        when = item.posted_at or item.collected_at
        for name in names:
            key = name.strip().lower()
            if not key or key in registered_names or key in registered_keys:
                continue
            counts[key] += 1
            if when:
                first_seen[key] = min(first_seen.get(key, when), when)
                last_seen[key] = max(last_seen.get(key, when), when)

    created_or_updated = 0
    for name, count in counts.items():
        if count < min_appearances:
            continue
        existing = session.execute(
            select(SourceSuggestion).where(
                SourceSuggestion.kind == SourceSuggestionKind.HANDLE,
                SourceSuggestion.platform == "unknown",
                SourceSuggestion.provider_key == name,
            )
        ).scalar_one_or_none()
        if existing:
            existing.appearances = count
            existing.last_seen_at = last_seen.get(name, _now())
            existing.reasons = f'["credited {count} time(s) via attribution phrases (via/according to/reported by/...)"]'
        else:
            session.add(SourceSuggestion(
                # `domain` has a unique constraint -- a shared "" for every
                # attribution-mined row means the second distinct name
                # crossing the threshold in the same refresh() call
                # collides with the first and raises IntegrityError on
                # flush (surfaced as an unhandled 500 from
                # POST /api/radar/source-suggestions/refresh). Give each
                # row a synthetic unique key instead, matching the
                # "legacy-handle:{platform}:{handle}" shape
                # LegacyRadarImporter already uses for imported handles.
                domain=f"handle:unknown:{name}"[:255],
                kind=SourceSuggestionKind.HANDLE,
                platform="unknown",  # the attribution phrase doesn't identify a specific platform
                provider_key=name,
                inferred_name=name.title(),
                score=min(count / 10.0, 1.0),
                reasons=f'["credited {count} time(s) via attribution phrases (via/according to/reported by/...)"]',
                appearances=count,
                status=SourceSuggestionStatus.PENDING,
                first_seen_at=first_seen.get(name, _now()),
                last_seen_at=last_seen.get(name, _now()),
            ))
        created_or_updated += 1

    session.flush()
    return created_or_updated


def accept_source_suggestion(session: Session, suggestion: SourceSuggestion, *, provider: Optional[str] = None) -> Source:
    """Idempotent acceptance into the canonical Source registry: for a
    domain suggestion this is unchanged existing behavior (see
    web/app.py's existing /api/source-suggestions/{id}/add); for a handle
    suggestion, resolve-or-create by (provider, provider_key) rather than
    by name, since two different platforms could plausibly share a handle
    string."""
    resolved_provider = provider or (suggestion.platform if suggestion.platform not in (None, "unknown") else "manual")
    provider_key = suggestion.provider_key or suggestion.domain

    existing = session.execute(
        select(Source).where(Source.provider == resolved_provider, Source.provider_key == provider_key)
    ).scalar_one_or_none()
    if existing:
        suggestion.status = SourceSuggestionStatus.ADDED
        return existing

    name = suggestion.inferred_name or provider_key
    # Source.name is globally unique -- disambiguate on collision rather
    # than failing the accept action.
    base_name = name
    suffix = 2
    while session.execute(select(Source.id).where(Source.name == name)).first():
        name = f"{base_name} ({suffix})"
        suffix += 1

    source = Source(
        name=name,
        type=SourceType.SOCIAL if resolved_provider not in ("rss", "manual") else SourceType.RSS,
        url=suggestion.feed_url,
        trust_weight=suggestion.inferred_reliability or 0.5,
        provider=resolved_provider,
        provider_key=provider_key,
        enabled=True,
        polling_enabled=False,  # operator opts into collection explicitly, per brief section 18
    )
    session.add(source)
    session.flush()
    suggestion.status = SourceSuggestionStatus.ADDED
    return source
