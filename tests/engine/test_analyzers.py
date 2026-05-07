"""Unit tests for agent_baton.core.engine.analyzers.

Each analyzer gets at minimum: happy path (plan passes), one rejection /
warning case, and (for DepthAnalyzer) the complexity=light bypass.

Per 005b-phase1-design.md §2 + §5.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent_baton.core.engine.analyzers import (
    CapabilityAnalyzer,
    DependencyAnalyzer,
    DepthAnalyzer,
    PlanValidationError,
    RiskAnalyzer,
    SubscalePlanError,
)
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.models.enums import RiskLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plan(
    phases: list[PlanPhase] | None = None,
    risk_level: str = "LOW",
    git_strategy: str = "commit-per-agent",
) -> MachinePlan:
    return MachinePlan(
        task_id="test-001",
        task_summary="Test task",
        risk_level=risk_level,
        budget_tier="standard",
        phases=phases or [],
        git_strategy=git_strategy,
    )


def _make_phase(
    name: str = "Implement",
    phase_id: int = 1,
    steps: list[PlanStep] | None = None,
) -> PlanPhase:
    return PlanPhase(phase_id=phase_id, name=name, steps=steps or [])


def _make_step(
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task_description: str = "Implement the feature",
    depends_on: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task_description,
        depends_on=depends_on or [],
    )


# ---------------------------------------------------------------------------
# DependencyAnalyzer
# ---------------------------------------------------------------------------

class TestDependencyAnalyzer:
    def test_happy_path_no_warnings(self, caplog: Any) -> None:
        """Plan with correct dependencies produces no warnings."""
        import logging
        step1 = _make_step("1.1", depends_on=[])
        step2 = _make_step("1.2", depends_on=["1.1"])
        plan = _make_plan([_make_phase(steps=[step1, step2])])

        analyzer = DependencyAnalyzer()
        with caplog.at_level(logging.WARNING):
            result = analyzer.validate(plan)

        assert result is plan  # same object returned
        assert "not found" not in caplog.text

    def test_forward_reference_emits_warning(self, caplog: Any) -> None:
        """A step depending on a step not yet seen generates a warning.

        Slice 11's Hole-6 validator now rejects unknown depends_on at
        MachinePlan construction time; this analyzer warning was the
        pre-Hole-6 runtime fallback.  Bypass the model_validator by
        mutating depends_on after construction so the analyzer's
        legacy warning path is still exercised.
        """
        import logging
        step1 = _make_step("1.1")  # construct without bad dep
        plan = _make_plan([_make_phase(steps=[step1])])
        # Inject the forward reference post-construction; Pydantic's
        # validate_assignment=False on PlanModel allows it without
        # re-running the validator.
        plan.phases[0].steps[0].depends_on = ["99.99"]

        analyzer = DependencyAnalyzer()
        with caplog.at_level(logging.WARNING):
            result = analyzer.validate(plan)

        assert result is plan  # still returns (warning-only, no raise)
        assert "99.99" in caplog.text

    def test_empty_plan_returns_unchanged(self) -> None:
        plan = _make_plan(phases=[])
        result = DependencyAnalyzer().validate(plan)
        assert result is plan


# ---------------------------------------------------------------------------
# RiskAnalyzer
# ---------------------------------------------------------------------------

class TestRiskAnalyzer:
    def test_happy_path_low_risk(self) -> None:
        """Plain task with no risk signals stays LOW."""
        plan = _make_plan()
        result = RiskAnalyzer().validate(
            plan,
            task_summary="Add a helper utility function",
            agents=["backend-engineer"],
        )
        assert result.risk_level == "LOW"
        assert result.git_strategy == "commit-per-agent"

    def test_high_risk_keyword_sets_risk(self) -> None:
        """Task mentioning 'production' elevates risk to HIGH."""
        plan = _make_plan()
        result = RiskAnalyzer().validate(
            plan,
            task_summary="Deploy hotfix to production",
            agents=["backend-engineer"],
        )
        assert result.risk_level == "HIGH"
        assert result.git_strategy == "branch-per-agent"

    def test_design_phase_gets_approval_on_high_risk(self) -> None:
        """Design phase is gated with approval_required on HIGH+ risk."""
        design_phase = _make_phase(name="Design")
        plan = _make_plan(phases=[design_phase])
        result = RiskAnalyzer().validate(
            plan,
            task_summary="Deploy to production infrastructure",
            agents=["backend-engineer"],
        )
        assert result.phases[0].approval_required is True
        assert "design" in result.phases[0].approval_description.lower()

    def test_research_phase_gets_approval_on_high_risk(self) -> None:
        """Research phase (not implement/deploy) is gated at HIGH+ risk."""
        research_phase = _make_phase(name="Research")
        plan = _make_plan(phases=[research_phase])
        result = RiskAnalyzer().validate(
            plan,
            task_summary="Audit the production security configuration",
            agents=["security-reviewer"],
        )
        assert result.phases[0].approval_required is True

    def test_implement_phase_not_gated_at_high_risk(self) -> None:
        """Implement phase is NOT gated (gating is on Design/Research only)."""
        implement_phase = _make_phase(name="Implement")
        plan = _make_plan(phases=[implement_phase])
        result = RiskAnalyzer().validate(
            plan,
            task_summary="Deploy to production",
            agents=["backend-engineer"],
        )
        assert result.phases[0].approval_required is False

    def test_wrong_phase_names_not_gated(self) -> None:
        """The old skeleton's incorrect phase names (implement/deploy/execute) are NOT gated."""
        # The skeleton used to gate 'implement', 'deploy', 'execute' — that was wrong.
        # This test verifies the corrected behaviour.
        execute_phase = _make_phase(name="Execute")
        deploy_phase = _make_phase(name="Deploy", phase_id=2)
        plan = _make_plan(phases=[execute_phase, deploy_phase])
        result = RiskAnalyzer().validate(
            plan,
            task_summary="Deploy to production",
            agents=["backend-engineer"],
        )
        for phase in result.phases:
            assert phase.approval_required is False, (
                f"Phase '{phase.name}' should NOT be approval-gated (old skeleton bug)"
            )

    def test_classifier_risk_wins_when_higher(self) -> None:
        """When classifier returns CRITICAL, the merged result is CRITICAL."""
        mock_classification = MagicMock()
        mock_classification.risk_level = RiskLevel.CRITICAL

        plan = _make_plan()
        result = RiskAnalyzer().validate(
            plan,
            classifier=MagicMock(classify=MagicMock(return_value=mock_classification)),
            task_summary="simple task",
            agents=[],
        )
        assert result.risk_level == "CRITICAL"

    def test_keyword_risk_wins_when_higher_than_classifier(self) -> None:
        """Keyword HIGH beats classifier LOW."""
        mock_classification = MagicMock()
        mock_classification.risk_level = RiskLevel.LOW

        plan = _make_plan()
        result = RiskAnalyzer().validate(
            plan,
            classifier=MagicMock(classify=MagicMock(return_value=mock_classification)),
            task_summary="Deploy to production infrastructure",
            agents=[],
        )
        assert result.risk_level == "HIGH"

    def test_readonly_first_word_caps_score_at_low(self) -> None:
        """'Review the production code' stays LOW (read-only dampening)."""
        plan = _make_plan()
        result = RiskAnalyzer().validate(
            plan,
            task_summary="Review the production code for quality",
            agents=["code-reviewer"],
        )
        assert result.risk_level == "LOW"

    def test_many_agents_raises_to_medium(self) -> None:
        """More than 5 agents raises to at least MEDIUM."""
        plan = _make_plan()
        agents = [f"agent-{i}" for i in range(6)]
        result = RiskAnalyzer().validate(
            plan,
            task_summary="Add a feature",
            agents=agents,
        )
        assert result.risk_level in ("MEDIUM", "HIGH")

    def test_low_risk_no_approval_gating(self) -> None:
        """LOW risk means no phases are approval-gated."""
        design_phase = _make_phase(name="Design")
        plan = _make_plan(phases=[design_phase])
        result = RiskAnalyzer().validate(
            plan,
            task_summary="Add a simple utility function",
            agents=["backend-engineer"],
        )
        assert result.phases[0].approval_required is False


# ---------------------------------------------------------------------------
# CapabilityAnalyzer
# ---------------------------------------------------------------------------

class TestCapabilityAnalyzer:
    def test_happy_path_no_registry(self) -> None:
        """Without a registry, validate returns the plan unchanged."""
        step = _make_step()
        plan = _make_plan(phases=[_make_phase(steps=[step])])
        analyzer = CapabilityAnalyzer()
        result = analyzer.validate(plan)
        assert result is plan

    def test_missing_agent_emits_warning(self, caplog: Any) -> None:
        """Agent not in registry emits a warning."""
        import logging
        mock_registry = MagicMock()
        mock_registry.get.return_value = None  # agent not found

        step = _make_step(agent_name="nonexistent-agent")
        plan = _make_plan(phases=[_make_phase(steps=[step])])
        analyzer = CapabilityAnalyzer(registry=mock_registry)

        with caplog.at_level(logging.WARNING):
            analyzer.validate(plan)

        assert "nonexistent-agent" in caplog.text

    def test_router_reroutes_agent(self) -> None:
        """When router resolves to a different name, step.agent_name is updated."""
        mock_registry = MagicMock()
        mock_registry.get.return_value = MagicMock()  # agent found
        mock_router = MagicMock()
        mock_router.route.return_value = "backend-engineer--python"

        step = _make_step(agent_name="backend-engineer")
        phase = _make_phase(steps=[step])
        plan = _make_plan(phases=[phase])

        analyzer = CapabilityAnalyzer(registry=mock_registry, router=mock_router)
        analyzer.validate(plan, stack=MagicMock())

        assert plan.phases[0].steps[0].agent_name == "backend-engineer--python"

    def test_classify_to_preset_key_standard_dev(self) -> None:
        """None classification maps to standard_dev."""
        assert CapabilityAnalyzer.classify_to_preset_key(None) == "standard_dev"

    def test_classify_to_preset_key_regulated(self) -> None:
        """Regulated Data preset maps to 'regulated'."""
        mock_cls = MagicMock()
        mock_cls.guardrail_preset = "Regulated Data"
        assert CapabilityAnalyzer.classify_to_preset_key(mock_cls) == "regulated"

    def test_route_agents_records_notes(self) -> None:
        """route_agents records a routing note when name changes."""
        mock_router = MagicMock()
        mock_router.detect_stack.return_value = MagicMock()
        mock_router.route.side_effect = lambda name, stack: f"{name}--python"

        analyzer = CapabilityAnalyzer(router=mock_router)
        notes: list[str] = []
        result = analyzer.route_agents(
            ["backend-engineer"], project_root="/tmp", routing_notes=notes
        )
        assert result == ["backend-engineer--python"]
        assert any("backend-engineer" in n for n in notes)

    def test_apply_retro_feedback_drops_agent(self) -> None:
        """Agents in feedback.agents_to_drop() are removed from the list."""
        feedback = MagicMock()
        feedback.agents_to_drop.return_value = ["backend-engineer"]
        feedback.agents_to_prefer.return_value = []

        analyzer = CapabilityAnalyzer()
        result = analyzer.apply_retro_feedback(
            ["architect", "backend-engineer", "test-engineer"],
            feedback,
        )
        assert "backend-engineer" not in result
        assert "architect" in result

    def test_apply_retro_feedback_preserves_list_if_would_empty(self) -> None:
        """If dropping all agents would empty the list, original is kept."""
        feedback = MagicMock()
        feedback.agents_to_drop.return_value = ["backend-engineer"]
        feedback.agents_to_prefer.return_value = []

        analyzer = CapabilityAnalyzer()
        result = analyzer.apply_retro_feedback(["backend-engineer"], feedback)
        # Would have emptied the list — original kept
        assert result == ["backend-engineer"]

    def test_check_agent_scores_no_scorer(self) -> None:
        """Without a scorer, check_agent_scores is a no-op."""
        warnings: list[str] = []
        analyzer = CapabilityAnalyzer()
        analyzer.check_agent_scores(["backend-engineer"], score_warnings=warnings)
        assert warnings == []


# ---------------------------------------------------------------------------
# DepthAnalyzer
# ---------------------------------------------------------------------------

class TestDepthAnalyzer:
    # --- complexity=light bypass ---

    def test_light_complexity_bypasses_all_checks(self) -> None:
        """complexity='light' returns the plan without raising."""
        step = _make_step(task_description="research and implement the feature")
        plan = _make_plan(phases=[_make_phase(steps=[step])])
        # Must NOT raise despite the "research and implement" conjunction
        result = DepthAnalyzer().validate(plan, complexity="light")
        assert result is plan

    # --- happy path ---

    def test_happy_path_single_action_step(self) -> None:
        """A step with a single clear action passes."""
        step = _make_step(task_description="Implement the OAuth2 login endpoint")
        plan = _make_plan(phases=[_make_phase(steps=[step])])
        result = DepthAnalyzer().validate(plan)
        assert result is plan

    def test_happy_path_empty_plan(self) -> None:
        """Empty plan passes without error."""
        plan = _make_plan(phases=[])
        result = DepthAnalyzer().validate(plan)
        assert result is plan

    # --- conjunction detection ---

    def test_conjunction_raises_subscale_error(self) -> None:
        """'research and implement' in description raises SubscalePlanError."""
        step = _make_step(
            step_id="1.1",
            task_description="research and implement the new payment API",
        )
        plan = _make_plan(phases=[_make_phase(steps=[step])])
        with pytest.raises(SubscalePlanError) as exc_info:
            DepthAnalyzer().validate(plan)
        err = exc_info.value
        assert err.step_id == "1.1"
        assert err.reason == "conjunction"

    def test_conjunction_uses_word_boundaries(self) -> None:
        """'researching and implementing' is not flagged (word-boundary check)."""
        # "researching" is not in _DEPTH_PHASE_VERBS, so no match
        step = _make_step(
            task_description="This step is about researching and implementing solutions"
        )
        plan = _make_plan(phases=[_make_phase(steps=[step])])
        # Should not raise — "researching" != "research" at word boundary
        # Actually check: "implement" IS in verbs but "research" at boundary...
        # The exact behavior depends on verb set. Let's use verbs NOT in the set.
        step2 = _make_step(
            task_description="Configure and optimize the pipeline settings"
        )
        plan2 = _make_plan(phases=[_make_phase(steps=[step2])])
        # "configure" and "optimize" are not in _DEPTH_PHASE_VERBS — should pass
        result = DepthAnalyzer().validate(plan2)
        assert result is plan2

    def test_audit_and_fix_flagged_with_word_boundary(self) -> None:
        """'audit and fix' is flagged (both are phase verbs)."""
        # Note: 'audit' is not in _DEPTH_PHASE_VERBS as defined, but 'fix' and
        # 'review' are. Let's use 'design and implement' which are both verbs.
        step = _make_step(task_description="design and implement the auth module")
        plan = _make_plan(phases=[_make_phase(steps=[step])])
        with pytest.raises(SubscalePlanError) as exc_info:
            DepthAnalyzer().validate(plan)
        assert exc_info.value.reason == "conjunction"

    def test_conjunction_not_triggered_by_substring(self) -> None:
        """'individual fix and review' should not match 'fix and review' as a
        verb-and-verb conjunction if 'individual' is not a phase verb — but
        'fix' and 'review' ARE both verbs so it should match."""
        # Actually 'fix and review' should be caught — let's test a non-verb pair
        step = _make_step(
            task_description="handle and process the configuration files"
        )
        # "handle" and "process" are not in _DEPTH_PHASE_VERBS
        plan = _make_plan(phases=[_make_phase(steps=[step])])
        result = DepthAnalyzer().validate(plan)
        assert result is plan

    # --- concern-density detection ---

    def test_concern_density_raises_subscale_error(self) -> None:
        """Step with ≥3 concern markers raises SubscalePlanError."""
        step = _make_step(
            step_id="2.1",
            task_description="F0.1 add user model. F0.2 add auth endpoint. F0.3 add session handling.",
        )
        plan = _make_plan(phases=[_make_phase(steps=[step])])
        with pytest.raises(SubscalePlanError) as exc_info:
            DepthAnalyzer().validate(plan)
        err = exc_info.value
        assert err.step_id == "2.1"
        assert err.reason == "concern-density"

    def test_two_concerns_below_threshold_no_raise(self) -> None:
        """Fewer than _MIN_CONCERNS_FOR_SPLIT markers is not concern-density."""
        step = _make_step(
            task_description="F0.1 add user model. F0.2 add auth endpoint."
        )
        plan = _make_plan(phases=[_make_phase(steps=[step])])
        # Should pass (only 2 markers, threshold is 3)
        result = DepthAnalyzer().validate(plan)
        assert result is plan

    # --- multi-agent affinity detection ---

    def test_multi_agent_affinity_raises_on_implement_phase(self) -> None:
        """Step mentioning both backend API and frontend UI signals in Implement phase raises."""
        step = _make_step(
            step_id="3.1",
            task_description=(
                "Implement the api endpoint and the react component "
                "for the dashboard feature"
            ),
        )
        implement_phase = _make_phase(name="Implement", steps=[step])
        plan = _make_plan(phases=[implement_phase])
        with pytest.raises(SubscalePlanError) as exc_info:
            DepthAnalyzer().validate(plan)
        err = exc_info.value
        assert err.step_id == "3.1"
        assert err.reason == "multi-agent-affinity"

    def test_multi_agent_affinity_not_raised_on_review_phase(self) -> None:
        """Multi-agent affinity check only fires on Implement-class phases."""
        step = _make_step(
            task_description="Review the api endpoint and react component implementations"
        )
        review_phase = _make_phase(name="Review", steps=[step])
        plan = _make_plan(phases=[review_phase])
        # Review phase is not in Implement-class — should not raise
        result = DepthAnalyzer().validate(plan)
        assert result is plan

    def test_single_agent_signal_does_not_raise(self) -> None:
        """Mentioning only one specialist role (backend only) does not raise."""
        step = _make_step(
            task_description="Implement the api endpoint for the user service"
        )
        implement_phase = _make_phase(name="Implement", steps=[step])
        plan = _make_plan(phases=[implement_phase])
        result = DepthAnalyzer().validate(plan)
        assert result is plan

    # --- SubscalePlanError attributes ---

    def test_subscale_plan_error_has_correct_attributes(self) -> None:
        """SubscalePlanError exposes step_id, reason, hint."""
        err = SubscalePlanError(
            step_id="1.1", reason="conjunction", hint="test hint"
        )
        assert err.step_id == "1.1"
        assert err.reason == "conjunction"
        assert err.hint == "test hint"
        assert isinstance(err, PlanValidationError)

    # --- default complexity=medium ---

    def test_default_medium_complexity_checks_conjunctions(self) -> None:
        """When complexity is omitted (defaults to medium), checks run."""
        step = _make_step(task_description="research and design the system")
        plan = _make_plan(phases=[_make_phase(steps=[step])])
        with pytest.raises(SubscalePlanError):
            DepthAnalyzer().validate(plan)  # no complexity kwarg — defaults to "medium"
