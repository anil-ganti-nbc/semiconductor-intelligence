"""Discord webhook notifier (M6). Outbox semantics: enqueue() persists the
fully rendered embed in the notifications table inside the crawl
transaction; drain() posts pending rows and marks sent/failed. A dead
Discord or a killed run loses nothing; dedup keys prevent doubles (ADR-1).
"""

from __future__ import annotations

import logging
from typing import Callable

import requests

from ...core.models import ChangeEvent, ChangeType, NormalizedProduct, Severity
from ...core.registry import notifiers

log = logging.getLogger("oem_radar.discord")

_COLORS = {
    Severity.BREAKING: 0x2ECC71,     # green — the good news color
    Severity.SIGNIFICANT: 0xE67E22,  # orange
    Severity.NOTABLE: 0x3498DB,      # blue
    Severity.MINOR: 0x95A5A6,        # grey
    Severity.NOISE: 0x95A5A6,
}
_TITLES = {
    ChangeType.NEW_PRODUCT: "🟢 NEW PRODUCT",
    ChangeType.COMPONENT_CHANGED: "🔧 COMPONENT CHANGED",
    ChangeType.SPEC_CHANGED: "📈 SPEC CHANGED",
    ChangeType.PRICE_CHANGED: "💰 PRICE CHANGED",
    ChangeType.AVAILABILITY_CHANGED: "📦 AVAILABILITY",
    ChangeType.IMAGES_CHANGED: "🖼️ NEW IMAGES",
    ChangeType.DESCRIPTION_CHANGED: "📝 DESCRIPTION",
    ChangeType.PRODUCT_REMOVED: "🔴 PRODUCT REMOVED",
    ChangeType.DUPLICATE_LISTING: "👯 DUPLICATE LISTING",
    ChangeType.REGIONAL_VARIANT: "🌍 REGIONAL VARIANT",
}


def stars(sev: int) -> str:
    return "★" * sev + "☆" * (5 - sev)


def build_embed(
    event: ChangeEvent,
    product: NormalizedProduct | None,
    *,
    event_id: int | None = None,
    review_base_url: str | None = None,
) -> dict:
    title = _TITLES.get(event.change_type, event.change_type.value.upper())
    fields = []
    if product:
        title += f" — {product.manufacturer} {product.model}"
        if product.cpu:
            cpu_label = product.cpu.raw
            if product.cpu.known is False:
                cpu_label += "  ⚠️ previously unseen"
            elif product.cpu.known is None:
                cpu_label += "  (unrecognized string)"
            fields.append({"name": "CPU", "value": cpu_label, "inline": True})
        if product.gpu:
            fields.append({"name": "GPU", "value": product.gpu.raw, "inline": True})
        if product.memory:
            fields.append({"name": "Memory", "value": product.memory, "inline": True})
        if product.storage:
            fields.append({"name": "Storage", "value": product.storage, "inline": True})
        if product.prices:
            p = product.prices[0]
            fields.append({"name": "Price", "value": f"{p.amount:g} {p.currency}", "inline": True})
    if event.field and event.old_value is not None:
        fields.append({"name": f"Changed: {event.field}",
                       "value": f"{event.old_value} → {event.new_value}"[:1024]})
    fields.append({"name": "Severity", "value": stars(int(event.severity)), "inline": True})
    if event.meta.get("hidden"):
        fields.append({"name": "Discovery", "value": "hidden listing (sitemap-only)", "inline": True})
    if product and product.confidence < 0.8:
        fields.append({"name": "Parse confidence",
                       "value": f"{product.confidence:.0%} — verify before publishing", "inline": True})

    # Feedback deep-link: Alert ID + review URL (when configured).
    footer_parts = ["OEM Radar"]
    if event_id is not None:
        footer_parts.append(f"Alert ID: {event_id}")
        if review_base_url:
            base = review_base_url.rstrip("/")
            footer_parts.append(f"Review: {base}/alerts/{event_id}")
    # Collector / alert type for triage without opening the dashboard.
    source_key = (event.product_key.split(":", 1)[0] if event.product_key else None)
    if source_key:
        fields.append({"name": "Collector", "value": source_key, "inline": True})
    fields.append({"name": "Alert type", "value": event.change_type.value, "inline": True})
    if product is not None and product.confidence is not None:
        fields.append({"name": "Confidence", "value": f"{product.confidence:.0%}", "inline": True})

    embed = {
        "title": title[:256],
        "color": _COLORS.get(event.severity, 0x95A5A6),
        "fields": fields[:25],
        "timestamp": event.detected_at.isoformat(),
        "footer": {"text": " · ".join(footer_parts)[:2048]},
    }
    if product:
        embed["url"] = product.source_url
        if product.images:
            embed["thumbnail"] = {"url": product.images[0]}
    return {"embeds": [embed]}


def build_story_embed(story) -> dict:
    """Rich embed for a cross-OEM story: headline, explainable score, and the
    evidence list (which OEM listed it, linked)."""
    lines = []
    for e in story.evidence:
        who = f"**{e['manufacturer'] if isinstance(e, dict) else e.manufacturer}**"
        model = e['model'] if isinstance(e, dict) else e.model
        url = (e.get('source_url') if isinstance(e, dict) else e.source_url) or ""
        label = f"{who}: {model}"
        lines.append(f"[{label}]({url})" if url else label)
    reasons = story.score_reasons if not isinstance(story, dict) else story.get("score_reasons", [])
    embed = {
        "title": ("\U0001F4F0 STORY  \u2014  " + story.title)[:256],
        "color": 0x9B59B6,  # purple = correlated story, distinct from single events
        "fields": [
            {"name": "Newsworthiness", "value": f"{story.score}/100", "inline": True},
            {"name": "Why", "value": " \u00b7 ".join(reasons)[:1024]},
            {"name": "Evidence", "value": "\n".join(lines)[:1024]},
        ],
        "footer": {"text": "OEM Radar \u2014 cross-OEM story"},
    }
    return {"embeds": [embed]}


def _post_webhook(webhook_url: str, payload: dict) -> tuple[bool, str | None]:
    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        if 200 <= resp.status_code < 300:
            return True, None
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except requests.RequestException as exc:
        return False, repr(exc)


@notifiers.register("discord")
class DiscordNotifier:
    def __init__(
        self,
        store,  # needs outbox_put/outbox_pending/outbox_mark/record_event
        webhook_url: str | None,
        min_severity: int = 3,
        sender: Callable[[str, dict], tuple[bool, str | None]] = _post_webhook,
        review_base_url: str | None = "http://127.0.0.1:8787",
        feedback_enabled: bool = True,
    ) -> None:
        self.store = store
        self.webhook_url = webhook_url
        self.min_severity = min_severity
        self.sender = sender
        self.review_base_url = review_base_url
        self.feedback_enabled = feedback_enabled

    def enqueue(self, event: ChangeEvent, product: NormalizedProduct | None = None) -> None:
        event_id = self.store.record_event(event)  # full audit trail regardless
        if event.meta.get("baseline"):
            status = "suppressed"  # first-ever crawl of a source: history, not news
        else:
            status = "pending" if int(event.severity) >= self.min_severity else "suppressed"
        review_url = self.review_base_url if self.feedback_enabled else None
        payload = build_embed(
            event, product, event_id=event_id, review_base_url=review_url,
        )
        self.store.outbox_put(
            "discord", event.dedup_key(), payload,
            event_id=event_id, status=status,
        )

    def enqueue_story(self, story) -> None:
        """Stories always notify (they're the high-signal product); they bypass
        the per-event severity threshold. Dedup by story dedup_key."""
        self.store.outbox_put(
            "discord", "story:" + story.dedup_key(), build_story_embed(story),
            event_id=None, status="pending",
        )

    def drain(self, sleep=None) -> int:
        import time as _time
        sleep = sleep or _time.sleep
        if not self.webhook_url:
            pending = self.store.outbox_pending("discord")
            if pending:
                log.warning("%d notification(s) pending but no webhook configured "
                            "(set the env var from radar.yaml notify.discord.webhook_url_env)",
                            len(pending))
            return 0
        sent = 0
        rows = self.store.outbox_pending("discord")
        for i, row in enumerate(rows):
            import json as _json
            ok, err = self.sender(self.webhook_url, _json.loads(row["payload_json"]))
            if ok:
                self.store.outbox_mark(row["id"], "sent")
                sent += 1
                if i < len(rows) - 1:
                    sleep(1.5)  # webhook rate limit is ~30/min; pace ourselves
            elif err and "429" in err:
                # Rate limited: leave the rest pending (no attempt burned),
                # they'll drain next run. Never fight Discord.
                self.store.outbox_mark(row["id"], "pending", err)
                log.warning("discord rate limit hit after %d send(s); "
                            "%d left in outbox for next run", sent, len(rows) - i)
                break
            else:
                status = "pending" if row["attempts"] < 5 else "failed"
                self.store.outbox_mark(row["id"], status, err)
                log.warning("discord send failed (%s): %s", status, err)
        return sent


@notifiers.register("console")
class ConsoleNotifier:
    """Dry-run/dev notifier: prints instead of posting."""

    def __init__(self, min_severity: int = 1) -> None:
        self.min_severity = min_severity
        self.events: list[ChangeEvent] = []

    def enqueue(self, event: ChangeEvent, product: NormalizedProduct | None = None) -> None:
        self.events.append(event)
        if int(event.severity) >= self.min_severity:
            name = f"{product.manufacturer} {product.model}" if product else event.product_key
            print(f"  [{stars(int(event.severity))}] {event.change_type.value}: {name}"
                  + (f" | {event.field}: {event.old_value} → {event.new_value}"
                     if event.field and event.old_value is not None else ""))

    def enqueue_story(self, story) -> None:
        print(f"  [STORY {story.score}/100] {story.title}")

    def drain(self) -> int:
        return 0
