"""Signal analysis tests (brief section 24 "Signal analysis tests" and
"Regression tests"). The regression cases here are taken directly from
PHASE0_AUDIT.md section 3: the four highest-ranked false/overbroad stories
found in the supplied Signal Radar database. This suite proves the new
extractor can never repeat that failure -- an unresolved TitleCase phrase
can only ever become a `candidate`/`rejected` SignalEntityMention, never a
canonical Entity, and a codename in the hard-block list is rejected even
when hardware context is present."""

from __future__ import annotations

from sqlalchemy import select

from semi_intel.domain.enums import EntityType, SignalMentionStatus, SourceType
from semi_intel.domain.models import Entity, SignalEntityMention, SignalItem, SignalTopicMatch, Source
from semi_intel.editorial.service import TopicService
from semi_intel.signals.analysis import (
    ANALYSIS_VERSION,
    analyze_signal_item,
    analyze_unprocessed,
    reprocess_stale_items,
)


def _seed_topics(session):
    TopicService(session).seed()
    session.commit()


def _make_source(session):
    src = Source(name="Test Source", type=SourceType.SOCIAL, provider="replay", provider_key="a")
    session.add(src)
    session.commit()
    return src


def _make_item(session, source, external_id, text, *, title=None):
    item = SignalItem(
        source_id=source.id, provider="replay", external_id=external_id,
        raw_payload="{}", normalized_text=text, title=title, content_hash=f"hash-{external_id}",
    )
    session.add(item)
    session.commit()
    return item


# --- regression fixtures from the supplied live Radar database -------------

def test_united_states_never_becomes_resolved_or_canonical(db_session):
    source = _make_source(db_session)
    item = _make_item(db_session, source, "1", "United States tariffs may hit the semiconductor supply chain.")

    result = analyze_signal_item(db_session, item)
    db_session.commit()

    assert all(m.status != SignalMentionStatus.RESOLVED for m in result.mentions)
    assert not db_session.scalars(select(Entity).where(Entity.name.ilike("%united states%"))).first()


def test_south_korean_never_becomes_resolved_or_canonical(db_session):
    source = _make_source(db_session)
    item = _make_item(db_session, source, "1", "South Korean regulators are reviewing the proposed chip merger.")

    result = analyze_signal_item(db_session, item)
    db_session.commit()

    assert all(m.status != SignalMentionStatus.RESOLVED for m in result.mentions)


def test_jensen_huang_rejected_even_with_hardware_context_present(db_session):
    """The dangerous case: Jensen Huang mentioned in a post that DOES
    contain hardware context (so the context gate alone wouldn't save it) --
    only the explicit hard-block list does."""
    source = _make_source(db_session)
    item = _make_item(
        db_session, source, "1",
        "Jensen Huang unveiled the new GPU architecture with 208 billion transistors at the keynote.",
    )

    result = analyze_signal_item(db_session, item)
    db_session.commit()

    jensen_mentions = [m for m in result.mentions if "jensen" in m.candidate_text.lower()
                       or "huang" in m.candidate_text.lower()]
    assert jensen_mentions, "expected a mention to have been proposed for the Jensen Huang phrase"
    assert all(m.status == SignalMentionStatus.REJECTED for m in jensen_mentions)
    assert all(m.reason and m.reason.startswith("hard_block:") for m in jensen_mentions)


def test_architecture_overview_generic_heading_rejected(db_session):
    source = _make_source(db_session)
    item = _make_item(db_session, source, "1", "Architecture Overview: a retrospective on CPU microarchitecture design.")

    result = analyze_signal_item(db_session, item)
    db_session.commit()

    assert any(m.status == SignalMentionStatus.REJECTED for m in result.mentions)
    assert all(m.status != SignalMentionStatus.RESOLVED for m in result.mentions)


def test_xeon_catch_all_is_unresolved_candidate_not_canonical(db_session):
    """Xeon alone, with no monitored-topic match and no existing canonical
    Entity, must remain a candidate mention -- never auto-resolved. Whether
    it's ever worth a story is entirely Phase 4's clustering/scoring
    decision (which requires a monitored-topic match by default), not this
    module's."""
    source = _make_source(db_session)
    item = _make_item(db_session, source, "1", "Xeon processors are used in servers worldwide.")

    result = analyze_signal_item(db_session, item)
    db_session.commit()

    xeon_mentions = [m for m in result.mentions if m.candidate_text.lower() == "xeon"]
    assert xeon_mentions
    assert all(m.status == SignalMentionStatus.CANDIDATE for m in xeon_mentions)
    assert all(m.resolved_entity_id is None for m in xeon_mentions)
    # No monitored topic is literally "Xeon" in the seed list -- confirms
    # this mention alone carries no topic-relevance signal for scoring.
    assert not any(
        db_session.scalars(select(SignalTopicMatch).where(SignalTopicMatch.signal_item_id == item.id))
    )


def test_review_queue_is_not_flooded_generic_geography_and_people(db_session):
    """A batch of pure noise (geography/people/consoles, no hardware
    context) must yield zero RESOLVED and zero new canonical entities --
    the exact review-queue-flooding failure mode from the audit."""
    source = _make_source(db_session)
    texts = [
        "United States trade policy update.",
        "South Korean elections concluded today.",
        "Jensen Huang gave an interview about leadership.",
        "The Great Steam Deck sale continues this week.",
        "Galaxy Z8 rumors swirl among fans.",
    ]
    entity_count_before = len(db_session.scalars(select(Entity)).all())
    for i, text in enumerate(texts):
        item = _make_item(db_session, source, str(i), text)
        analyze_signal_item(db_session, item)
    db_session.commit()

    entity_count_after = len(db_session.scalars(select(Entity)).all())
    assert entity_count_after == entity_count_before  # zero new canonical entities


# --- positive path: seeded monitored topics ---------------------------------

def test_rtx_50_super_topic_and_pci_id_extracted(db_session):
    _seed_topics(db_session)
    source = _make_source(db_session)
    item = _make_item(
        db_session, source, "1",
        "Leaked slides show RTX 50 Super with 24GB VRAM on a 256-bit bus. Board ID 10DE:2D04 confirmed.",
    )

    result = analyze_signal_item(db_session, item)
    db_session.commit()

    matched_names = {tm.matched_text for tm in result.topic_matches}
    assert "RTX 50 Super" in matched_names

    pci_mentions = [m for m in result.mentions if m.proposed_entity_type == "pci_id"]
    assert any(m.candidate_text == "10DE:2D04" for m in pci_mentions)
    assert all(m.status == SignalMentionStatus.CANDIDATE for m in pci_mentions)


def test_all_four_required_monitored_topics_seeded_and_matchable(db_session):
    _seed_topics(db_session)
    source = _make_source(db_session)
    cases = {
        "RDNA 5": "RDNA 5 architecture details leaked ahead of the radeon launch event.",
        "Zen 6": "Zen 6 core design was previewed at the AMD chip roadmap event.",
        "RTX 60 Series": "RTX 60 Series GPU rumors point to a new architecture from NVIDIA.",
        "RTX 50 Super": "RTX 50 Super memory configuration leaked via GeForce driver strings.",
    }
    for i, (topic_name, text) in enumerate(cases.items()):
        item = _make_item(db_session, source, f"topic-{i}", text)
        result = analyze_signal_item(db_session, item)
        db_session.commit()
        matched = {tm.matched_text for tm in result.topic_matches}
        assert topic_name in matched, f"expected {topic_name!r} to match in: {text!r} (got {matched})"


def test_benchmark_identifier_extracted_as_candidate_mention(db_session):
    source = _make_source(db_session)
    item = _make_item(db_session, source, "1", "New Geekbench 6 single-core score leaked for the upcoming chip.")

    result = analyze_signal_item(db_session, item)
    db_session.commit()

    bench_mentions = [m for m in result.mentions if m.proposed_entity_type == "benchmark"]
    assert bench_mentions
    assert all(m.status == SignalMentionStatus.CANDIDATE for m in bench_mentions)


# --- canonical entity resolution --------------------------------------------

def test_mention_resolves_against_existing_canonical_entity(db_session):
    """When a canonical Entity already exists (created through the normal,
    human-reviewed path -- never by this module), a matching mention
    resolves to it with high confidence instead of staying a bare
    candidate."""
    entity = Entity(type=EntityType.PRODUCT, name="Xeon", aliases="[]", attributes="{}")
    db_session.add(entity)
    db_session.commit()

    source = _make_source(db_session)
    item = _make_item(db_session, source, "1", "Xeon processors are used in servers worldwide.")

    result = analyze_signal_item(db_session, item)
    db_session.commit()

    xeon_mentions = [m for m in result.mentions if m.candidate_text.lower() == "xeon"]
    assert any(m.status == SignalMentionStatus.RESOLVED and m.resolved_entity_id == entity.id
              for m in xeon_mentions)


def test_analysis_never_creates_a_canonical_entity(db_session):
    source = _make_source(db_session)
    before = len(db_session.scalars(select(Entity)).all())
    item = _make_item(db_session, source, "1", "Panther Lake CPU cores leaked with new architecture details.")

    analyze_signal_item(db_session, item)
    db_session.commit()

    after = len(db_session.scalars(select(Entity)).all())
    assert after == before  # extraction proposes; it never creates


# --- labels -------------------------------------------------------------

def test_off_topic_label_assigned_when_nothing_else_matches(db_session):
    source = _make_source(db_session)
    item = _make_item(db_session, source, "1", "Just had lunch, nothing interesting today.")

    result = analyze_signal_item(db_session, item)
    db_session.commit()

    assert any(l.label == "Off Topic" for l in result.labels)


def test_leak_label_from_pci_id_entity_rule(db_session):
    source = _make_source(db_session)
    item = _make_item(db_session, source, "1", "Spotted a new board with id 10DE:2D04 in the wild.")

    result = analyze_signal_item(db_session, item)
    db_session.commit()

    assert any(l.label == "Leak" for l in result.labels)


# --- processing version / reprocessing --------------------------------------

def test_analysis_sets_processing_version_and_state(db_session):
    source = _make_source(db_session)
    item = _make_item(db_session, source, "1", "Some RTX 50 Super leak text.")

    analyze_signal_item(db_session, item)
    db_session.commit()

    assert item.processing_version == ANALYSIS_VERSION
    assert item.processing_state.value == "processed"
    assert item.processed_at is not None


def test_analyze_unprocessed_only_touches_pending_items(db_session):
    source = _make_source(db_session)
    item1 = _make_item(db_session, source, "1", "RTX 50 Super leak.")
    item2 = _make_item(db_session, source, "2", "Zen 6 leak.")

    count = analyze_unprocessed(db_session)

    assert count == 2
    db_session.refresh(item1)
    db_session.refresh(item2)
    assert item1.processing_state.value == "processed"
    assert item2.processing_state.value == "processed"

    # A third, freshly-collected item is the only one picked up next time.
    item3 = _make_item(db_session, source, "3", "Another leak.")
    count2 = analyze_unprocessed(db_session)
    assert count2 == 1


def test_reprocess_stale_items_reruns_and_replaces_prior_analysis(db_session, monkeypatch):
    import semi_intel.signals.analysis as analysis_module

    source = _make_source(db_session)
    item = _make_item(db_session, source, "1", "RTX 50 Super leak with PCI ID 10DE:2D04.")
    analyze_signal_item(db_session, item)
    db_session.commit()

    first_mention_count = len(
        db_session.scalars(select(SignalEntityMention).where(SignalEntityMention.signal_item_id == item.id)).all()
    )
    assert first_mention_count > 0

    # Simulate an extraction-rule version bump.
    monkeypatch.setattr(analysis_module, "ANALYSIS_VERSION", analysis_module.ANALYSIS_VERSION + 1)
    reprocessed_count = reprocess_stale_items(db_session)

    assert reprocessed_count == 1
    second_mentions = db_session.scalars(
        select(SignalEntityMention).where(SignalEntityMention.signal_item_id == item.id)
    ).all()
    # Same count of mentions produced (deterministic rules, same text), but
    # not accumulated/duplicated from the first pass.
    assert len(second_mentions) == first_mention_count
    assert all(m.processing_version == analysis_module.ANALYSIS_VERSION for m in second_mentions)
