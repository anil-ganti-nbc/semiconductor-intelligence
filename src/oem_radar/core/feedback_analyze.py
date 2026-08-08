"""Deterministic offline analysis of reviewed alerts → rule suggestions.

Never activates rules. Produces structured, fingerprintable suggestions.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .feedback import FeedbackError, normalize_reason_codes
from .feedback_analytics import _parse_ts, _rate


RULE_TYPES = frozenset({
    "require_consecutive_missing",
    "suppress_exact_duplicate",
    "normalize_image_url",
    "compare_content_hash",
    "minimum_price_change_percent",
    "compare_normalized_specs",
})


@dataclass
class SuggestionCandidate:
    collector: str
    alert_type: str
    reason_code: str | None
    rule_type: str
    parameters: dict[str, Any]
    explanation: str
    supporting_alert_count: int
    outcome_distribution: dict[str, int]
    estimated_noise_reduction: float
    estimated_hit_loss: float
    estimated_interesting_loss: float
    estimated_signal_loss: float
    sample_start: str | None = None
    sample_end: str | None = None
    matched_alert_ids: list[int] = field(default_factory=list)

    def structured_rule(self) -> dict[str, Any]:
        return {
            "version": 1,
            "rule_type": self.rule_type,
            "conditions": {
                "collector": self.collector,
                "alert_type": self.alert_type,
                "reason_code": self.reason_code,
            },
            "parameters": dict(self.parameters),
        }

    def fingerprint(self) -> str:
        payload = {
            "version": 1,
            "rule_type": self.rule_type,
            "collector": self.collector,
            "alert_type": self.alert_type,
            "reason_code": self.reason_code,
            "parameters": self.parameters,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def human_rule(self) -> str:
        return f"{self.rule_type}: {self.explanation}"


def _load_reviewed_rows(
    conn: sqlite3.Connection,
    *,
    start: str | None = None,
    end: str | None = None,
    collector: str | None = None,
    alert_type: str | None = None,
) -> list[dict[str, Any]]:
    start = _parse_ts(start, "start")
    end = _parse_ts(end, "end")
    where = ["1=1"]
    params: list[Any] = []
    if start:
        where.append("e.detected_at >= ?")
        params.append(start)
    if end:
        where.append("e.detected_at < ?")
        params.append(end)
    if alert_type:
        where.append("e.change_type = ?")
        params.append(alert_type)
    sql = (
        "SELECT e.id, e.product_key, e.change_type, e.field, e.old_value_json, "
        "e.new_value_json, e.meta_json, e.detected_at, r.outcome, r.reason_codes_json "
        "FROM change_events e JOIN alert_reviews r ON r.alert_id = e.id "
        f"WHERE {' AND '.join(where)} ORDER BY e.id"
    )
    rows = []
    for r in conn.execute(sql, params).fetchall():
        pk = r["product_key"] or ""
        coll = pk.split(":", 1)[0] if ":" in pk else pk
        if collector and coll != collector:
            continue
        try:
            reasons = normalize_reason_codes(json.loads(r["reason_codes_json"] or "[]"))
        except Exception:
            reasons = []
        try:
            old = json.loads(r["old_value_json"]) if r["old_value_json"] else None
        except Exception:
            old = r["old_value_json"]
        try:
            new = json.loads(r["new_value_json"]) if r["new_value_json"] else None
        except Exception:
            new = r["new_value_json"]
        try:
            meta = json.loads(r["meta_json"] or "{}")
        except Exception:
            meta = {}
        rows.append({
            "id": r["id"],
            "product_key": pk,
            "collector": coll,
            "alert_type": r["change_type"],
            "field": r["field"],
            "old": old,
            "new": new,
            "meta": meta,
            "detected_at": r["detected_at"],
            "outcome": r["outcome"],
            "reasons": reasons,
        })
    return rows


def _outcome_dist(rows: list[dict]) -> dict[str, int]:
    d = {"HIT": 0, "INTERESTING": 0, "NOISE": 0, "BUG": 0}
    for r in rows:
        if r["outcome"] in d:
            d[r["outcome"]] += 1
    return d


def _metrics(rows: list[dict]) -> tuple[float, float, float, float]:
    dist = _outcome_dist(rows)
    n = len(rows) or 1
    noise_ratio = dist["NOISE"] / n
    hit_loss = dist["HIT"] / n
    int_loss = dist["INTERESTING"] / n
    signal_loss = (dist["HIT"] + dist["INTERESTING"]) / n
    return noise_ratio, hit_loss, int_loss, signal_loss


def _passes_thresholds(
    rows: list[dict],
    *,
    min_samples: int,
    min_noise: float,
    max_signal_loss: float,
) -> bool:
    if len(rows) < min_samples:
        return False
    noise_ratio, _, _, signal_loss = _metrics(rows)
    if noise_ratio < min_noise:
        return False
    if signal_loss > max_signal_loss:
        return False
    return True


def _period(rows: list[dict]) -> tuple[str | None, str | None]:
    if not rows:
        return None, None
    times = [r["detected_at"] for r in rows if r.get("detected_at")]
    if not times:
        return None, None
    return min(times), max(times)


# ---- detectors -------------------------------------------------------------

def detect_temporary_404(
    rows: list[dict], *, min_samples: int, min_noise: float, max_signal_loss: float,
) -> list[SuggestionCandidate]:
    """Removal/missing alerts mostly NOISE with TEMPORARY_404."""
    out = []
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if r["alert_type"] not in ("product_removed", "PRODUCT_REMOVED", "availability_changed"):
            # project uses lowercase ChangeType values
            if r["alert_type"] not in ("product_removed", "availability_changed"):
                continue
        if "TEMPORARY_404" not in r["reasons"] and r["outcome"] == "NOISE":
            # still group removal events; reason preferred but not required if noise-heavy
            pass
        groups[(r["collector"], r["alert_type"])].append(r)

    for (coll, atype), group in groups.items():
        # Prefer rows that cited TEMPORARY_404; fall back to all if enough noise
        focused = [r for r in group if "TEMPORARY_404" in r["reasons"]] or group
        if not _passes_thresholds(focused, min_samples=min_samples, min_noise=min_noise,
                                  max_signal_loss=max_signal_loss):
            # May still be risky — handled by caller wanting all candidates optionally
            noise_ratio, hit_loss, int_loss, signal_loss = _metrics(focused)
            if len(focused) < min_samples or noise_ratio < min_noise:
                continue
            # signal loss too high → still emit with high signal loss for simulator classification
        noise_ratio, hit_loss, int_loss, signal_loss = _metrics(focused)
        if len(focused) < min_samples or noise_ratio < min_noise:
            continue
        start, end = _period(focused)
        out.append(SuggestionCandidate(
            collector=coll,
            alert_type=atype,
            reason_code="TEMPORARY_404",
            rule_type="require_consecutive_missing",
            parameters={"minimum_observations": 2, "minimum_elapsed_hours": 24},
            explanation=(
                f"{sum(1 for r in focused if r['outcome']=='NOISE')} of {len(focused)} reviewed "
                f"{atype} alerts marked NOISE (often TEMPORARY_404). "
                f"Require 2 consecutive missing observations over ≥24h before alerting."
            ),
            supporting_alert_count=len(focused),
            outcome_distribution=_outcome_dist(focused),
            estimated_noise_reduction=noise_ratio,
            estimated_hit_loss=hit_loss,
            estimated_interesting_loss=int_loss,
            estimated_signal_loss=signal_loss,
            sample_start=start,
            sample_end=end,
            matched_alert_ids=[r["id"] for r in focused],
        ))
    return out


def detect_duplicate_alerts(
    rows: list[dict], *, min_samples: int, min_noise: float, max_signal_loss: float,
) -> list[SuggestionCandidate]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if "DUPLICATE_ALERT" not in r["reasons"] and r["outcome"] != "NOISE":
            continue
        groups[(r["collector"], r["alert_type"])].append(r)

    out = []
    for (coll, atype), group in groups.items():
        focused = [r for r in group if "DUPLICATE_ALERT" in r["reasons"]] or [
            r for r in group if r["outcome"] == "NOISE"
        ]
        # Need repeated product keys
        by_pk: dict[str, int] = defaultdict(int)
        for r in focused:
            by_pk[r["product_key"]] += 1
        if sum(1 for c in by_pk.values() if c >= 2) < 1 and len(focused) < min_samples:
            continue
        if len(focused) < min_samples:
            continue
        noise_ratio, hit_loss, int_loss, signal_loss = _metrics(focused)
        if noise_ratio < min_noise:
            continue
        start, end = _period(focused)
        out.append(SuggestionCandidate(
            collector=coll,
            alert_type=atype,
            reason_code="DUPLICATE_ALERT",
            rule_type="suppress_exact_duplicate",
            parameters={"window_hours": 24},
            explanation=(
                f"{len(focused)} reviewed alerts on {coll}/{atype} look like duplicates "
                f"(noise ratio {noise_ratio:.0%}). Suppress exact duplicate payloads within 24h."
            ),
            supporting_alert_count=len(focused),
            outcome_distribution=_outcome_dist(focused),
            estimated_noise_reduction=noise_ratio,
            estimated_hit_loss=hit_loss,
            estimated_interesting_loss=int_loss,
            estimated_signal_loss=signal_loss,
            sample_start=start,
            sample_end=end,
            matched_alert_ids=[r["id"] for r in focused],
        ))
    return out


def _urls_differ_only_by_query(a: Any, b: Any) -> bool:
    if not isinstance(a, list) or not isinstance(b, list):
        return False
    if len(a) != len(b) or not a:
        return False
    def strip(u: str) -> str:
        u = str(u)
        return u.split("?", 1)[0]
    return [strip(x) for x in a] == [strip(x) for x in b] and a != b


def detect_image_cdn_churn(
    rows: list[dict], *, min_samples: int, min_noise: float, max_signal_loss: float,
) -> list[SuggestionCandidate]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if r["alert_type"] != "images_changed":
            continue
        churn_reasons = {"CDN_URL_CHURN", "ROUTINE_IMAGE_CHANGE", "MARKETING_ASSET_REFRESH"}
        if churn_reasons.isdisjoint(r["reasons"]) and not _urls_differ_only_by_query(r["old"], r["new"]):
            continue
        groups[(r["collector"], r["alert_type"])].append(r)

    out = []
    for (coll, atype), focused in groups.items():
        if len(focused) < min_samples:
            continue
        noise_ratio, hit_loss, int_loss, signal_loss = _metrics(focused)
        if noise_ratio < min_noise:
            continue
        start, end = _period(focused)
        primary = "CDN_URL_CHURN"
        for code in ("CDN_URL_CHURN", "ROUTINE_IMAGE_CHANGE", "MARKETING_ASSET_REFRESH"):
            if any(code in r["reasons"] for r in focused):
                primary = code
                break
        out.append(SuggestionCandidate(
            collector=coll,
            alert_type=atype,
            reason_code=primary,
            rule_type="normalize_image_url",
            parameters={"strip_query": True, "normalize_cdn_host": True},
            explanation=(
                f"{sum(1 for r in focused if r['outcome']=='NOISE')} of {len(focused)} image-change "
                f"alerts were NOISE (CDN/query churn). Normalize image URLs before comparison."
            ),
            supporting_alert_count=len(focused),
            outcome_distribution=_outcome_dist(focused),
            estimated_noise_reduction=noise_ratio,
            estimated_hit_loss=hit_loss,
            estimated_interesting_loss=int_loss,
            estimated_signal_loss=signal_loss,
            sample_start=start,
            sample_end=end,
            matched_alert_ids=[r["id"] for r in focused],
        ))
    return out


def detect_unchanged_document(
    rows: list[dict], *, min_samples: int, min_noise: float, max_signal_loss: float,
) -> list[SuggestionCandidate]:
    """Only fires when meta contains content_hash equality evidence."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        reasons = set(r["reasons"])
        if not reasons & {"UNCHANGED_DOCUMENT", "DOCUMENT_METADATA_ONLY"}:
            continue
        meta = r.get("meta") or {}
        # Require explicit hash evidence when present
        if "content_hash_before" in meta and "content_hash_after" in meta:
            if meta["content_hash_before"] != meta["content_hash_after"]:
                continue
        elif "identical_content_hash" not in meta:
            # Skip — insufficient structured evidence in payload
            continue
        groups[(r["collector"], r["alert_type"])].append(r)

    out = []
    for (coll, atype), focused in groups.items():
        if len(focused) < min_samples:
            continue
        noise_ratio, hit_loss, int_loss, signal_loss = _metrics(focused)
        if noise_ratio < min_noise:
            continue
        start, end = _period(focused)
        out.append(SuggestionCandidate(
            collector=coll,
            alert_type=atype,
            reason_code="UNCHANGED_DOCUMENT",
            rule_type="compare_content_hash",
            parameters={},
            explanation=(
                f"{len(focused)} document alerts with identical content hashes were mostly NOISE. "
                f"Compare content hash before emitting document-change alerts."
            ),
            supporting_alert_count=len(focused),
            outcome_distribution=_outcome_dist(focused),
            estimated_noise_reduction=noise_ratio,
            estimated_hit_loss=hit_loss,
            estimated_interesting_loss=int_loss,
            estimated_signal_loss=signal_loss,
            sample_start=start,
            sample_end=end,
            matched_alert_ids=[r["id"] for r in focused],
        ))
    return out


def detect_minor_price(
    rows: list[dict], *, min_samples: int, min_noise: float, max_signal_loss: float,
) -> list[SuggestionCandidate]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if r["alert_type"] != "price_changed":
            continue
        mag = (r.get("meta") or {}).get("magnitude_pct")
        if mag is None:
            continue
        try:
            if float(mag) >= 5.0:
                continue
        except (TypeError, ValueError):
            continue
        groups[(r["collector"], r["alert_type"])].append(r)

    out = []
    for (coll, atype), focused in groups.items():
        if len(focused) < min_samples:
            continue
        noise_ratio, hit_loss, int_loss, signal_loss = _metrics(focused)
        if noise_ratio < min_noise:
            continue
        start, end = _period(focused)
        out.append(SuggestionCandidate(
            collector=coll,
            alert_type=atype,
            reason_code="MINOR_PRICE_FLUCTUATION",
            rule_type="minimum_price_change_percent",
            parameters={"minimum_pct": 5.0},
            explanation=(
                f"{len(focused)} price changes under 5% were mostly NOISE. "
                f"Introduce a configurable minimum percentage threshold."
            ),
            supporting_alert_count=len(focused),
            outcome_distribution=_outcome_dist(focused),
            estimated_noise_reduction=noise_ratio,
            estimated_hit_loss=hit_loss,
            estimated_interesting_loss=int_loss,
            estimated_signal_loss=signal_loss,
            sample_start=start,
            sample_end=end,
            matched_alert_ids=[r["id"] for r in focused],
        ))
    return out


DETECTORS = [
    ("temporary_404", detect_temporary_404),
    ("duplicate_alerts", detect_duplicate_alerts),
    ("image_cdn_churn", detect_image_cdn_churn),
    ("unchanged_document", detect_unchanged_document),
    ("minor_price", detect_minor_price),
]


def analyze_reviews(
    conn: sqlite3.Connection,
    *,
    start: str | None = None,
    end: str | None = None,
    collector: str | None = None,
    alert_type: str | None = None,
    min_samples: int = 10,
    min_noise_ratio: float = 0.75,
    max_signal_loss_ratio: float = 0.05,
) -> list[SuggestionCandidate]:
    rows = _load_reviewed_rows(
        conn, start=start, end=end, collector=collector, alert_type=alert_type,
    )
    seen_fp: set[str] = set()
    results: list[SuggestionCandidate] = []
    for _name, fn in DETECTORS:
        for cand in fn(
            rows,
            min_samples=min_samples,
            min_noise=min_noise_ratio,
            max_signal_loss=max_signal_loss_ratio,
        ):
            # Emit even if signal loss is high — simulation classifies SAFE/RISKY.
            # Only require min samples + min noise at detector level for emission.
            fp = cand.fingerprint()
            if fp in seen_fp:
                continue
            seen_fp.add(fp)
            results.append(cand)
    return results


def persist_candidates(
    store,
    candidates: list[SuggestionCandidate],
    *,
    max_signal_loss_ratio: float = 0.05,
) -> list[dict]:
    """Insert or refresh suggestions by fingerprint. Never auto-activates.

    Policy:
    - New fingerprint → insert PROPOSED
    - Existing PROPOSED/ACCEPTED/IMPLEMENTED → refresh evidence metrics, keep status
    - Existing REJECTED/REVERTED → do not revive automatically
    """
    results = []
    for cand in candidates:
        fp = cand.fingerprint()
        existing = store.db.execute(
            "SELECT * FROM rule_suggestions WHERE fingerprint=?", (fp,)
        ).fetchone()
        rule_json = json.dumps(cand.structured_rule(), sort_keys=True)
        dist_json = json.dumps(cand.outcome_distribution, sort_keys=True)
        if existing is None:
            store.db.execute(
                "INSERT INTO rule_suggestions("
                "collector, alert_type, reason_code, suggested_rule, "
                "supporting_alert_count, estimated_noise_reduction, estimated_hit_loss, "
                "status, fingerprint, rule_json, explanation, estimated_interesting_loss, "
                "estimated_signal_loss, outcome_distribution_json, sample_start, sample_end"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cand.collector, cand.alert_type, cand.reason_code, cand.human_rule(),
                    cand.supporting_alert_count, cand.estimated_noise_reduction,
                    cand.estimated_hit_loss, "PROPOSED", fp, rule_json, cand.explanation,
                    cand.estimated_interesting_loss, cand.estimated_signal_loss,
                    dist_json, cand.sample_start, cand.sample_end,
                ),
            )
            store.db.commit()
            row = store.db.execute(
                "SELECT * FROM rule_suggestions WHERE fingerprint=?", (fp,)
            ).fetchone()
            results.append(dict(row))
            continue

        status = existing["status"]
        if status in ("REJECTED", "REVERTED"):
            results.append(dict(existing))
            continue
        store.db.execute(
            "UPDATE rule_suggestions SET supporting_alert_count=?, "
            "estimated_noise_reduction=?, estimated_hit_loss=?, "
            "estimated_interesting_loss=?, estimated_signal_loss=?, "
            "outcome_distribution_json=?, explanation=?, rule_json=?, "
            "suggested_rule=?, sample_start=?, sample_end=?, "
            "updated_at=datetime('now') WHERE fingerprint=?",
            (
                cand.supporting_alert_count, cand.estimated_noise_reduction,
                cand.estimated_hit_loss, cand.estimated_interesting_loss,
                cand.estimated_signal_loss, dist_json, cand.explanation, rule_json,
                cand.human_rule(), cand.sample_start, cand.sample_end, fp,
            ),
        )
        store.db.commit()
        row = store.db.execute(
            "SELECT * FROM rule_suggestions WHERE fingerprint=?", (fp,)
        ).fetchone()
        results.append(dict(row))
    return results
