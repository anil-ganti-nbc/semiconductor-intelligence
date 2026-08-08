"""Deterministic operator-facing management and health for Radar sources."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import ProviderRunStatus
from semi_intel.domain.models import ProviderRun, Source
from semi_intel.notifications.service import safe_error
from semi_intel.signals.providers.replay import ReplayProvider


_HTTP_STATUS = re.compile(r"\b([45]\d\d)\b")


def _rss_url(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("RSS source must use a complete http:// or https:// feed URL.")
    if parsed.username or parsed.password:
        raise ValueError("Feed URLs containing credentials are not supported.")
    return value


def normalize_provider_key(provider: str, value: str) -> str:
    """Validate source identity without performing network activity."""
    if provider == "rss":
        return _rss_url(value)
    if provider == "x":
        candidate = ReplayProvider(name="x").validate(value.strip())
        if hasattr(candidate, "reason"):
            raise ValueError(candidate.reason)
        return candidate.provider_key
    raise ValueError(f"Source provider {provider!r} cannot be edited here.")


def classify_error(value: str | None) -> tuple[str, str | None]:
    summary = safe_error(value)
    if not summary:
        return "failed", None
    low = summary.lower()
    if "timed out" in low or "timeout" in low:
        return "timed_out", summary
    if "rate limit" in low or "too many requests" in low or "429" in low:
        return "rate_limited", summary
    if "challenge" in low or "captcha" in low:
        return "challenged", summary
    if any(marker in low for marker in ("not authenticated", "session expired", "authentication required")):
        return "authentication_required", summary
    match = _HTTP_STATUS.search(summary)
    if match:
        return "http_error", summary
    if any(marker in low for marker in ("not a valid rss", "could not be parsed", "invalid feed")):
        return "invalid_feed", summary
    return "failed", summary


class SourceManagementService:
    def __init__(self, session: Session):
        self.session = session

    def latest_run(self, source_id: int) -> ProviderRun | None:
        return self.session.scalar(
            select(ProviderRun)
            .where(ProviderRun.source_id == source_id)
            .order_by(ProviderRun.started_at.desc(), ProviderRun.id.desc())
        )

    def health(self, source: Source) -> dict:
        latest = self.latest_run(source.id)
        if not source.enabled:
            state, summary = "disabled", safe_error(source.error_state)
        elif source.error_state:
            state, summary = classify_error(source.error_state)
        elif source.last_success_at is not None:
            state, summary = "healthy", None
        else:
            state, summary = "untested", None
        return {
            "state": state,
            "error_summary": summary,
            "last_attempt_at": (
                (latest.finished_at or latest.started_at).isoformat() if latest else None
            ),
            "last_attempt_status": latest.status.value if latest else None,
        }

    def serialize(self, source: Source) -> dict:
        return {
            "id": source.id,
            "name": source.name,
            "provider": source.provider,
            "provider_key": source.provider_key,
            "url": source.url,
            "enabled": source.enabled,
            "polling_enabled": source.polling_enabled,
            "muted": source.muted,
            "priority": source.priority,
            "trust_weight": source.trust_weight,
            "last_success_at": source.last_success_at.isoformat() if source.last_success_at else None,
            "last_observed_item_at": (
                source.last_observed_item_at.isoformat() if source.last_observed_item_at else None
            ),
            "health": self.health(source),
        }

    def update(
        self,
        source: Source,
        *,
        name: str,
        handle_or_url: str,
        priority: int,
        trust_weight: float,
        enabled: bool,
        polling_enabled: bool,
    ) -> Source:
        name = name.strip()
        if not name:
            raise ValueError("Display name is required.")
        duplicate_name = self.session.scalar(
            select(Source).where(Source.name == name, Source.id != source.id)
        )
        if duplicate_name:
            raise ValueError(f"A source named {name!r} already exists.")

        provider_key = normalize_provider_key(source.provider, handle_or_url)
        duplicate_key = self.session.scalar(
            select(Source).where(
                Source.provider == source.provider,
                Source.provider_key == provider_key,
                Source.id != source.id,
            )
        )
        if duplicate_key:
            raise ValueError("That feed or account is already registered.")

        identity_changed = provider_key != source.provider_key
        source.name = name
        source.provider_key = provider_key
        source.url = provider_key if source.provider == "rss" else None
        source.priority = priority
        source.trust_weight = trust_weight
        source.enabled = enabled
        source.polling_enabled = polling_enabled if enabled else False
        if identity_changed:
            source.cursor = None
            source.last_success_at = None
            source.last_observed_item_at = None
            source.error_state = None
        self.session.commit()
        return source
