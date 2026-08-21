"""Safe SemInt Windows Phase 0 evidence harness.

Default mode validates an existing evidence JSON file or emits a plan. It does
not install, edit, start, or delete a scheduled task.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path


UNKNOWN = "UNKNOWN"
REQUIRED_RUN_FIELDS = {
    "task_instance_id",
    "triggered_at_utc",
    "last_task_result",
    "application_start_utc",
    "job_id",
    "job_status",
    "job_finished_at_utc",
    "database_commit_evidence",
    "success_heartbeat_before_utc",
    "success_heartbeat_after_utc",
    "source_freshness_utc",
    "notification_test_target",
    "notification_deduplication_key",
    "notification_result",
}


def validate_evidence(data: dict) -> list[str]:
    errors: list[str] = []
    action = data.get("task_action") or {}
    for field in ("execute", "arguments", "working_directory", "export_digest"):
        if action.get(field, UNKNOWN) == UNKNOWN:
            errors.append(f"task_action.{field} is UNKNOWN")
    if " " not in str(action.get("execute", "")) and " " not in str(action.get("working_directory", "")):
        errors.append("task action does not evidence a path containing spaces")

    runs = data.get("unattended_runs") or []
    if len(runs) != 2:
        errors.append("exactly two unattended run records are required")
    for index, run in enumerate(runs, 1):
        missing = REQUIRED_RUN_FIELDS - run.keys()
        if missing:
            errors.append(f"run {index} missing: {', '.join(sorted(missing))}")
        unknown = [
            field for field in REQUIRED_RUN_FIELDS
            if run.get(field) in (None, "", UNKNOWN)
        ]
        if unknown:
            errors.append(f"run {index} unverified: {', '.join(sorted(unknown))}")
        if run.get("operator_triggered") is not False:
            errors.append(f"run {index} was not evidenced as unattended")
        if run.get("last_task_result") != 0 or run.get("job_status") != "SUCCESSFUL":
            errors.append(f"run {index} is not a successful task and committed job")
        if run.get("success_heartbeat_after_utc") in (None, UNKNOWN, run.get("success_heartbeat_before_utc")):
            errors.append(f"run {index} success-only heartbeat did not advance")
        if run.get("notification_test_target") in (None, UNKNOWN, "PRODUCTION"):
            errors.append(f"run {index} did not use an explicit non-production notification target")
        if run.get("notification_result") != "SUCCESSFUL":
            errors.append(f"run {index} notification was not successful")
        try:
            finished = dt.datetime.fromisoformat(str(run["job_finished_at_utc"]).replace("Z", "+00:00"))
            heartbeat = dt.datetime.fromisoformat(
                str(run["success_heartbeat_after_utc"]).replace("Z", "+00:00")
            )
            if heartbeat < finished:
                errors.append(f"run {index} heartbeat predates the committed job")
        except (KeyError, TypeError, ValueError):
            errors.append(f"run {index} has invalid job/heartbeat timestamps")

    for field in ("task_instance_id", "job_id", "notification_deduplication_key"):
        values = [run.get(field) for run in runs]
        if len(values) == 2 and len(set(values)) != 2:
            errors.append(f"unattended runs do not have unique {field} values")

    negative = data.get("negative_heartbeat_evidence") or {}
    for status in ("PARTIAL", "FAILED", "NO_JOB_DUE"):
        if negative.get(status) != "UNCHANGED":
            errors.append(f"{status} success heartbeat must be UNCHANGED")
    broken = data.get("broken_path_test") or {}
    for field in ("isolated_task_name", "nonexistent_execute", "task_failure_result", "independent_alert_id", "alert_delivery_utc"):
        if broken.get(field, UNKNOWN) == UNKNOWN:
            errors.append(f"broken_path_test.{field} is UNKNOWN")
    if broken.get("application_started") is not False or broken.get("success_heartbeat_changed") is not False:
        errors.append("broken path must not start the app or advance success heartbeat")
    if broken.get("task_failure_result") in (None, UNKNOWN, 0, "0"):
        errors.append("broken path task result must evidence failure")
    return errors


def _safe_output(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute() or path.exists() or not path.parent.exists():
        raise ValueError("output must be a new absolute path with an existing parent")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="SemInt Phase 0 Windows evidence harness")
    parser.add_argument("--evidence", type=Path, help="validate an operator-completed evidence JSON")
    parser.add_argument("--emit-template", help="write a blank evidence template to a new absolute path")
    parser.add_argument("--emit-broken-path-plan", help="write a non-executing staging plan to a new absolute path")
    parser.add_argument("--staging-task-name", default="SemiIntel Phase0 Broken Path STAGING")
    args = parser.parse_args()

    if args.evidence:
        data = json.loads(args.evidence.read_text(encoding="utf-8"))
        errors = validate_evidence(data)
        print(json.dumps({"gate": "FAIL" if errors else "PASS", "errors": errors}, indent=2))
        return 2 if errors else 0
    if args.emit_template:
        output = _safe_output(args.emit_template)
        template = {
            "gate": "OPEN",
            "task_action": {"execute": UNKNOWN, "arguments": UNKNOWN, "working_directory": UNKNOWN, "export_digest": UNKNOWN},
            "unattended_runs": [],
            "negative_heartbeat_evidence": {"PARTIAL": UNKNOWN, "FAILED": UNKNOWN, "NO_JOB_DUE": UNKNOWN},
            "broken_path_test": {
                "isolated_task_name": UNKNOWN,
                "nonexistent_execute": UNKNOWN,
                "task_failure_result": UNKNOWN,
                "application_started": UNKNOWN,
                "success_heartbeat_changed": UNKNOWN,
                "independent_alert_id": UNKNOWN,
                "alert_delivery_utc": UNKNOWN,
            },
            "operator": UNKNOWN,
            "reviewer": UNKNOWN,
        }
        output.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
        print(f"template written without host mutation: {output}")
        return 0
    if args.emit_broken_path_plan:
        if not re.fullmatch(r"[A-Za-z0-9 ._-]{8,100}", args.staging_task_name):
            parser.error("staging task name is invalid or ambiguous")
        output = _safe_output(args.emit_broken_path_plan)
        plan = {
            "mode": "NON_EXECUTING_PLAN",
            "task_name": args.staging_task_name,
            "constraints": [
                "staging host only",
                "clone task under the isolated task name; never edit production task",
                "use an approved unique nonexistent executable path",
                "allow one native trigger; do not use Run now",
                "independent monitor must alert because the app cannot start",
                "export final evidence; operator handles scheduler cleanup under separate authority",
            ],
            "powershell_executed": False,
        }
        output.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        print(f"broken-path plan emitted; no scheduler state changed: {output}")
        return 0
    parser.print_help()
    print("\nDefault is non-mutating. Choose one evidence/template/plan option.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
