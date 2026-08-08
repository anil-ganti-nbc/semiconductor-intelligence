"""One explicit, environment-configured HTTPS webhook adapter.

No URL or token is persisted. Preview is pure and never opens a socket.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from semi_intel import __version__
from semi_intel.domain.enums import NotificationDeliveryState, NotificationSeverity
from semi_intel.domain.models import (
    DeliveryAdapterStatus, Notification, NotificationDigest, NotificationSettings,
)
from semi_intel.notifications.delivery import AdapterResult, DeliveryService
from semi_intel.notifications.service import aware, get_settings, safe_error, utcnow


URL_ENV = "SEMI_INTEL_WEBHOOK_URL"
TOKEN_ENV = "SEMI_INTEL_WEBHOOK_TOKEN"
TIMEOUT_ENV = "SEMI_INTEL_WEBHOOK_TIMEOUT"

# Discord's incoming-webhook API only recognizes its own message schema
# (content/embeds/username/...) -- posting the platform's generic
# type/severity/title/body/reason envelope gets rejected as an empty
# message (HTTP 400). Detect Discord's own webhook hosts and translate to
# their documented payload shape instead of adding a per-service adapter.
DISCORD_HOSTS = {"discord.com", "discordapp.com"}


def validate_webhook_url(value: str) -> tuple[str, str]:
    parsed = urlsplit(value)
    if parsed.username or parsed.password:
        raise ValueError("Webhook URL must not contain embedded credentials.")
    if not parsed.hostname:
        raise ValueError("Webhook URL must include a host.")
    loopback = parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (loopback and parsed.scheme == "http"):
        raise ValueError("Webhook URL must use HTTPS (HTTP is allowed only for loopback testing).")
    if parsed.scheme not in {"https", "http"}:
        raise ValueError("Unsupported webhook URL scheme.")
    return parsed.hostname.lower(), parsed.scheme


def redact_webhook_error(value: str | None) -> str | None:
    cleaned = safe_error(value)
    if not cleaned:
        return cleaned
    cleaned = re.sub(r"https?://[^/\s?#]+[^\s]*", "[redacted webhook endpoint]", cleaned)
    cleaned = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", cleaned)
    return cleaned[:500]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def synthetic_payload(*, idempotency_key: str = "test:phase9") -> dict:
    return {
        "type": "test", "severity": "informational",
        "title": "Semiconductor Intelligence Platform test",
        "body": "External delivery configuration is working.",
        "reason": "Synthetic operator-requested test; contains no candidate intelligence.",
        "event_at": utcnow().isoformat(), "occurrence_count": 1,
        "related": {}, "dashboard_path": "/#notifications",
        "idempotency_key": idempotency_key, "application_version": __version__,
    }


class WebhookAdapter:
    name = "generic_https_webhook"
    channel = "webhook"

    def __init__(
        self, *, url: str | None = None, token: str | None = None,
        timeout: float | None = None, opener=None,
    ):
        self.url = url if url is not None else os.environ.get(URL_ENV, "")
        self.token = token if token is not None else os.environ.get(TOKEN_ENV)
        self.timeout = min(max(
            timeout if timeout is not None else float(os.environ.get(TIMEOUT_ENV, "8")),
            1.0,
        ), 30.0)
        self.opener = opener or urllib.request.build_opener(_NoRedirect)
        if self.url:
            validate_webhook_url(self.url)

    @property
    def configured(self) -> bool:
        return bool(self.url)

    @property
    def host(self) -> str | None:
        return validate_webhook_url(self.url)[0] if self.url else None

    def preview(self, payload: dict | None = None) -> dict:
        if payload is None:
            synthetic = synthetic_payload()
            payload = (
                {"content": synthetic["body"]} if self.host in DISCORD_HOSTS else synthetic
            )
        return {
            "configured": self.configured, "host": self.host,
            "payload": payload, "network_contacted": False,
        }

    def deliver(self, text: str, *, idempotency_key: str) -> AdapterResult:
        if not self.configured:
            return AdapterResult(delivered=False, error="Webhook configuration is missing.", retryable=False)
        if self.host in DISCORD_HOSTS:
            payload = {"content": (text or "(no content)")[:2000]}
        else:
            payload = synthetic_payload(idempotency_key=idempotency_key)
            payload["body"] = text[:4000]
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"SemiIntel/{__version__}",
            "Idempotency-Key": idempotency_key,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            response = self.opener.open(request, timeout=self.timeout)
            status = getattr(response, "status", 200)
            response_body = response.read(64 * 1024 + 1)
            if len(response_body) > 64 * 1024:
                return AdapterResult(
                    delivered=False,
                    error="Webhook response exceeded the 64 KiB safety limit.",
                    retryable=False,
                )
            if not 200 <= status < 300:
                return AdapterResult(
                    delivered=False, error=f"Webhook returned HTTP {status}.",
                    retryable=status >= 500 or status in {408, 429},
                )
            return AdapterResult(
                delivered=True,
                external_message_id=response.headers.get("X-Message-ID") or idempotency_key,
            )
        except urllib.error.HTTPError as exc:
            return AdapterResult(
                delivered=False, error=f"Webhook returned HTTP {exc.code}.",
                retryable=exc.code >= 500 or exc.code in {408, 429},
            )
        except Exception as exc:  # noqa: BLE001 - adapter boundary
            return AdapterResult(delivered=False, error=redact_webhook_error(str(exc)), retryable=True)


class WebhookConfigurationService:
    def __init__(self, session: Session):
        self.session = session

    def status_row(self) -> DeliveryAdapterStatus:
        row = self.session.get(DeliveryAdapterStatus, 1)
        adapter = WebhookAdapter()
        if row is None:
            row = DeliveryAdapterStatus(id=1)
            self.session.add(row)
            try:
                self.session.flush()
            except IntegrityError:
                self.session.rollback()
                row = self.session.get(DeliveryAdapterStatus, 1)
        row.configuration_present = adapter.configured
        row.configured_host = adapter.host
        if not adapter.configured:
            row.enabled = False
        self.session.flush()
        return row

    def status(self) -> dict:
        row = self.status_row()
        return {
            "adapter": row.adapter_type, "configured": row.configuration_present,
            "host": row.configured_host, "enabled": row.enabled,
            "tested": row.last_test_at is not None,
            "last_test_successful": row.last_test_successful,
            "last_test_at": row.last_test_at.isoformat() if row.last_test_at else None,
            "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
            "last_error_summary": row.last_error_summary,
        }

    def set_enabled(self, enabled: bool, *, allow_untested: bool = False) -> dict:
        row = self.status_row()
        if enabled and not row.configuration_present:
            raise ValueError("Webhook configuration is missing.")
        if enabled and row.last_test_successful is not True and not allow_untested:
            raise ValueError("Run a successful webhook test before enabling delivery.")
        row.enabled = enabled
        get_settings(self.session).external_delivery_enabled = enabled
        self.session.commit()
        return self.status()

    def preview(self) -> dict:
        return WebhookAdapter().preview()

    def test(self, *, adapter: WebhookAdapter | None = None, now: dt.datetime | None = None) -> AdapterResult:
        now = now or utcnow()
        adapter = adapter or WebhookAdapter()
        row = self.status_row()
        result = adapter.deliver(
            synthetic_payload()["body"], idempotency_key=f"webhook-test:{now.isoformat()}"
        )
        row.last_test_at = now
        row.last_test_successful = result.delivered
        row.last_error_summary = redact_webhook_error(result.error)
        if result.delivered:
            row.last_success_at = now
        self.session.commit()
        return result


class ExternalDeliveryService:
    """Deliver only new, important local records after explicit adapter enable.

    DeliveryService remains authoritative for quiet hours, caps, idempotency and
    retry limits. This coordinator only chooses the bounded eligible set.
    """

    def __init__(self, session: Session, *, adapter: WebhookAdapter | None = None):
        self.session = session
        self.adapter = adapter or WebhookAdapter()

    def _enabled(self) -> bool:
        row = WebhookConfigurationService(self.session).status_row()
        settings = get_settings(self.session)
        return bool(
            row.enabled and row.configuration_present
            and settings.external_delivery_enabled and self.adapter.configured
        )

    def deliver_pending(self, *, now: dt.datetime | None = None, limit: int = 50) -> dict:
        now = now or utcnow()
        if not self._enabled():
            self.session.commit()
            return {"notifications": 0, "digests": 0, "disabled": True}
        settings = get_settings(self.session)
        notifications = list(self.session.scalars(
            select(Notification).where(
                Notification.created_at >= aware(settings.activation_at),
                Notification.severity.in_([
                    NotificationSeverity.IMPORTANT, NotificationSeverity.URGENT,
                ]),
                Notification.muted.is_(False),
                Notification.dismissed_at.is_(None),
                Notification.delivery_state.in_([
                    NotificationDeliveryState.IN_APP,
                    NotificationDeliveryState.PENDING,
                    NotificationDeliveryState.DEFERRED,
                    NotificationDeliveryState.FAILED,
                ]),
            ).order_by(Notification.event_at.asc()).limit(min(max(limit, 1), 200))
        ))
        delivery = DeliveryService(self.session)
        notification_attempts = 0
        for notification in notifications:
            before = notification.delivery_state
            attempt = delivery.deliver_notification(notification, self.adapter, now=now)
            if attempt is not None and notification.delivery_state != before:
                notification_attempts += 1

        digests = list(self.session.scalars(
            select(NotificationDigest).where(
                NotificationDigest.generated_at >= aware(settings.activation_at),
                NotificationDigest.delivery_state.in_([
                    NotificationDeliveryState.IN_APP,
                    NotificationDeliveryState.PENDING,
                    NotificationDeliveryState.DEFERRED,
                    NotificationDeliveryState.FAILED,
                ]),
            ).order_by(NotificationDigest.generated_at.asc()).limit(5)
        ))
        digest_attempts = 0
        for digest in digests:
            before = digest.delivery_state
            attempt = delivery.deliver_digest(digest, self.adapter, now=now)
            if attempt is not None and digest.delivery_state != before:
                digest_attempts += 1
        self.session.commit()
        return {
            "notifications": notification_attempts,
            "digests": digest_attempts,
            "disabled": False,
        }
