"""Data models for parallel (multi-execution) support.

ExecutionRecord tracks the metadata of a single namespaced execution,
enabling ``baton execute list`` to display all active/completed executions
at a glance.

ResourceLimits defines concurrency constraints for the parallel execution
engine (Stage 2 of Proposal 004).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExecutionRecord:
    """Summary record for one namespaced execution.

    This is a *read model* assembled from ``execution-state.json``, PID
    files, and plan metadata.  It is never persisted directly -- it is
    projected on the fly by ``exec_list`` and the ``list_workers()`` API.

    Attributes:
        execution_id: The namespaced task ID (directory name under
            ``executions/``).
        project_path: Absolute path of the project root.
        status: Execution status (``running``, ``complete``, ``failed``,
            ``gate_pending``, ``approval_pending``).
        plan_summary: First ~120 chars of the plan's task_summary.
        worker_pid: PID of the daemon worker, or 0 if not running.
        started_at: ISO 8601 timestamp when execution started.
        updated_at: ISO 8601 timestamp of last state write.
        risk_level: Risk level from the plan (``LOW``, ``MEDIUM``, ``HIGH``).
        budget_tier: Budget tier from the plan (``lean``, ``standard``,
            ``full``).
        steps_total: Total number of steps in the plan.
        steps_complete: Number of steps that have completed successfully.
        git_branch: Git branch associated with this execution, if known.
        tokens_estimated: Estimated total tokens consumed so far.
    """

    execution_id: str
    project_path: str = ""
    status: str = "running"
    plan_summary: str = ""
    worker_pid: int = 0
    started_at: str = ""
    updated_at: str = ""
    risk_level: str = "LOW"
    budget_tier: str = "lean"
    steps_total: int = 0
    steps_complete: int = 0
    git_branch: str = ""
    tokens_estimated: int = 0

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "project_path": self.project_path,
            "status": self.status,
            "plan_summary": self.plan_summary,
            "worker_pid": self.worker_pid,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "risk_level": self.risk_level,
            "budget_tier": self.budget_tier,
            "steps_total": self.steps_total,
            "steps_complete": self.steps_complete,
            "git_branch": self.git_branch,
            "tokens_estimated": self.tokens_estimated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExecutionRecord:
        return cls(
            execution_id=data["execution_id"],
            project_path=data.get("project_path", ""),
            status=data.get("status", "running"),
            plan_summary=data.get("plan_summary", ""),
            worker_pid=int(data.get("worker_pid", 0)),
            started_at=data.get("started_at", ""),
            updated_at=data.get("updated_at", ""),
            risk_level=data.get("risk_level", "LOW"),
            budget_tier=data.get("budget_tier", "lean"),
            steps_total=int(data.get("steps_total", 0)),
            steps_complete=int(data.get("steps_complete", 0)),
            git_branch=data.get("git_branch", ""),
            tokens_estimated=int(data.get("tokens_estimated", 0)),
        )


@dataclass
class ResourceLimits:
    """Concurrency and resource constraints for parallel execution.

    These defaults are intentionally conservative.  Stage 2 of Proposal 004
    will add CLI overrides and per-project configuration.

    Attributes:
        max_concurrent_executions: Maximum number of executions that can
            be running simultaneously (across all projects).
        max_concurrent_agents: Maximum total agent subprocesses across
            all active executions.
        max_tokens_per_minute: Token rate limit (0 = unlimited).
        max_concurrent_per_project: Maximum executions within a single
            project directory.
    """

    max_concurrent_executions: int = 3
    max_concurrent_agents: int = 8
    max_tokens_per_minute: int = 0
    max_concurrent_per_project: int = 2

    def to_dict(self) -> dict:
        return {
            "max_concurrent_executions": self.max_concurrent_executions,
            "max_concurrent_agents": self.max_concurrent_agents,
            "max_tokens_per_minute": self.max_tokens_per_minute,
            "max_concurrent_per_project": self.max_concurrent_per_project,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ResourceLimits:
        return cls(
            max_concurrent_executions=int(
                data.get("max_concurrent_executions", 3)
            ),
            max_concurrent_agents=int(
                data.get("max_concurrent_agents", 8)
            ),
            max_tokens_per_minute=int(
                data.get("max_tokens_per_minute", 0)
            ),
            max_concurrent_per_project=int(
                data.get("max_concurrent_per_project", 2)
            ),
        )
