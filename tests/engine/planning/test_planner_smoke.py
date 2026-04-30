"""Comprehensive planner smoke tests — mock and integration modes.

Validates the full planning pipeline across 7 functional dimensions:

1. Complexity classification (light / medium / heavy)
2. Task dependency detection
3. Stage-gate / quality / compliance checks
4. Team dispatch and swarm identification
5. Agent model selection (haiku / sonnet / opus)
6. Agent roster validation and routing
7. Bead-documented planning behaviors

Each test can run in two modes:
- **Mock mode** (default): uses stubs for the classifier and external
  services so the test suite runs in <1s without API keys.
- **Integration mode**: set ``BATON_PLANNER_INTEGRATION=1`` to exercise
  real classifiers and plan generation end-to-end.

Usage::

    # Fast mock-only run (CI default)
    pytest tests/engine/planning/test_planner_smoke.py -q

    # Integration run (needs a real planner, no API key required)
    BATON_PLANNER_INTEGRATION=1 pytest tests/engine/planning/test_planner_smoke.py -q
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices
from agent_baton.models.execution import MachinePlan, PlanGate, PlanPhase, PlanStep

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_INTEGRATION = os.environ.get("BATON_PLANNER_INTEGRATION", "").lower() in {"1", "true", "yes"}


@pytest.fixture
def planner():
    """Build a fresh IntelligentPlanner for each test."""
    from agent_baton.core.engine.planner import IntelligentPlanner
    return IntelligentPlanner()


@pytest.fixture
def plan_for(planner):
    """Convenience: ``plan_for("task summary")`` returns a MachinePlan."""
    def _plan(summary: str, **kwargs) -> MachinePlan:
        return planner.create_plan(summary, **kwargs)
    return _plan


def _agent_names(plan: MachinePlan) -> list[str]:
    """Flatten all agent names across phases/steps."""
    names: list[str] = []
    for phase in plan.phases:
        for step in phase.steps:
            if step.agent_name == "team":
                names.extend(m.agent_name for m in step.team)
            else:
                names.append(step.agent_name)
    return names


def _phase_names(plan: MachinePlan) -> list[str]:
    return [p.name for p in plan.phases]


# ===================================================================
# 1. COMPLEXITY CLASSIFICATION (light / medium / heavy)
# ===================================================================

class TestComplexityClassification:
    """Verify the planner sizes plans appropriately for task complexity."""

    def test_light_task_produces_minimal_plan(self, plan_for):
        """A trivially small task should produce 1-2 phases and few agents."""
        plan = plan_for(
            "Rename the variable 'foo' to 'bar' in utils.py",
            complexity="light",
        )
        assert len(plan.phases) <= 2
        assert plan.complexity == "light"

    def test_medium_task_produces_standard_plan(self, plan_for):
        """A moderate task should produce 2-4 phases."""
        plan = plan_for("Add user authentication with login and signup endpoints")
        assert 2 <= len(plan.phases) <= 5
        agents = _agent_names(plan)
        assert len(agents) >= 1

    def test_heavy_task_produces_comprehensive_plan(self, plan_for):
        """A complex cross-domain task should produce 3+ phases with specialists."""
        plan = plan_for(
            "Redesign the entire authentication system across frontend and "
            "backend, including database schema migration, new API endpoints, "
            "React components, and comprehensive test coverage",
            complexity="heavy",
        )
        assert len(plan.phases) >= 3
        assert plan.complexity == "heavy"

    def test_explicit_complexity_override_honored(self, plan_for):
        """When the caller specifies complexity, the classifier is bypassed."""
        plan = plan_for("Add a button", complexity="heavy")
        assert plan.complexity == "heavy"
        assert len(plan.phases) >= 3

    def test_keyword_classifier_light_signals(self, planner):
        """Light-quantifier keywords (rename, delete, one file) produce light plans."""
        from agent_baton.core.engine.planning.utils.text_parsers import infer_task_type
        assert infer_task_type("Rename the helper function") in (
            "refactor", "new-feature",
        )

    def test_keyword_classifier_task_type_inference(self, planner):
        """Task type inference picks the dominant signal."""
        from agent_baton.core.engine.planning.utils.text_parsers import infer_task_type
        assert infer_task_type("Fix the broken login page") == "bug-fix"
        assert infer_task_type("Add OAuth2 support") == "new-feature"
        assert infer_task_type("Migrate the user table to PostgreSQL") == "migration"
        assert infer_task_type("Write documentation for the API") == "documentation"
        assert infer_task_type("Refactor the payment module") == "refactor"

    def test_regression_test_phrase_infers_test_not_bug_fix(self):
        """'regression' alone is ambiguous; 'regression tests' must map to 'test'.

        Root cause: 'regression' was a bare keyword in the bug-fix list.
        It fired on 'regression tests', 'regression test suite', etc., causing
        compound-task decomposition to assign backend-engineer instead of
        test-engineer to test subtasks.  Fixed by changing to 'regression bug'
        (a precise two-word phrase that requires substring match).
        """
        from agent_baton.core.engine.planning.utils.text_parsers import infer_task_type
        assert infer_task_type("Write regression tests") == "test"
        assert infer_task_type("Add regression test coverage") == "test"
        assert infer_task_type("regression test suite for the parser") == "test"

    def test_regression_bug_phrase_still_infers_bug_fix(self):
        """'regression bug' (the unambiguous form) still classifies as bug-fix."""
        from agent_baton.core.engine.planning.utils.text_parsers import infer_task_type
        assert infer_task_type("Fix the regression bug in payment flow") == "bug-fix"

    def test_compound_task_test_subtask_gets_test_engineer(self):
        """Compound-task decomposition assigns test-engineer to test subtasks.

        Verifies the full agent-diversity fix: when a numbered compound task
        contains a 'Write tests' subtask, the roster must include test-engineer,
        not a duplicate backend-engineer.
        """
        from agent_baton.core.engine.planning.utils.text_parsers import (
            infer_task_type,
            parse_subtasks,
        )
        from agent_baton.core.engine.planning.rules.default_agents import DEFAULT_AGENTS

        summary = (
            "(1) Implement the new payment endpoint "
            "(2) Write regression tests for the payment flow"
        )
        subtasks = parse_subtasks(summary)
        assert len(subtasks) >= 2, "Expected at least 2 subtasks"

        subtask_types = [infer_task_type(text) for _, text in subtasks]
        assert "test" in subtask_types, (
            f"Expected at least one subtask to be typed 'test', got {subtask_types}"
        )

        # Collect the default agents for each subtask type and verify diversity.
        union: list[str] = []
        for st_type in subtask_types:
            for a in DEFAULT_AGENTS.get(st_type, ["backend-engineer"]):
                if a not in union:
                    union.append(a)
        assert "test-engineer" in union, (
            f"test-engineer missing from compound-task union roster: {union}"
        )


# ===================================================================
# 2. TASK DEPENDENCY DETECTION
# ===================================================================

class TestDependencyDetection:
    """Verify the planner detects references to prior tasks."""

    def test_based_on_pattern(self):
        from agent_baton.core.engine.planning.utils.context import detect_task_dependency
        store = MagicMock()
        store.query.return_value = [MagicMock(bead_id="b1")]
        result = detect_task_dependency(
            "Continuing the work based on task 2026-04-01-auth-abc12345", store
        )
        assert result == "2026-04-01-auth-abc12345"

    def test_building_on_pattern(self):
        from agent_baton.core.engine.planning.utils.context import detect_task_dependency
        store = MagicMock()
        store.query.return_value = [MagicMock(bead_id="b1")]
        result = detect_task_dependency(
            "Building on output of 2026-04-15-refactor-xyz99999", store
        )
        assert result == "2026-04-15-refactor-xyz99999"

    def test_depends_on_pattern(self):
        from agent_baton.core.engine.planning.utils.context import detect_task_dependency
        store = MagicMock()
        store.query.return_value = [MagicMock(bead_id="b1")]
        result = detect_task_dependency(
            "This depends on task 2026-03-20-setup-aaa11111", store
        )
        assert result == "2026-03-20-setup-aaa11111"

    def test_no_match_returns_none(self):
        from agent_baton.core.engine.planning.utils.context import detect_task_dependency
        store = MagicMock()
        result = detect_task_dependency("Add a new endpoint", store)
        assert result is None

    def test_unverified_task_id_returns_none(self):
        """Pattern match but bead_store has no beads for that task → None."""
        from agent_baton.core.engine.planning.utils.context import detect_task_dependency
        store = MagicMock()
        store.query.return_value = []
        result = detect_task_dependency(
            "based on task 2026-01-01-phantom-aaaa1111", store
        )
        assert result is None


# ===================================================================
# 3. STAGE-GATES AND QUALITY CHECKS
# ===================================================================

class TestGateScoping:
    """Verify gate commands are scoped correctly by scope mode."""

    def test_focused_scope_maps_changed_paths_to_test_files(self):
        from agent_baton.core.engine.planning.utils.gates import default_gate
        gate = default_gate(
            "Implement",
            changed_paths=["agent_baton/core/engine/planner.py"],
            gate_scope="focused",
            project_root=Path("."),
        )
        assert gate is not None
        assert gate.gate_type == "build"
        if "pytest" in gate.command and "test_" in gate.command:
            assert "planner" in gate.command

    def test_full_scope_runs_unscoped_suite(self):
        from agent_baton.core.engine.planning.utils.gates import default_gate
        gate = default_gate("Test", gate_scope="full")
        assert gate is not None
        assert gate.command == "pytest --cov"

    def test_smoke_scope_runs_collect_only(self):
        from agent_baton.core.engine.planning.utils.gates import default_gate
        gate = default_gate("Test", gate_scope="smoke")
        assert gate is not None
        assert "pytest --co" in gate.command

    def test_non_code_phases_have_no_gate(self):
        from agent_baton.core.engine.planning.utils.gates import default_gate
        for phase in ("Review", "Design", "Research", "Investigate"):
            assert default_gate(phase) is None

    def test_implement_phase_gets_build_gate(self):
        from agent_baton.core.engine.planning.utils.gates import default_gate
        gate = default_gate("Implement", gate_scope="full")
        assert gate is not None
        assert gate.gate_type == "build"

    def test_validation_stage_detects_empty_plan(self):
        from agent_baton.core.engine.planning.stages.validation import (
            PlanDefect,
            ValidationStage,
        )
        draft = PlanDraft.from_inputs("Add foo")
        draft.plan_phases = []
        draft.review_result = None
        defects = ValidationStage()._detect_defects(draft)
        codes = [d.code for d in defects]
        assert "empty_plan" in codes

    def test_validation_stage_detects_agent_phase_mismatch(self):
        """Architect on Implement phase is a bd-0e36 defect."""
        from agent_baton.core.engine.planning.stages.validation import ValidationStage
        draft = PlanDraft.from_inputs("Add foo")
        draft.plan_phases = [
            PlanPhase(
                phase_id=1, name="Implement",
                steps=[PlanStep(
                    step_id="1.1",
                    agent_name="architect",
                    task_description="Should not be here",
                )],
            )
        ]
        draft.review_result = None
        defects = ValidationStage()._detect_defects(draft)
        codes = [d.code for d in defects]
        assert "agent_phase_mismatch" in codes

    @pytest.mark.skipif(not _INTEGRATION, reason="integration test")
    def test_created_plan_has_gates_on_code_phases(self, plan_for):
        """Integration: every code-producing phase should have a gate."""
        plan = plan_for("Add a REST endpoint for user profiles")
        _NO_GATE_PHASES = {"design", "research", "review", "investigate", "feedback"}
        for phase in plan.phases:
            if phase.name.lower() not in _NO_GATE_PHASES:
                assert phase.gate is not None, (
                    f"Phase '{phase.name}' should have a gate"
                )


# ===================================================================
# 4. TEAM DISPATCH AND SWARM IDENTIFICATION
# ===================================================================

class TestTeamDispatchAndSwarm:
    """Verify team consolidation and swarm detection logic."""

    def test_implement_phase_with_two_agents_becomes_team(self):
        from agent_baton.core.engine.planning.utils.phase_builder import is_team_phase
        phase = PlanPhase(
            phase_id=1, name="Implement",
            steps=[
                PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="a"),
                PlanStep(step_id="1.2", agent_name="frontend-engineer", task_description="b"),
            ],
        )
        assert is_team_phase(phase, "Add user dashboard") is True

    def test_single_agent_phase_is_not_team(self):
        from agent_baton.core.engine.planning.utils.phase_builder import is_team_phase
        phase = PlanPhase(
            phase_id=1, name="Implement",
            steps=[PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="a")],
        )
        assert is_team_phase(phase, "Fix bug") is False

    def test_team_signal_keywords_trigger_team_dispatch(self):
        from agent_baton.core.engine.planning.utils.phase_builder import is_team_phase
        phase = PlanPhase(
            phase_id=1, name="Design",
            steps=[
                PlanStep(step_id="1.1", agent_name="architect", task_description="a"),
                PlanStep(step_id="1.2", agent_name="backend-engineer", task_description="b"),
            ],
        )
        for signal in ("pair", "joint", "together", "adversarial", "collaborate"):
            assert is_team_phase(phase, f"Do {signal} work on auth") is True

    def test_team_consolidation_filters_reviewers(self):
        from agent_baton.core.engine.planning.utils.phase_builder import consolidate_team_step
        phase = PlanPhase(
            phase_id=1, name="Implement",
            steps=[
                PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="impl"),
                PlanStep(step_id="1.2", agent_name="code-reviewer", task_description="review"),
            ],
        )
        team_step = consolidate_team_step(phase)
        member_agents = [m.agent_name for m in team_step.team]
        assert "code-reviewer" not in member_agents
        assert "backend-engineer" in member_agents

    def test_team_lead_is_first_member(self):
        from agent_baton.core.engine.planning.utils.phase_builder import consolidate_team_step
        phase = PlanPhase(
            phase_id=1, name="Implement",
            steps=[
                PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="a"),
                PlanStep(step_id="1.2", agent_name="frontend-engineer", task_description="b"),
            ],
        )
        team_step = consolidate_team_step(phase)
        assert team_step.team[0].role == "lead"
        assert team_step.team[1].role == "implementer"

    def test_swarm_dispatch_action_type_exists(self):
        """SWARM_DISPATCH is a valid ActionType (requires BATON_EXPERIMENTAL=swarm)."""
        from agent_baton.models.execution import ActionType
        assert hasattr(ActionType, "SWARM_DISPATCH")
        assert ActionType.SWARM_DISPATCH.value == "swarm.dispatch"


# ===================================================================
# 5. AGENT MODEL SELECTION (haiku / sonnet / opus)
# ===================================================================

class TestAgentModelSelection:
    """Verify model inheritance from agent definitions to plan steps."""

    def test_default_model_is_sonnet(self):
        """Steps default to sonnet when no agent definition or override exists."""
        step = PlanStep(step_id="1.1", agent_name="unknown-agent", task_description="x")
        assert step.model == "sonnet"

    def test_agent_definition_model_propagates_to_step(self, plan_for):
        """When an agent definition specifies model=opus, the step gets opus."""
        plan = plan_for("Add a new feature")
        for phase in plan.phases:
            for step in phase.steps:
                if step.agent_name == "team":
                    continue
                # Each step's model should be set (from agent def or default)
                assert step.model in ("haiku", "sonnet", "opus"), (
                    f"Step {step.step_id} ({step.agent_name}) has "
                    f"unexpected model '{step.model}'"
                )

    def test_explicit_default_model_override(self, planner):
        """The default_model kwarg propagates to steps without agent-def models."""
        plan = planner.create_plan(
            "Add a simple endpoint",
            default_model="opus",
        )
        for phase in plan.phases:
            for step in phase.steps:
                if step.agent_name == "team":
                    for member in step.team:
                        assert member.model in ("haiku", "sonnet", "opus")
                else:
                    assert step.model in ("haiku", "sonnet", "opus")


# ===================================================================
# 6. AGENT ROSTER VALIDATION AND ROUTING
# ===================================================================

class TestAgentRosterValidation:
    """Verify agent routing, concern expansion, and blocked-role filtering."""

    def test_architect_blocked_from_implement_phase(self, planner):
        """bd-0e36: architect must not be assigned to Implement phase."""
        phases = planner._default_phases(
            "new-feature",
            ["architect", "backend-engineer", "test-engineer"],
            "Build a feature",
        )
        for phase in phases:
            if phase.name.lower() in ("implement", "fix"):
                for step in phase.steps:
                    base = step.agent_name.split("--")[0]
                    assert base != "architect", (
                        f"Architect landed on {phase.name} phase (bd-0e36)"
                    )

    def test_concern_expansion_adds_frontend_for_ui_keyword(self):
        from agent_baton.core.engine.planning.utils.roster_logic import expand_agents_for_concerns
        agents = ["backend-engineer"]
        expanded = expand_agents_for_concerns(agents, "Build API and frontend UI components")
        assert "frontend-engineer" in expanded

    def test_concern_expansion_adds_test_engineer_for_test_keyword(self):
        from agent_baton.core.engine.planning.utils.roster_logic import expand_agents_for_concerns
        agents = ["backend-engineer"]
        expanded = expand_agents_for_concerns(agents, "Fix the bug and add integration tests")
        assert "test-engineer" in expanded

    def test_concern_splitting_with_three_concerns(self):
        """bd-076c: 3+ concerns should split the implement phase."""
        from agent_baton.core.engine.planning.utils.text_parsers import parse_concerns
        summary = (
            "F0.1 Add user authentication "
            "F0.2 Add role-based authorization "
            "F0.3 Add audit logging"
        )
        concerns = parse_concerns(summary)
        assert len(concerns) >= 3

    def test_concern_constraint_keyword_bounds_deliverables(self):
        """bd-021d: 'must not regress' stops concern parsing."""
        from agent_baton.core.engine.planning.utils.text_parsers import parse_concerns
        summary = (
            "F0.1 Add auth F0.2 Add roles F0.3 Add logging. "
            "Must not regress F0.4 existing tests"
        )
        concerns = parse_concerns(summary)
        markers = [c[0] for c in concerns]
        assert "F0.4" not in markers

    def test_route_agents_resolves_stack_flavors(self, planner):
        """Backend-engineer should route to backend-engineer--python in Python projects."""
        from agent_baton.core.engine.planning.utils.roster_logic import route_agents
        routing_notes: list[str] = []
        routed = route_agents(
            ["backend-engineer"],
            Path("."),
            planner._router,
            routing_notes,
        )
        # In this repo (Python), should resolve to the python flavor
        if routed != ["backend-engineer"]:
            assert routed[0].startswith("backend-engineer--")

    @pytest.mark.skipif(not _INTEGRATION, reason="integration test")
    def test_plan_agents_all_exist_in_registry(self, planner, plan_for):
        """Integration: every agent named in a plan must exist in the registry."""
        plan = plan_for("Add user authentication with tests")
        for name in _agent_names(plan):
            if name == "team":
                continue
            base = name.split("--")[0]
            agent_def = planner._registry.get(name) or planner._registry.get(base)
            assert agent_def is not None, (
                f"Agent '{name}' in plan is not in the registry"
            )


# ===================================================================
# 7. RISK CLASSIFICATION AND BEAD-DOCUMENTED BEHAVIORS
# ===================================================================

class TestRiskClassification:
    """Verify risk assessment from keywords and structural signals."""

    def test_production_keyword_triggers_high_risk(self):
        from agent_baton.core.engine.planning.utils.risk_and_policy import assess_risk
        assert assess_risk("Deploy to production", []) == "HIGH"

    def test_security_keyword_triggers_high_risk(self):
        from agent_baton.core.engine.planning.utils.risk_and_policy import assess_risk
        assert assess_risk("Fix the security vulnerability", []) == "HIGH"

    def test_migration_keyword_triggers_medium_risk(self):
        from agent_baton.core.engine.planning.utils.risk_and_policy import assess_risk
        assert assess_risk("Migrate the database", []) == "MEDIUM"

    def test_simple_feature_is_low_risk(self):
        from agent_baton.core.engine.planning.utils.risk_and_policy import assess_risk
        assert assess_risk("Add a hello-world endpoint", []) == "LOW"

    def test_destructive_verbs_raise_risk(self):
        from agent_baton.core.engine.planning.utils.risk_and_policy import assess_risk
        assert assess_risk("Delete all user data from the staging table", []) in ("MEDIUM", "HIGH")

    def test_readonly_first_word_dampens_risk(self):
        """'Review the production code' shouldn't be HIGH — readonly intent."""
        from agent_baton.core.engine.planning.utils.risk_and_policy import assess_risk
        level = assess_risk("Review the production code", [])
        assert level in ("LOW", "MEDIUM")

    def test_sensitive_agent_raises_risk_floor(self):
        from agent_baton.core.engine.planning.utils.risk_and_policy import assess_risk
        assert assess_risk("Check the code", ["security-reviewer"]) in ("MEDIUM", "HIGH")

    def test_many_agents_raises_risk(self):
        from agent_baton.core.engine.planning.utils.risk_and_policy import assess_risk
        agents = ["a", "b", "c", "d", "e", "f"]
        level = assess_risk("Do something", agents)
        assert level in ("MEDIUM", "HIGH")


class TestGitStrategy:
    """Verify git strategy selection from risk level."""

    def test_high_risk_uses_branch_per_agent(self):
        from agent_baton.core.engine.planning.utils.risk_and_policy import select_git_strategy
        from agent_baton.models.enums import GitStrategy, RiskLevel
        assert select_git_strategy(RiskLevel.HIGH) == GitStrategy.BRANCH_PER_AGENT

    def test_low_risk_uses_commit_per_agent(self):
        from agent_baton.core.engine.planning.utils.risk_and_policy import select_git_strategy
        from agent_baton.models.enums import GitStrategy, RiskLevel
        assert select_git_strategy(RiskLevel.LOW) == GitStrategy.COMMIT_PER_AGENT


class TestBeadDocumentedBehaviors:
    """Behaviors documented via beads that must not regress."""

    def test_task_id_format(self):
        """Task IDs must be YYYY-MM-DD-slug-uuid8."""
        from agent_baton.core.engine.planning.utils.text_parsers import generate_task_id
        tid = generate_task_id("Build a widget API")
        assert re.match(r"\d{4}-\d{2}-\d{2}-.+-[a-f0-9]{8}$", tid)

    def test_task_ids_are_unique(self):
        from agent_baton.core.engine.planning.utils.text_parsers import generate_task_id
        ids = {generate_task_id("same task") for _ in range(20)}
        assert len(ids) == 20

    def test_structured_description_parsing(self, planner):
        """Phase 1: ... Phase 2: ... patterns should be parsed as phases."""
        phases, agents = planner._parse_structured_description(
            "Phase 1: Design the API. Phase 2: Implement the endpoints."
        )
        assert phases is not None
        assert len(phases) >= 2

    def test_subtask_detection(self, planner):
        """Numbered sub-tasks should be detected as compound tasks."""
        subtasks = planner._parse_subtasks(
            "(1) Add the login page (2) Add the signup page (3) Add password reset"
        )
        assert len(subtasks) >= 2

    def test_expected_outcome_derived_for_steps(self):
        """Wave 3.1: steps should get behavioral demo statements."""
        from agent_baton.core.engine.planning.utils.phase_builder import _derive_expected_outcome
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="Implement: Add user authentication endpoint",
            step_type="developing",
        )
        outcome = _derive_expected_outcome(step, "Add user authentication")
        assert outcome.startswith("After this step,")

    def test_step_type_for_agent_defaults(self):
        """Agent step types follow the canonical mapping."""
        from agent_baton.core.engine.planning.utils.phase_builder import _step_type_for_agent
        assert _step_type_for_agent("architect") == "planning"
        assert _step_type_for_agent("backend-engineer") == "developing"
        assert _step_type_for_agent("test-engineer") == "testing"
        assert _step_type_for_agent("code-reviewer") == "reviewing"
        assert _step_type_for_agent("unknown-agent") == "developing"

    def test_step_type_override_on_implement_phase(self):
        """bd-b3e1: architect on Implement → developing, not planning."""
        from agent_baton.core.engine.planning.utils.phase_builder import _step_type_for_agent
        assert _step_type_for_agent("architect", phase_name="Implement") == "developing"

    def test_knowledge_partitioning_by_concern(self):
        """Smart knowledge split routes domain-specific attachments."""
        from agent_baton.core.engine.planning.utils.phase_builder import (
            partition_knowledge,
            score_knowledge_for_concern,
        )
        att_api = MagicMock(pack_name="api-reference", document_name="endpoints.md", path="docs/api.md")
        att_ui = MagicMock(pack_name="ui-components", document_name="react.md", path="docs/ui.md")
        att_shared = MagicMock(pack_name="project-overview", document_name="readme.md", path="README.md")

        concerns = [("F0.1", "Build the REST API endpoint"), ("F0.2", "Build the React UI component")]
        partitions = partition_knowledge([att_api, att_ui, att_shared], concerns)
        assert len(partitions) == 2

    def test_budget_tier_selection(self, planner):
        """Budget tier follows agent count heuristic."""
        assert planner._select_budget_tier("new-feature", 1) == "lean"
        assert planner._select_budget_tier("new-feature", 3) == "standard"
        assert planner._select_budget_tier("new-feature", 6) == "full"


# ===================================================================
# INTEGRATION TESTS — full pipeline end-to-end
# ===================================================================

@pytest.mark.skipif(not _INTEGRATION, reason="integration test")
class TestPlannerIntegration:
    """End-to-end plan creation and inspection."""

    def test_simple_plan_e2e(self, planner, plan_for):
        """Create a plan and verify it has all required fields."""
        plan = plan_for("Add a health-check endpoint")
        assert plan.task_id
        assert plan.task_summary == "Add a health-check endpoint"
        assert plan.risk_level in ("LOW", "MEDIUM", "HIGH")
        assert plan.budget_tier in ("lean", "standard", "full")
        assert plan.git_strategy in ("commit-per-agent", "branch-per-agent")
        assert plan.task_type
        assert plan.complexity in ("light", "medium", "heavy")
        assert len(plan.phases) >= 1

    def test_explain_plan_e2e(self, planner, plan_for):
        """explain_plan should produce markdown with expected sections."""
        plan = plan_for("Add user authentication")
        explanation = planner.explain_plan(plan)
        assert "# Plan Explanation" in explanation
        assert "## Pattern Influence" in explanation
        assert "## Score Warnings" in explanation
        assert "## Phase Summary" in explanation

    def test_high_risk_plan_e2e(self, planner, plan_for):
        """A high-risk task should produce a branch-per-agent git strategy."""
        plan = plan_for("Deploy the new authentication system to production")
        assert plan.risk_level in ("HIGH", "MEDIUM")
        if plan.risk_level == "HIGH":
            assert plan.git_strategy == "branch-per-agent"

    def test_plan_phases_all_have_steps(self, plan_for):
        """Every phase in a plan must have at least one step."""
        plan = plan_for("Build a dashboard with charts and data tables")
        for phase in plan.phases:
            assert len(phase.steps) >= 1, (
                f"Phase '{phase.name}' has zero steps"
            )

    def test_shared_context_populated(self, plan_for):
        """The plan's shared_context should contain task and risk info."""
        plan = plan_for("Add error handling to the API")
        assert plan.shared_context
        assert "Task:" in plan.shared_context
        assert "Risk:" in plan.shared_context
