"""Read-only deterministic operational and feedback trend summaries."""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from semi_intel.domain.enums import NotificationFeedbackRating, OperationalJobStatus
from semi_intel.domain.models import Notification, NotificationFeedback, OperationalJobRun
from semi_intel.notifications.service import aware, utcnow


SUPPORTED_WINDOWS = {7, 30, 90}
DISPLAYED_JOB_STATUSES = (
    OperationalJobStatus.SUCCESSFUL,
    OperationalJobStatus.PARTIAL,
    OperationalJobStatus.FAILED,
    OperationalJobStatus.SKIPPED,
)


class OperationalTrendService:
    """Aggregate existing rows without mutating operational or alert state."""

    def __init__(self, session: Session):
        self.session = session

    def summarize(
        self, window_days: int = 30, *, now: dt.datetime | None = None
    ) -> dict:
        if window_days not in SUPPORTED_WINDOWS:
            raise ValueError("Trend window must be 7, 30, or 90 days.")
        now = aware(now or utcnow())
        cutoff = now - dt.timedelta(days=window_days)

        jobs = list(self.session.scalars(
            select(OperationalJobRun)
            .where(OperationalJobRun.started_at >= cutoff)
            .order_by(OperationalJobRun.started_at.asc())
        ))
        feedback_rows = list(self.session.execute(
            select(NotificationFeedback, Notification)
            .join(Notification, Notification.id == NotificationFeedback.notification_id)
            .where(NotificationFeedback.updated_at >= cutoff)
            .order_by(NotificationFeedback.updated_at.asc())
        ))

        job_summary = self._jobs(jobs)
        feedback_summary = self._feedback(feedback_rows)
        return {
            "window_days": window_days,
            "window_start": cutoff.isoformat(),
            "generated_at": now.isoformat(),
            "headline": self._headline(window_days, job_summary, feedback_summary),
            "jobs": job_summary,
            "feedback": feedback_summary,
        }

    @staticmethod
    def _jobs(rows: list[OperationalJobRun]) -> dict:
        status_counts = {
            status.value: sum(row.status == status for row in rows)
            for status in DISPLAYED_JOB_STATUSES
        }
        by_type: dict[str, dict] = {}
        grouped: dict[str, list[OperationalJobRun]] = defaultdict(list)
        for row in rows:
            grouped[row.job_type.value].append(row)
        for job_type in sorted(grouped):
            durations = [
                max((aware(row.finished_at) - aware(row.started_at)).total_seconds(), 0)
                for row in grouped[job_type] if row.finished_at is not None
            ]
            by_type[job_type] = {
                "count": len(grouped[job_type]),
                "average_duration_seconds": (
                    round(sum(durations) / len(durations), 1) if durations else None
                ),
            }
        reliability_denominator = (
            status_counts["successful"] + status_counts["partial"] + status_counts["failed"]
        )
        reliability_rate = (
            round(
                (status_counts["successful"] + status_counts["partial"])
                / reliability_denominator,
                4,
            )
            if reliability_denominator else None
        )
        return {
            "total": len(rows),
            "status_counts": status_counts,
            "reliability_rate": reliability_rate,
            "by_job_type": by_type,
        }

    @staticmethod
    def _feedback(rows: list[tuple[NotificationFeedback, Notification]]) -> dict:
        useful = sum(
            feedback.rating == NotificationFeedbackRating.USEFUL
            for feedback, _notification in rows
        )
        not_useful = len(rows) - useful
        grouped: dict[str, dict[str, int]] = defaultdict(
            lambda: {"useful": 0, "not_useful": 0}
        )
        reasons: Counter[str] = Counter()
        for feedback, notification in rows:
            key = (
                "useful"
                if feedback.rating == NotificationFeedbackRating.USEFUL
                else "not_useful"
            )
            grouped[notification.event_type.value][key] += 1
            if key == "not_useful" and feedback.reason:
                reasons[feedback.reason] += 1
        by_event_type = {}
        for event_type in sorted(grouped):
            counts = grouped[event_type]
            total = counts["useful"] + counts["not_useful"]
            by_event_type[event_type] = {
                **counts,
                "total": total,
                "useful_rate": round(counts["useful"] / total, 4),
            }
        return {
            "total": len(rows),
            "useful": useful,
            "not_useful": not_useful,
            "useful_rate": round(useful / len(rows), 4) if rows else None,
            "by_event_type": by_event_type,
            "top_not_useful_reasons": [
                {"reason": reason, "count": count}
                for reason, count in sorted(
                    reasons.items(), key=lambda item: (-item[1], item[0])
                )[:5]
            ],
        }

    @staticmethod
    def _headline(window_days: int, jobs: dict, feedback: dict) -> str:
        if jobs["total"] == 0 and feedback["total"] == 0:
            return f"No job or feedback activity was recorded in the last {window_days} days."
        parts = []
        if jobs["total"]:
            if jobs["reliability_rate"] is None:
                parts.append(f"{jobs['total']} operational job(s) were recorded")
            else:
                parts.append(
                    f"{round(jobs['reliability_rate'] * 100)}% of completed operational "
                    "jobs finished without a full failure"
                )
        if feedback["total"]:
            parts.append(
                f"{round(feedback['useful_rate'] * 100)}% of rated alerts were useful"
            )
        return "; ".join(parts) + f" in the last {window_days} days."
