"""Windows Task Scheduler command construction.

Execution is deliberately separate so tests and dry-runs never mutate the OS.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


TASK_NAME = "SemiIntel Operational Cycle"


def install_command(executable: Path, working_directory: Path, *, interval_minutes: int) -> list[str]:
    executable = executable.resolve()
    working_directory = working_directory.resolve()
    task_action = (
        f'cmd /c "cd /d ""{working_directory}"" '
        f'&& ""{executable}"" automation cycle"'
    )
    return [
        "schtasks.exe", "/Create", "/F", "/SC", "MINUTE", "/MO", str(interval_minutes),
        "/TN", TASK_NAME, "/TR", task_action,
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
    matches = re.findall(r'"+([^"\r\n]+?\.exe)"+', arguments or "", flags=re.I)
    return matches[-1] if matches else None


class WindowsTaskStatusService:
    """Read-only Task Scheduler inspection with a mockable command runner."""

    def __init__(self, runner=execute):
        self.runner = runner

    def status(self, *, expected_executable: Path | None = None) -> dict:
        expected = (expected_executable or current_executable()).resolve()
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
        path_exists = configured_path.exists() if configured_path else False
        return {
            "supported": True,
            "installed": True,
            "state": str(payload.get("state") or "unknown").lower(),
            "expected_executable": str(expected),
            "configured_executable": str(configured_path) if configured_path else None,
            "working_directory": payload.get("working_directory") or None,
            "arguments": payload.get("arguments") or None,
            "path_exists": path_exists,
            "path_matches_current": configured_path == expected if configured_path else False,
            "last_run": payload.get("last_run") or None,
            "next_run": payload.get("next_run") or None,
            "last_result": payload.get("last_result"),
            "error": None,
        }
