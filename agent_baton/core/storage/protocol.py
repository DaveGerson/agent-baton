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
        """Persist a single step result (upsert)."""
        ...

    def save_gate_result(self, task_id: str, result: GateResult) -> None:
        """Append a gate check result."""
        ...

    def save_approval_result(self, task_id: str, result: ApprovalResult) -> None:
        """Append an approval decision."""
        ...

    def save_amendment(self, task_id: str, amendment: PlanAmendment) -> None:
        """Persist a plan amendment (upsert)."""
        ...

    # ── Events ─────────────────────────────────────────────────────────────

    def append_event(self, event: Event) -> None:
        """Append a domain event to the event log."""
        ...

    def read_events(self, task_id: str, from_seq: int = 0) -> list[Event]:
        """Return events for a task starting from a given sequence number."""
        ...

    # ── Usage ──────────────────────────────────────────────────────────────

    def log_usage(self, record: TaskUsageRecord) -> None:
        """Persist a task usage record with agent-level detail."""
        ...

    def read_usage(self, limit: int | None = None) -> list[TaskUsageRecord]:
        """Return usage records, most recent first."""
        ...

    # ── Telemetry ──────────────────────────────────────────────────────────

    def log_telemetry(self, event: dict) -> None:
        """Append a telemetry event dict."""
        ...

    def read_telemetry(self, limit: int | None = None) -> list[dict]:
        """Return telemetry events as dicts, most recent first."""
        ...

    # ── Retrospectives ─────────────────────────────────────────────────────

    def save_retrospective(self, retro: Retrospective) -> None:
        """Persist a retrospective and all its child collections."""
        ...

    def load_retrospective(self, task_id: str) -> Retrospective | None:
        """Load a retrospective by task ID. Returns None if not found."""
        ...

    def list_retrospective_ids(self, limit: int = 100) -> list[str]:
        """Return task IDs of stored retrospectives, most recent first."""
        ...

    # ── Traces ─────────────────────────────────────────────────────────────

    def save_trace(self, trace: TaskTrace) -> None:
        """Persist an execution trace and its events."""
        ...

    def load_trace(self, task_id: str) -> TaskTrace | None:
        """Load a trace by task ID. Returns None if not found."""
        ...

    # ── Patterns & Budget ──────────────────────────────────────────────────

    def save_patterns(self, patterns: list) -> None:
        """Replace all learned patterns (full replacement write)."""
        ...

    def load_patterns(self) -> list:
        """Return all learned patterns."""
        ...

    def save_budget_recommendations(self, recs: list) -> None:
        """Replace all budget recommendations (full replacement write)."""
        ...

    def load_budget_recommendations(self) -> list:
        """Return all budget recommendations."""
        ...

    # ── Mission Log ────────────────────────────────────────────────────────

    def append_mission_log(self, task_id: str, entry: MissionLogEntry) -> None:
        """Append a mission log entry for a dispatched agent."""
        ...

    def read_mission_log(self, task_id: str) -> str | None:
        """Read the mission log for a task."""
        ...

    # ── Shared Context & Profile ───────────────────────────────────────────

    def save_context(self, task_id: str, content: str, **sections: str) -> None:
        """Persist shared context for a task (free-text + structured sections)."""
        ...

    def read_context(self, task_id: str) -> str | None:
        """Read the shared context content for a task."""
        ...

    def save_profile(self, content: str) -> None:
        """Persist the singleton codebase profile."""
        ...

    def read_profile(self) -> str | None:
        """Read the codebase profile content."""
        ...
