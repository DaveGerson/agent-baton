"""Plan builder — creates and manages execution plans."""
from __future__ import annotations

from pathlib import Path

from agent_baton.models.enums import (
    BudgetTier,
    ExecutionMode,
    GitStrategy,
    RiskLevel,
    TrustLevel,
)
from agent_baton.models.plan import (
    AgentAssignment,
    ExecutionPlan,
    Phase,
    QAGate,
)


# Risk signal keywords → risk level
RISK_SIGNALS: dict[str, RiskLevel] = {
    "production": RiskLevel.HIGH,
    "infrastructure": RiskLevel.HIGH,
    "docker": RiskLevel.HIGH,
    "ci/cd": RiskLevel.HIGH,
    "deploy": RiskLevel.HIGH,
    "terraform": RiskLevel.HIGH,
    "compliance": RiskLevel.HIGH,
    "regulated": RiskLevel.HIGH,
    "audit": RiskLevel.HIGH,
    "migration": RiskLevel.MEDIUM,
    "database": RiskLevel.MEDIUM,
    "schema": RiskLevel.MEDIUM,
    "bash": RiskLevel.MEDIUM,
    "security": RiskLevel.HIGH,
    "authentication": RiskLevel.HIGH,
    "secrets": RiskLevel.HIGH,
}


class PlanBuilder:
    """Build execution plans from task descriptions and agent assignments."""

    def create(
        self,
        task_summary: str,
        phases: list[Phase] | None = None,
        *,
        risk_level: RiskLevel | None = None,
        budget_tier: BudgetTier | None = None,
        execution_mode: ExecutionMode = ExecutionMode.PHASED,
        git_strategy: GitStrategy | None = None,
    ) -> ExecutionPlan:
        """Create a new execution plan.

        Args:
            task_summary: One-line description of the task.
            phases: Pre-built phases, or None for an empty plan.
            risk_level: Override auto-detected risk. None = auto-detect.
            budget_tier: Override auto-selected tier. None = auto-select.
            execution_mode: How phases are sequenced.
            git_strategy: Override based on risk. None = auto-select.

        Returns:
            A fully constructed ExecutionPlan.
        """
        if risk_level is None:
            risk_level = self.assess_risk(task_summary)

        if budget_tier is None:
            total_agents = sum(len(p.steps) for p in (phases or []))
            budget_tier = self._select_budget_tier(total_agents)

        if git_strategy is None:
            git_strategy = self._select_git_strategy(risk_level)

        return ExecutionPlan(
            task_summary=task_summary,
            risk_level=risk_level,
            budget_tier=budget_tier,
            execution_mode=execution_mode,
            git_strategy=git_strategy,
            phases=phases or [],
        )

    def add_phase(
        self,
        plan: ExecutionPlan,
        name: str,
        steps: list[AgentAssignment] | None = None,
        gate: QAGate | None = None,
    ) -> Phase:
        """Add a phase to an existing plan.

        Returns:
            The newly created Phase.
        """
        phase = Phase(name=name, steps=steps or [], gate=gate)
        plan.phases.append(phase)
        return phase

    def add_step(
        self,
        phase: Phase,
        agent_name: str,
        *,
        task_description: str = "",
        model: str = "sonnet",
        trust_level: TrustLevel = TrustLevel.FULL_AUTONOMY,
        depends_on: list[str] | None = None,
        deliverables: list[str] | None = None,
        allowed_paths: list[str] | None = None,
        blocked_paths: list[str] | None = None,
    ) -> AgentAssignment:
        """Add a step (agent assignment) to a phase.

        Returns:
            The newly created AgentAssignment.
        """
        step = AgentAssignment(
            agent_name=agent_name,
            model=model,
            trust_level=trust_level,
            task_description=task_description,
            depends_on=depends_on or [],
            deliverables=deliverables or [],
            allowed_paths=allowed_paths or [],
            blocked_paths=blocked_paths or [],
        )
        phase.steps.append(step)
        return step

    def assess_risk(self, task_description: str) -> RiskLevel:
        """Assess risk level from task description keywords.

        Scans for known risk signal words and returns the highest
        risk level found.
        """
        description_lower = task_description.lower()
        highest = RiskLevel.LOW

        for keyword, level in RISK_SIGNALS.items():
            if keyword in description_lower:
                if self._risk_ordinal(level) > self._risk_ordinal(highest):
                    highest = level

        return highest

    def write_to_disk(self, plan: ExecutionPlan, path: Path) -> None:
        """Write the execution plan as markdown to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plan.to_markdown(), encoding="utf-8")

    @staticmethod
    def _select_budget_tier(agent_count: int) -> BudgetTier:
        if agent_count <= 2:
            return BudgetTier.LEAN
        elif agent_count <= 5:
            return BudgetTier.STANDARD
        return BudgetTier.FULL

    @staticmethod
    def _select_git_strategy(risk: RiskLevel) -> GitStrategy:
        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return GitStrategy.BRANCH_PER_AGENT
        return GitStrategy.COMMIT_PER_AGENT

    @staticmethod
    def _risk_ordinal(level: RiskLevel) -> int:
        return {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.CRITICAL: 3,
        }[level]
