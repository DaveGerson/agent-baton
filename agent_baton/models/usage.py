"""Usage tracking models — token and resource consumption per agent and task.

These records are persisted to the usage log after each execution
completes.  They feed the budget tuner, pattern learner, and
dashboard analytics.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class AgentUsageRecord:
    """Resource consumption record for a single agent within a task.

    One record is created per agent dispatch.  Multiple records for
    the same agent appear when the agent is retried or dispatched
    across different steps.

    Attributes:
        name: Agent name (matches ``AgentDefinition.name``).
        model: LLM model used for this dispatch.
        steps: Number of plan steps this agent handled.
        retries: How many times the dispatch was retried.
        gate_results: Gate outcome strings that followed this agent's work.
        estimated_tokens: Estimated token consumption for the dispatch.
        duration_seconds: Wall-clock time the agent was running.
    """

    name: str
    model: str = "sonnet"
    steps: int = 1
    retries: int = 0
    gate_results: list[str] = field(default_factory=list)
    estimated_tokens: int = 0
    duration_seconds: float = 0.0
    # Tenancy attribution (F0.2)
    agent_type: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> AgentUsageRecord:
        return cls(
            name=data["name"],
            model=data.get("model", "sonnet"),
            steps=data.get("steps", 1),
            retries=data.get("retries", 0),
            gate_results=data.get("gate_results", []),
            estimated_tokens=data.get("estimated_tokens", 0),
            duration_seconds=data.get("duration_seconds", 0.0),
            agent_type=data.get("agent_type", ""),
        )


@dataclass
class TaskUsageRecord:
    """Aggregate resource consumption for a complete orchestrated task.

    Written to the usage log when ``baton execute complete`` finalizes
    an execution.  The budget tuner and pattern learner read these
    records to generate ``BudgetRecommendation`` and ``LearnedPattern``
    instances.

    Attributes:
        task_id: Unique execution identifier.
        timestamp: ISO 8601 time when the record was written.
        agents_used: Per-agent usage breakdowns.
        total_agents: Count of distinct agents dispatched.
        risk_level: Risk tier assigned to the plan.
        sequencing_mode: Execution mode used (maps to ``ExecutionMode``).
        gates_passed: Number of QA gates that passed.
        gates_failed: Number of QA gates that failed.
        outcome: Final verdict — ``"SHIP"``, ``"SHIP WITH NOTES"``,
            ``"REVISE"``, or ``"BLOCK"``.
        notes: Free-text notes from the orchestrator.
    """

    task_id: str
    timestamp: str  # ISO format
    agents_used: list[AgentUsageRecord] = field(default_factory=list)
    total_agents: int = 0
    risk_level: str = "LOW"
    sequencing_mode: str = "phased_delivery"
    gates_passed: int = 0
    gates_failed: int = 0
    outcome: str = ""  # "SHIP", "SHIP WITH NOTES", "REVISE", "BLOCK"
    notes: str = ""
    # Tenancy attribution (F0.2)
    org_id: str = ""
    team_id: str = ""
    user_id: str = ""
    spec_author_id: str = ""
    cost_center: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # asdict handles nested dataclasses recursively, so agents_used is
        # already a list[dict] at this point — no manual conversion needed.
        return d

    @classmethod
    def from_dict(cls, data: dict) -> TaskUsageRecord:
        agents_used = [
            AgentUsageRecord.from_dict(a)
            for a in data.get("agents_used", [])
        ]
        return cls(
            task_id=data["task_id"],
            timestamp=data["timestamp"],
            agents_used=agents_used,
            total_agents=data.get("total_agents", 0),
            risk_level=data.get("risk_level", "LOW"),
            sequencing_mode=data.get("sequencing_mode", "phased_delivery"),
            gates_passed=data.get("gates_passed", 0),
            gates_failed=data.get("gates_failed", 0),
            outcome=data.get("outcome", ""),
            notes=data.get("notes", ""),
            org_id=data.get("org_id", ""),
            team_id=data.get("team_id", ""),
            user_id=data.get("user_id", ""),
            spec_author_id=data.get("spec_author_id", ""),
            cost_center=data.get("cost_center", ""),
        )
