"""Deterministic feedback analytics over change_events + alert_reviews.

Signal = HIT + INTERESTING. BUG is never counted as NOISE.
SNR = signal_count / noise_count when noise_count > 0; otherwise null
(with signal_to_noise_infinite=True when signal>0 and noise==0).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from .feedback import FeedbackError

GROUP_BY_ALLOWLIST = frozenset({
    "oem", "collector", "alert_type", "reason_code", "day", "week", "confidence",
})

CONFIDENCE_BUCKETS = (
    ("0.00-0.19", 0.0, 0.2),
    ("0.20-0.39", 0.2, 0.4),
    ("0.40-0.59", 0.4, 0.6),
    ("0.60-0.79", 0.6, 0.8),
    ("0.80-1.00", 0.8, 1.01),
)


def _parse_ts(value: str | None, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise FeedbackError(f"{field} must be an ISO-8601 string")
    try:
        # Accept date or datetime; store as string for SQL comparison.
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise FeedbackError(f"invalid {field}: {value!r}") from e
    return value


def _rate(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return round(num / den, 4)


def _snr(signal: int, noise: int) -> tuple[float | None, bool]:
    if noise > 0:
        return round(signal / noise, 4), False
    if signal > 0:
        return None, True
    return None, False


def _confidence_bucket(score: float | None) -> str:
    if score is None:
        return "unknown"
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "unknown"
    for label, lo, hi in CONFIDENCE_BUCKETS:
        if lo <= s < hi:
            return label
    if s >= 1.0:
        return "0.80-1.00"
    return "unknown"


def compute_summary(conn: sqlite3.Connection, start: str | None = None,
                    end: str | None = None) -> dict[str, Any]:
    start = _parse_ts(start, "start")
    end = _parse_ts(end, "end")
    where = []
    params: list[Any] = []
    if start:
        where.append("e.detected_at >= ?")
        params.append(start)
    if end:
        where.append("e.detected_at < ?")
        params.append(end)
    wsql = (" WHERE " + " AND ".join(where)) if where else ""

    total = conn.execute(f"SELECT COUNT(*) FROM change_events e{wsql}", params).fetchone()[0]
    reviewed = conn.execute(
        f"SELECT COUNT(*) FROM change_events e "
        f"JOIN alert_reviews r ON r.alert_id = e.id{wsql}",
        params,
    ).fetchone()[0]
    unreviewed = total - reviewed

    counts = {"HIT": 0, "INTERESTING": 0, "NOISE": 0, "BUG": 0}
    rows = conn.execute(
        f"SELECT r.outcome, COUNT(*) c FROM change_events e "
        f"JOIN alert_reviews r ON r.alert_id = e.id{wsql} "
        f"GROUP BY r.outcome",
        params,
    ).fetchall()
    for r in rows:
        if r[0] in counts:
            counts[r[0]] = r[1]

    signal = counts["HIT"] + counts["INTERESTING"]
    noise = counts["NOISE"]
    snr, snr_inf = _snr(signal, noise)

    return {
        "total_alerts": total,
        "reviewed_alerts": reviewed,
        "unreviewed_alerts": unreviewed,
        "review_completion_rate": _rate(reviewed, total),
        "hit_count": counts["HIT"],
        "interesting_count": counts["INTERESTING"],
        "noise_count": noise,
        "bug_count": counts["BUG"],
        "signal_count": signal,
        "signal_rate": _rate(signal, reviewed),
        "noise_rate": _rate(noise, reviewed),
        "bug_rate": _rate(counts["BUG"], reviewed),
        "hit_rate": _rate(counts["HIT"], reviewed),
        "interesting_rate": _rate(counts["INTERESTING"], reviewed),
        "signal_to_noise_ratio": snr,
        "signal_to_noise_infinite": snr_inf,
    }


def _dim_key_sql(group_by: str) -> str:
    if group_by == "collector":
        return "CASE WHEN instr(e.product_key, ':') > 0 THEN substr(e.product_key, 1, instr(e.product_key, ':') - 1) ELSE e.product_key END"
    if group_by == "alert_type":
        return "e.change_type"
    if group_by == "day":
        return "substr(e.detected_at, 1, 10)"
    if group_by == "week":
        # ISO-ish: YYYY-Www via strftime
        return "strftime('%Y-W%W', e.detected_at)"
    if group_by == "oem":
        # manufacturer from latest snapshot is expensive; approximate via product_key prefix OEM when available
        # Prefer join via listings/products when present.
        return (
            "COALESCE(("
            "SELECT m.name FROM listings l "
            "JOIN products p ON p.id = l.product_id "
            "JOIN manufacturers m ON m.id = p.manufacturer_id "
            "WHERE l.product_key = e.product_key LIMIT 1"
            "), CASE WHEN instr(e.product_key, ':') > 0 THEN substr(e.product_key, 1, instr(e.product_key, ':') - 1) ELSE e.product_key END)"
        )
    if group_by == "reason_code":
        return "r.reason_codes_json"  # expanded in Python
    if group_by == "confidence":
        return "e.id"  # resolved in Python via snapshot confidence
    raise FeedbackError(f"unsupported group_by: {group_by}")


def compute_breakdown(
    conn: sqlite3.Connection,
    group_by: str,
    start: str | None = None,
    end: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if group_by not in GROUP_BY_ALLOWLIST:
        raise FeedbackError(f"unsupported group_by: {group_by}")
    start = _parse_ts(start, "start")
    end = _parse_ts(end, "end")
    where = []
    params: list[Any] = []
    if start:
        where.append("e.detected_at >= ?")
        params.append(start)
    if end:
        where.append("e.detected_at < ?")
        params.append(end)
    wsql = (" WHERE " + " AND ".join(where)) if where else ""

    if group_by == "reason_code":
        return _breakdown_reason_codes(conn, wsql, params, limit)
    if group_by == "confidence":
        return _breakdown_confidence(conn, wsql, params, limit)

    key_sql = _dim_key_sql(group_by)
    sql = (
        f"SELECT ({key_sql}) AS dim, "
        f"COUNT(*) AS total, "
        f"SUM(CASE WHEN r.id IS NOT NULL THEN 1 ELSE 0 END) AS reviewed, "
        f"SUM(CASE WHEN r.outcome='HIT' THEN 1 ELSE 0 END) AS hit, "
        f"SUM(CASE WHEN r.outcome='INTERESTING' THEN 1 ELSE 0 END) AS interesting, "
        f"SUM(CASE WHEN r.outcome='NOISE' THEN 1 ELSE 0 END) AS noise, "
        f"SUM(CASE WHEN r.outcome='BUG' THEN 1 ELSE 0 END) AS bug "
        f"FROM change_events e "
        f"LEFT JOIN alert_reviews r ON r.alert_id = e.id"
        f"{wsql} "
        f"GROUP BY dim ORDER BY total DESC LIMIT ?"
    )
    params2 = list(params) + [limit]
    rows = conn.execute(sql, params2).fetchall()
    out = []
    for r in rows:
        reviewed = r["reviewed"] or 0
        hit = r["hit"] or 0
        interesting = r["interesting"] or 0
        noise = r["noise"] or 0
        bug = r["bug"] or 0
        signal = hit + interesting
        out.append({
            "key": r["dim"],
            "total_alerts": r["total"],
            "reviewed": reviewed,
            "hit": hit,
            "interesting": interesting,
            "noise": noise,
            "bug": bug,
            "signal_rate": _rate(signal, reviewed),
            "noise_rate": _rate(noise, reviewed),
            "bug_rate": _rate(bug, reviewed),
        })
    return out


def _breakdown_reason_codes(conn, wsql, params, limit) -> list[dict[str, Any]]:
    from .feedback import reasons_from_json
    rows = conn.execute(
        f"SELECT r.reason_codes_json, r.outcome FROM change_events e "
        f"JOIN alert_reviews r ON r.alert_id = e.id{wsql}",
        params,
    ).fetchall()
    agg: dict[str, dict[str, int]] = {}
    for r in rows:
        try:
            codes = reasons_from_json(r["reason_codes_json"])
        except Exception:
            codes = []
        if not codes:
            codes = ["(none)"]
        for c in codes:
            slot = agg.setdefault(c, {"total": 0, "HIT": 0, "INTERESTING": 0, "NOISE": 0, "BUG": 0})
            slot["total"] += 1
            if r["outcome"] in slot:
                slot[r["outcome"]] += 1
    items = sorted(agg.items(), key=lambda kv: kv[1]["total"], reverse=True)[:limit]
    out = []
    for key, s in items:
        reviewed = s["total"]
        signal = s["HIT"] + s["INTERESTING"]
        out.append({
            "key": key,
            "total_alerts": reviewed,
            "reviewed": reviewed,
            "hit": s["HIT"],
            "interesting": s["INTERESTING"],
            "noise": s["NOISE"],
            "bug": s["BUG"],
            "signal_rate": _rate(signal, reviewed),
            "noise_rate": _rate(s["NOISE"], reviewed),
            "bug_rate": _rate(s["BUG"], reviewed),
        })
    return out


def _breakdown_confidence(conn, wsql, params, limit) -> list[dict[str, Any]]:
    # Join latest snapshot confidence when available; else unknown.
    rows = conn.execute(
        f"SELECT e.id, r.outcome, "
        f"(SELECT s.confidence FROM snapshots s "
        f" JOIN listings l ON l.id = s.listing_id "
        f" WHERE l.product_key = e.product_key ORDER BY s.id DESC LIMIT 1) AS conf "
        f"FROM change_events e "
        f"LEFT JOIN alert_reviews r ON r.alert_id = e.id{wsql}",
        params,
    ).fetchall()
    buckets: dict[str, dict[str, int]] = {}
    for r in rows:
        b = _confidence_bucket(r["conf"])
        slot = buckets.setdefault(b, {"total": 0, "reviewed": 0, "HIT": 0, "INTERESTING": 0, "NOISE": 0, "BUG": 0})
        slot["total"] += 1
        if r["outcome"]:
            slot["reviewed"] += 1
            if r["outcome"] in slot:
                slot[r["outcome"]] += 1
    out = []
    for key, s in buckets.items():
        reviewed = s["reviewed"]
        signal = s["HIT"] + s["INTERESTING"]
        out.append({
            "key": key,
            "total_alerts": s["total"],
            "reviewed": reviewed,
            "hit": s["HIT"],
            "interesting": s["INTERESTING"],
            "noise": s["NOISE"],
            "bug": s["BUG"],
            "signal_rate": _rate(signal, reviewed),
            "noise_rate": _rate(s["NOISE"], reviewed),
            "bug_rate": _rate(s["BUG"], reviewed),
        })
    out.sort(key=lambda x: x["total_alerts"], reverse=True)
    return out[:limit]


def compute_rankings(
    conn: sqlite3.Connection,
    min_sample: int = 5,
    start: str | None = None,
    end: str | None = None,
    limit: int = 10,
) -> dict[str, list[dict[str, Any]]]:
    collectors = compute_breakdown(conn, "collector", start, end, limit=100)
    types = compute_breakdown(conn, "alert_type", start, end, limit=100)
    reasons = compute_breakdown(conn, "reason_code", start, end, limit=50)

    def with_sample(rows, pred, key_rate):
        filtered = [r for r in rows if r["reviewed"] >= min_sample]
        filtered.sort(key=key_rate, reverse=True)
        return filtered[:limit]

    noisiest = with_sample(collectors, None, lambda r: r["noise_rate"] or 0)
    highest_hit = with_sample(collectors, None, lambda r: (r["hit"] / r["reviewed"]) if r["reviewed"] else 0)
    low_value_types = with_sample(types, None, lambda r: r["noise_rate"] or 0)
    high_hit_types = with_sample(types, None, lambda r: (r["hit"] / r["reviewed"]) if r["reviewed"] else 0)

    noise_reasons = [r for r in reasons if r["noise"] > 0]
    noise_reasons.sort(key=lambda r: r["noise"], reverse=True)
    bug_reasons = [r for r in reasons if r["bug"] > 0]
    bug_reasons.sort(key=lambda r: r["bug"], reverse=True)

    # Unreviewed by collector
    start = _parse_ts(start, "start")
    end = _parse_ts(end, "end")
    where = ["r.id IS NULL"]
    params: list[Any] = []
    if start:
        where.append("e.detected_at >= ?")
        params.append(start)
    if end:
        where.append("e.detected_at < ?")
        params.append(end)
    wsql = " WHERE " + " AND ".join(where)
    key_sql = _dim_key_sql("collector")
    unrev_rows = conn.execute(
        f"SELECT ({key_sql}) AS dim, COUNT(*) c FROM change_events e "
        f"LEFT JOIN alert_reviews r ON r.alert_id = e.id{wsql} "
        f"GROUP BY dim ORDER BY c DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    unreviewed_collectors = [{"key": r["dim"], "unreviewed": r["c"]} for r in unrev_rows]

    oldest = conn.execute(
        f"SELECT e.id, e.product_key, e.change_type, e.detected_at FROM change_events e "
        f"LEFT JOIN alert_reviews r ON r.alert_id = e.id "
        f"WHERE r.id IS NULL "
        + (" AND e.detected_at >= ?" if start else "")
        + (" AND e.detected_at < ?" if end else "")
        + " ORDER BY e.detected_at ASC LIMIT ?",
        ([start] if start else []) + ([end] if end else []) + [limit],
    ).fetchall()
    oldest_unreviewed = [
        {"id": r["id"], "product_key": r["product_key"], "type": r["change_type"],
         "detected_at": r["detected_at"]}
        for r in oldest
    ]

    return {
        "noisiest_collectors": noisiest,
        "highest_hit_rate_collectors": highest_hit,
        "most_common_noise_reasons": noise_reasons[:limit],
        "most_common_bug_reasons": bug_reasons[:limit],
        "lowest_value_alert_types": low_value_types,
        "highest_hit_conversion_alert_types": high_hit_types,
        "most_unreviewed_collectors": unreviewed_collectors,
        "oldest_unreviewed_alerts": oldest_unreviewed,
    }


def build_metrics_payload(
    conn: sqlite3.Connection,
    *,
    start: str | None = None,
    end: str | None = None,
    group_by: str | None = None,
    limit: int = 50,
    min_sample: int = 5,
) -> dict[str, Any]:
    summary = compute_summary(conn, start, end)
    breakdown = compute_breakdown(conn, group_by, start, end, limit) if group_by else []
    rankings = compute_rankings(conn, min_sample=min_sample, start=start, end=end, limit=min(limit, 20))
    return {
        "summary": summary,
        "breakdown": breakdown,
        "rankings": rankings,
        "filters": {"start": start, "end": end, "group_by": group_by, "limit": limit},
    }
