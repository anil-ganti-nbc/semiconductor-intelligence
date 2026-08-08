"""Delivery adapter boundary with quiet hours, rate caps and bounded retry.

No network adapter ships in Phase 8.  InAppAdapter is the built-in local
implementation; tests inject a fake external adapter to verify the boundary
without contacting any service.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import DeliveryAttemptStatus, NotificationDeliveryState
from semi_intel.domain.models import (
    Notification,
    NotificationDeliveryAttempt,
    NotificationDigest,
)
from semi_intel.notifications.service import aware, get_settings, safe_error, utcnow


@dataclass
class AdapterResult:
    delivered: bool
    external_message_id: str | None = None
    error: str | None = None
    retryable: bool = True


class DeliveryAdapter(Protocol):
    name: str
    channel: str

    def deliver(self, text: str, *, idempotency_key: str) -> AdapterResult: ...


class InAppAdapter:
    name = "in_app"
    channel = "in_app"

    def deliver(self, text: str, *, idempotency_key: str) -> AdapterResult:
        return AdapterResult(delivered=True, external_message_id=idempotency_key)


def _parse_clock(value: str) -> dt.time:
    try:
        return dt.time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid time {value!r}; expected HH:MM.") from exc


def quiet_hours_end(
    now: dt.datetime, timezone: str, start: str, end: str
) -> dt.datetime | None:
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone {timezone!r}.") from exc
    local = aware(now).astimezone(zone)
    start_time, end_time = _parse_clock(start), _parse_clock(end)
    if start_time == end_time:
        return None
    if start_time < end_time:
        inside = start_time <= local.time() < end_time
        end_date = local.date()
    else:
        inside = local.time() >= start_time or local.time() < end_time
        end_date = local.date() + (dt.timedelta(days=1) if local.time() >= start_time else dt.timedelta())
    if not inside:
        return None
    return dt.datetime.combine(end_date, end_time, tzinfo=zone).astimezone(dt.UTC)


class DeliveryService:
    def __init__(self, session: Session):
        self.session = session

    def deliver_notification(
        self, notification: Notification, adapter: DeliveryAdapter,
        *, now: dt.datetime | None = None, delivery_text: str | None = None,
    ) -> NotificationDeliveryAttempt | None:
        return self._deliver(
            notification=notification, digest=None, adapter=adapter, now=now or utcnow(),
            delivery_text=delivery_text,
        )

    def deliver_digest(
        self, digest: NotificationDigest, adapter: DeliveryAdapter,
        *, now: dt.datetime | None = None,
    ) -> NotificationDeliveryAttempt | None:
        return self._deliver(
            notification=None, digest=digest, adapter=adapter, now=now or utcnow(),
            delivery_text=None,
        )

    def _deliver(
        self,
        *,
        notification: Notification | None,
        digest: NotificationDigest | None,
        adapter: DeliveryAdapter,
        now: dt.datetime,
        delivery_text: str | None,
    ) -> NotificationDeliveryAttempt | None:
        settings = get_settings(self.session, now=now)
        target_id = notification.id if notification else digest.id
        target_kind = "notification" if notification else "digest"
        idempotency_key = f"{target_kind}:{target_id}:{adapter.channel}"

        if (
            adapter.channel == "windows_desktop"
            and not settings.windows_desktop_notifications_enabled
        ):
            return None
        if (
            adapter.channel not in {"in_app", "windows_desktop"}
            and not settings.external_delivery_enabled
        ):
            return None

        attempts = list(self.session.scalars(
            select(NotificationDeliveryAttempt)
            .where(
                NotificationDeliveryAttempt.notification_id == (
                    notification.id if notification else None
                ),
                NotificationDeliveryAttempt.digest_id == (digest.id if digest else None),
                NotificationDeliveryAttempt.channel == adapter.channel,
            )
            .order_by(NotificationDeliveryAttempt.attempt_number.asc())
        ))
        if any(attempt.status == DeliveryAttemptStatus.DELIVERED for attempt in attempts):
            return next(
                attempt for attempt in attempts
                if attempt.status == DeliveryAttemptStatus.DELIVERED
            )
        if attempts:
            latest = attempts[-1]
            if latest.retry_after and aware(latest.retry_after) > now:
                return latest
            if latest.status == DeliveryAttemptStatus.FAILED and latest.retry_after is None:
                return latest
            if len(attempts) >= settings.maximum_delivery_attempts:
                return latest

        if adapter.channel != "in_app":
            quiet_end = quiet_hours_end(
                now, settings.timezone,
                settings.quiet_hours_start, settings.quiet_hours_end,
            )
            if quiet_end:
                return self._deferred(
                    notification, digest, adapter, len(attempts) + 1,
                    now, quiet_end, idempotency_key, "quiet hours",
                )
            delivered_last_hour = self.session.scalar(
                select(func.count()).select_from(NotificationDeliveryAttempt).where(
                    NotificationDeliveryAttempt.channel == adapter.channel,
                    NotificationDeliveryAttempt.status == DeliveryAttemptStatus.DELIVERED,
                    NotificationDeliveryAttempt.completed_at >= now - dt.timedelta(hours=1),
                )
            ) or 0
            if delivered_last_hour >= settings.maximum_immediate_per_hour:
                return self._deferred(
                    notification, digest, adapter, len(attempts) + 1,
                    now, now + dt.timedelta(hours=1), idempotency_key, "hourly delivery cap",
                )

        text = delivery_text or (notification.body if notification else digest.rendered_text)
        attempt_number = len(attempts) + 1
        try:
            result = adapter.deliver(text, idempotency_key=idempotency_key)
        except Exception as exc:  # noqa: BLE001 - adapters are fault-isolated
            result = AdapterResult(delivered=False, error=safe_error(str(exc)))

        if result.delivered:
            attempt = NotificationDeliveryAttempt(
                notification_id=notification.id if notification else None,
                digest_id=digest.id if digest else None,
                channel=adapter.channel,
                adapter_name=adapter.name,
                attempt_number=attempt_number,
                status=DeliveryAttemptStatus.DELIVERED,
                attempted_at=now,
                completed_at=now,
                external_message_id=result.external_message_id,
                idempotency_key=idempotency_key,
            )
            if notification and adapter.channel != "windows_desktop":
                notification.delivery_state = NotificationDeliveryState.DELIVERED
            elif digest and adapter.channel != "windows_desktop":
                digest.delivery_state = NotificationDeliveryState.DELIVERED
        else:
            retry_after = (
                now + dt.timedelta(minutes=5 * (2 ** (attempt_number - 1)))
                if result.retryable and attempt_number < settings.maximum_delivery_attempts else None
            )
            attempt = NotificationDeliveryAttempt(
                notification_id=notification.id if notification else None,
                digest_id=digest.id if digest else None,
                channel=adapter.channel,
                adapter_name=adapter.name,
                attempt_number=attempt_number,
                status=DeliveryAttemptStatus.FAILED,
                attempted_at=now,
                completed_at=now,
                retry_after=retry_after,
                error_summary=safe_error(result.error),
                idempotency_key=idempotency_key,
            )
            if notification and adapter.channel != "windows_desktop":
                notification.delivery_state = NotificationDeliveryState.FAILED
            elif digest and adapter.channel != "windows_desktop":
                digest.delivery_state = NotificationDeliveryState.FAILED
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def _deferred(
        self,
        notification: Notification | None,
        digest: NotificationDigest | None,
        adapter: DeliveryAdapter,
        attempt_number: int,
        now: dt.datetime,
        retry_after: dt.datetime,
        idempotency_key: str,
        reason: str,
    ) -> NotificationDeliveryAttempt:
        attempt = NotificationDeliveryAttempt(
            notification_id=notification.id if notification else None,
            digest_id=digest.id if digest else None,
            channel=adapter.channel,
            adapter_name=adapter.name,
            attempt_number=attempt_number,
            status=DeliveryAttemptStatus.DEFERRED,
            attempted_at=now,
            completed_at=now,
            retry_after=retry_after,
            error_summary=reason,
            idempotency_key=idempotency_key,
        )
        if notification and adapter.channel != "windows_desktop":
            notification.delivery_state = NotificationDeliveryState.DEFERRED
        elif digest and adapter.channel != "windows_desktop":
            digest.delivery_state = NotificationDeliveryState.DEFERRED
        self.session.add(attempt)
        self.session.flush()
        return attempt
