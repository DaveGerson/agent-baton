"""StorageBackend protocol — abstract interface for execution data persistence.

Both ``SqliteStorage`` and ``FileStorage`` implement this protocol.
The ``ExecutionEngine`` and other consumers accept any object that
satisfies this interface, enabling seamless switching between backends.

Usage::

    from agent_baton.core.storage import get_project_storage

    storage = get_project_storage(context_root)  # auto-detects backend
    engine = ExecutionEngine(storage=storage, bus=bus)
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_baton.models.events import Event
from agent_baton.models.execution import (
    ApprovalResult,
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanAmendment,
    StepResult,
)
from agent_baton.models.plan import MissionLogEntry
from agent_baton.models.retrospective import Retrospective
from agent_baton.models.trace import TaskTrace
from agent_baton.models.usage import TaskUsageRecord


@runtime_checkable
class StorageBackend(Protocol):
    """Abstract storage contract for execution data.

    Both ``SqliteStorage`` (baton.db) and ``FileStorage`` (legacy
    JSON/JSONL files) implement this protocol.  The engine and CLI
    use whichever backend ``get_project_storage()`` returns.

    All methods are synchronous.  Storage backends are expected to
    handle their own atomicity (e.g., WAL mode for SQLite, tmp+rename
    for files).
    """

    def close(self) -> None:
        """Release resources (close DB connections, etc.)."""
        ...

    # ── Execution State ────────────────────────────────────────────────────

    def save_execution(self, state: ExecutionState) -> None:
        """Persist full execution state (upsert)."""
        ...

    def load_execution(self, task_id: str) -> ExecutionState | None:
        """Load execution state by task ID. Returns None if not found."""
        ...

    def list_executions(self) -> list[str]:
        """Return all known task IDs."""
        ...

    def delete_execution(self, task_id: str) -> None:
        """Remove execution and all related data (cascade)."""
        ...

    # ── Active Task ────────────────────────────────────────────────────────

    def set_active_task(self, task_id: str) -> None:
        """Mark a task as the default/active execution."""
        ...

    def get_active_task(self) -> str | None:
        """Read the active task ID. Returns None if not set."""
        ...

    # ── Plans ──────────────────────────────────────────────────────────────

    def save_plan(self, plan: MachinePlan) -> None:
        """Save a plan (creates a 'queued' execution entry if needed)."""
        ...

    def load_plan(self, task_id: str) -> MachinePlan | None:
        """Load a plan by task ID."""
        ...

    # ── Step/Gate/Approval Results ─────────────────────────────────────────

    def save_step_result(self, task_id: str, result: StepResult) -> None:
        ...

    def save_gate_result(self, task_id: str, result: GateResult) -> None:
        ...

    def save_approval_result(self, task_id: str, result: ApprovalResult) -> None:
        ...

    def save_amendment(self, task_id: str, amendment: PlanAmendment) -> None:
        ...

    # ── Events ─────────────────────────────────────────────────────────────

    def append_event(self, event: Event) -> None:
        ...

    def read_events(self, task_id: str, from_seq: int = 0) -> list[Event]:
        ...

    # ── Usage ──────────────────────────────────────────────────────────────

    def log_usage(self, record: TaskUsageRecord) -> None:
        ...

    def read_usage(self, limit: int | None = None) -> list[TaskUsageRecord]:
        ...

    # ── Telemetry ──────────────────────────────────────────────────────────

    def log_telemetry(self, event: dict) -> None:
        ...

    def read_telemetry(self, limit: int | None = None) -> list[dict]:
        ...

    # ── Retrospectives ─────────────────────────────────────────────────────

    def save_retrospective(self, retro: Retrospective) -> None:
        ...

    def load_retrospective(self, task_id: str) -> Retrospective | None:
        ...

    def list_retrospective_ids(self, limit: int = 100) -> list[str]:
        ...

    # ── Traces ─────────────────────────────────────────────────────────────

    def save_trace(self, trace: TaskTrace) -> None:
        ...

    def load_trace(self, task_id: str) -> TaskTrace | None:
        ...

    # ── Patterns & Budget ──────────────────────────────────────────────────

    def save_patterns(self, patterns: list) -> None:
        ...

    def load_patterns(self) -> list:
        ...

    def save_budget_recommendations(self, recs: list) -> None:
        ...

    def load_budget_recommendations(self) -> list:
        ...

    # ── Mission Log ────────────────────────────────────────────────────────

    def append_mission_log(self, task_id: str, entry: MissionLogEntry) -> None:
        ...

    def read_mission_log(self, task_id: str) -> str | list[MissionLogEntry] | None:
        ...

    # ── Shared Context & Profile ───────────────────────────────────────────

    def save_context(self, task_id: str, content: str, **sections: str) -> None:
        ...

    def read_context(self, task_id: str) -> str | None:
        ...

    def save_profile(self, content: str) -> None:
        ...

    def read_profile(self) -> str | None:
        ...
