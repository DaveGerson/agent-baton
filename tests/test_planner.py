"""Tests for agent_baton.core.plan.PlanBuilder."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.plan import PlanBuilder, RISK_SIGNALS
from agent_baton.models.enums import (
    BudgetTier,
    ExecutionMode,
    GitStrategy,
    RiskLevel,
    TrustLevel,
)
from agent_baton.models.plan import AgentAssignment, ExecutionPlan, Phase, QAGate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _plan_with_n_agents(builder: PlanBuilder, n: int) -> ExecutionPlan:
    """Create a plan with exactly n agents spread across one or two phases."""
    steps = [AgentAssignment(agent_name=f"agent-{i}") for i in range(n)]
    phase = Phase(name="Phase 1", steps=steps)
    return builder.create("Task", phases=[phase])


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------

class TestCreate:
    def test_returns_execution_plan(self):
        builder = PlanBuilder()
        plan = builder.create("Build an API")
        assert isinstance(plan, ExecutionPlan)

    def test_task_summary_is_set(self):
        builder = PlanBuilder()
        plan = builder.create("Deploy microservices")
        assert plan.task_summary == "Deploy microservices"

    def test_default_execution_mode_is_phased(self):
        builder = PlanBuilder()
        plan = builder.create("Some task")
        assert plan.execution_mode == ExecutionMode.PHASED

    def test_explicit_execution_mode_is_respected(self):
        builder = PlanBuilder()
        plan = builder.create("Some task", execution_mode=ExecutionMode.PARALLEL)
        assert plan.execution_mode == ExecutionMode.PARALLEL

    def test_empty_phases_when_none_given(self):
        builder = PlanBuilder()
        plan = builder.create("Some task")
        assert plan.phases == []

    def test_phases_are_passed_through(self):
        builder = PlanBuilder()
        phase = Phase(name="P1", steps=[AgentAssignment(agent_name="architect")])
        plan = builder.create("Some task", phases=[phase])
        assert len(plan.phases) == 1
        assert plan.phases[0].name == "P1"

    def test_explicit_risk_level_overrides_auto_detection(self):
        builder = PlanBuilder()
        # Task summary contains no risk words; override with CRITICAL
        plan = builder.create("Refactor utility module", risk_level=RiskLevel.CRITICAL)
        assert plan.risk_level == RiskLevel.CRITICAL

    def test_auto_risk_detection_is_used_when_not_overridden(self):
        builder = PlanBuilder()
        plan = builder.create("Deploy to production")
        # "production" and "deploy" are both HIGH signals
        assert plan.risk_level == RiskLevel.HIGH

    def test_explicit_budget_tier_overrides_auto(self):
        builder = PlanBuilder()
        plan = builder.create("Some task", budget_tier=BudgetTier.FULL)
        assert plan.budget_tier == BudgetTier.FULL

    def test_explicit_git_strategy_overrides_auto(self):
        builder = PlanBuilder()
        plan = builder.create(
            "Some task",
            git_strategy=GitStrategy.NONE,
        )
        assert plan.git_strategy == GitStrategy.NONE

    def test_plan_has_created_at_timestamp(self):
        builder = PlanBuilder()
        plan = builder.create("Some task")
        assert plan.created_at is not None


# ---------------------------------------------------------------------------
# assess_risk()
# ---------------------------------------------------------------------------

class TestAssessRisk:
    @pytest.mark.parametrize("keyword", [
        "production", "infrastructure", "docker", "ci/cd", "deploy",
        "terraform", "compliance", "regulated", "audit", "security",
        "authentication", "secrets",
    ])
    def test_high_risk_keywords(self, keyword: str):
        builder = PlanBuilder()
        result = builder.assess_risk(f"Task involves {keyword}")
        assert result == RiskLevel.HIGH

    @pytest.mark.parametrize("keyword", [
        "migration", "database", "schema", "bash",
    ])
    def test_medium_risk_keywords(self, keyword: str):
        builder = PlanBuilder()
        result = builder.assess_risk(f"Update the {keyword} configuration")
        assert result == RiskLevel.MEDIUM

    def test_benign_description_returns_low(self):
        builder = PlanBuilder()
        result = builder.assess_risk("Rename a variable in a utility module")
        assert result == RiskLevel.LOW

    def test_refactor_returns_low(self):
        builder = PlanBuilder()
        result = builder.assess_risk("Refactor the payment service helper")
        assert result == RiskLevel.LOW

    def test_highest_risk_wins_when_multiple_signals(self):
        builder = PlanBuilder()
        # database=MEDIUM, deploy=HIGH → should be HIGH
        result = builder.assess_risk("Deploy new database migration")
        assert result == RiskLevel.HIGH

    def test_case_insensitive_matching(self):
        builder = PlanBuilder()
        result = builder.assess_risk("Update PRODUCTION environment")
        assert result == RiskLevel.HIGH

    def test_empty_string_returns_low(self):
        builder = PlanBuilder()
        result = builder.assess_risk("")
        assert result == RiskLevel.LOW

    def test_partial_keyword_match_triggers_risk(self):
        """'deployment' contains 'deploy', which is a signal."""
        builder = PlanBuilder()
        result = builder.assess_risk("Create a deployment pipeline")
        assert result == RiskLevel.HIGH


# ---------------------------------------------------------------------------
# add_phase() and add_step()
# ---------------------------------------------------------------------------

class TestAddPhase:
    def test_adds_phase_to_plan(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        builder.add_phase(plan, "Implementation")
        assert len(plan.phases) == 1
        assert plan.phases[0].name == "Implementation"

    def test_returns_the_new_phase(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        phase = builder.add_phase(plan, "Review")
        assert isinstance(phase, Phase)
        assert phase.name == "Review"

    def test_adds_multiple_phases(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        builder.add_phase(plan, "Phase 1")
        builder.add_phase(plan, "Phase 2")
        assert len(plan.phases) == 2

    def test_adds_phase_with_gate(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        gate = QAGate(gate_type="Build Check")
        phase = builder.add_phase(plan, "Build", gate=gate)
        assert phase.gate is not None
        assert phase.gate.gate_type == "Build Check"

    def test_adds_phase_with_predefined_steps(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        steps = [AgentAssignment(agent_name="architect")]
        phase = builder.add_phase(plan, "Design", steps=steps)
        assert len(phase.steps) == 1


class TestAddStep:
    def test_adds_step_to_phase(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        phase = builder.add_phase(plan, "Phase 1")
        builder.add_step(phase, "architect")
        assert len(phase.steps) == 1
        assert phase.steps[0].agent_name == "architect"

    def test_returns_agent_assignment(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        phase = builder.add_phase(plan, "Phase 1")
        step = builder.add_step(phase, "architect")
        assert isinstance(step, AgentAssignment)

    def test_step_task_description_set(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        phase = builder.add_phase(plan, "Phase 1")
        step = builder.add_step(phase, "architect", task_description="Design the schema")
        assert step.task_description == "Design the schema"

    def test_step_default_trust_level(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        phase = builder.add_phase(plan, "Phase 1")
        step = builder.add_step(phase, "architect")
        assert step.trust_level == TrustLevel.FULL_AUTONOMY

    def test_step_explicit_trust_level(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        phase = builder.add_phase(plan, "Phase 1")
        step = builder.add_step(phase, "architect", trust_level=TrustLevel.RESTRICTED)
        assert step.trust_level == TrustLevel.RESTRICTED

    def test_step_depends_on(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        phase = builder.add_phase(plan, "Phase 1")
        step = builder.add_step(phase, "test-engineer", depends_on=["1.1"])
        assert step.depends_on == ["1.1"]

    def test_step_deliverables(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        phase = builder.add_phase(plan, "Phase 1")
        step = builder.add_step(phase, "architect", deliverables=["schema.sql"])
        assert step.deliverables == ["schema.sql"]

    def test_step_allowed_paths(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        phase = builder.add_phase(plan, "Phase 1")
        step = builder.add_step(phase, "backend-engineer--python", allowed_paths=["src/"])
        assert step.allowed_paths == ["src/"]

    def test_step_blocked_paths(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        phase = builder.add_phase(plan, "Phase 1")
        step = builder.add_step(phase, "backend-engineer--python", blocked_paths=["secrets/"])
        assert step.blocked_paths == ["secrets/"]

    def test_multiple_steps_added_in_order(self):
        builder = PlanBuilder()
        plan = builder.create("Task")
        phase = builder.add_phase(plan, "Phase 1")
        builder.add_step(phase, "architect")
        builder.add_step(phase, "backend-engineer--python")
        assert phase.steps[0].agent_name == "architect"
        assert phase.steps[1].agent_name == "backend-engineer--python"


# ---------------------------------------------------------------------------
# write_to_disk()
# ---------------------------------------------------------------------------

class TestWriteToDisk:
    def test_creates_file(self, tmp_path: Path):
        builder = PlanBuilder()
        plan = builder.create("Write a utility")
        output = tmp_path / "plan.md"
        builder.write_to_disk(plan, output)
        assert output.exists()

    def test_file_contains_task_summary(self, tmp_path: Path):
        builder = PlanBuilder()
        plan = builder.create("Build authentication module")
        output = tmp_path / "plan.md"
        builder.write_to_disk(plan, output)
        content = output.read_text(encoding="utf-8")
        assert "Build authentication module" in content

    def test_creates_parent_directories(self, tmp_path: Path):
        builder = PlanBuilder()
        plan = builder.create("Task")
        nested_path = tmp_path / "deep" / "nested" / "dir" / "plan.md"
        builder.write_to_disk(plan, nested_path)
        assert nested_path.exists()

    def test_file_is_valid_markdown(self, tmp_path: Path):
        builder = PlanBuilder()
        plan = builder.create("Task")
        phase = builder.add_phase(plan, "Implementation")
        builder.add_step(phase, "architect", task_description="Design system")
        output = tmp_path / "plan.md"
        builder.write_to_disk(plan, output)
        content = output.read_text(encoding="utf-8")
        assert content.startswith("# Execution Plan")

    def test_overwrites_existing_file(self, tmp_path: Path):
        builder = PlanBuilder()
        output = tmp_path / "plan.md"
        output.write_text("old content", encoding="utf-8")
        plan = builder.create("New task")
        builder.write_to_disk(plan, output)
        content = output.read_text(encoding="utf-8")
        assert "old content" not in content
        assert "New task" in content


# ---------------------------------------------------------------------------
# Budget tier selection
# ---------------------------------------------------------------------------

class TestBudgetTierSelection:
    @pytest.mark.parametrize("n_agents,expected_tier", [
        (0, BudgetTier.LEAN),
        (1, BudgetTier.LEAN),
        (2, BudgetTier.LEAN),
    ])
    def test_lean_tier(self, n_agents: int, expected_tier: BudgetTier):
        tier = PlanBuilder._select_budget_tier(n_agents)
        assert tier == expected_tier

    @pytest.mark.parametrize("n_agents,expected_tier", [
        (3, BudgetTier.STANDARD),
        (4, BudgetTier.STANDARD),
        (5, BudgetTier.STANDARD),
    ])
    def test_standard_tier(self, n_agents: int, expected_tier: BudgetTier):
        tier = PlanBuilder._select_budget_tier(n_agents)
        assert tier == expected_tier

    @pytest.mark.parametrize("n_agents,expected_tier", [
        (6, BudgetTier.FULL),
        (7, BudgetTier.FULL),
        (10, BudgetTier.FULL),
    ])
    def test_full_tier(self, n_agents: int, expected_tier: BudgetTier):
        tier = PlanBuilder._select_budget_tier(n_agents)
        assert tier == expected_tier

    def test_create_auto_selects_lean_for_one_agent(self):
        builder = PlanBuilder()
        steps = [AgentAssignment(agent_name="architect")]
        phase = Phase(name="P1", steps=steps)
        plan = builder.create("Task", phases=[phase])
        assert plan.budget_tier == BudgetTier.LEAN

    def test_create_auto_selects_standard_for_four_agents(self):
        builder = PlanBuilder()
        steps = [AgentAssignment(agent_name=f"a{i}") for i in range(4)]
        phase = Phase(name="P1", steps=steps)
        plan = builder.create("Task", phases=[phase])
        assert plan.budget_tier == BudgetTier.STANDARD

    def test_create_auto_selects_full_for_seven_agents(self):
        builder = PlanBuilder()
        steps = [AgentAssignment(agent_name=f"a{i}") for i in range(7)]
        phase = Phase(name="P1", steps=steps)
        plan = builder.create("Task", phases=[phase])
        assert plan.budget_tier == BudgetTier.FULL


# ---------------------------------------------------------------------------
# Git strategy selection
# ---------------------------------------------------------------------------

class TestGitStrategySelection:
    @pytest.mark.parametrize("risk", [RiskLevel.HIGH, RiskLevel.CRITICAL])
    def test_high_and_critical_risk_gets_branch_per_agent(self, risk: RiskLevel):
        strategy = PlanBuilder._select_git_strategy(risk)
        assert strategy == GitStrategy.BRANCH_PER_AGENT

    @pytest.mark.parametrize("risk", [RiskLevel.LOW, RiskLevel.MEDIUM])
    def test_low_and_medium_risk_gets_commit_per_agent(self, risk: RiskLevel):
        strategy = PlanBuilder._select_git_strategy(risk)
        assert strategy == GitStrategy.COMMIT_PER_AGENT

    def test_deploy_task_gets_branch_per_agent_strategy(self):
        builder = PlanBuilder()
        plan = builder.create("Deploy to production")
        assert plan.git_strategy == GitStrategy.BRANCH_PER_AGENT

    def test_benign_task_gets_commit_per_agent_strategy(self):
        builder = PlanBuilder()
        plan = builder.create("Add a docstring to the main function")
        assert plan.git_strategy == GitStrategy.COMMIT_PER_AGENT
