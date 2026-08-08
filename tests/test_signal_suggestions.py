"""Unified source-suggestion mining tests (brief section 24)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from semi_intel.domain.enums import SourceSuggestionKind, SourceSuggestionStatus, SourceType
from semi_intel.domain.models import Source, SourceSuggestion, SignalItem
from semi_intel.signals.suggestions import accept_source_suggestion, refresh_handle_suggestions

BASE = dt.datetime(2026, 1, 1, 12, 0, 0)


def _source(session, name="Aggregator"):
    src = Source(name=name, type=SourceType.SOCIAL, provider="rss")
    session.add(src)
    session.commit()
    return src


def _item(session, source, ext_id, text, *, posted=BASE):
    item = SignalItem(
        source_id=source.id, provider="rss", external_id=ext_id, raw_payload="{}",
        normalized_text=text, content_hash=f"h-{ext_id}", posted_at=posted,
    )
    session.add(item)
    session.commit()
    return item


def test_no_suggestion_below_min_appearances(db_session):
    source = _source(db_session)
    for i in range(2):  # below MIN_APPEARANCES=3
        _item(db_session, source, str(i), "According to VideoCardz, RTX 50 Super ships with 24GB VRAM.")

    count = refresh_handle_suggestions(db_session)

    assert count == 0
    assert list(db_session.scalars(select(SourceSuggestion))) == []


def test_suggestion_created_once_appearance_threshold_met(db_session):
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), "According to VideoCardz, RTX 50 Super ships with 24GB VRAM.")

    count = refresh_handle_suggestions(db_session)

    assert count == 1
    suggestion = db_session.scalars(select(SourceSuggestion)).one()
    assert suggestion.kind == SourceSuggestionKind.HANDLE
    assert suggestion.provider_key == "videocardz"
    assert suggestion.appearances == 4
    assert suggestion.status == SourceSuggestionStatus.PENDING


def test_two_distinct_handles_crossing_threshold_in_one_pass_both_persist(db_session):
    """Regression test for a production 500: every attribution-mined
    SourceSuggestion previously shared the literal domain="" value, but
    SourceSuggestion.domain has a unique DB constraint. The moment a
    SECOND distinct attributed name also crossed MIN_APPEARANCES in the
    same refresh_handle_suggestions() call, its insert collided with the
    first row's domain="" and raised an unhandled IntegrityError --
    surfaced as a 500 from POST /api/radar/source-suggestions/refresh
    once that endpoint was actually wired into regular use (v0.9.3)."""
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), "According to VideoCardz, RTX 50 Super ships with 24GB VRAM.")
    for i in range(4, 8):
        _item(db_session, source, str(i), "According to MooresLawIsDead, the next node shrinks further.")

    count = refresh_handle_suggestions(db_session)

    assert count == 2
    suggestions = list(db_session.scalars(select(SourceSuggestion)))
    assert len(suggestions) == 2
    domains = {s.domain for s in suggestions}
    assert len(domains) == 2  # each row kept a distinct domain -- no collision
    provider_keys = {s.provider_key for s in suggestions}
    assert provider_keys == {"videocardz", "mooreslawisdead"}


def test_already_registered_source_is_not_suggested_again(db_session):
    _source(db_session, name="VideoCardz")
    unrelated_source = _source(db_session, name="Some Aggregator")
    for i in range(5):
        _item(db_session, unrelated_source, str(i), "According to VideoCardz, RTX 50 Super ships with 24GB VRAM.")

    refresh_handle_suggestions(db_session)

    # VideoCardz is already a registered Source name -- must not also
    # appear as a pending suggestion.
    assert list(db_session.scalars(select(SourceSuggestion))) == []


def test_refresh_is_idempotent_updates_not_duplicates(db_session):
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), "According to VideoCardz, RTX 50 Super ships with 24GB VRAM.")
    refresh_handle_suggestions(db_session)

    for i in range(4, 7):
        _item(db_session, source, str(i), "According to VideoCardz, RTX 50 Super ships with 24GB VRAM.")
    refresh_handle_suggestions(db_session)

    suggestions = list(db_session.scalars(select(SourceSuggestion)))
    assert len(suggestions) == 1  # still one row, not two
    assert suggestions[0].appearances == 7


def test_accept_handle_suggestion_creates_source_with_polling_disabled(db_session):
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), "According to VideoCardz, RTX 50 Super ships with 24GB VRAM.")
    refresh_handle_suggestions(db_session)
    suggestion = db_session.scalars(select(SourceSuggestion)).one()

    new_source = accept_source_suggestion(db_session, suggestion, provider="rss")
    db_session.commit()

    assert new_source.provider == "rss"
    assert new_source.provider_key == "videocardz"
    assert new_source.polling_enabled is False  # brief section 18: never silently enable collection
    assert suggestion.status == SourceSuggestionStatus.ADDED


def test_accept_is_idempotent_reuses_existing_source(db_session):
    source = _source(db_session)
    for i in range(4):
        _item(db_session, source, str(i), "According to VideoCardz, RTX 50 Super ships with 24GB VRAM.")
    refresh_handle_suggestions(db_session)
    suggestion = db_session.scalars(select(SourceSuggestion)).one()

    first = accept_source_suggestion(db_session, suggestion, provider="rss")
    db_session.commit()

    second = accept_source_suggestion(db_session, suggestion, provider="rss")
    db_session.commit()

    assert first.id == second.id
    assert len(list(db_session.scalars(select(Source)))) == 2  # original "Aggregator" + the one accepted source
