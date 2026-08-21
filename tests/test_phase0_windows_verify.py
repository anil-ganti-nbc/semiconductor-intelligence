from __future__ import annotations

import copy

from scripts.phase0_windows_verify import validate_evidence


def _run(number: int) -> dict:
    return {
        "task_instance_id": f"task-{number}",
        "triggered_at_utc": f"2026-08-2{number}T01:00:00+00:00",
        "last_task_result": 0,
        "application_start_utc": f"2026-08-2{number}T01:00:01+00:00",
        "job_id": f"job-{number}",
        "job_status": "SUCCESSFUL",
        "job_finished_at_utc": f"2026-08-2{number}T01:02:00+00:00",
        "database_commit_evidence": f"transaction-{number}",
        "success_heartbeat_before_utc": f"2026-08-2{number}T00:00:00+00:00",
        "success_heartbeat_after_utc": f"2026-08-2{number}T01:02:01+00:00",
        "source_freshness_utc": f"2026-08-2{number}T01:01:00+00:00",
        "notification_test_target": "phase0-sink",
        "notification_deduplication_key": f"phase0-{number}",
        "notification_result": "SUCCESSFUL",
        "operator_triggered": False,
    }


def _valid_evidence() -> dict:
    return {
        "task_action": {
            "execute": r"C:\Program Files\Semi Intel\python.exe",
            "arguments": "-m semi_intel.operator run-scheduled-cycle",
            "working_directory": r"C:\Program Files\Semi Intel",
            "export_digest": "sha256:example",
        },
        "unattended_runs": [_run(1), _run(2)],
        "negative_heartbeat_evidence": {
            "PARTIAL": "UNCHANGED",
            "FAILED": "UNCHANGED",
            "NO_JOB_DUE": "UNCHANGED",
        },
        "broken_path_test": {
            "isolated_task_name": "SemiIntel Phase0 Broken Path STAGING",
            "nonexistent_execute": r"C:\Phase0 Missing\never-exists.exe",
            "task_failure_result": 1,
            "application_started": False,
            "success_heartbeat_changed": False,
            "independent_alert_id": "alert-123",
            "alert_delivery_utc": "2026-08-23T01:10:00+00:00",
        },
    }


def test_complete_operator_evidence_passes_validation():
    assert validate_evidence(_valid_evidence()) == []


def test_unknown_commit_and_reused_job_evidence_fail_validation():
    evidence = copy.deepcopy(_valid_evidence())
    evidence["unattended_runs"][0]["database_commit_evidence"] = "UNKNOWN"
    evidence["unattended_runs"][1]["job_id"] = evidence["unattended_runs"][0]["job_id"]

    errors = validate_evidence(evidence)

    assert any("database_commit_evidence" in error for error in errors)
    assert any("unique job_id" in error for error in errors)


def test_failed_or_premature_heartbeat_and_missing_alert_fail_validation():
    evidence = copy.deepcopy(_valid_evidence())
    evidence["unattended_runs"][0]["success_heartbeat_after_utc"] = "2026-08-21T01:01:00+00:00"
    evidence["negative_heartbeat_evidence"]["FAILED"] = "ADVANCED"
    evidence["broken_path_test"]["task_failure_result"] = 0
    evidence["broken_path_test"]["independent_alert_id"] = "UNKNOWN"

    errors = validate_evidence(evidence)

    assert any("heartbeat predates" in error for error in errors)
    assert any("FAILED success heartbeat" in error for error in errors)
    assert any("task result must evidence failure" in error for error in errors)
    assert any("independent_alert_id" in error for error in errors)
