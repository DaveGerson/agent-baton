"""Protocols for the execution engine."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent_baton.models.execution import ExecutionAction, MachinePlan


class ExecutionDriver(Protocol):
    """Interface contract between the async worker layer and the execution engine.

    This is the most critical contract in the system — it defines how the async
    TaskWorker drives the synchronous state machine. Any class implementing this
    protocol can serve as the engine for orchestrated execution.
    """

    def start(self, plan: MachinePlan) -> ExecutionAction: ...

    def next_action(self) -> ExecutionAction: ...

    def next_actions(self) -> list[ExecutionAction]: ...

    def mark_dispatched(self, step_id: str, agent_name: str) -> None: ...

    def record_step_result(
        self,
        step_id: str,
        agent_name: str,
        status: str = "complete",
        outcome: str = "",
        files_changed: list[str] | None = None,
        commit_hash: str = "",
        estimated_tokens: int = 0,
        duration_seconds: float = 0.0,
        error: str = "",
    ) -> None: ...

    def record_gate_result(
        self,
        phase_id: int,
        passed: bool,
        output: str = "",
    ) -> None: ...

    def complete(self) -> str: ...

    def status(self) -> dict: ...

    def resume(self) -> ExecutionAction: ...
