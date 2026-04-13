"""Async task dispatch -- dispatch and track long-running tasks.

Provides on-disk tracking for tasks that run outside the main orchestration
loop (e.g. long-running CI pipelines, external builds, manual approvals).
Each task is stored as a JSON file under
``.claude/team-context/async-tasks/`` so its status can be polled by any
agent session.

Task lifecycle::

    pending --> dispatched --> completed | failed

The ``AsyncDispatcher`` records task intent on disk but does NOT launch
subprocesses itself. The caller is responsible for actually executing the
task and calling ``mark_complete()`` or ``mark_failed()`` when done.

"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AsyncTask:
    """A task dispatched for asynchronous execution.

    Attributes:
        task_id: Unique identifier for the task.
        command: Shell command, script path, or free-text description
            of the work to perform.
        dispatch_type: How the task is executed. One of ``"shell"``
            (subprocess), ``"script"`` (script file), or ``"manual"``
            (human action).
        status: Current lifecycle state. One of ``"pending"``,
            ``"dispatched"``, ``"completed"``, or ``"failed"``.
        dispatched_at: ISO-8601 timestamp of when the task was dispatched.
        completed_at: ISO-8601 timestamp of completion or failure.
        result: Output text or summary from the completed task.
        exit_code: Process exit code (``None`` if not applicable or
            not yet completed).
    """

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
    """Dispatch and track long-running tasks via on-disk JSON files.

    Task specs are stored as individual JSON files under
    ``.claude/team-context/async-tasks/`` (or a custom directory supplied at
    construction). Each file is named ``<task_id>.json`` with unsafe
    characters replaced by hyphens.

    The dispatcher does not execute tasks itself -- it records dispatch
    intent and tracks status transitions. The actual execution is the
    caller's responsibility.
    """

    _DEFAULT_TASKS_DIR = Path(".claude/team-context/async-tasks")

    def __init__(self, tasks_dir: Path | None = None) -> None:
        self._dir = (tasks_dir or self._DEFAULT_TASKS_DIR).resolve()

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

        Sets the task's status to ``"dispatched"`` and persists it as a
        JSON file. The caller is responsible for actually launching the
        subprocess or triggering the external action.

        Args:
            task: The ``AsyncTask`` to dispatch.

        Returns:
            Path to the written JSON file.
        """
        task.status = "dispatched"
        return self._write_task(task)

    # ── Status ─────────────────────────────────────────────────────────────

    def check_status(self, task_id: str) -> AsyncTask | None:
        """Read task status from disk.

        Args:
            task_id: Identifier of the task to check.

        Returns:
            The ``AsyncTask`` if found, or ``None`` if the task file
            does not exist or cannot be parsed.
        """
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
        """Mark a task as completed with an optional result.

        No-op if the task does not exist on disk.

        Args:
            task_id: Identifier of the task to complete.
            result: Output text or summary from the completed task.
            exit_code: Process exit code (default 0 for success).
        """
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
        """Mark a task as failed with an optional error message.

        No-op if the task does not exist on disk.

        Args:
            task_id: Identifier of the task that failed.
            result: Error message or failure summary.
            exit_code: Process exit code (default 1 for failure).
        """
        task = self.check_status(task_id)
        if task is None:
            return
        task.status = "failed"
        task.result = result
        task.exit_code = exit_code
        self._write_task(task)

    # ── List ───────────────────────────────────────────────────────────────

    def list_tasks(self, status: str | None = None) -> list[AsyncTask]:
        """List all tasks, optionally filtered by status.

        Args:
            status: If provided, only return tasks with this status
                (e.g. ``"pending"``, ``"dispatched"``, ``"completed"``,
                ``"failed"``). If ``None``, returns all tasks.

        Returns:
            List of ``AsyncTask`` objects sorted by filename (task ID).
        """
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
