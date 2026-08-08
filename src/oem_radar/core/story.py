"""Story detection — turns per-product change events into cross-OEM narratives
("three makers listed the same unannounced CPU this week"). The platform's
editorial edge (DESIGN_REVIEW §7).

Pure and rule-driven like the diff/severity layers: detect(rows, rules) ->
list[Story]. Same inputs -> same stories; no I/O, no clock beyond event
timestamps. AI (if enabled) only narrates a finished Story; it never decides
one exists. Scoring is additive and fully explained — never fabricated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import StoryRule
from .models import ChangeEvent, Story, StoryEvidence

# The runner supplies (event, manufacturer, model, source_url) per event.
EventRow = tuple[ChangeEvent, str, str, "str | None"]


def _matches(event: ChangeEvent, match: dict) -> bool:
    attrs = {"change_type": event.change_type.value, "field": event.field, **event.meta}
    return all(attrs.get(k) == v for k, v in match.items())


def _group_value(event: ChangeEvent, field: str):
    if field in ("new_value", "old_value"):
        return getattr(event, field)
    if field == "field":
        return event.field
    return event.meta.get(field)


def _hashable(v):
    return tuple(v) if isinstance(v, list) else v


def detect(rows, rules, now: datetime | None = None) -> list[Story]:
    now = now or datetime.now(timezone.utc)
    stories: list[Story] = []

    for rule in rules:
        if not rule.enabled:
            continue
        cutoff = now - timedelta(seconds=rule.window_s)
        groups: dict[object, dict[str, StoryEvidence]] = {}
        for event, manufacturer, model, url in rows:
            det = event.detected_at
            if det.tzinfo is None:
                det = det.replace(tzinfo=timezone.utc)
            if det < cutoff or not _matches(event, rule.match):
                continue
            key = _group_value(event, rule.group_by)
            if key in (None, "", []):
                continue
            ev = StoryEvidence(
                manufacturer=manufacturer, model=model,
                product_key=event.product_key,
                detected_at=det.isoformat(), source_url=url,
            )
            bucket = groups.setdefault(_hashable(key), {})
            if manufacturer not in bucket or ev.detected_at < bucket[manufacturer].detected_at:
                bucket[manufacturer] = ev

        for key, per_oem in groups.items():
            if len(per_oem) < rule.min_distinct_manufacturers:
                continue
            oems = sorted(per_oem)
            evidence = sorted(per_oem.values(), key=lambda e: e.detected_at)
            extra = len(oems) - rule.min_distinct_manufacturers
            score = min(100, rule.base_score + extra * rule.per_extra_oem)
            reasons = [
                f"{len(oems)} distinct OEMs ({', '.join(oems)})",
                f"within {rule.window}",
                f"base {rule.base_score}"
                + (f" +{extra * rule.per_extra_oem} for {extra} extra OEM(s)" if extra else ""),
            ]
            stories.append(Story(
                rule_id=rule.id, key=str(key),
                title=rule.title.format(n=len(oems), key=key, oems=", ".join(oems)),
                score=score, score_reasons=reasons,
                manufacturers=oems, evidence=evidence,
            ))
    stories.sort(key=lambda s: s.score, reverse=True)
    return stories
