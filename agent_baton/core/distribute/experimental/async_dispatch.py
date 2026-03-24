"""Async Task Dispatch — dispatch and track long-running tasks."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AsyncTask:
    """A task dispatched for asynchronous execution."""

    task_id: str
    command: str  # shell command, script path, or description
    dispatch_type: str = "shell"  # "shell", "script", "manual"
    status: str = "pending"  # "pending", "dispatched", "completed", "failed"
    dispatched_at: str = ""
    completed_at: str = ""
    result: str = ""
    exit_code: int | None = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "command": self.command,
            "dispatch_type": self.dispatch_type,
            "status": self.status,
            "dispatched_at": self.dispatched_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "exit_code": self.exit_code,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AsyncTask:
        return cls(
            task_id=data.get("task_id", ""),
            command=data.get("command", ""),
            dispatch_type=data.get("dispatch_type", "shell"),
            status=data.get("status", "pending"),
            dispatched_at=data.get("dispatched_at", ""),
            completed_at=data.get("completed_at", ""),
            result=data.get("result", ""),
            exit_code=data.get("exit_code"),
        )


class AsyncDispatcher:
    """Dispatch and track long-running tasks.

    Task specs are stored as individual JSON files under
    .claude/team-context/async-tasks/ (or a custom directory supplied at
    construction).  Each file is named <task_id>.json.
    """

    _DEFAULT_TASKS_DIR = Path(".claude/team-context/async-tasks")

    def __init__(self, tasks_dir: Path | None = None) -> None:
        self._dir = tasks_dir or self._DEFAULT_TASKS_DIR

    @property
    def tasks_dir(self) -> Path:
        return self._dir

    def _task_path(self, task_id: str) -> Path:
        safe_id = re.sub(r'[^a-zA-Z0-9_.-]', '-', task_id)
        return self._dir / f"{safe_id}.json"

    def _write_task(self, task: AsyncTask) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._task_path(task.task_id)
        path.write_text(
            json.dumps(task.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    # ── Dispatch ───────────────────────────────────────────────────────────

    def dispatch(self, task: AsyncTask) -> Path:
        """Write task spec to disk and mark as dispatched.

        For shell tasks the caller is responsible for actually launching
        the subprocess; this method records the intent on disk so status
        can be polled later.
        """
        task.status = "dispatched"
        return self._write_task(task)

    # ── Status ─────────────────────────────────────────────────────────────

    def check_status(self, task_id: str) -> AsyncTask | None:
        """Read task status from disk. Returns None if not found."""
        path = self._task_path(task_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return AsyncTask.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return None

    def mark_complete(
        self, task_id: str, result: str = "", exit_code: int = 0
    ) -> None:
        """Mark a task as completed with result."""
        task = self.check_status(task_id)
        if task is None:
            return
        task.status = "completed"
        task.result = result
        task.exit_code = exit_code
        self._write_task(task)

    def mark_failed(
        self, task_id: str, result: str = "", exit_code: int = 1
    ) -> None:
        """Mark a task as failed."""
        task = self.check_status(task_id)
        if task is None:
            return
        task.status = "failed"
        task.result = result
        task.exit_code = exit_code
        self._write_task(task)

    # ── List ───────────────────────────────────────────────────────────────

    def list_tasks(self, status: str | None = None) -> list[AsyncTask]:
        """List all tasks, optionally filtered by status."""
        if not self._dir.is_dir():
            return []
        tasks: list[AsyncTask] = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                task = AsyncTask.from_dict(data)
                if status is None or task.status == status:
                    tasks.append(task)
            except (json.JSONDecodeError, OSError):
                continue
        return tasks

    def list_pending(self) -> list[AsyncTask]:
        """Shortcut for list_tasks(status='pending')."""
        return self.list_tasks(status="pending")
