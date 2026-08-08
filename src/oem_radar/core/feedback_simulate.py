"""Counterfactual simulation of proposed rules against historical alerts.

Does not modify collectors or notifications. Classification is advisory only.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .feedback import FeedbackError
from .feedback_analytics import _parse_ts, _rate
from .feedback_analyze import _load_reviewed_rows, _urls_differ_only_by_query


def _load_rule(store_or_conn, rule_id: int) -> dict[str, Any]:
    if hasattr(store_or_conn, "db"):
        conn = store_or_conn.db
    else:
        conn = store_or_conn
    row = conn.execute("SELECT * FROM rule_suggestions WHERE id=?", (rule_id,)).fetchone()
    if row is None:
        raise FeedbackError(f"no rule_suggestion with id={rule_id}")
    d = dict(row)
    if d.get("rule_json"):
        try:
            d["rule"] = json.loads(d["rule_json"])
        except json.JSONDecodeError:
            d["rule"] = None
    else:
        d["rule"] = None
    return d


def _match_rule(row: dict, rule: dict) -> bool:
    if not rule:
        return False
    cond = rule.get("conditions") or {}
    if cond.get("collector") and row["collector"] != cond["collector"]:
        return False
    if cond.get("alert_type") and row["alert_type"] != cond["alert_type"]:
        return False
    rtype = rule.get("rule_type")
    params = rule.get("parameters") or {}

    if rtype == "require_consecutive_missing":
        return row["alert_type"] in ("product_removed", "availability_changed")
    if rtype == "suppress_exact_duplicate":
        return True  # matched by collector/type; simulation counts all such reviewed
    if rtype == "normalize_image_url":
        if row["alert_type"] != "images_changed":
            return False
        return (
            _urls_differ_only_by_query(row.get("old"), row.get("new"))
            or bool({"CDN_URL_CHURN", "ROUTINE_IMAGE_CHANGE", "MARKETING_ASSET_REFRESH"}
                    & set(row.get("reasons") or []))
            or True  # collector+type match is enough for retrospective estimate
        )
    if rtype == "compare_content_hash":
        meta = row.get("meta") or {}
        return (
            "identical_content_hash" in meta
            or (
                meta.get("content_hash_before") is not None
                and meta.get("content_hash_before") == meta.get("content_hash_after")
            )
        )
    if rtype == "minimum_price_change_percent":
        if row["alert_type"] != "price_changed":
            return False
        mag = (row.get("meta") or {}).get("magnitude_pct")
        try:
            return mag is not None and float(mag) < float(params.get("minimum_pct", 5))
        except (TypeError, ValueError):
            return False
    if rtype == "compare_normalized_specs":
        return row["alert_type"] == "spec_changed"
    return False


def simulate_rule(
    conn: sqlite3.Connection,
    *,
    rule_id: int | None = None,
    rule: dict | None = None,
    start: str | None = None,
    end: str | None = None,
    min_samples: int = 10,
    max_signal_loss_ratio: float = 0.05,
    min_review_coverage: float = 0.5,
) -> dict[str, Any]:
    start = _parse_ts(start, "start")
    end = _parse_ts(end, "end")

    if rule is None:
        if rule_id is None:
            raise FeedbackError("rule_id or rule required")
        row = conn.execute("SELECT * FROM rule_suggestions WHERE id=?", (rule_id,)).fetchone()
        if row is None:
            raise FeedbackError(f"no rule_suggestion with id={rule_id}")
        rule = json.loads(row["rule_json"]) if row["rule_json"] else None
        suggestion = dict(row)
    else:
        suggestion = None

    if not rule:
        raise FeedbackError("suggestion has no structured rule_json")

    # Evaluate against all events in range (reviewed + unreviewed) for match count;
    # outcomes only for reviewed matches.
    where = ["1=1"]
    params: list[Any] = []
    if start:
        where.append("e.detected_at >= ?")
        params.append(start)
    if end:
        where.append("e.detected_at < ?")
        params.append(end)
    cond = rule.get("conditions") or {}
    if cond.get("alert_type"):
        where.append("e.change_type = ?")
        params.append(cond["alert_type"])

    sql = (
        "SELECT e.id, e.product_key, e.change_type, e.field, e.old_value_json, "
        "e.new_value_json, e.meta_json, e.detected_at, r.outcome, r.reason_codes_json "
        "FROM change_events e LEFT JOIN alert_reviews r ON r.alert_id = e.id "
        f"WHERE {' AND '.join(where)}"
    )
    matched = []
    for r in conn.execute(sql, params).fetchall():
        pk = r["product_key"] or ""
        coll = pk.split(":", 1)[0] if ":" in pk else pk
        if cond.get("collector") and coll != cond["collector"]:
            continue
        try:
            reasons = json.loads(r["reason_codes_json"] or "[]") if r["reason_codes_json"] else []
        except Exception:
            reasons = []
        try:
            old = json.loads(r["old_value_json"]) if r["old_value_json"] else None
        except Exception:
            old = None
        try:
            new = json.loads(r["new_value_json"]) if r["new_value_json"] else None
        except Exception:
            new = None
        try:
            meta = json.loads(r["meta_json"] or "{}")
        except Exception:
            meta = {}
        rowd = {
            "id": r["id"], "product_key": pk, "collector": coll,
            "alert_type": r["change_type"], "field": r["field"],
            "old": old, "new": new, "meta": meta, "detected_at": r["detected_at"],
            "outcome": r["outcome"], "reasons": reasons,
        }
        if _match_rule(rowd, rule):
            matched.append(rowd)

    reviewed = [m for m in matched if m["outcome"]]
    unreviewed = [m for m in matched if not m["outcome"]]
    dist = {"HIT": 0, "INTERESTING": 0, "NOISE": 0, "BUG": 0}
    for m in reviewed:
        if m["outcome"] in dist:
            dist[m["outcome"]] += 1

    signal = dist["HIT"] + dist["INTERESTING"]
    noise = dist["NOISE"]
    n_rev = len(reviewed)
    coverage = _rate(n_rev, len(matched)) if matched else None

    # Precision/recall: treat signal as positive class among reviewed matched.
    # Before: all matched reviewed would have been "alerted".
    # After: suppress matched → remaining non-matched signal is outside scope;
    # we report matched-only counters and estimated rates among matched reviewed.
    precision_before = _rate(signal, n_rev)
    # After suppression of all matched: precision of remaining system unknown → null
    precision_after = None
    recall_before = 1.0 if signal else None  # among matched signal, all were emitted
    recall_after = 0.0 if signal else None

    noise_reduction = _rate(noise, n_rev)
    signal_loss = _rate(signal, n_rev)
    hit_loss = _rate(dist["HIT"], n_rev)
    interesting_loss = _rate(dist["INTERESTING"], n_rev)

    warnings = []
    if coverage is None or coverage < min_review_coverage:
        warnings.append("low_review_coverage")
    if n_rev < min_samples:
        warnings.append("below_minimum_sample")

    if n_rev < min_samples or coverage is None or coverage < min_review_coverage:
        assessment = "INSUFFICIENT_EVIDENCE"
    elif signal_loss is not None and signal_loss > max_signal_loss_ratio:
        assessment = "RISKY"
    else:
        assessment = "SAFE_CANDIDATE"

    return {
        "rule_id": rule_id,
        "rule": rule,
        "suggestion": (
            {k: suggestion[k] for k in suggestion.keys()} if suggestion else None
        ),
        "total_matched": len(matched),
        "reviewed_matched": n_rev,
        "unreviewed_matched": len(unreviewed),
        "noise_affected": dist["NOISE"],
        "bug_affected": dist["BUG"],
        "hit_affected": dist["HIT"],
        "interesting_affected": dist["INTERESTING"],
        "signal_affected": signal,
        "estimated_noise_reduction": noise_reduction,
        "estimated_signal_loss": signal_loss,
        "estimated_hit_loss": hit_loss,
        "estimated_interesting_loss": interesting_loss,
        "precision_before": precision_before,
        "precision_after": precision_after,
        "recall_before": recall_before,
        "recall_after": recall_after,
        "review_coverage": coverage,
        "assessment": assessment,
        "warnings": warnings,
        "filters": {"start": start, "end": end},
    }
