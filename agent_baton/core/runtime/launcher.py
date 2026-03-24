"""Launcher protocol and dry-run implementation.

AgentLauncher is the protocol that all launcher implementations must satisfy.
Implementations can be Claude Code subagents, subprocess calls, API requests,
or dry-run mocks for testing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LaunchResult:
    """Result of launching an agent."""
    step_id: str
    agent_name: str
    status: str = "complete"  # "complete" or "failed"
    outcome: str = ""
    files_changed: list[str] = field(default_factory=list)
    commit_hash: str = ""
    estimated_tokens: int = 0
    duration_seconds: float = 0.0
    error: str = ""


class AgentLauncher(Protocol):
    """Protocol for launching agents.

    Implementations can be Claude Code subagents, subprocess calls, API
    requests, or dry-run mocks.
    """

    async def launch(
        self,
        agent_name: str,
        model: str,
        prompt: str,
        step_id: str = "",
    ) -> LaunchResult:
        """Launch an agent and return its result."""
        ...


class DryRunLauncher:
    """Mock launcher that logs dispatches and returns synthetic results.

    Useful for testing without actually calling Claude.  Optionally, callers
    can pre-configure per-step results via :meth:`set_result` before running.
    """

    def __init__(self) -> None:
        self.launches: list[dict] = []
        self._results: dict[str, LaunchResult] = {}  # step_id -> pre-configured result

    def set_result(self, step_id: str, result: LaunchResult) -> None:
        """Pre-configure a result for a specific step."""
        self._results[step_id] = result

    async def launch(
        self,
        agent_name: str,
        model: str,
        prompt: str,
        step_id: str = "",
    ) -> LaunchResult:
        """Record the launch and return the pre-configured or default result."""
        self.launches.append(
            {"agent_name": agent_name, "model": model, "step_id": step_id}
        )
        if step_id in self._results:
            return self._results[step_id]
        return LaunchResult(
            step_id=step_id,
            agent_name=agent_name,
            outcome="dry-run complete",
        )
