"""Semantic diff over normalized snapshots + rules-driven severity (ADR-6).

Pure: no I/O, no clock beyond event timestamps, same inputs → same events.
That purity is what makes replay and rule-tuning-by-regression possible
(DESIGN_REVIEW §8) — do not compromise it.

Severity rules support comparison operators on numeric attrs:
  {match: {change_type: price_changed, magnitude_pct: ">10"}, severity: 3}
and direction matching on numeric spec fields:
  {match: {change_type: spec_changed, field: memory, direction: up}, severity: 4}
"""

from __future__ import annotations

import re

from .config import SeverityRule
from .models import ChangeEvent, ChangeType, Component, NormalizedProduct, Severity

DEFAULT_RULES: list[SeverityRule] = [
    SeverityRule(match={"change_type": "component_changed", "unseen_component": True}, severity=5),
    SeverityRule(match={"change_type": "new_product"}, severity=5),
    SeverityRule(match={"change_type": "spec_changed", "field": "memory"}, severity=4),
    SeverityRule(match={"change_type": "spec_changed", "field": "display"}, severity=4),
    SeverityRule(match={"change_type": "spec_changed", "field": "battery"}, severity=4),
    SeverityRule(match={"change_type": "component_changed"}, severity=4),
    SeverityRule(match={"change_type": "spec_changed", "field": "configurations"}, severity=3),
    SeverityRule(match={"change_type": "price_changed"}, severity=3),
    SeverityRule(match={"change_type": "images_changed"}, severity=3),
    SeverityRule(match={"change_type": "availability_changed"}, severity=3),
    SeverityRule(match={"change_type": "description_changed"}, severity=2),
    SeverityRule(match={}, severity=2),
]

_COMPONENT_FIELDS = ("cpu", "gpu", "npu")
_SPEC_FIELDS = ("memory", "storage", "display", "battery", "wireless", "operating_system")
_OP_RE = re.compile(r"^(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)$")
_SIZE_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(TB|GB|MB|WH|MAH)?", re.IGNORECASE)
_UNIT_FACTOR = {"tb": 1024.0, "gb": 1.0, "mb": 1.0 / 1024, "wh": 1.0, "mah": 1.0, None: 1.0}


def _numeric(value) -> float | None:
    """'96 GB' → 96, '1 TB' → 1024, '65 Wh' → 65, 42 → 42.0."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    m = _SIZE_NUM_RE.search(value)
    if not m:
        return None
    unit = (m.group(2) or "").lower() or None
    return float(m.group(1)) * _UNIT_FACTOR.get(unit, 1.0)


def _match_value(rule_val, actual) -> bool:
    if isinstance(rule_val, str):
        op = _OP_RE.match(rule_val.strip())
        if op:
            actual_n = _numeric(actual)
            if actual_n is None:
                return False
            threshold = float(op.group(2))
            return {"<": actual_n < threshold, ">": actual_n > threshold,
                    ">=": actual_n >= threshold, "<=": actual_n <= threshold}[op.group(1)]
    return actual == rule_val


def _event_attrs(event: ChangeEvent) -> dict:
    return {"change_type": event.change_type.value, "field": event.field, **event.meta}


def score(event: ChangeEvent, rules: list[SeverityRule] | None = None) -> Severity:
    """First matching rule wins; rules subset-match on event attrs, with
    comparison-operator support for numeric values."""
    attrs = _event_attrs(event)
    for rule in rules or DEFAULT_RULES:
        if all(_match_value(v, attrs.get(k)) for k, v in rule.match.items()):
            return Severity(rule.severity)
    return Severity.MINOR  # unreachable if rules end with a catch-all


def _component_value(c: Component | None) -> str | None:
    if c is None:
        return None
    return c.canonical or c.raw


def _canon_img(url: str) -> str:
    return url.split("?", 1)[0]  # defense in depth; engines should pre-strip


def _direction_meta(old, new) -> dict:
    o, n = _numeric(old), _numeric(new)
    if o is None or n is None or o == n:
        return {}
    return {"direction": "up" if n > o else "down"}


def diff(
    before: NormalizedProduct | None,
    after: NormalizedProduct,
    product_key: str,
    rules: list[SeverityRule] | None = None,
) -> list[ChangeEvent]:
    events: list[ChangeEvent] = []

    if before is None:
        events.append(
            ChangeEvent(product_key=product_key, change_type=ChangeType.NEW_PRODUCT,
                        new_value=after.model, meta={"hidden": False})
        )
    else:
        for f in _COMPONENT_FIELDS:
            old, new = _component_value(getattr(before, f)), _component_value(getattr(after, f))
            if old != new and new is not None:
                after_comp = getattr(after, f)
                events.append(ChangeEvent(
                    product_key=product_key, change_type=ChangeType.COMPONENT_CHANGED,
                    field=f, old_value=old, new_value=new,
                    # known=False → confirmed unseen; known=None → couldn't
                    # canonicalize (renderer caveats, doesn't max out severity)
                    meta={"unseen_component": after_comp.known is False},
                ))
        for f in _SPEC_FIELDS:
            old, new = getattr(before, f), getattr(after, f)
            if old != new:
                events.append(ChangeEvent(
                    product_key=product_key, change_type=ChangeType.SPEC_CHANGED,
                    field=f, old_value=old, new_value=new,
                    meta=_direction_meta(old, new),
                ))

        # Configurations: skip entirely when `before` predates the variant
        # model (empty configs) — that's a schema migration boundary, not a
        # product change (DESIGN_REVIEW §4).
        if before.configurations and \
           {c.key() for c in before.configurations} != {c.key() for c in after.configurations}:
            b_keys = {c.key() for c in before.configurations}
            a_keys = {c.key() for c in after.configurations}
            events.append(ChangeEvent(
                product_key=product_key, change_type=ChangeType.SPEC_CHANGED,
                field="configurations",
                old_value=sorted(b_keys), new_value=sorted(a_keys),
                meta={"added": sorted(a_keys - b_keys), "removed": sorted(b_keys - a_keys)},
            ))

        b_price_set = {(p.currency, p.region, p.amount) for p in before.prices}
        a_price_set = {(p.currency, p.region, p.amount) for p in after.prices}
        b_prices = {(p.currency, p.region): p.amount for p in before.prices}
        a_prices = {(p.currency, p.region): p.amount for p in after.prices}
        if b_price_set != a_price_set:  # set compare: never misses multi-config changes
            pct = 0.0
            for k, new_amt in a_prices.items():
                old_amt = b_prices.get(k)
                if old_amt:
                    pct = max(pct, abs(new_amt - old_amt) / old_amt * 100)
            meta = {"magnitude_pct": round(pct, 1)} if pct else {}
            common = [k for k in a_prices if k in b_prices and a_prices[k] != b_prices[k]]
            if common:
                meta["direction"] = "up" if a_prices[common[0]] > b_prices[common[0]] else "down"
            events.append(ChangeEvent(
                product_key=product_key, change_type=ChangeType.PRICE_CHANGED,
                field="prices",
                old_value=[p.model_dump() for p in before.prices],
                new_value=[p.model_dump() for p in after.prices],
                meta=meta,
            ))

        if {_canon_img(u) for u in before.images} != {_canon_img(u) for u in after.images}:
            events.append(ChangeEvent(
                product_key=product_key, change_type=ChangeType.IMAGES_CHANGED, field="images",
            ))
        if (before.description or "").split() != (after.description or "").split():
            events.append(ChangeEvent(
                product_key=product_key, change_type=ChangeType.DESCRIPTION_CHANGED,
                field="description",
            ))

    for e in events:
        e.severity = score(e, rules)
    return events
