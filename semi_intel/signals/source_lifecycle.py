"""Source-scoped silent baseline and experimental delivery gates.

Semiconductor Intelligence already models Reddit as `platform=reddit` with
collection `provider=rss`. This module does not introduce `provider="reddit"`.

Reuse of existing fields (no schema migration):

* `Source.muted` -- experimental / external-delivery blocked. The column was
  imported from Signal Radar and serialized, but it was not a collection or
  notification gate. M0 wires it as the source-owned Discord/admission block.
* `Source.provider_metadata` JSON -- durable baseline and admission watermarks.

Laws this implements locally (not fleet-wide machinery):

* STD-DATA-COM-001 -- baseline/continuity is explicit and queryable.
* STD-DATA-COM-002 -- first seen by the Clank is not genuine novelty.
* STD-OPS-COM-001 -- a successful first populate is not an editorial outcome.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import SourceType
from semi_intel.domain.models import CandidateSignalItem, SignalItem, Source

HARDWARE_FEED_URL = "https://www.reddit.com/r/hardware/.rss"
HARDWARE_SOURCE_NAME = "Reddit r/hardware"
HARDWARE_SUBREDDIT = "hardware"

MATURITY_EXPERIMENTAL = "experimental"
MATURITY_ADMITTED = "admitted"

METADATA_PLATFORM = "platform"
METADATA_MATURITY = "maturity"
METADATA_SUBREDDIT = "subreddit"
METADATA_BASELINE_AT = "baseline_completed_at"
METADATA_DELIVERY_ADMITTED_AT = "delivery_admitted_at"
METADATA_DELIVERY_ADMISSION_REQUIRED = "delivery_admission_required"


class SourceRegistrationConflict(ValueError):
    """Display name collides with a source that is not the r/hardware RSS identity."""


def _naive_utc(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(dt.UTC).replace(tzinfo=None)


def _isoformat(value: dt.datetime) -> str:
    naive = _naive_utc(value) or value
    return naive.isoformat()


def parse_iso(value: Any) -> dt.datetime | None:
    """Parse an ISO timestamp. Malformed or unusable values fail closed (None)."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return _naive_utc(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return _naive_utc(parsed) or parsed


def source_metadata(source: Source) -> dict[str, Any]:
    raw = source.provider_metadata or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_source_metadata(source: Source, metadata: dict[str, Any]) -> None:
    source.provider_metadata = json.dumps(metadata, sort_keys=True)


def source_baseline_completed_at(source: Source) -> dt.datetime | None:
    return parse_iso(source_metadata(source).get(METADATA_BASELINE_AT))


def source_delivery_admitted_at(source: Source) -> dt.datetime | None:
    return parse_iso(source_metadata(source).get(METADATA_DELIVERY_ADMITTED_AT))


def source_maturity(source: Source) -> str | None:
    value = source_metadata(source).get(METADATA_MATURITY)
    return str(value) if value else None


def source_requires_delivery_admission(source: Source) -> bool:
    """Sticky experimental-admission contract. Survives unmute and maturity changes."""
    value = source_metadata(source).get(METADATA_DELIVERY_ADMISSION_REQUIRED)
    return value is True


def source_delivery_blocked(source: Source) -> bool:
    if source.muted:
        return True
    if source_requires_delivery_admission(source) and source_delivery_admitted_at(source) is None:
        return True
    return False


def ensure_delivery_admission_required(source: Source) -> None:
    """Set the sticky contract flag without clearing existing watermarks."""
    meta = source_metadata(source)
    if meta.get(METADATA_DELIVERY_ADMISSION_REQUIRED) is True:
        return
    meta[METADATA_DELIVERY_ADMISSION_REQUIRED] = True
    write_source_metadata(source, meta)


def source_lifecycle_view(source: Source) -> dict[str, Any]:
    meta = source_metadata(source)
    return {
        "platform": meta.get(METADATA_PLATFORM),
        "maturity": meta.get(METADATA_MATURITY),
        "subreddit": meta.get(METADATA_SUBREDDIT),
        "baseline_completed_at": meta.get(METADATA_BASELINE_AT),
        "delivery_admitted_at": meta.get(METADATA_DELIVERY_ADMITTED_AT),
        "delivery_admission_required": source_requires_delivery_admission(source),
        "delivery_blocked": source_delivery_blocked(source),
    }


def item_is_baseline(item: SignalItem, source: Source) -> bool:
    """True when this observation landed on the source's first-success populate."""
    boundary = source_baseline_completed_at(source)
    collected = _naive_utc(item.collected_at)
    if boundary is None or collected is None:
        return False
    return collected <= boundary


def candidate_member_rows(session: Session, candidate) -> list[tuple[SignalItem, Source]]:
    return list(
        session.execute(
            select(SignalItem, Source)
            .join(CandidateSignalItem, CandidateSignalItem.signal_item_id == SignalItem.id)
            .join(Source, Source.id == SignalItem.source_id)
            .where(CandidateSignalItem.candidate_id == candidate.id)
        )
    )


def item_is_delivery_admitted(item: SignalItem, source: Source) -> bool:
    """Whether this observation may enter the ordinary novelty / Discord path.

    Ordinary SemInt sources (no experimental admission contract) keep prior
    behaviour: unmuted, non-baseline observations are admitted.

    Sources under the sticky `delivery_admission_required` contract fail
    closed unless `admit_source_for_delivery` has stamped a usable
    `delivery_admitted_at`. Clearing `Source.muted` alone never grants
    authority. A missing or malformed watermark is denial, not a fallback
    to `baseline_completed_at`.
    """
    if source.muted:
        return False
    if item_is_baseline(item, source):
        return False
    collected = _naive_utc(item.collected_at)
    if source_requires_delivery_admission(source):
        admitted_at = source_delivery_admitted_at(source)
        if admitted_at is None or collected is None:
            return False
        return collected > admitted_at
    admitted_at = source_delivery_admitted_at(source)
    if admitted_at is None:
        return True
    if collected is None:
        return False
    return collected > admitted_at


def candidate_has_admitted_novelty(session: Session, candidate) -> bool:
    """True when any member observation is delivery-admitted.

    Candidates with no linked SignalItems keep prior behaviour (admitted).
    Test fixtures and some in-app-only candidates have no membership rows.

    This is lifetime membership, not transition authority. Notification
    emit paths must use `candidate_transition_has_admitted_material`.
    """
    rows = candidate_member_rows(session, candidate)
    if not rows:
        return True
    return any(item_is_delivery_admitted(item, source) for item, source in rows)


def candidate_transition_has_admitted_material(
    session: Session,
    candidate,
    *,
    previously_seen_item_ids: set[int] | frozenset[int],
) -> bool:
    """True when new member material since the last evaluation is admitted.

    An older admitted member must not authorise a transition caused only by
    later experimental observations. Empty membership keeps prior behaviour
    (admitted). No new members since the watermark is denial, not a fallback
    to lifetime membership.
    """
    rows = candidate_member_rows(session, candidate)
    if not rows:
        return True
    new_rows = [
        (item, source) for item, source in rows
        if item.id not in previously_seen_item_ids
    ]
    if not new_rows:
        return False
    return any(item_is_delivery_admitted(item, source) for item, source in new_rows)


def source_uses_silent_baseline(source: Source) -> bool:
    """First-populate silence is source-scoped to admission-controlled sources.

    Ordinary SemInt sources keep prior novelty behaviour (global activation
    watermark only). Applying baseline to every first success would change
    existing unmuted RSS/replay sources.
    """
    return (
        bool(source.muted)
        or source_maturity(source) == MATURITY_EXPERIMENTAL
        or source_requires_delivery_admission(source)
    )


def stamp_first_success_baseline(source: Source, *, now: dt.datetime) -> bool:
    """Stamp a durable baseline boundary on a source's first successful collect.

    Returns True if this run is the first-populate baseline. Sources that
    already have `last_success_at` are past first-populate and are not stamped.
    Call this *before* assigning `source.last_success_at` for the current run.
    """
    if source.last_success_at is not None:
        return False
    if not source_uses_silent_baseline(source):
        return False
    meta = source_metadata(source)
    if meta.get(METADATA_BASELINE_AT):
        return False
    meta[METADATA_BASELINE_AT] = _isoformat(now)
    write_source_metadata(source, meta)
    return True


def clear_collection_identity_watermarks(source: Source) -> None:
    """When the collectable identity changes, first-populate starts over.

    `delivery_admission_required` is lifetime provenance and is not cleared.
    """
    meta = source_metadata(source)
    meta.pop(METADATA_BASELINE_AT, None)
    meta.pop(METADATA_DELIVERY_ADMITTED_AT, None)
    write_source_metadata(source, meta)


def register_reddit_hardware(session: Session) -> tuple[Source, bool]:
    """Idempotently register the r/hardware RSS pilot. Performs no network I/O.

    Created state: enabled, polling disabled, muted (experimental / Discord
    blocked), provider=rss, exact Reddit RSS URL. Existing rows are returned
    unchanged so a later operator promotion or polling enable is not clobbered.

    A different source that only reuses the display name is a conflict: this
    helper does not mutate or adopt that row.
    """
    existing = session.scalar(
        select(Source).where(
            Source.provider == "rss",
            Source.provider_key == HARDWARE_FEED_URL,
        )
    )
    if existing is not None:
        ensure_delivery_admission_required(existing)
        session.commit()
        return existing, False
    existing_name = session.scalar(select(Source).where(Source.name == HARDWARE_SOURCE_NAME))
    if existing_name is not None:
        raise SourceRegistrationConflict(
            f"A source named {HARDWARE_SOURCE_NAME!r} already exists with "
            f"provider={existing_name.provider!r} "
            f"provider_key={existing_name.provider_key!r}. "
            "The r/hardware RSS pilot requires "
            f"provider='rss' and provider_key={HARDWARE_FEED_URL!r} "
            "and will not adopt an unrelated row."
        )

    source = Source(
        name=HARDWARE_SOURCE_NAME,
        type=SourceType.RSS,
        provider="rss",
        provider_key=HARDWARE_FEED_URL,
        url=HARDWARE_FEED_URL,
        enabled=True,
        polling_enabled=False,
        muted=True,
        provider_metadata=json.dumps(
            {
                METADATA_PLATFORM: "reddit",
                METADATA_MATURITY: MATURITY_EXPERIMENTAL,
                METADATA_SUBREDDIT: HARDWARE_SUBREDDIT,
                METADATA_DELIVERY_ADMISSION_REQUIRED: True,
            },
            sort_keys=True,
        ),
    )
    session.add(source)
    session.commit()
    return source, True


def admit_source_for_delivery(source: Source, *, now: dt.datetime | None = None) -> Source:
    """Lift the experimental Discord gate without replaying historical novelty.

    Unmutes the source and stamps `delivery_admitted_at`. Observations collected
    at or before that watermark stay non-admitted even after unmute. Polling
    is not enabled here -- that remains a separate operator action.
    """
    now = now or dt.datetime.utcnow()
    source.muted = False
    meta = source_metadata(source)
    meta[METADATA_DELIVERY_ADMISSION_REQUIRED] = True
    if meta.get(METADATA_MATURITY) == MATURITY_EXPERIMENTAL or not meta.get(METADATA_MATURITY):
        meta[METADATA_MATURITY] = MATURITY_ADMITTED
    if source_delivery_admitted_at(source) is None:
        meta[METADATA_DELIVERY_ADMITTED_AT] = _isoformat(now)
    write_source_metadata(source, meta)
    return source
