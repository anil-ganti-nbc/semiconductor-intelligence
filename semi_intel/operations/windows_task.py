"""Windows Task Scheduler command construction.

Execution is deliberately separate so tests and dry-runs never mutate the OS.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


TASK_NAME = "SemiIntel Operational Cycle"
TASK_ARGUMENTS = "automation cycle"


def _ps_literal(value: str | Path) -> str:
    """Return a PowerShell single-quoted literal without invoking a shell parser."""
    return "'" + str(value).replace("'", "''") + "'"


def install_command(executable: Path, working_directory: Path, *, interval_minutes: int) -> list[str]:
    executable = executable.resolve()
    working_directory = working_directory.resolve()
    if interval_minutes < 1:
        raise ValueError("Task interval must be at least one minute.")
    # Keep Execute, Arguments, and WorkingDirectory as native Task Scheduler
    # action fields.  The previous cmd /c wrapper was split at the first space
    # in an installed path and exited before SemInt could write its heartbeat.
    action = (
        "$action=New-ScheduledTaskAction "
        f"-Execute {_ps_literal(executable)} "
        f"-Argument {_ps_literal(TASK_ARGUMENTS)} "
        f"-WorkingDirectory {_ps_literal(working_directory)};"
        "$trigger=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
        f"-RepetitionInterval (New-TimeSpan -Minutes {interval_minutes});"
        f"$existing=Get-ScheduledTask -TaskName {_ps_literal(TASK_NAME)} -ErrorAction SilentlyContinue;"
        "if($existing){"
        f"Register-ScheduledTask -TaskName {_ps_literal(TASK_NAME)} -Action $action -Trigger $trigger "
        "-Settings $existing.Settings -Principal $existing.Principal -Force | Out-Null"
        "}else{"
        f"Register-ScheduledTask -TaskName {_ps_literal(TASK_NAME)} -Action $action -Trigger $trigger "
        "-Description 'Runs one bounded SemInt operational cycle.' -Force | Out-Null"
        "}"
    )
    return [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-Command", action,
    ]


def remove_command() -> list[str]:
    return ["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"]


def execute(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def status_command() -> list[str]:
    script = (
        f"$task=Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop;"
        f"$info=Get-ScheduledTaskInfo -TaskName '{TASK_NAME}' -ErrorAction Stop;"
        "$action=$task.Actions|Select-Object -First 1;"
        "[pscustomobject]@{state=[string]$task.State;execute=$action.Execute;"
        "arguments=$action.Arguments;working_directory=$action.WorkingDirectory;"
        "last_run=$info.LastRunTime.ToString('o');next_run=$info.NextRunTime.ToString('o');"
        "last_result=$info.LastTaskResult}|ConvertTo-Json -Compress"
    )
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]


def current_executable() -> Path:
    return Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve()


def _action_executable(execute_value: str | None, arguments: str | None) -> str | None:
    execute_value = (execute_value or "").strip().strip('"')
    if execute_value and Path(execute_value).name.lower() not in {"cmd", "cmd.exe"}:
        return execute_value
    # Old cmd-wrapped tasks are deliberately treated as stale.  Guessing an
    # executable out of their malformed argument string previously made a
    # broken task look correctly configured.
    return None


def task_result_explanation(value: int | None) -> str:
    if value is None:
        return "Task Scheduler has not reported a result."
    unsigned = value & 0xFFFFFFFF
    messages = {
        0: "The last task invocation completed successfully.",
        1: "The task process exited with code 1 before completing successfully.",
        0x41300: "The task is ready but has not started.",
        0x41301: "The task is currently running.",
        0x41303: "The task has not yet run.",
        0x8004130F: "Task Scheduler could not use the stored account credentials.",
    }
    return messages.get(unsigned, f"Task Scheduler reported result 0x{unsigned:08X}.")


class WindowsTaskStatusService:
    """Read-only Task Scheduler inspection with a mockable command runner."""

    def __init__(self, runner=execute):
        self.runner = runner

    def status(
        self, *, expected_executable: Path | None = None,
        expected_working_directory: Path | None = None,
    ) -> dict:
        expected = (expected_executable or current_executable()).resolve()
        expected_workdir = (expected_working_directory or expected.parent).resolve()
        try:
            result = self.runner(status_command())
        except (FileNotFoundError, OSError) as exc:
            return {
                "supported": False, "installed": False, "state": "unavailable",
                "expected_executable": str(expected), "error": str(exc)[:300],
            }
        if result.returncode:
            message = (result.stderr or result.stdout or "Task is not installed.").strip()
            return {
                "supported": os.name == "nt", "installed": False, "state": "not_installed",
                "expected_executable": str(expected), "error": message[:300],
            }
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            return {
                "supported": True, "installed": False, "state": "query_failed",
                "expected_executable": str(expected), "error": "Task Scheduler returned unreadable status.",
            }
        configured_executable = _action_executable(payload.get("execute"), payload.get("arguments"))
        configured_path = Path(configured_executable).resolve() if configured_executable else None
        configured_workdir = Path(payload["working_directory"]).resolve() if payload.get("working_directory") else None
        path_exists = configured_path.exists() if configured_path else False
        arguments = (payload.get("arguments") or "").strip()
        executable_matches = configured_path == expected if configured_path else False
        working_directory_matches = configured_workdir == expected_workdir if configured_workdir else False
        arguments_match = arguments == TASK_ARGUMENTS
        last_result = payload.get("last_result")
        return {
            "supported": True,
            "installed": True,
            "state": str(payload.get("state") or "unknown").lower(),
            "expected_executable": str(expected),
            "configured_executable": str(configured_path) if configured_path else None,
            "working_directory": str(configured_workdir) if configured_workdir else None,
            "expected_working_directory": str(expected_workdir),
            "arguments": arguments or None,
            "path_exists": path_exists,
            "path_matches_current": executable_matches,
            "working_directory_matches_current": working_directory_matches,
            "arguments_match_current": arguments_match,
            "action_matches_current": executable_matches and working_directory_matches and arguments_match,
            "last_run": payload.get("last_run") or None,
            "next_run": payload.get("next_run") or None,
            "last_result": last_result,
            "last_result_explanation": task_result_explanation(last_result),
            "error": None,
        }
