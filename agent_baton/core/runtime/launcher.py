"""Launcher protocol and dry-run implementation.

Defines the ``AgentLauncher`` protocol that all launcher implementations must
satisfy, and provides ``DryRunLauncher`` for testing without real agent calls.

The launcher abstraction decouples the execution runtime from the specific
mechanism used to invoke agents.  Production uses ``ClaudeCodeLauncher``
(in ``claude_launcher.py``); tests use ``DryRunLauncher`` with pre-configured
results.

Implementations:

- ``DryRunLauncher`` -- returns synthetic results for testing.
- ``ClaudeCodeLauncher`` -- invokes the ``claude`` CLI as an async subprocess.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LaunchResult:
    """Result of launching an agent.

    Carries both success data (outcome, files_changed, commit_hash) and
    failure data (error) so the caller can process the result uniformly
    regardless of outcome.  The ``status`` field determines which fields
    are meaningful.

    Attributes:
        step_id: The plan step that was executed.
        agent_name: The agent that was launched.
        status: ``"complete"`` or ``"failed"``.
        outcome: Free-text summary of the agent's work (truncated by the
            launcher to ``max_outcome_length``).
        files_changed: Files modified by the agent (populated from git
            diff when available).
        commit_hash: Git commit hash if the agent committed changes.
        estimated_tokens: Token count from the agent's usage report.
        duration_seconds: Wall-clock execution time in seconds.
        error: Error message when status is ``"failed"``.
    """
    step_id: str
    agent_name: str
    status: str = "complete"
    outcome: str = ""
    files_changed: list[str] = field(default_factory=list)
    commit_hash: str = ""
    estimated_tokens: int = 0
    duration_seconds: float = 0.0
    error: str = ""


class AgentLauncher(Protocol):
    """Protocol for launching agents.

    Any class implementing this protocol can be used by ``StepScheduler``
    and ``TaskWorker`` to dispatch plan steps.  The protocol requires a
    single async method that takes agent metadata and a prompt, and returns
    a ``LaunchResult``.

    Implementations can be Claude Code subagents, subprocess calls, API
    requests, or dry-run mocks.
    """

    async def launch(
        self,
        agent_name: str,
        model: str,
        prompt: str,
        step_id: str = "",
        mcp_servers: list[str] | None = None,
    ) -> LaunchResult:
        """Launch an agent and return its result.

        Args:
            agent_name: Name of the agent to launch (e.g.,
                ``"backend-engineer--python"``).
            model: Model identifier (e.g., ``"sonnet"``, ``"opus"``).
            prompt: The complete delegation prompt for the agent.
            step_id: Plan step identifier for tracking.
            mcp_servers: Optional list of MCP server names to enable for
                this dispatch.  When non-empty, the launcher passes them
                to the ``claude`` CLI via ``--mcp-config``.

        Returns:
            A ``LaunchResult`` with status, outcome, and metadata.
        """
        ...


class DryRunLauncher:
    """Mock launcher that logs dispatches and returns synthetic results.

    Useful for testing without actually calling Claude.  Optionally, callers
    can pre-configure per-step results via ``set_result()`` before running.

    Attributes:
        launches: List of dicts recording every launch call (agent_name,
            model, step_id).  Useful for asserting dispatch behavior in tests.
        _results: Pre-configured results keyed by step_id.  When a step_id
            has a configured result, ``launch()`` returns it instead of the
            default synthetic result.
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
        mcp_servers: list[str] | None = None,
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
