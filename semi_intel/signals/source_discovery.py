"""Multi-provider Suggested Sources discovery (v0.9.4, brief section 14
continued). Mines already-collected `SignalItem` data for candidate
websites, forums, subreddits, and GitHub repositories -- the domain half
of Suggested Sources previously only came from `Evidence` citation HTML
(`EditorialDiscoveryService._refresh_source_suggestions()`), never from
`SignalItem.url`/`.expanded_links`/`.normalized_text`, which is a
completely separate, previously-unmined corpus.

Every generator here is read-only over already-collected data (no new
network fetching except a small, bounded, per-candidate feed-URL
validation for Reddit/GitHub, reusing `RSSProvider.validate()`), and every
generator is wrapped in isolated try/except by `run_source_discovery()` so
one generator's failure (a bad regex match, a validation timeout) never
suppresses the others -- see `DiscoveryReport`.

Design note on why Reddit/GitHub suggestions are `kind=DOMAIN`, not a new
`kind`/`provider` value: `CollectionService._provider_for()`
(semi_intel/signals/collection.py) only recognizes "rss", "replay", and
the special-cased "x" -- creating a Source with `provider="reddit"` or
`provider="github"` would be accepted into the DB but would fail on every
poll cycle forever (ProviderUnavailable), since no collector is
registered for those names. A subreddit's `.rss` URL and a GitHub repo's
`releases.atom` URL are both literally RSS/Atom feeds, so instead these
suggestions are represented exactly like a website suggestion (kind=DOMAIN,
a synthetic `domain` identity, an optional `feed_url`) and flow through the
*existing*, already-tested `/api/source-suggestions/{id}/add` endpoint,
which creates a real `type=RSS` Source that the existing RSS polling path
picks up correctly. `platform` is used purely as a display/badge tag on
these domain-kind rows, not as a new collection provider.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import SourceSuggestionKind, SourceSuggestionStatus
from semi_intel.domain.models import Source, SourceSuggestion, SignalItem
from semi_intel.editorial.service import URL_RE, canonical_domain, is_noise_domain
from semi_intel.signals.providers.rss import RSSProvider
from semi_intel.signals.providers import SourceCandidate, ValidationError
from semi_intel.signals.suggestions import refresh_handle_suggestions

MIN_MENTIONS = 3  # matches refresh_handle_suggestions' MIN_APPEARANCES convention

SHORTENER_DOMAINS = {
    "bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly", "buff.ly",
    "is.gd", "rebrand.ly", "lnkd.in", "shorturl.at",
}
CDN_OR_ASSET_DOMAINS = {
    "imgur.com", "gstatic.com", "githubusercontent.com", "raw.githubusercontent.com",
    "akamaized.net", "fastly.net", "unpkg.com", "jsdelivr.net",
}
# Domains a dedicated generator already gives a specific, more useful
# identity to -- the generic domain generator would otherwise also
# suggest "add github.com" or "add reddit.com" as a plain website
# alongside the actually-useful per-repo/per-subreddit suggestion.
PLATFORM_HANDLED_DOMAINS = {"github.com", "gist.github.com", "reddit.com", "old.reddit.com"}
FORUM_PATH_MARKERS = (
    "/forum/", "/forums/", "/threads/", "/thread/", "/viewtopic",
    "/showthread", "/community/", "/t/", "/c/",
)
GITHUB_NON_REPO_SEGMENTS = {
    "about", "marketplace", "settings", "login", "join", "notifications",
    "sponsors", "orgs", "topics", "search", "features", "pricing",
    "contact", "site", "apps", "collections", "trending", "explore",
    "issues", "pulls", "codespaces", "new",
}

_REDDIT_RE = re.compile(r"reddit\.com/r/([A-Za-z0-9_]+)", re.I)
_GITHUB_RE = re.compile(r"github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)", re.I)


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


@dataclass
class GeneratorResult:
    status: str = "SUCCESS"  # SUCCESS | PARTIAL | FAILED
    examined: int = 0
    created: int = 0
    updated: int = 0
    duplicates_skipped: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class DiscoveryReport:
    started_at: dt.datetime
    finished_at: Optional[dt.datetime] = None
    generators: dict[str, GeneratorResult] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        if not self.finished_at:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def overall_status(self) -> str:
        statuses = {g.status for g in self.generators.values()}
        if not statuses or statuses == {"SUCCESS"}:
            return "SUCCESS"
        if statuses == {"FAILED"}:
            return "FAILED"
        return "PARTIAL"

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": round(self.duration_seconds, 3),
            "overall_status": self.overall_status,
            "generators": {
                name: {
                    "status": g.status, "examined": g.examined, "created": g.created,
                    "updated": g.updated, "duplicates_skipped": g.duplicates_skipped,
                    "rejected": g.rejected, "errors": g.errors,
                }
                for name, g in self.generators.items()
            },
        }


def _iter_signal_urls(item: SignalItem) -> list[str]:
    urls: list[str] = []
    if item.url:
        urls.append(item.url)
    try:
        urls.extend(json.loads(item.expanded_links or "[]"))
    except (TypeError, ValueError):
        pass
    urls.extend(URL_RE.findall(item.normalized_text or ""))
    return urls


def _registered_domains(session: Session) -> set[str]:
    return {
        canonical_domain(row[0])
        for row in session.execute(select(Source.url)) if row[0]
    }


def _existing_suggestion_domains(session: Session) -> set[str]:
    return {row[0] for row in session.execute(select(SourceSuggestion.domain))}


def _is_excluded_domain(domain: str) -> bool:
    if not domain:
        return True
    if is_noise_domain(domain):
        return True
    if domain in SHORTENER_DOMAINS or domain in CDN_OR_ASSET_DOMAINS or domain in PLATFORM_HANDLED_DOMAINS:
        return True
    if any(domain.endswith(f".{item}") for item in CDN_OR_ASSET_DOMAINS):
        return True
    return False


def _is_forum_shaped(path: str) -> bool:
    low = path.lower()
    return any(marker in low for marker in FORUM_PATH_MARKERS)


def _upsert_domain_suggestion(
    session: Session, *, domain: str, platform: Optional[str], inferred_name: str,
    example_url: str, count: int, origin_sources: set[int], reason: str,
) -> bool:
    """Returns True if a new row was created, False if an existing row was
    updated. Never touches `status` on an existing row (an operator's
    ignore/block decision is never silently reversed by a later scan)."""
    existing = session.execute(
        select(SourceSuggestion).where(SourceSuggestion.domain == domain)
    ).scalar_one_or_none()
    score = min((count + len(origin_sources)) / 10.0, 1.0)
    if existing:
        existing.appearances = count
        existing.independent_origin_count = len(origin_sources)
        existing.score = max(existing.score, score)
        existing.last_seen_at = _now()
        existing.reasons = json.dumps([reason])
        return False
    session.add(SourceSuggestion(
        domain=domain, kind=SourceSuggestionKind.DOMAIN, platform=platform,
        inferred_name=inferred_name, score=score, appearances=count,
        independent_origin_count=len(origin_sources),
        reasons=json.dumps([reason]), status=SourceSuggestionStatus.PENDING,
        first_seen_at=_now(), last_seen_at=_now(),
    ))
    return True


def discover_domain_candidates(session: Session) -> GeneratorResult:
    """Generator A + D: repeated external websites and forum-shaped URLs
    mined from SignalItem.url/expanded_links/normalized_text -- a corpus
    the citation-based domain miner (EditorialDiscoveryService) never
    reads at all."""
    result = GeneratorResult()
    registered = _registered_domains(session)
    existing_domains = _existing_suggestion_domains(session)

    counts: dict[str, int] = defaultdict(int)
    origins: dict[str, set[int]] = defaultdict(set)
    examples: dict[str, str] = {}
    forum_hint: dict[str, bool] = defaultdict(bool)

    try:
        items = session.scalars(select(SignalItem)).all()
        for item in items:
            result.examined += 1
            for url in _iter_signal_urls(item):
                try:
                    parsed = urlparse(url)
                except ValueError:
                    continue
                if parsed.scheme not in ("http", "https"):
                    continue
                domain = canonical_domain(url)
                if not domain or domain in registered or _is_excluded_domain(domain):
                    continue
                counts[domain] += 1
                origins[domain].add(item.source_id)
                examples.setdefault(domain, url)
                if _is_forum_shaped(parsed.path):
                    forum_hint[domain] = True
    except Exception as exc:  # noqa: BLE001
        result.status = "FAILED"
        result.errors.append(f"domain mining: {exc}")
        return result

    for domain, count in counts.items():
        if count < MIN_MENTIONS:
            result.rejected += 1
            continue
        platform = "forum" if forum_hint[domain] else None
        reason = (
            f"Cited by {count} signal item(s) across {len(origins[domain])} "
            f"independent source(s)" + (" (forum-shaped links observed)" if platform else "")
        )
        try:
            created = _upsert_domain_suggestion(
                session, domain=domain, platform=platform,
                inferred_name=domain.split(".")[0].title(), example_url=examples[domain],
                count=count, origin_sources=origins[domain], reason=reason,
            )
        except Exception as exc:  # noqa: BLE001
            result.status = "PARTIAL"
            result.errors.append(f"domain {domain}: {exc}")
            continue
        if created:
            result.created += 1
        elif domain in existing_domains:
            result.duplicates_skipped += 1
        else:
            result.updated += 1

    session.flush()
    return result


def _validate_deterministic_feed(url: str) -> Optional[str]:
    """Bounded, single-URL validation reusing the existing RSS validator --
    no crawling, no discovery, just "does this exact known feed URL work
    right now". Returns the feed URL if valid, None otherwise (never
    raises -- a blocked/unreachable feed is a normal, expected outcome,
    same as the VideoCardz 403 case in the website Find Feed workflow)."""
    outcome = RSSProvider().validate(url)
    if isinstance(outcome, SourceCandidate):
        return url
    return None


def discover_subreddit_candidates(session: Session, *, max_validations: int = 20) -> GeneratorResult:
    """Generator C: subreddit identities extracted from Reddit URLs
    observed in signal text/links. reddit.com is excluded from the
    citation-based domain miner's NOISE_DOMAINS, so this is the only path
    that surfaces subreddits at all."""
    result = GeneratorResult()
    existing_domains = _existing_suggestion_domains(session)
    counts: dict[str, int] = defaultdict(int)
    origins: dict[str, set[int]] = defaultdict(set)

    try:
        items = session.scalars(select(SignalItem)).all()
        for item in items:
            result.examined += 1
            for url in _iter_signal_urls(item):
                match = _REDDIT_RE.search(url)
                if not match:
                    continue
                name = match.group(1).lower()
                if name in ("all", "popular", "search"):
                    continue
                counts[name] += 1
                origins[name].add(item.source_id)
    except Exception as exc:  # noqa: BLE001
        result.status = "FAILED"
        result.errors.append(f"subreddit mining: {exc}")
        return result

    validations_used = 0
    for name, count in counts.items():
        if count < MIN_MENTIONS:
            result.rejected += 1
            continue
        domain = f"reddit:r/{name}"
        feed_url = None
        if domain not in existing_domains and validations_used < max_validations:
            validations_used += 1
            try:
                feed_url = _validate_deterministic_feed(f"https://www.reddit.com/r/{name}/.rss")
            except Exception as exc:  # noqa: BLE001
                result.status = "PARTIAL"
                result.errors.append(f"r/{name} validation: {exc}")
        reason = f"Subreddit r/{name} linked by {count} signal item(s) across {len(origins[name])} independent source(s)"
        try:
            existing = session.execute(
                select(SourceSuggestion).where(SourceSuggestion.domain == domain)
            ).scalar_one_or_none()
            score = min((count + len(origins[name])) / 10.0, 1.0)
            if existing:
                existing.appearances = count
                existing.independent_origin_count = len(origins[name])
                existing.score = max(existing.score, score)
                existing.last_seen_at = _now()
                existing.reasons = json.dumps([reason])
                if feed_url and not existing.feed_url:
                    existing.feed_url = feed_url
                result.duplicates_skipped += 1
            else:
                session.add(SourceSuggestion(
                    domain=domain, kind=SourceSuggestionKind.DOMAIN, platform="reddit",
                    provider_key=name, inferred_name=f"r/{name}", feed_url=feed_url,
                    score=score, appearances=count, independent_origin_count=len(origins[name]),
                    reasons=json.dumps([reason]), status=SourceSuggestionStatus.PENDING,
                    first_seen_at=_now(), last_seen_at=_now(),
                ))
                result.created += 1
        except Exception as exc:  # noqa: BLE001
            result.status = "PARTIAL"
            result.errors.append(f"r/{name}: {exc}")

    session.flush()
    return result


def discover_github_candidates(session: Session, *, max_validations: int = 20) -> GeneratorResult:
    """Generator E: owner/repository identities extracted from GitHub URLs.
    Only "releases" monitoring is offered (github.com/{owner}/{repo}/releases.atom,
    a real Atom feed with zero auth) -- commits/tags/issues modes are
    deliberately not implemented this pass (see docs/SOURCE_SUGGESTION_ARCHITECTURE.md)."""
    result = GeneratorResult()
    existing_domains = _existing_suggestion_domains(session)
    counts: dict[str, int] = defaultdict(int)
    origins: dict[str, set[int]] = defaultdict(set)

    try:
        items = session.scalars(select(SignalItem)).all()
        for item in items:
            result.examined += 1
            for url in _iter_signal_urls(item):
                match = _GITHUB_RE.search(url)
                if not match:
                    continue
                owner, repo = match.group(1).lower(), match.group(2).lower()
                if owner in GITHUB_NON_REPO_SEGMENTS or repo in GITHUB_NON_REPO_SEGMENTS:
                    continue
                repo = repo.removesuffix(".git")
                if not repo:
                    continue
                key = f"{owner}/{repo}"
                counts[key] += 1
                origins[key].add(item.source_id)
    except Exception as exc:  # noqa: BLE001
        result.status = "FAILED"
        result.errors.append(f"github mining: {exc}")
        return result

    validations_used = 0
    for key, count in counts.items():
        if count < MIN_MENTIONS:
            result.rejected += 1
            continue
        domain = f"github:{key}"
        feed_url = None
        if domain not in existing_domains and validations_used < max_validations:
            validations_used += 1
            try:
                feed_url = _validate_deterministic_feed(f"https://github.com/{key}/releases.atom")
            except Exception as exc:  # noqa: BLE001
                result.status = "PARTIAL"
                result.errors.append(f"{key} validation: {exc}")
        reason = f"GitHub repository {key} linked by {count} signal item(s) across {len(origins[key])} independent source(s)"
        try:
            existing = session.execute(
                select(SourceSuggestion).where(SourceSuggestion.domain == domain)
            ).scalar_one_or_none()
            score = min((count + len(origins[key])) / 10.0, 1.0)
            if existing:
                existing.appearances = count
                existing.independent_origin_count = len(origins[key])
                existing.score = max(existing.score, score)
                existing.last_seen_at = _now()
                existing.reasons = json.dumps([reason])
                if feed_url and not existing.feed_url:
                    existing.feed_url = feed_url
                result.duplicates_skipped += 1
            else:
                session.add(SourceSuggestion(
                    domain=domain, kind=SourceSuggestionKind.DOMAIN, platform="github",
                    provider_key=key, inferred_name=key, feed_url=feed_url,
                    score=score, appearances=count, independent_origin_count=len(origins[key]),
                    reasons=json.dumps([reason]), status=SourceSuggestionStatus.PENDING,
                    first_seen_at=_now(), last_seen_at=_now(),
                ))
                result.created += 1
        except Exception as exc:  # noqa: BLE001
            result.status = "PARTIAL"
            result.errors.append(f"{key}: {exc}")

    session.flush()
    return result


GENERATORS = {
    "domain_and_forum": discover_domain_candidates,
    "subreddit": discover_subreddit_candidates,
    "github_repository": discover_github_candidates,
    "attribution_handle": refresh_handle_suggestions,
}


def run_source_discovery(session: Session) -> DiscoveryReport:
    """Runs every registered generator, each fault-isolated -- one
    generator's exception never suppresses the others (Phase 10: a run can
    complete with conditions, never a false empty success when every
    generator failed)."""
    report = DiscoveryReport(started_at=_now())
    for name, generator in GENERATORS.items():
        try:
            if name == "attribution_handle":
                count = generator(session)
                report.generators[name] = GeneratorResult(status="SUCCESS", created=count, examined=count)
            else:
                report.generators[name] = generator(session)
            # Commit after each generator so a LATER generator's failure
            # (caught below) can only roll back its own uncommitted work,
            # never a prior generator's already-successful suggestions --
            # this is what makes fault isolation between generators real
            # rather than illusory within one shared session.
            session.commit()
        except Exception as exc:  # noqa: BLE001 - one generator's crash must not stop the others
            session.rollback()
            report.generators[name] = GeneratorResult(status="FAILED", errors=[str(exc)])
    report.finished_at = _now()
    return report
