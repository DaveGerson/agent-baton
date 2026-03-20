from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from agent_baton.models.enums import (
    BudgetTier,
    ExecutionMode,
    FailureClass,
    GateOutcome,
    GitStrategy,
    RiskLevel,
    TrustLevel,
)


@dataclass
class AgentAssignment:
    """A single agent's assignment within an execution plan."""
    agent_name: str
    model: str = "sonnet"
    trust_level: TrustLevel = TrustLevel.FULL_AUTONOMY
    task_description: str = ""
    depends_on: list[str] = field(default_factory=list)  # step IDs
    deliverables: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    blocked_paths: list[str] = field(default_factory=list)


@dataclass
class QAGate:
    """A quality gate between execution phases."""
    gate_type: str  # "Build Check", "Test Gate", "Contract Check", etc.
    description: str = ""
    fail_criteria: list[str] = field(default_factory=list)
    outcome: GateOutcome | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Phase:
    """A phase in a phased execution plan."""
    name: str
    steps: list[AgentAssignment] = field(default_factory=list)
    gate: QAGate | None = None


@dataclass
class ExecutionPlan:
    """The full execution plan for an orchestrated task."""
    task_summary: str
    risk_level: RiskLevel = RiskLevel.LOW
    budget_tier: BudgetTier = BudgetTier.STANDARD
    execution_mode: ExecutionMode = ExecutionMode.PHASED
    git_strategy: GitStrategy = GitStrategy.COMMIT_PER_AGENT
    phases: list[Phase] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def all_agents(self) -> list[str]:
        """Return all agent names in the plan."""
        return [step.agent_name for phase in self.phases for step in phase.steps]

    @property
    def total_steps(self) -> int:
        return sum(len(phase.steps) for phase in self.phases)

    @property
    def requires_auditor(self) -> bool:
        return self.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)

    def to_markdown(self) -> str:
        """Render the execution plan as markdown for writing to disk."""
        lines = [
            f"# Execution Plan",
            f"",
            f"**Task**: {self.task_summary}",
            f"**Risk Level**: {self.risk_level.value}",
            f"**Budget Tier**: {self.budget_tier.value}",
            f"**Execution Mode**: {self.execution_mode.value}",
            f"**Git Strategy**: {self.git_strategy.value}",
            f"**Created**: {self.created_at.isoformat()}",
            f"",
        ]

        for i, phase in enumerate(self.phases, 1):
            lines.append(f"## Phase {i}: {phase.name}")
            lines.append("")
            for j, step in enumerate(phase.steps, 1):
                lines.append(f"### Step {i}.{j}: {step.agent_name}")
                lines.append(f"- **Model**: {step.model}")
                lines.append(f"- **Trust Level**: {step.trust_level.value}")
                if step.task_description:
                    lines.append(f"- **Task**: {step.task_description}")
                if step.depends_on:
                    lines.append(f"- **Depends on**: {', '.join(step.depends_on)}")
                if step.deliverables:
                    lines.append(f"- **Deliverables**: {', '.join(step.deliverables)}")
                if step.allowed_paths:
                    lines.append(f"- **Writes to**: {', '.join(step.allowed_paths)}")
                if step.blocked_paths:
                    lines.append(f"- **Blocked from**: {', '.join(step.blocked_paths)}")
                lines.append("")

            if phase.gate:
                lines.append(f"### Gate: {phase.gate.gate_type}")
                if phase.gate.description:
                    lines.append(phase.gate.description)
                if phase.gate.fail_criteria:
                    lines.append("**FAIL if:**")
                    for c in phase.gate.fail_criteria:
                        lines.append(f"- {c}")
                lines.append("")

        return "\n".join(lines)


@dataclass
class MissionLogEntry:
    """A single entry in the mission log."""
    agent_name: str
    status: str  # "COMPLETE", "FAILED", "RETRIED", "ESCALATED"
    assignment: str = ""
    result: str = ""
    files: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    handoff: str = ""
    commit_hash: str = ""
    failure_class: FailureClass | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_markdown(self) -> str:
        lines = [
            f"### {self.timestamp.isoformat()} — {self.agent_name} — {self.status}",
            f"Assignment: {self.assignment}",
        ]
        if self.result:
            lines.append(f"Result: {self.result}")
        if self.files:
            lines.append(f"Files: {', '.join(self.files)}")
        if self.decisions:
            lines.append("Decisions:")
            for d in self.decisions:
                lines.append(f"  - {d}")
        if self.issues:
            lines.append("Issues:")
            for i in self.issues:
                lines.append(f"  - {i}")
        if self.handoff:
            lines.append(f"Handoff: {self.handoff}")
        if self.commit_hash:
            lines.append(f"Commit: {self.commit_hash}")
        if self.failure_class:
            lines.append(f"Failure class: {self.failure_class.value}")
        lines.append("")
        return "\n".join(lines)
