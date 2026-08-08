from __future__ import annotations

import io
import json
import urllib.error

import pytest

from semi_intel.notifications.delivery import DeliveryService
from semi_intel.domain.enums import NotificationSeverity
from semi_intel.notifications.service import NotificationService
from semi_intel.operations.webhook import (
    ExternalDeliveryService, WebhookAdapter, WebhookConfigurationService,
    validate_webhook_url,
)


class Response:
    status = 200
    headers = {"X-Message-ID": "message-9"}

    def read(self, limit):
        return b"ok"


class Opener:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or Response()

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_url_validation_is_conservative():
    assert validate_webhook_url("https://example.com/hook")[0] == "example.com"
    assert validate_webhook_url("http://127.0.0.1:9000/hook")[0] == "127.0.0.1"
    with pytest.raises(ValueError):
        validate_webhook_url("http://example.com/hook")
    with pytest.raises(ValueError):
        validate_webhook_url("https://user:pass@example.com/hook")


def test_preview_never_contacts_network():
    opener = Opener()
    adapter = WebhookAdapter(url="https://example.com/hook?secret=x", opener=opener)
    preview = adapter.preview()
    assert preview["network_contacted"] is False
    assert preview["host"] == "example.com"
    assert opener.calls == []


def test_discord_webhook_uses_discord_content_schema():
    opener = Opener()
    adapter = WebhookAdapter(
        url="https://discord.com/api/webhooks/123/abc", opener=opener
    )
    preview = adapter.preview()
    assert preview["payload"] == {"content": preview["payload"]["content"]}
    assert "content" in preview["payload"]

    result = adapter.deliver("Hello from Semi Intel", idempotency_key="notification:1:webhook")
    assert result.delivered
    request = opener.calls[0][0]
    sent = json.loads(request.data)
    assert sent == {"content": "Hello from Semi Intel"}
    assert "type" not in sent and "title" not in sent and "body" not in sent


def test_discordapp_legacy_host_also_uses_discord_schema():
    opener = Opener()
    adapter = WebhookAdapter(
        url="https://discordapp.com/api/webhooks/123/abc", opener=opener
    )
    adapter.deliver("legacy host text", idempotency_key="notification:1:webhook")
    sent = json.loads(opener.calls[0][0].data)
    assert sent == {"content": "legacy host text"}


def test_non_discord_host_keeps_generic_envelope():
    opener = Opener()
    adapter = WebhookAdapter(url="https://example.com/hook", opener=opener)
    adapter.deliver("plain text", idempotency_key="notification:1:webhook")
    sent = json.loads(opener.calls[0][0].data)
    assert sent["body"] == "plain text"
    assert "content" not in sent


def test_webhook_success_has_idempotency_and_no_duplicate_delivery(db_session):
    opener = Opener()
    adapter = WebhookAdapter(url="https://example.com/hook", token="secret", opener=opener)
    notification = NotificationService(db_session).create_test_notification()
    settings = NotificationService(db_session).settings()
    settings.external_delivery_enabled = True
    settings.quiet_hours_start = settings.quiet_hours_end = "00:00"
    delivery = DeliveryService(db_session)
    first = delivery.deliver_notification(notification, adapter)
    second = delivery.deliver_notification(notification, adapter)
    assert first.id == second.id
    assert len(opener.calls) == 1
    request = opener.calls[0][0]
    assert request.headers["Idempotency-key"].startswith("notification:")
    assert request.headers["Authorization"] == "Bearer secret"


def test_permanent_http_failure_is_not_retried(db_session):
    error = urllib.error.HTTPError(
        "https://example.com/hook?token=secret", 400, "bad", {}, io.BytesIO()
    )
    opener = Opener(error)
    adapter = WebhookAdapter(url="https://example.com/hook", opener=opener)
    notification = NotificationService(db_session).create_test_notification()
    settings = NotificationService(db_session).settings()
    settings.external_delivery_enabled = True
    settings.quiet_hours_start = settings.quiet_hours_end = "00:00"
    delivery = DeliveryService(db_session)
    first = delivery.deliver_notification(notification, adapter)
    second = delivery.deliver_notification(notification, adapter)
    assert first.id == second.id
    assert first.retry_after is None
    assert len(opener.calls) == 1


def test_configuration_defaults_disabled_and_requires_successful_test(db_session, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_WEBHOOK_URL", "https://example.com/hook")
    service = WebhookConfigurationService(db_session)
    assert service.status()["enabled"] is False
    with pytest.raises(ValueError):
        service.set_enabled(True)
    result = service.test(adapter=WebhookAdapter(
        url="https://example.com/hook", opener=Opener()
    ))
    assert result.delivered
    assert service.set_enabled(True)["enabled"] is True


def test_enabled_adapter_delivers_new_important_alert_once(db_session, monkeypatch):
    monkeypatch.setenv("SEMI_INTEL_WEBHOOK_URL", "https://example.com/hook")
    opener = Opener()
    adapter = WebhookAdapter(url="https://example.com/hook", opener=opener)
    configuration = WebhookConfigurationService(db_session)
    assert configuration.test(adapter=adapter).delivered
    assert configuration.set_enabled(True)["enabled"] is True
    settings = NotificationService(db_session).settings()
    assert settings.external_delivery_enabled is True
    settings.quiet_hours_start = settings.quiet_hours_end = "00:00"

    notification = NotificationService(db_session).create_test_notification()
    notification.severity = NotificationSeverity.IMPORTANT
    db_session.commit()
    service = ExternalDeliveryService(db_session, adapter=adapter)
    first = service.deliver_pending()
    second = service.deliver_pending()
    assert first["notifications"] == 1
    assert second["notifications"] == 0
    # One synthetic configuration test and one real safe alert delivery.
    assert len(opener.calls) == 2
