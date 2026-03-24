"""Tests for agent_baton data model classes."""
from __future__ import annotations

from datetime import datetime

import pytest

from agent_baton.models.agent import AgentDefinition
from agent_baton.models.enums import (
    AgentCategory,
    BudgetTier,
    ExecutionMode,
    FailureClass,
    GateOutcome,
    GitStrategy,
    RiskLevel,
    TrustLevel,
)
from agent_baton.models.plan import (
    AgentAssignment,
    ExecutionPlan,
    MissionLogEntry,
    Phase,
    QAGate,
)


# ---------------------------------------------------------------------------
# AgentDefinition
# ---------------------------------------------------------------------------

# DECISION: Parameterize base_name, flavor, and is_flavored into one test each.
# The individual unflavored/flavored/hyphenated cases are preserved as tuples.

class TestAgentDefinitionBaseName:
    @pytest.mark.parametrize("name,expected", [
        ("architect", "architect"),
        ("backend-engineer--python", "backend-engineer"),
        ("frontend-engineer--react", "frontend-engineer"),
        ("test-engineer", "test-engineer"),
    ])
    def test_base_name(self, name, expected):
        agent = AgentDefinition(name=name, description="")
        assert agent.base_name == expected


class TestAgentDefinitionFlavor:
    @pytest.mark.parametrize("name,expected", [
        ("architect", None),
        ("backend-engineer--python", "python"),
        ("frontend-engineer--react", "react"),
        ("backend-engineer--node", "node"),
    ])
    def test_flavor(self, name, expected):
        agent = AgentDefinition(name=name, description="")
        assert agent.flavor == expected


class TestAgentDefinitionIsFlavored:
    @pytest.mark.parametrize("name,expected", [
        ("architect", False),
        ("backend-engineer--python", True),
        ("test-engineer", False),
    ])
    def test_is_flavored(self, name, expected):
        agent = AgentDefinition(name=name, description="")
        assert agent.is_flavored is expected


class TestAgentDefinitionCategory:
    @pytest.mark.parametrize("name,expected", [
        ("architect", AgentCategory.ENGINEERING),
        ("backend-engineer", AgentCategory.ENGINEERING),
        ("backend-engineer--python", AgentCategory.ENGINEERING),
        ("frontend-engineer", AgentCategory.ENGINEERING),
        ("frontend-engineer--react", AgentCategory.ENGINEERING),
        ("devops-engineer", AgentCategory.ENGINEERING),
        ("test-engineer", AgentCategory.ENGINEERING),
        ("data-engineer", AgentCategory.ENGINEERING),
    ])
    def test_engineering_agents(self, name, expected):
        agent = AgentDefinition(name=name, description="")
        assert agent.category == expected

    @pytest.mark.parametrize("name,expected", [
        ("data-scientist", AgentCategory.DATA),
        ("data-analyst", AgentCategory.DATA),
        ("visualization-expert", AgentCategory.DATA),
    ])
    def test_data_agents(self, name, expected):
        agent = AgentDefinition(name=name, description="")
        assert agent.category == expected

    @pytest.mark.parametrize("name,expected", [
        ("subject-matter-expert", AgentCategory.DOMAIN),
    ])
    def test_domain_agents(self, name, expected):
        agent = AgentDefinition(name=name, description="")
        assert agent.category == expected

    @pytest.mark.parametrize("name,expected", [
        ("security-reviewer", AgentCategory.REVIEW),
        ("code-reviewer", AgentCategory.REVIEW),
        ("auditor", AgentCategory.REVIEW),
    ])
    def test_review_agents(self, name, expected):
        agent = AgentDefinition(name=name, description="")
        assert agent.category == expected

    @pytest.mark.parametrize("name,expected", [
        ("talent-builder", AgentCategory.META),
        ("orchestrator", AgentCategory.META),
    ])
    def test_meta_agents(self, name, expected):
        agent = AgentDefinition(name=name, description="")
        assert agent.category == expected

    def test_unknown_name_defaults_to_engineering(self):
        agent = AgentDefinition(name="unknown-widget", description="")
        assert agent.category == AgentCategory.ENGINEERING



# ---------------------------------------------------------------------------
# ExecutionPlan
# ---------------------------------------------------------------------------

def _make_plan(**kwargs) -> ExecutionPlan:
    defaults = dict(
        task_summary="Test task",
        risk_level=RiskLevel.LOW,
        budget_tier=BudgetTier.STANDARD,
        execution_mode=ExecutionMode.PHASED,
        git_strategy=GitStrategy.COMMIT_PER_AGENT,
        phases=[],
    )
    defaults.update(kwargs)
    return ExecutionPlan(**defaults)


class TestExecutionPlanAllAgents:
    def test_empty_plan_has_no_agents(self):
        plan = _make_plan()
        assert plan.all_agents == []

    def test_single_phase_single_step(self):
        step = AgentAssignment(agent_name="architect")
        phase = Phase(name="Phase 1", steps=[step])
        plan = _make_plan(phases=[phase])
        assert plan.all_agents == ["architect"]

    def test_multiple_phases_multiple_steps(self):
        steps1 = [AgentAssignment(agent_name="architect"),
                  AgentAssignment(agent_name="backend-engineer--python")]
        steps2 = [AgentAssignment(agent_name="test-engineer")]
        phases = [
            Phase(name="Phase 1", steps=steps1),
            Phase(name="Phase 2", steps=steps2),
        ]
        plan = _make_plan(phases=phases)
        assert plan.all_agents == ["architect", "backend-engineer--python", "test-engineer"]

    def test_returns_list_not_set(self):
        step = AgentAssignment(agent_name="architect")
        plan = _make_plan(phases=[Phase(name="p", steps=[step, step])])
        assert len(plan.all_agents) == 2  # duplicates preserved


class TestExecutionPlanTotalSteps:
    def test_empty_plan_zero_steps(self):
        plan = _make_plan()
        assert plan.total_steps == 0

    def test_counts_across_phases(self):
        phases = [
            Phase(name="P1", steps=[AgentAssignment(agent_name="a"),
                                    AgentAssignment(agent_name="b")]),
            Phase(name="P2", steps=[AgentAssignment(agent_name="c")]),
        ]
        plan = _make_plan(phases=phases)
        assert plan.total_steps == 3


class TestExecutionPlanRequiresAuditor:
    @pytest.mark.parametrize("risk", [RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL])
    def test_medium_and_above_requires_auditor(self, risk):
        plan = _make_plan(risk_level=risk)
        assert plan.requires_auditor is True

    def test_low_risk_does_not_require_auditor(self):
        plan = _make_plan(risk_level=RiskLevel.LOW)
        assert plan.requires_auditor is False


class TestExecutionPlanToMarkdown:
    # DECISION: Consolidated 10 separate field-presence tests into 2 parameterized
    # tests (scalar fields and structured content), plus kept tests for structural
    # properties (starts_with_h1) and special behaviours (gate, deliverables,
    # depends_on, paths) that each test distinct rendering logic.

    @pytest.mark.parametrize("kwargs,expected_substring", [
        ({"task_summary": "Deploy new payment service"}, "Deploy new payment service"),
        ({"risk_level": RiskLevel.HIGH}, "HIGH"),
        ({"budget_tier": BudgetTier.LEAN}, "Lean"),
        ({"git_strategy": GitStrategy.BRANCH_PER_AGENT}, "Branch-per-agent"),
    ])
    def test_scalar_fields_appear_in_output(self, kwargs, expected_substring):
        plan = _make_plan(**kwargs)
        assert expected_substring in plan.to_markdown()

    def test_starts_with_h1(self):
        plan = _make_plan()
        assert plan.to_markdown().startswith("# Execution Plan")

    def test_phase_name_appears(self):
        phase = Phase(name="Implementation", steps=[])
        plan = _make_plan(phases=[phase])
        md = plan.to_markdown()
        assert "Implementation" in md

    def test_step_agent_name_and_task_appear(self):
        step = AgentAssignment(agent_name="architect", task_description="Design API")
        phase = Phase(name="P1", steps=[step])
        plan = _make_plan(phases=[phase])
        md = plan.to_markdown()
        assert "architect" in md
        assert "Design API" in md

    def test_step_deliverables_appear(self):
        step = AgentAssignment(
            agent_name="architect",
            deliverables=["openapi.yaml", "erd.md"],
        )
        phase = Phase(name="P1", steps=[step])
        plan = _make_plan(phases=[phase])
        md = plan.to_markdown()
        assert "openapi.yaml" in md
        assert "erd.md" in md

    def test_gate_appears_when_set(self):
        gate = QAGate(gate_type="Test Gate", description="All tests must pass.")
        phase = Phase(name="P1", steps=[], gate=gate)
        plan = _make_plan(phases=[phase])
        md = plan.to_markdown()
        assert "Test Gate" in md
        assert "All tests must pass." in md

    def test_gate_fail_criteria_appear(self):
        gate = QAGate(
            gate_type="Build Check",
            fail_criteria=["Coverage < 80%", "Lint errors present"],
        )
        phase = Phase(name="P1", steps=[], gate=gate)
        plan = _make_plan(phases=[phase])
        md = plan.to_markdown()
        assert "Coverage < 80%" in md
        assert "Lint errors present" in md

    def test_depends_on_appears(self):
        step = AgentAssignment(agent_name="test-engineer", depends_on=["1.1", "1.2"])
        phase = Phase(name="P1", steps=[step])
        plan = _make_plan(phases=[phase])
        md = plan.to_markdown()
        assert "1.1" in md
        assert "1.2" in md

    def test_allowed_and_blocked_paths_appear(self):
        step = AgentAssignment(
            agent_name="backend-engineer--python",
            allowed_paths=["src/"],
            blocked_paths=["secrets/"],
        )
        phase = Phase(name="P1", steps=[step])
        plan = _make_plan(phases=[phase])
        md = plan.to_markdown()
        assert "src/" in md
        assert "secrets/" in md


# ---------------------------------------------------------------------------
# MissionLogEntry
# ---------------------------------------------------------------------------

class TestMissionLogEntryToMarkdown:
    # DECISION: Collapsed 13 individual "is X in markdown" tests into 2
    # parameterized tests covering always-present fields and optional fields,
    # plus kept the distinct conditional-absence tests and structural tests
    # (ends_with_blank_line). Each tuple preserves the original scenario's
    # input/output pair.

    def _make_entry(self, **kwargs) -> MissionLogEntry:
        defaults = dict(
            agent_name="architect",
            status="COMPLETE",
            assignment="Design the API",
            timestamp=datetime(2026, 1, 15, 10, 0, 0),
        )
        defaults.update(kwargs)
        return MissionLogEntry(**defaults)

    @pytest.mark.parametrize("kwargs,expected", [
        ({"agent_name": "backend-engineer--python"}, "backend-engineer--python"),
        ({"status": "FAILED"}, "FAILED"),
        ({"assignment": "Write the migration script"}, "Write the migration script"),
    ])
    def test_always_present_fields(self, kwargs, expected):
        entry = self._make_entry(**kwargs)
        assert expected in entry.to_markdown()

    @pytest.mark.parametrize("kwargs,expected", [
        ({"result": "Migration successful"}, "Migration successful"),
        ({"handoff": "Hand off to test-engineer"}, "Hand off to test-engineer"),
        ({"commit_hash": "abc1234"}, "abc1234"),
        ({"timestamp": datetime(2026, 1, 15, 10, 0, 0)}, "2026-01-15"),
    ])
    def test_optional_fields_appear_when_set(self, kwargs, expected):
        entry = self._make_entry(**kwargs)
        assert expected in entry.to_markdown()

    def test_result_absent_when_empty(self):
        entry = self._make_entry(result="")
        md = entry.to_markdown()
        assert "Result:" not in md

    def test_files_appear_when_set(self):
        entry = self._make_entry(files=["src/api.py", "tests/test_api.py"])
        md = entry.to_markdown()
        assert "src/api.py" in md
        assert "tests/test_api.py" in md

    def test_decisions_appear_as_list(self):
        entry = self._make_entry(decisions=["Used FastAPI", "SQLite for dev"])
        md = entry.to_markdown()
        assert "Used FastAPI" in md
        assert "SQLite for dev" in md
        assert "Decisions:" in md

    def test_issues_appear_as_list(self):
        entry = self._make_entry(issues=["Auth not implemented"])
        md = entry.to_markdown()
        assert "Auth not implemented" in md
        assert "Issues:" in md

    def test_failure_class_appears_when_set(self):
        entry = self._make_entry(
            status="FAILED",
            failure_class=FailureClass.QUALITY,
        )
        md = entry.to_markdown()
        assert "Quality Failure" in md

    def test_failure_class_absent_when_none(self):
        entry = self._make_entry(failure_class=None)
        md = entry.to_markdown()
        assert "Failure class:" not in md

    def test_ends_with_blank_line(self):
        entry = self._make_entry()
        md = entry.to_markdown()
        assert md.endswith("\n")


# ---------------------------------------------------------------------------
# Phase and QAGate
# ---------------------------------------------------------------------------

class TestPhase:
    def test_phase_starts_with_no_steps(self):
        phase = Phase(name="Implementation")
        assert phase.steps == []

    def test_phase_starts_with_no_gate(self):
        phase = Phase(name="Implementation")
        assert phase.gate is None

    def test_phase_with_steps_and_gate(self):
        steps = [AgentAssignment(agent_name="architect")]
        gate = QAGate(gate_type="Design Review")
        phase = Phase(name="Design", steps=steps, gate=gate)
        assert len(phase.steps) == 1
        assert phase.gate.gate_type == "Design Review"


class TestQAGate:
    def test_gate_has_type(self):
        gate = QAGate(gate_type="Build Check")
        assert gate.gate_type == "Build Check"

    def test_gate_default_outcome_is_none(self):
        gate = QAGate(gate_type="Build Check")
        assert gate.outcome is None

    def test_gate_outcome_can_be_set(self):
        gate = QAGate(gate_type="Build Check", outcome=GateOutcome.PASS)
        assert gate.outcome == GateOutcome.PASS

    def test_gate_fail_criteria_starts_empty(self):
        gate = QAGate(gate_type="Build Check")
        assert gate.fail_criteria == []

    def test_gate_notes_starts_empty(self):
        gate = QAGate(gate_type="Build Check")
        assert gate.notes == []
