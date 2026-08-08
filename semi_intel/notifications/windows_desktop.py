"""Optional, local-only Windows desktop notification delivery.

The adapter uses the Windows PowerShell toast API already present on supported
Windows installations. It is deliberately isolated here so importing the rest
of the application remains safe on every platform.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import DeliveryAttemptStatus, NotificationSeverity
from semi_intel.domain.models import MonitoredTopic, Notification, NotificationDeliveryAttempt
from semi_intel.notifications.delivery import AdapterResult, DeliveryService
from semi_intel.notifications.service import aware, get_settings, utcnow


WINDOWS_DESKTOP_CHANNEL = "windows_desktop"
WINDOWS_DESKTOP_ADAPTER = "windows_powershell_toast"


@dataclass(frozen=True)
class DesktopSupport:
    supported: bool
    state: str
    message: str


def _default_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, **kwargs)


class WindowsDesktopAdapter:
    name = WINDOWS_DESKTOP_ADAPTER
    channel = WINDOWS_DESKTOP_CHANNEL

    def __init__(
        self,
        *,
        system_name: str | None = None,
        executable: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ):
        self.system_name = system_name or platform.system()
        self.executable = executable or (
            shutil.which("powershell.exe") or shutil.which("powershell")
        )
        self.runner = runner or _default_runner

    def support(self) -> DesktopSupport:
        if self.system_name != "Windows":
            return DesktopSupport(False, "unavailable", "Windows desktop notifications are Windows-only.")
        if not self.executable:
            return DesktopSupport(False, "unavailable", "Windows PowerShell is not available.")
        return DesktopSupport(True, "available", "Windows desktop notifications are available.")

    def deliver(self, text: str, *, idempotency_key: str) -> AdapterResult:
        support = self.support()
        if not support.supported:
            return AdapterResult(delivered=False, error=support.message, retryable=False)

        title, _, body = text.partition("\n")
        title = (title.strip() or "Semiconductor Intelligence Platform")[:120]
        body = (body.strip() or title)[:500]
        payload = base64.b64encode(
            json.dumps({"title": title, "body": body}).encode("utf-8")
        ).decode("ascii")
        script = r"""
$payloadJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__'))
$payload = $payloadJson | ConvertFrom-Json
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)
$nodes = $xml.GetElementsByTagName('text')
$nodes.Item(0).AppendChild($xml.CreateTextNode([string]$payload.title)) > $null
$nodes.Item(1).AppendChild($xml.CreateTextNode([string]$payload.body)) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Microsoft.WindowsPowerShell').Show($toast)
""".replace("__PAYLOAD__", payload)
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            completed = self.runner(
                [self.executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=creation_flags,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return AdapterResult(
                delivered=False,
                error="Windows desktop notification process could not be started.",
                retryable=True,
            )
        if completed.returncode != 0:
            return AdapterResult(
                delivered=False,
                error="Windows rejected the desktop notification.",
                retryable=False,
            )
        return AdapterResult(delivered=True, external_message_id=idempotency_key)


def render_desktop_notification(notification: Notification, *, topic_name: str | None = None) -> str:
    severity = notification.severity.value.replace("_", " ").title()
    topic = f" · {topic_name}" if topic_name else ""
    title = f"Semiconductor Intelligence Platform · {severity}"
    reason = (notification.reason or notification.body or "New intelligence alert").strip()
    body = f"{notification.title}{topic}\n{reason}"
    return f"{title}\n{body[:500]}"


class WindowsDesktopDeliveryService:
    """Select and deliver a bounded set without changing story/read state."""

    def __init__(self, session: Session, *, adapter: WindowsDesktopAdapter | None = None):
        self.session = session
        self.adapter = adapter or WindowsDesktopAdapter()

    def status(self) -> dict:
        settings = get_settings(self.session)
        support = self.adapter.support()
        latest = self.session.scalar(
            select(NotificationDeliveryAttempt)
            .where(NotificationDeliveryAttempt.channel == WINDOWS_DESKTOP_CHANNEL)
            .order_by(NotificationDeliveryAttempt.attempted_at.desc())
        )
        if not support.supported:
            state = "unavailable"
            message = support.message
        elif not settings.windows_desktop_notifications_enabled:
            state = "disabled"
            message = "Windows desktop notifications are available but disabled."
        elif latest and latest.status == DeliveryAttemptStatus.FAILED:
            state = "error"
            message = latest.error_summary or "The latest desktop notification failed."
        else:
            state = "available"
            message = "Windows desktop notifications are enabled and available."
        return {
            "enabled": settings.windows_desktop_notifications_enabled,
            "supported": support.supported,
            "state": state,
            "message": message,
            "last_attempt_at": aware(latest.attempted_at).isoformat() if latest else None,
            "last_attempt_status": latest.status.value if latest else None,
        }

    def set_enabled(self, enabled: bool) -> dict:
        support = self.adapter.support()
        if enabled and not support.supported:
            raise ValueError(support.message)
        settings = get_settings(self.session)
        settings.windows_desktop_notifications_enabled = bool(enabled)
        self.session.commit()
        return self.status()

    def test(self) -> AdapterResult:
        support = self.adapter.support()
        if not support.supported:
            return AdapterResult(delivered=False, error=support.message, retryable=False)
        return self.adapter.deliver(
            "Semiconductor Intelligence Platform · Test\nDesktop notifications are configured correctly.",
            idempotency_key=f"windows-desktop-test:{utcnow().isoformat()}",
        )

    def deliver_pending(self, *, now: dt.datetime | None = None, limit: int = 50) -> dict:
        now = now or utcnow()
        settings = get_settings(self.session, now=now)
        support = self.adapter.support()
        if not settings.windows_desktop_notifications_enabled or not support.supported:
            self.session.commit()
            return {"notifications": 0, "disabled": not settings.windows_desktop_notifications_enabled,
                    "supported": support.supported}
        rows = list(self.session.scalars(
            select(Notification).where(
                Notification.created_at >= aware(settings.activation_at),
                Notification.severity.in_([NotificationSeverity.IMPORTANT, NotificationSeverity.URGENT]),
                Notification.muted.is_(False),
                Notification.dismissed_at.is_(None),
            ).order_by(Notification.event_at.asc()).limit(min(max(limit, 1), 200))
        ))
        delivery = DeliveryService(self.session)
        processed = 0
        for notification in rows:
            before_count = self.session.scalar(
                select(func.count()).select_from(NotificationDeliveryAttempt).where(
                    NotificationDeliveryAttempt.notification_id == notification.id,
                    NotificationDeliveryAttempt.channel == WINDOWS_DESKTOP_CHANNEL,
                )
            ) or 0
            topic = self.session.get(MonitoredTopic, notification.topic_id) if notification.topic_id else None
            attempt = delivery.deliver_notification(
                notification,
                self.adapter,
                now=now,
                delivery_text=render_desktop_notification(
                    notification, topic_name=topic.name if topic else None
                ),
            )
            after_count = self.session.scalar(
                select(func.count()).select_from(NotificationDeliveryAttempt).where(
                    NotificationDeliveryAttempt.notification_id == notification.id,
                    NotificationDeliveryAttempt.channel == WINDOWS_DESKTOP_CHANNEL,
                )
            ) or 0
            if attempt is not None and after_count > before_count:
                processed += 1
        self.session.commit()
        return {"notifications": processed, "disabled": False, "supported": True}
