"""Signal analysis: topic matching, entity mentions, artifacts, labels.

This is the direct fix for the failure mode documented in PHASE0_AUDIT.md
section 3 -- `United States / The Six Fi`, `South Korean / Galaxy Z8`,
`Jensen Huang`, and a catch-all `Xeon` story all became high-ranked
"editorial" stories in the supplied Signal Radar database because its
extractor could mint a canonical entity from any TitleCase-shaped phrase and
its story engine attached evidence on a single shared-entity match.

The rule enforced here (brief section 8) is three tiers, and only the first
two can ever influence anything with real weight before a human or the
promotion step (Phase 5) looks at it:

  1. Known monitored topic or alias match (`SignalTopicMatch`) -- high
     confidence. Reuses semi_intel.editorial.service.match_topic() exactly;
     no second topic-matching implementation.
  2. Known canonical Entity or alias match -- high/medium confidence.
     Resolved against entities that already exist; this module NEVER
     creates a canonical Entity. That is the one rule that actually fixes
     Jensen Huang/Xeon/United States: an extractor can propose all it wants,
     but nothing here can make a proposal canonical.
  3. Unknown entity-shaped phrase -- stays a `SignalEntityMention` with
     status=candidate (or status=rejected, with a reason, if it fails the
     hardware-context/hard-block/edge-trim gates below). Never seeds
     anything on its own.

Structured artifacts (PCI IDs, benchmark identifiers, version/driver
strings) are a separate, higher-trust tier: tight regexes, not TitleCase
heuristics, so they are not subject to the codename noise filters -- but
they are still stored as `status=candidate` mentions, never auto-resolved to
a canonical Entity, consistent with "propose, promotion confirms."

Ported from Signal Radar's `core/entities/__init__.py` (extraction
grammars/lexicons/blocklists, kept verbatim -- they were already correct)
and `core/classifier/__init__.py` (label rules, kept verbatim). What is NOT
ported is `_link()`'s behavior of calling `resolve_or_create` on every hit:
here, "resolve" only ever means "match an existing canonical Entity";
nothing is ever created.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import SignalMentionStatus, SignalProcessingState
from semi_intel.domain.models import (
    Entity,
    MonitoredTopic,
    SignalEntityMention,
    SignalItem,
    SignalLabel,
    SignalTopicMatch,
)
from semi_intel.editorial.service import match_topic, normalize_phrase

# Bump whenever extraction rules change in a way that would produce
# different results for already-processed items. `radar reprocess` compares
# this against SignalItem.processing_version to find stale analysis.
ANALYSIS_VERSION = 1

# --- lexicons (seed; a future increment could move these to the DB, same
# note Signal Radar's own module made about its lexicons) -------------------
COMPANIES = {
    "intel", "amd", "nvidia", "arm", "qualcomm", "tsmc", "samsung", "apple",
    "mediatek", "micron", "sk hynix", "broadcom", "asml",
}
RETAILERS = {
    "gmktec", "minisforum", "geekbench", "newegg", "amazon", "aoostar", "beelink",
}

# --- grammars (structured artifacts) ---------------------------------------
RX_PCI_ID = re.compile(r"\b([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})\b")
RX_VERSION = re.compile(r"\b\d+(?:\.\d+){1,4}\b")
RX_BENCH = re.compile(r"\b(Geekbench|GB[56]|3DMark|Cinebench|CPU-Z|SiSoft|OpenCL|Vulkan)\b", re.I)

# A codename only counts as story-worthy if the post is actually about
# silicon. Broad news/social text is full of TitleCase phrases ("Game Boy
# Advance", "Jensen Huang") that trip a naive matcher -- this gate kills that
# noise. A named company also satisfies the context requirement.
RX_CODENAME = re.compile(
    r"\b([A-Z][a-z]+(?:\s(?:AI\s)?[A-Z][a-z0-9+]+){1,3}"
    r"|[A-Z]{2,4}\d{2,4}"
    r")\b"
)
HW_CONTEXT = re.compile(
    r"\b(cpu|gpu|apu|npu|soc|chip(?:set)?s?|silicon|wafer|die\b|tdp|cores?|threads?|"
    r"ghz|mhz|[0-9]\s?nm|process node|node|igpu|vram|gddr\d?|ddr[45]|hbm\d?|pcie|pci-e|"
    r"socket|am[45]\b|lga\d*|geekbench|benchmark|teraflops?|tflops|foundry|tape-?out|"
    r"microarchitecture|architecture|instruction set|mini pc|handheld|motherboard|"
    r"vbios|bios|firmware|driver|radeon|geforce|ryzen|core ultra|snapdragon|epyc|xeon|"
    r"threadripper|rtx|\brx\s?\d|arc\b|rdna|cdna|zen\s?\d|blackwell|lovelace|hopper|"
    r"lunar lake|arrow lake|panther lake|nova lake|strix|granite rapids|turin)\b",
    re.I,
)

# HARD block: if any word of the candidate phrase is here, reject the whole
# phrase outright (people, consoles, phones, media/AI brands, geography,
# orgs). This is exactly the list that was added late to Signal Radar and
# never retroactively applied to its existing database (PHASE0_AUDIT.md
# section 3) -- here it is a day-one, unconditional gate.
CODENAME_HARD_BLOCK = {
    "jensen", "huang", "lisa", "su", "pat", "gelsinger", "eric", "demers", "chey", "tae",
    "claude", "gemini", "chatgpt", "copilot", "kimi", "gpt", "grok", "llama",
    "nintendo", "switch", "playstation", "xbox", "steam", "deck", "capcom", "sony",
    "disney", "marvel", "netflix", "ubisoft", "sega", "gameboy", "game", "boy",
    "galaxy", "iphone", "ipad", "ipados", "macbook", "imac", "pixel", "garmin",
    "fitbit", "casio", "oneplus", "oppo", "vivo",
    "creality", "sermoon", "pika", "gezine",
    "united", "states", "korea", "korean", "south", "north", "china", "chinese",
    "japan", "japanese", "chairman", "chamber", "forum", "group", "ministry",
    "republic", "university", "jeju", "india", "indian",
    # Additional regression-fixture terms (PHASE0_AUDIT.md): section-heading
    # noise that a naive TitleCase matcher would happily grab.
    "overview", "architecture", "introduction", "summary", "conclusion", "abstract",
}
CODENAME_EDGE_STOP = {
    "the", "a", "an", "new", "great", "some", "upcoming", "latest", "official",
    "officially", "install", "installing", "developer", "number", "tough", "solar",
    "windows", "machine", "this", "that", "more", "first",
}

KNOWN_PRODUCT_HINTS = re.compile(
    r"\b(Ryzen(?:\s\w+)*|Core\s(?:Ultra\s)?\d?|Radeon(?:\s\w+)*|GeForce(?:\s\w+)*|"
    r"RTX\s?\d{3,4}|RX\s?\d{3,4}|Snapdragon(?:\s\w+)*|Threadripper|EPYC|Xeon)\b",
    re.I,
)

# --- labels (ported verbatim from Signal Radar's core/classifier) ----------
_RX = lambda p: re.compile(p, re.I)  # noqa: E731

LEXICAL_LABEL_RULES: list[tuple[str, re.Pattern, float, str]] = [
    ("Benchmark", _RX(r"\b(geekbench|3dmark|cinebench|cpu-z|benchmark|score|fps|opencl|vulkan)\b"), 0.8, "rx:benchmark"),
    ("Retail Listing", _RX(r"\b(listed|listing|pre-?order|in stock|price|\$\d|€\d|now available|retail)\b"), 0.7, "rx:retail"),
    ("Driver", _RX(r"\b(driver|whql|adrenalin|game ready|24\.\d|25\.\d|linux-firmware)\b"), 0.7, "rx:driver"),
    ("Firmware", _RX(r"\b(bios|uefi|firmware|microcode|agesa|vbios|ec firmware)\b"), 0.7, "rx:firmware"),
    ("PCB", _RX(r"\b(pcb|board shot|die shot|package|substrate|solder)\b"), 0.6, "rx:pcb"),
    ("Roadmap", _RX(r"\b(roadmap|q[1-4]\s?20\d\d|h[12]\s?20\d\d|launch(es)?|release date|20(2[6-9]|3\d))\b"), 0.6, "rx:roadmap"),
    ("Specification", _RX(r"\b(\d+\s?(cores?|threads?|mb|gb|tb|w tdp|w|nm|mhz|ghz)|tdp|cache|ipc)\b"), 0.6, "rx:spec"),
    ("Patent", _RX(r"\b(patent|uspto|filing|wipo)\b"), 0.7, "rx:patent"),
    ("Job Posting", _RX(r"\b(hiring|job|role|linkedin|we're looking|join our team)\b"), 0.6, "rx:job"),
    ("Official Announcement", _RX(r"\b(officially|we are announcing|press release|today we|introducing the)\b"), 0.7, "rx:official"),
    ("Correction", _RX(r"\b(correction|i was wrong|to clarify|edit:|updated:|retract)\b"), 0.7, "rx:correction"),
    ("Photo", _RX(r"\b(photo|pictured|leaked image|hands[- ]on|spotted in the wild)\b"), 0.5, "rx:photo"),
    ("Opinion", _RX(r"\b(i think|imo|my take|honestly|probably|i believe|feels like)\b"), 0.5, "rx:opinion"),
    ("Meme", _RX(r"\b(lol|lmao|meme|copium|hopium)\b"), 0.5, "rx:meme"),
    ("Rumor", _RX(r"\b(rumor|rumour|heard|allegedly|supposedly|word is|maybe|might be)\b"), 0.6, "rx:rumor"),
    ("Leak", _RX(r"\b(leak|exclusive|confirmed sku|got the specs|early sample|es chip|qs chip)\b"), 0.7, "rx:leak"),
]
ENTITY_TYPE_LABEL_RULES: list[tuple[str, str, float, str]] = [
    ("Benchmark", "benchmark", 0.85, "entity:benchmark"),
    ("Leak", "pci_id", 0.8, "entity:pci_id"),
    ("Specification", "pci_id", 0.6, "entity:pci_id"),
    ("Driver", "version", 0.4, "entity:version"),
]


def _clean_codename(token: str, has_context: bool) -> Optional[str]:
    if not has_context:
        return None
    words = token.split()
    if any(w.lower() in CODENAME_HARD_BLOCK for w in words):
        return None
    while words and words[0].lower() in CODENAME_EDGE_STOP:
        words.pop(0)
    while words and words[-1].lower() in CODENAME_EDGE_STOP:
        words.pop()
    cleaned = " ".join(words).strip()
    return cleaned if len(cleaned) >= 3 else None


def _rejection_reason(token: str, has_context: bool) -> str:
    if not has_context:
        return "no_hardware_context"
    words = [w.lower() for w in token.split()]
    blocked = [w for w in words if w in CODENAME_HARD_BLOCK]
    if blocked:
        return f"hard_block:{','.join(blocked)}"
    return "trimmed_to_empty"


@dataclass
class AnalysisResult:
    topic_matches: list[SignalTopicMatch] = field(default_factory=list)
    mentions: list[SignalEntityMention] = field(default_factory=list)
    labels: list[SignalLabel] = field(default_factory=list)


def _resolve_entity(session: Session, entity_type: str, candidate_text: str) -> Optional[Entity]:
    """Match against entities that already exist. NEVER creates one -- see
    module docstring. Case/normalize-insensitive, but still requires the
    normalized text to equal a normalized name or alias exactly (not a
    substring), so "NVIDIA" doesn't resolve against an entity named
    "NVIDIA Blog"."""
    target = normalize_phrase(candidate_text)
    if not target:
        return None
    stmt = select(Entity).where(Entity.type == entity_type) if _is_valid_entity_type(entity_type) else select(Entity)
    for entity in session.scalars(stmt):
        if normalize_phrase(entity.name) == target:
            return entity
        aliases = json.loads(entity.aliases or "[]")
        if any(normalize_phrase(a) == target for a in aliases):
            return entity
    return None


def _is_valid_entity_type(value: str) -> bool:
    from semi_intel.domain.enums import EntityType

    try:
        EntityType(value)
        return True
    except ValueError:
        return False


def _match_topics(session: Session, item: SignalItem, text: str) -> list[SignalTopicMatch]:
    matches: list[SignalTopicMatch] = []
    for topic in session.scalars(select(MonitoredTopic).where(MonitoredTopic.enabled.is_(True))):
        matched_text = match_topic(topic, text)
        if not matched_text:
            continue
        existing = session.execute(
            select(SignalTopicMatch.id).where(
                SignalTopicMatch.signal_item_id == item.id, SignalTopicMatch.topic_id == topic.id
            )
        ).first()
        if existing:
            continue
        row = SignalTopicMatch(
            signal_item_id=item.id, topic_id=topic.id, matched_text=matched_text,
            processing_version=ANALYSIS_VERSION,
        )
        session.add(row)
        matches.append(row)
    return matches


def _add_mention(
    session: Session, item: SignalItem, *, candidate_text: str, proposed_entity_type: str,
    extractor: str, span: tuple[int, int], confidence: float, status: SignalMentionStatus,
    reason: Optional[str] = None, resolved_entity: Optional[Entity] = None,
) -> SignalEntityMention:
    mention = SignalEntityMention(
        signal_item_id=item.id,
        candidate_text=candidate_text,
        proposed_entity_type=proposed_entity_type,
        resolved_entity_id=resolved_entity.id if resolved_entity else None,
        span_start=span[0],
        span_end=span[1],
        extractor=extractor,
        confidence=confidence,
        processing_version=ANALYSIS_VERSION,
        status=status,
        reason=reason,
    )
    session.add(mention)
    return mention


def _extract_mentions(session: Session, item: SignalItem, text: str, *, suffix: str = "") -> list[SignalEntityMention]:
    if not text:
        return []
    mentions: list[SignalEntityMention] = []
    low = text.lower()
    has_context = any(c in low for c in COMPANIES) or bool(HW_CONTEXT.search(text))

    for name in COMPANIES:
        idx = low.find(name)
        if idx < 0:
            continue
        display = name.title()
        entity = _resolve_entity(session, "company", display)
        mentions.append(_add_mention(
            session, item, candidate_text=display, proposed_entity_type="company",
            extractor="lexicon:company" + suffix, span=(idx, idx + len(name)),
            confidence=1.0 if entity else 0.6,
            status=SignalMentionStatus.RESOLVED if entity else SignalMentionStatus.CANDIDATE,
            resolved_entity=entity,
        ))

    for name in RETAILERS:
        idx = low.find(name)
        if idx < 0:
            continue
        display = name.title()
        mentions.append(_add_mention(
            session, item, candidate_text=display, proposed_entity_type="retailer",
            extractor="lexicon:retailer" + suffix, span=(idx, idx + len(name)),
            confidence=0.9, status=SignalMentionStatus.CANDIDATE,
        ))

    for m in RX_PCI_ID.finditer(text):
        mentions.append(_add_mention(
            session, item, candidate_text=m.group(0).upper(), proposed_entity_type="pci_id",
            extractor="grammar:pci_id" + suffix, span=m.span(), confidence=1.0,
            status=SignalMentionStatus.CANDIDATE,
        ))

    for m in RX_BENCH.finditer(text):
        mentions.append(_add_mention(
            session, item, candidate_text=m.group(0), proposed_entity_type="benchmark",
            extractor="grammar:benchmark" + suffix, span=m.span(), confidence=0.9,
            status=SignalMentionStatus.CANDIDATE,
        ))

    for m in KNOWN_PRODUCT_HINTS.finditer(text):
        candidate_text = m.group(0).strip()
        entity = _resolve_entity(session, "product", candidate_text)
        mentions.append(_add_mention(
            session, item, candidate_text=candidate_text, proposed_entity_type="product",
            extractor="grammar:product" + suffix, span=m.span(),
            confidence=0.95 if entity else 0.6,
            status=SignalMentionStatus.RESOLVED if entity else SignalMentionStatus.CANDIDATE,
            resolved_entity=entity,
        ))

    for m in RX_CODENAME.finditer(text):
        token = m.group(1).strip()
        if token.lower() in COMPANIES:
            continue
        cleaned = _clean_codename(token, has_context)
        if not cleaned:
            mentions.append(_add_mention(
                session, item, candidate_text=token, proposed_entity_type="codename",
                extractor="grammar:codename" + suffix, span=m.span(), confidence=0.0,
                status=SignalMentionStatus.REJECTED, reason=_rejection_reason(token, has_context),
            ))
            continue
        entity = _resolve_entity(session, "product", cleaned) or _resolve_entity(session, "architecture", cleaned)
        mentions.append(_add_mention(
            session, item, candidate_text=cleaned, proposed_entity_type="codename",
            extractor="grammar:codename" + suffix, span=m.span(),
            confidence=0.9 if entity else 0.5,
            status=SignalMentionStatus.RESOLVED if entity else SignalMentionStatus.CANDIDATE,
            resolved_entity=entity,
        ))

    for m in RX_VERSION.finditer(text):
        mentions.append(_add_mention(
            session, item, candidate_text=m.group(0), proposed_entity_type="version",
            extractor="grammar:version" + suffix, span=m.span(), confidence=0.8,
            status=SignalMentionStatus.CANDIDATE,
        ))

    return mentions


def _classify_labels(session: Session, item: SignalItem, text: str, mentions: list[SignalEntityMention]) -> list[SignalLabel]:
    scored: dict[str, tuple[float, str]] = {}

    def add(label: str, conf: float, rule: str) -> None:
        cur = scored.get(label)
        if cur is None or conf > cur[0]:
            scored[label] = (conf, rule)

    for label, rx, conf, rule in LEXICAL_LABEL_RULES:
        if rx.search(text):
            add(label, conf, rule)

    mention_types = {m.proposed_entity_type for m in mentions if m.status != SignalMentionStatus.REJECTED}
    for label, etype, conf, rule in ENTITY_TYPE_LABEL_RULES:
        if etype in mention_types:
            add(label, conf, rule)

    if not scored:
        add("Off Topic", 0.4, "rx:default")

    rows: list[SignalLabel] = []
    for label, (conf, rule) in scored.items():
        row = SignalLabel(
            signal_item_id=item.id, label=label, confidence=conf, rule=rule,
            processing_version=ANALYSIS_VERSION,
        )
        session.add(row)
        rows.append(row)
    return rows


def analyze_signal_item(session: Session, item: SignalItem) -> AnalysisResult:
    """Idempotent-per-version: re-running with the same ANALYSIS_VERSION on
    an already-processed item is a caller error avoided by
    `reprocess_stale_items` checking processing_version first; this function
    itself does not delete prior rows, so calling it twice would duplicate
    matches/mentions/labels -- callers must not do that (see
    reprocess_stale_items, which does the version check and deletes stale
    rows before re-analyzing)."""
    text = "\n\n".join(filter(None, [item.title, item.normalized_text]))
    result = AnalysisResult()
    result.topic_matches = _match_topics(session, item, text)
    result.mentions = _extract_mentions(session, item, text)
    result.labels = _classify_labels(session, item, text, result.mentions)

    item.processing_version = ANALYSIS_VERSION
    item.processing_state = SignalProcessingState.PROCESSED
    item.processing_error = None
    item.processed_at = dt.datetime.utcnow()
    return result


def reprocess_stale_items(session: Session, *, limit: Optional[int] = None) -> int:
    """Re-analyze every SignalItem whose processing_version is behind
    ANALYSIS_VERSION (brief section 23). Deletes that item's prior
    topic-match/mention/label rows first so re-analysis doesn't duplicate
    them -- SignalItem.raw_payload itself is never touched."""
    from sqlalchemy import delete

    stmt = select(SignalItem).where(SignalItem.processing_version < ANALYSIS_VERSION)
    if limit:
        stmt = stmt.limit(limit)
    items = list(session.scalars(stmt))
    for item in items:
        session.execute(delete(SignalTopicMatch).where(SignalTopicMatch.signal_item_id == item.id))
        session.execute(delete(SignalEntityMention).where(SignalEntityMention.signal_item_id == item.id))
        session.execute(delete(SignalLabel).where(SignalLabel.signal_item_id == item.id))
        analyze_signal_item(session, item)
    session.commit()
    return len(items)


def analyze_unprocessed(session: Session, *, limit: Optional[int] = None) -> int:
    """Analyze every SignalItem that has never been processed at all
    (processing_state=pending). This is the normal per-cycle pipeline step;
    reprocess_stale_items is the explicit `radar reprocess` path after
    extraction rules change."""
    stmt = select(SignalItem).where(SignalItem.processing_state == SignalProcessingState.PENDING)
    if limit:
        stmt = stmt.limit(limit)
    items = list(session.scalars(stmt))
    for item in items:
        analyze_signal_item(session, item)
    session.commit()
    return len(items)
