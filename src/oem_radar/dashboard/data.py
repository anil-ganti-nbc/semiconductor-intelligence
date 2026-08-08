"""Dashboard data layer (M12). Pure read-only queries over the SQLite DB;
returns plain JSON-serializable dicts so it is trivially testable and the
render layer needs no DB knowledge. No writes, no migration — opens the DB
read-only (safe during a live crawl)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ..providers.sqlite import product_from_snapshot_row


def _loads(s, default=None):
    try:
        return json.loads(s) if s else default
    except (TypeError, json.JSONDecodeError):
        return default


def _latest_product_brief(conn: sqlite3.Connection, product_key: str,
                          cache: dict) -> dict:
    """The current display facts for a product_key: model, silicon, price,
    image, and the all-important store URL. Cached per call."""
    if product_key in cache:
        return cache[product_key]
    row = conn.execute(
        "SELECT s.* FROM snapshots s JOIN listings l ON s.listing_id = l.id "
        "WHERE l.product_key = ? ORDER BY s.id DESC LIMIT 1", (product_key,),
    ).fetchone()
    brief: dict = {}
    if row:
        p = product_from_snapshot_row(row)
        price = p.prices[0] if p.prices else None
        brief = {
            "manufacturer": p.manufacturer,
            "model": p.model,
            "cpu": (p.cpu.raw if p.cpu else None),
            "cpu_unseen": (p.cpu.known is False if p.cpu else False),
            "gpu": (p.gpu.raw if p.gpu else None),
            "memory": p.memory,
            "storage": p.storage,
            "price": (f"{price.amount:g} {price.currency}" if price else None),
            "region": (price.region if price else None),
            "image": (p.images[0] if p.images else None),
            "url": p.source_url,
            "confidence": p.confidence,
            "configs": len(p.configurations),
        }
    cache[product_key] = brief
    return brief


def collect(conn: sqlite3.Connection, limit: int = 300) -> dict:
    brief_cache: dict = {}

    def brief(key):
        return _latest_product_brief(conn, key, brief_cache)

    # ---- events (the heart of the view) ----
    # Review join is optional: older DBs without alert_reviews still work.
    try:
        event_rows = conn.execute(
            "SELECT e.id, e.product_key, e.change_type, e.field, e.old_value_json, "
            "e.new_value_json, e.severity, e.meta_json, e.detected_at, "
            "n.status AS notif_status, r.outcome AS review_outcome "
            "FROM change_events e "
            "LEFT JOIN notifications n ON n.change_event_id = e.id "
            "LEFT JOIN alert_reviews r ON r.alert_id = e.id "
            "ORDER BY e.detected_at DESC, e.id DESC LIMIT ?", (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        event_rows = conn.execute(
            "SELECT e.id, e.product_key, e.change_type, e.field, e.old_value_json, "
            "e.new_value_json, e.severity, e.meta_json, e.detected_at, "
            "n.status AS notif_status "
            "FROM change_events e "
            "LEFT JOIN notifications n ON n.change_event_id = e.id "
            "ORDER BY e.detected_at DESC, e.id DESC LIMIT ?", (limit,),
        ).fetchall()
    events = []
    for r in event_rows:
        b = brief(r["product_key"])
        meta = _loads(r["meta_json"], {})
        events.append({
            "id": r["id"],
            "product_key": r["product_key"],
            "type": r["change_type"],
            "field": r["field"],
            "old": _loads(r["old_value_json"]),
            "new": _loads(r["new_value_json"]),
            "severity": r["severity"],
            "detected_at": r["detected_at"],
            "notified": r["notif_status"] in ("pending", "sent"),
            "unseen_component": bool(meta.get("unseen_component")),
            "hidden": bool(meta.get("hidden")),
            "magnitude_pct": meta.get("magnitude_pct"),
            "direction": meta.get("direction"),
            "added": meta.get("added"),
            "removed": meta.get("removed"),
            "manufacturer": b.get("manufacturer"),
            "model": b.get("model"),
            "cpu": b.get("cpu"),
            "cpu_unseen": b.get("cpu_unseen"),
            "gpu": b.get("gpu"),
            "memory": b.get("memory"),
            "storage": b.get("storage"),
            "price": b.get("price"),
            "region": b.get("region"),
            "image": b.get("image"),
            "url": b.get("url"),
            "confidence": b.get("confidence"),
            "review_outcome": (r["review_outcome"] if "review_outcome" in r.keys() else None),
            "review_status": (
                (r["review_outcome"] if "review_outcome" in r.keys() and r["review_outcome"] else None)
                or "UNREVIEWED"
            ),
        })

    # ---- unseen / discovered hardware feed ----
    comp_rows = conn.execute(
        "SELECT kind, canonical_name, first_raw, source, first_seen_at "
        "FROM components WHERE source = 'discovered' "
        "ORDER BY first_seen_at DESC, id DESC LIMIT 100"
    ).fetchall()
    components = [dict(r) for r in comp_rows]

    # ---- manufacturers overview ----
    man_rows = conn.execute(
        "SELECT m.name, COUNT(DISTINCT p.id) AS products "
        "FROM manufacturers m LEFT JOIN products p ON p.manufacturer_id = m.id "
        "GROUP BY m.id ORDER BY products DESC"
    ).fetchall()
    manufacturers = [dict(r) for r in man_rows]

    # ---- run telemetry ----
    run_rows = conn.execute(
        "SELECT source_key, started_at, finished_at, status, stats_json "
        "FROM crawler_runs ORDER BY id DESC LIMIT 40"
    ).fetchall()
    runs = []
    for r in run_rows:
        stats = _loads(r["stats_json"], {})
        runs.append({
            "source": r["source_key"], "started_at": r["started_at"],
            "status": r["status"],
            "snapshots": stats.get("snapshots_written"),
            "events": stats.get("events"),
            "discovered": stats.get("discovered"),
            "errors": stats.get("errors"),
        })

    def scalar(sql):
        try:
            return conn.execute(sql).fetchone()[0]
        except sqlite3.OperationalError:
            return 0  # table not present in an older DB

    last_run = conn.execute(
        "SELECT started_at FROM crawler_runs WHERE status='ok' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()

    total_events = scalar("SELECT COUNT(*) FROM change_events")
    reviewed_events = scalar("SELECT COUNT(*) FROM alert_reviews")
    unreviewed_events = scalar(
        "SELECT COUNT(*) FROM change_events e "
        "WHERE NOT EXISTS (SELECT 1 FROM alert_reviews r WHERE r.alert_id = e.id)")
    hit_n = scalar("SELECT COUNT(*) FROM alert_reviews WHERE outcome='HIT'")
    int_n = scalar("SELECT COUNT(*) FROM alert_reviews WHERE outcome='INTERESTING'")
    noise_n = scalar("SELECT COUNT(*) FROM alert_reviews WHERE outcome='NOISE'")
    bug_n = scalar("SELECT COUNT(*) FROM alert_reviews WHERE outcome='BUG'")
    signal_n = hit_n + int_n
    signal_rate = (round(signal_n / reviewed_events, 4) if reviewed_events else None)
    noise_rate = (round(noise_n / reviewed_events, 4) if reviewed_events else None)
    active_suggestions = scalar(
        "SELECT COUNT(*) FROM rule_suggestions WHERE status IN ('PROPOSED','ACCEPTED')")
    # Recent run health (last 40 already loaded below conceptually; use SQL)
    runs_failed = scalar(
        "SELECT COUNT(*) FROM crawler_runs WHERE id IN "
        "(SELECT id FROM crawler_runs ORDER BY id DESC LIMIT 40) AND status='failed'")
    runs_degraded = scalar(
        "SELECT COUNT(*) FROM crawler_runs WHERE id IN "
        "(SELECT id FROM crawler_runs ORDER BY id DESC LIMIT 40) "
        "AND status='ok' AND (stats_json LIKE '%degraded%')")

    summary = {
        "products": scalar("SELECT COUNT(*) FROM products"),
        "stories": scalar("SELECT COUNT(*) FROM stories"),
        "snapshots": scalar("SELECT COUNT(*) FROM snapshots"),
        "events": total_events,
        "unseen_components": scalar(
            "SELECT COUNT(*) FROM components WHERE source='discovered'"),
        "manufacturers": scalar("SELECT COUNT(*) FROM manufacturers"),
        "pending_notifications": scalar(
            "SELECT COUNT(*) FROM notifications WHERE status='pending'"),
        "unreviewed_events": unreviewed_events,
        "reviewed_events": reviewed_events,
        "hit_count": hit_n,
        "interesting_count": int_n,
        "noise_count": noise_n,
        "bug_count": bug_n,
        "signal_count": signal_n,
        "signal_rate": signal_rate,
        "noise_rate": noise_rate,
        "active_suggestions": active_suggestions,
        "recent_failed_runs": runs_failed,
        "recent_degraded_runs": runs_degraded,
        "last_run": (last_run["started_at"] if last_run else None),
    }

    try:
        story_rows = conn.execute(
            "SELECT rule_id, story_key, title, score, manufacturers_json, evidence_json, "
            "score_reasons_json, created_at FROM stories ORDER BY id DESC LIMIT 50"
        ).fetchall()
    except sqlite3.OperationalError:
        story_rows = []  # pre-v3 DB not yet re-crawled; degrade to empty tab
    stories = []
    for r in story_rows:
        stories.append({
            "rule_id": r["rule_id"], "key": r["story_key"], "title": r["title"],
            "score": r["score"], "created_at": r["created_at"],
            "manufacturers": _loads(r["manufacturers_json"], []),
            "evidence": _loads(r["evidence_json"], []),
            "reasons": _loads(r["score_reasons_json"], []),
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": summary,
        "events": events,
        "components": components,
        "manufacturers": manufacturers,
        "runs": runs,
        "stories": stories,
    }


def collect_alert_detail(conn: sqlite3.Connection, alert_id: int) -> dict | None:
    """Full payload for GET /alerts/{id} and GET /api/alerts/{id}/review."""
    row = conn.execute(
        "SELECT e.id, e.product_key, e.change_type, e.field, e.old_value_json, "
        "e.new_value_json, e.severity, e.meta_json, e.detected_at "
        "FROM change_events e WHERE e.id = ?",
        (alert_id,),
    ).fetchone()
    if row is None:
        return None
    # Adjacent alerts by id for prev/next navigation
    prev_row = conn.execute(
        "SELECT id FROM change_events WHERE id < ? ORDER BY id DESC LIMIT 1",
        (alert_id,),
    ).fetchone()
    next_row = conn.execute(
        "SELECT id FROM change_events WHERE id > ? ORDER BY id ASC LIMIT 1",
        (alert_id,),
    ).fetchone()

    brief_cache: dict = {}
    b = _latest_product_brief(conn, row["product_key"], brief_cache)
    meta = _loads(row["meta_json"], {})

    # Source / collector from product_key prefix and sources table when possible.
    source_key = row["product_key"].split(":", 1)[0] if row["product_key"] else None
    source_row = None
    if source_key:
        source_row = conn.execute(
            "SELECT source_key, engine, base_url FROM sources WHERE source_key=?",
            (source_key,),
        ).fetchone()

    # Related previous events: same product_key, older than this one.
    related = []
    rel_rows = conn.execute(
        "SELECT id, change_type, field, severity, detected_at, old_value_json, new_value_json "
        "FROM change_events WHERE product_key=? AND id < ? "
        "ORDER BY id DESC LIMIT 10",
        (row["product_key"], alert_id),
    ).fetchall()
    for rr in rel_rows:
        related.append({
            "id": rr["id"],
            "type": rr["change_type"],
            "field": rr["field"],
            "severity": rr["severity"],
            "detected_at": rr["detected_at"],
            "old": _loads(rr["old_value_json"]),
            "new": _loads(rr["new_value_json"]),
        })

    # Current review + history via store-shaped dicts (read-only SQL here).
    review = None
    history: list[dict] = []
    try:
        rev = conn.execute(
            "SELECT * FROM alert_reviews WHERE alert_id=?", (alert_id,)
        ).fetchone()
        if rev:
            from ..core.feedback import reasons_from_json
            review = {
                "id": rev["id"],
                "alert_id": rev["alert_id"],
                "outcome": rev["outcome"],
                "reason_codes": reasons_from_json(rev["reason_codes_json"]),
                "reviewer_note": rev["reviewer_note"],
                "reviewed_at": rev["reviewed_at"],
                "reviewer": rev["reviewer"],
                "created_at": rev["created_at"],
                "updated_at": rev["updated_at"],
            }
        hist_rows = conn.execute(
            "SELECT * FROM alert_review_history WHERE alert_id=? "
            "ORDER BY changed_at ASC, id ASC",
            (alert_id,),
        ).fetchall()
        from ..core.feedback import reasons_from_json as _rfj
        for h in hist_rows:
            history.append({
                "id": h["id"],
                "alert_id": h["alert_id"],
                "previous_outcome": h["previous_outcome"],
                "new_outcome": h["new_outcome"],
                "previous_reason_codes": _rfj(h["previous_reason_codes_json"]),
                "new_reason_codes": _rfj(h["new_reason_codes_json"]),
                "changed_at": h["changed_at"],
                "changed_by": h["changed_by"],
                "change_note": h["change_note"],
            })
    except sqlite3.OperationalError:
        pass  # pre-v4 DB

    return {
        "id": row["id"],
        "product_key": row["product_key"],
        "type": row["change_type"],
        "field": row["field"],
        "old": _loads(row["old_value_json"]),
        "new": _loads(row["new_value_json"]),
        "severity": row["severity"],
        "meta": meta,
        "detected_at": row["detected_at"],
        "manufacturer": b.get("manufacturer"),
        "model": b.get("model"),
        "cpu": b.get("cpu"),
        "gpu": b.get("gpu"),
        "memory": b.get("memory"),
        "storage": b.get("storage"),
        "price": b.get("price"),
        "region": b.get("region"),
        "image": b.get("image"),
        "url": b.get("url"),
        "confidence": b.get("confidence"),
        "sku": b.get("sku") if "sku" in b else None,
        "collector": source_key,
        "engine": (source_row["engine"] if source_row else None),
        "source_base_url": (source_row["base_url"] if source_row else None),
        "related_events": related,
        "review": review,
        "history": history,
        "review_status": (review["outcome"] if review else "UNREVIEWED"),
        "prev_id": (prev_row["id"] if prev_row else None),
        "next_id": (next_row["id"] if next_row else None),
    }
