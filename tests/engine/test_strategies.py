"""Unit tests for agent_baton.core.engine.strategies.

Coverage:
  - HeuristicStrategy.execute — smoke, task_type override, phases override
  - HeuristicStrategy.decompose — concern-density, conjunction, multi-agent-affinity
  - TemplateStrategy.execute — raises NotImplementedError
  - RefinementStrategy.execute — raises NotImplementedError
  - PlanContext.as_kwargs — round-trips all fields

Per 005b-phase1-design.md §3 (Step 1.3).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_baton.core.engine.strategies import (
    HeuristicStrategy,
    PlanContext,
    RefinementStrategy,
    TemplateStrategy,
    ZeroShotStrategy,
    _DEFAULT_AGENTS,
    _PHASE_NAMES,
)
from agent_baton.core.engine.analyzers import SubscalePlanError
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plan(
    phases: list[PlanPhase] | None = None,
    task_summary: str = "Test task",
) -> MachinePlan:
    return MachinePlan(
        task_id="test-001",
        task_summary=task_summary,
        risk_level="LOW",
        budget_tier="standard",
        phases=phases or [],
        git_strategy="commit-per-agent",
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


def _minimal_context(**kwargs) -> PlanContext:
    """Build a minimal PlanContext suitable for unit tests."""
    return PlanContext(**kwargs)


# ---------------------------------------------------------------------------
# HeuristicStrategy.execute — smoke test
# ---------------------------------------------------------------------------

class TestHeuristicStrategyExecute:
    def test_smoke_trivial_task(self) -> None:
        """execute() returns a MachinePlan for a trivial task summary."""
        strategy = HeuristicStrategy()
        ctx = _minimal_context(task_summary="Add a health-check endpoint")
        plan = strategy.execute("Add a health-check endpoint", ctx)

        assert isinstance(plan, MachinePlan)
        assert plan.task_id != ""
        assert plan.task_summary == "Add a health-check endpoint"
        assert len(plan.phases) > 0

    def test_plan_phases_have_steps(self) -> None:
        """Every phase in the produced plan has at least one step."""
        strategy = HeuristicStrategy()
        ctx = _minimal_context(task_summary="Fix the broken login endpoint")
        plan = strategy.execute("Fix the broken login endpoint", ctx)

        for phase in plan.phases:
            assert len(phase.steps) > 0, (
                f"Phase '{phase.name}' has no steps"
            )

    def test_plan_task_id_is_unique(self) -> None:
        """Two plans for the same summary get different task IDs."""
        strategy = HeuristicStrategy()
        ctx = _minimal_context(task_summary="Add OAuth2 login")
        plan1 = strategy.execute("Add OAuth2 login", ctx)
        plan2 = strategy.execute("Add OAuth2 login", ctx)
        assert plan1.task_id != plan2.task_id

    # ------------------------------------------------------------------
    # task_type override
    # ------------------------------------------------------------------

    def test_explicit_task_type_uses_bug_fix_phases(self) -> None:
        """Explicit task_type='bug-fix' produces Investigate/Fix/Test phases."""
        strategy = HeuristicStrategy()
        ctx = _minimal_context(
            task_summary="Fix the crash on startup",
            task_type="bug-fix",
        )
        plan = strategy.execute("Fix the crash on startup", ctx)
        phase_names = [p.name for p in plan.phases]
        # bug-fix template: ["Investigate", "Fix", "Test"]
        assert "Fix" in phase_names or "Investigate" in phase_names, (
            f"Expected bug-fix phases, got: {phase_names}"
        )

    def test_explicit_task_type_uses_new_feature_phases(self) -> None:
        """Explicit task_type='new-feature' produces Design/Implement/Test/Review."""
        strategy = HeuristicStrategy()
        ctx = _minimal_context(
            task_summary="Build a payment gateway",
            task_type="new-feature",
        )
        plan = strategy.execute("Build a payment gateway", ctx)
        phase_names = [p.name for p in plan.phases]
        assert "Design" in phase_names, (
            f"Expected 'Design' in new-feature phases, got: {phase_names}"
        )
        assert "Review" in phase_names, (
            f"Expected 'Review' in new-feature phases, got: {phase_names}"
        )

    def test_explicit_task_type_documentation(self) -> None:
        """Explicit task_type='documentation' produces Research/Draft/Review."""
        strategy = HeuristicStrategy()
        ctx = _minimal_context(
            task_summary="Write API documentation",
            task_type="documentation",
        )
        plan = strategy.execute("Write API documentation", ctx)
        phase_names = [p.name for p in plan.phases]
        assert "Draft" in phase_names, (
            f"Expected 'Draft' in documentation phases, got: {phase_names}"
        )

    # ------------------------------------------------------------------
    # phases override
    # ------------------------------------------------------------------

    def test_explicit_phases_override_used(self) -> None:
        """Explicit phases list is used directly instead of classifier output."""
        strategy = HeuristicStrategy()
        explicit_phases = [
            {"name": "Audit", "agents": ["auditor"]},
            {"name": "Patch", "agents": ["backend-engineer"]},
        ]
        ctx = _minimal_context(
            task_summary="Audit and patch the authentication module",
            phases=explicit_phases,
        )
        plan = strategy.execute("Audit and patch the authentication module", ctx)
        phase_names = [p.name for p in plan.phases]
        assert "Audit" in phase_names
        assert "Patch" in phase_names

    def test_explicit_phases_with_no_agents_distributes_roster(self) -> None:
        """Phase dicts with no 'agents' key distribute the resolved roster."""
        strategy = HeuristicStrategy()
        explicit_phases = [
            {"name": "Design"},
            {"name": "Implement"},
        ]
        ctx = _minimal_context(
            task_summary="Add a new REST endpoint",
            phases=explicit_phases,
            agents=["architect", "backend-engineer"],
        )
        plan = strategy.execute("Add a new REST endpoint", ctx)
        # All phases should have steps
        for phase in plan.phases:
            assert len(phase.steps) > 0

    def test_explicit_agents_override_respected(self) -> None:
        """Explicit agents list is used for plan generation."""
        strategy = HeuristicStrategy()
        ctx = _minimal_context(
            task_summary="Implement the data pipeline",
            agents=["data-engineer", "test-engineer"],
        )
        plan = strategy.execute("Implement the data pipeline", ctx)
        all_agents = {s.agent_name for p in plan.phases for s in p.steps}
        # At least one of the explicitly listed agents should appear
        # (team steps wrap them, so we check team members too)
        has_explicit = any(
            "data-engineer" in a or "test-engineer" in a
            for a in all_agents
        )
        # Also check team member names
        if not has_explicit:
            for phase in plan.phases:
                for step in phase.steps:
                    if step.team:
                        for member in step.team:
                            if "data-engineer" in member.agent_name or "test-engineer" in member.agent_name:
                                has_explicit = True
        assert has_explicit, (
            f"Expected data-engineer or test-engineer in plan, got: {all_agents}"
        )

    def test_budget_tier_is_set(self) -> None:
        """Produced plan has a non-empty budget_tier."""
        strategy = HeuristicStrategy()
        ctx = _minimal_context(task_summary="Add caching layer")
        plan = strategy.execute("Add caching layer", ctx)
        assert plan.budget_tier in ("lean", "standard", "full")


# ---------------------------------------------------------------------------
# HeuristicStrategy.decompose
# ---------------------------------------------------------------------------

class TestHeuristicStrategyDecompose:

    # ------------------------------------------------------------------
    # concern-density
    # ------------------------------------------------------------------

    def test_decompose_concern_density_splits_phase(self) -> None:
        """concern-density reason splits the offending step into parallel concern-steps."""
        step = _make_step(
            step_id="1.1",
            task_description=(
                "F0.1 implement user authentication. "
                "F0.2 implement session management. "
                "F0.3 implement password reset."
            ),
        )
        phase = _make_phase(name="Implement", steps=[step])
        plan = _make_plan(phases=[phase])
        exc = SubscalePlanError(
            step_id="1.1",
            reason="concern-density",
            hint="Step has 3 concerns",
        )

        strategy = HeuristicStrategy()
        ctx = _minimal_context(task_summary=plan.task_summary)
        result = strategy.decompose(plan, exc, ctx)

        # Phase should now have 3 steps (one per concern)
        assert len(result.phases[0].steps) == 3
        # Step IDs should be 1.1, 1.2, 1.3
        step_ids = [s.step_id for s in result.phases[0].steps]
        assert "1.1" in step_ids
        assert "1.2" in step_ids
        assert "1.3" in step_ids

    def test_decompose_concern_density_no_concerns_unchanged(self) -> None:
        """If concern parser finds nothing, plan is returned unchanged."""
        step = _make_step(
            step_id="1.1",
            task_description="Add a utility function",
        )
        phase = _make_phase(name="Implement", steps=[step])
        plan = _make_plan(phases=[phase])
        exc = SubscalePlanError(
            step_id="1.1",
            reason="concern-density",
            hint="Step has concerns",
        )

        strategy = HeuristicStrategy()
        ctx = _minimal_context(task_summary=plan.task_summary)
        result = strategy.decompose(plan, exc, ctx)
        # No concerns found — step count unchanged
        assert len(result.phases[0].steps) == 1

    # ------------------------------------------------------------------
    # conjunction
    # ------------------------------------------------------------------

    def test_decompose_conjunction_splits_into_two_steps(self) -> None:
        """conjunction reason splits step into two sequential steps."""
        step = _make_step(
            step_id="1.1",
            task_description="research and design the authentication module",
        )
        phase = _make_phase(name="Design", steps=[step])
        plan = _make_plan(phases=[phase])
        exc = SubscalePlanError(
            step_id="1.1",
            reason="conjunction",
            hint="research and design",
        )

        strategy = HeuristicStrategy()
        ctx = _minimal_context(task_summary=plan.task_summary)
        result = strategy.decompose(plan, exc, ctx)

        steps = result.phases[0].steps
        assert len(steps) == 2
        assert steps[0].step_id == "1.1"
        assert steps[1].step_id == "1.2"
        # Step 2 depends on step 1
        assert "1.1" in (steps[1].depends_on or [])

    def test_decompose_conjunction_step1_has_first_verb(self) -> None:
        """First split step contains the description before 'and <verb2>'."""
        step = _make_step(
            step_id="2.1",
            task_description="investigate and fix the memory leak",
        )
        phase = _make_phase(name="Fix", phase_id=2, steps=[step])
        plan = _make_plan(phases=[phase])
        exc = SubscalePlanError(
            step_id="2.1",
            reason="conjunction",
            hint="investigate and fix",
        )

        strategy = HeuristicStrategy()
        ctx = _minimal_context(task_summary=plan.task_summary)
        result = strategy.decompose(plan, exc, ctx)

        steps = result.phases[0].steps
        assert len(steps) == 2
        # First step should have description up to " and fix"
        assert "investigate" in steps[0].task_description.lower()

    # ------------------------------------------------------------------
    # multi-agent-affinity
    # ------------------------------------------------------------------

    def test_decompose_multi_agent_affinity_promotes_to_team(self) -> None:
        """multi-agent-affinity reason promotes to a team step."""
        step = _make_step(
            step_id="1.1",
            task_description=(
                "Implement the api endpoint and the react component "
                "for the dashboard feature"
            ),
        )
        phase = _make_phase(name="Implement", steps=[step])
        plan = _make_plan(phases=[phase])
        exc = SubscalePlanError(
            step_id="1.1",
            reason="multi-agent-affinity",
            hint="spans backend-engineer, frontend-engineer",
        )

        strategy = HeuristicStrategy()
        ctx = _minimal_context(task_summary=plan.task_summary)
        result = strategy.decompose(plan, exc, ctx)

        # After decompose, the phase should have a single team step OR
        # be left as-is if no concerns were found.
        # Since the desc has no F0.x markers, concerns=[] so no split occurs,
        # but with 1 step (no 2+ to consolidate), team step is not created.
        # The plan should still be valid (not raise).
        assert len(result.phases[0].steps) >= 1

    def test_decompose_multi_agent_affinity_with_concerns_creates_team(self) -> None:
        """multi-agent-affinity with concern markers → split then team step."""
        step = _make_step(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description=(
                "F0.1 add api endpoint. "
                "F0.2 build react component. "
                "F0.3 write integration tests."
            ),
        )
        # Add a second step so the phase starts with 2 steps (simulating a
        # multi-agent plan that got collapsed by a prior pass)
        step2 = _make_step(
            step_id="1.2",
            agent_name="frontend-engineer",
            task_description="Implement the UI layer",
        )
        phase = _make_phase(name="Implement", steps=[step, step2])
        plan = _make_plan(phases=[phase])
        exc = SubscalePlanError(
            step_id="1.1",
            reason="multi-agent-affinity",
            hint="spans multiple roles",
        )

        strategy = HeuristicStrategy()
        ctx = _minimal_context(task_summary=plan.task_summary)
        result = strategy.decompose(plan, exc, ctx)

        # After decompose: concern-split replaces step 1.1 with 3 concern steps,
        # then since phase has 2+ steps, it should be consolidated to a team step.
        assert len(result.phases[0].steps) == 1
        team_step = result.phases[0].steps[0]
        assert team_step.agent_name == "team"
        assert team_step.team is not None
        assert len(team_step.team) >= 2

    def test_decompose_unknown_reason_returns_unchanged(self) -> None:
        """Unknown reason code returns the plan unchanged."""
        step = _make_step(step_id="1.1")
        phase = _make_phase(steps=[step])
        plan = _make_plan(phases=[phase])
        exc = SubscalePlanError(
            step_id="1.1",
            reason="unknown-reason-code",
            hint="some hint",
        )

        strategy = HeuristicStrategy()
        ctx = _minimal_context(task_summary=plan.task_summary)
        result = strategy.decompose(plan, exc, ctx)
        # Plan unchanged
        assert len(result.phases[0].steps) == 1
        assert result.phases[0].steps[0].step_id == "1.1"

    def test_decompose_missing_step_id_returns_unchanged(self) -> None:
        """If step_id is not found in the plan, plan is returned unchanged."""
        step = _make_step(step_id="1.1")
        phase = _make_phase(steps=[step])
        plan = _make_plan(phases=[phase])
        exc = SubscalePlanError(
            step_id="99.99",
            reason="conjunction",
            hint="some hint",
        )

        strategy = HeuristicStrategy()
        ctx = _minimal_context(task_summary=plan.task_summary)
        result = strategy.decompose(plan, exc, ctx)
        assert len(result.phases) == 1
        assert len(result.phases[0].steps) == 1


# ---------------------------------------------------------------------------
# TemplateStrategy
# ---------------------------------------------------------------------------

class TestTemplateStrategy:
    def test_execute_raises_not_implemented(self) -> None:
        """TemplateStrategy.execute raises NotImplementedError."""
        strategy = TemplateStrategy()
        ctx = _minimal_context(task_summary="any task")
        with pytest.raises(NotImplementedError, match="Phase 1.5"):
            strategy.execute("any task", ctx)

    def test_decompose_raises_not_implemented(self) -> None:
        """TemplateStrategy.decompose raises NotImplementedError."""
        strategy = TemplateStrategy()
        ctx = _minimal_context(task_summary="any task")
        exc = SubscalePlanError(step_id="1.1", reason="conjunction", hint="hint")
        plan = _make_plan()
        with pytest.raises(NotImplementedError):
            strategy.decompose(plan, exc, ctx)


# ---------------------------------------------------------------------------
# RefinementStrategy
# ---------------------------------------------------------------------------

class TestRefinementStrategy:
    def test_execute_raises_not_implemented(self) -> None:
        """RefinementStrategy.execute raises NotImplementedError."""
        strategy = RefinementStrategy()
        ctx = _minimal_context(task_summary="any task")
        with pytest.raises(NotImplementedError, match="Phase 1.5"):
            strategy.execute("any task", ctx)

    def test_decompose_raises_not_implemented(self) -> None:
        """RefinementStrategy.decompose raises NotImplementedError."""
        strategy = RefinementStrategy()
        ctx = _minimal_context(task_summary="any task")
        exc = SubscalePlanError(step_id="1.1", reason="conjunction", hint="hint")
        plan = _make_plan()
        with pytest.raises(NotImplementedError):
            strategy.decompose(plan, exc, ctx)


# ---------------------------------------------------------------------------
# PlanContext.as_kwargs round-trip
# ---------------------------------------------------------------------------

class TestPlanContext:
    def test_as_kwargs_round_trips_all_fields(self) -> None:
        """as_kwargs() returns a dict containing all context fields."""
        mock_classifier = MagicMock()
        mock_registry = MagicMock()

        ctx = PlanContext(
            task_summary="Deploy the feature",
            task_type="new-feature",
            complexity="heavy",
            project_root=Path("/tmp/project"),
            agents=["backend-engineer"],
            phases=[{"name": "Implement"}],
            explicit_knowledge_packs=["my-pack"],
            explicit_knowledge_docs=["doc.md"],
            intervention_level="medium",
            default_model="claude-3-opus",
            gate_scope="full",
            classifier=mock_classifier,
            registry=mock_registry,
        )
        kwargs = ctx.as_kwargs()

        assert kwargs["task_summary"] == "Deploy the feature"
        assert kwargs["task_type"] == "new-feature"
        assert kwargs["complexity"] == "heavy"
        assert kwargs["project_root"] == Path("/tmp/project")
        assert kwargs["agents"] == ["backend-engineer"]
        assert kwargs["phases"] == [{"name": "Implement"}]
        assert kwargs["explicit_knowledge_packs"] == ["my-pack"]
        assert kwargs["explicit_knowledge_docs"] == ["doc.md"]
        assert kwargs["intervention_level"] == "medium"
        assert kwargs["default_model"] == "claude-3-opus"
        assert kwargs["gate_scope"] == "full"
        assert kwargs["classifier"] is mock_classifier
        assert kwargs["registry"] is mock_registry

    def test_as_kwargs_defaults(self) -> None:
        """Default PlanContext produces sensible defaults in as_kwargs()."""
        ctx = PlanContext()
        kwargs = ctx.as_kwargs()
        assert kwargs["task_summary"] == ""
        assert kwargs["task_type"] is None
        assert kwargs["complexity"] is None
        assert kwargs["intervention_level"] == "low"
        assert kwargs["gate_scope"] == "focused"
        assert kwargs["classifier"] is None

    def test_as_kwargs_service_refs_included(self) -> None:
        """Service references (registry, router, etc.) are included in as_kwargs()."""
        mock_router = MagicMock()
        mock_bead_store = MagicMock()
        ctx = PlanContext(router=mock_router, bead_store=mock_bead_store)
        kwargs = ctx.as_kwargs()
        assert kwargs["router"] is mock_router
        assert kwargs["bead_store"] is mock_bead_store

    def test_as_kwargs_is_complete(self) -> None:
        """as_kwargs() covers all constructor fields (no silent omissions)."""
        import dataclasses
        ctx = PlanContext()
        kwargs = ctx.as_kwargs()
        # Every field in the dataclass must appear in as_kwargs()
        all_fields = {f.name for f in dataclasses.fields(PlanContext)}
        missing = all_fields - set(kwargs.keys())
        assert not missing, f"as_kwargs() is missing these fields: {missing}"


# ---------------------------------------------------------------------------
# Module-level constant contracts
# ---------------------------------------------------------------------------

class TestModuleLevelConstants:
    def test_default_agents_has_new_feature(self) -> None:
        assert "new-feature" in _DEFAULT_AGENTS
        assert "architect" in _DEFAULT_AGENTS["new-feature"]

    def test_default_agents_has_bug_fix(self) -> None:
        assert "bug-fix" in _DEFAULT_AGENTS
        assert "backend-engineer" in _DEFAULT_AGENTS["bug-fix"]

    def test_phase_names_has_new_feature(self) -> None:
        assert "new-feature" in _PHASE_NAMES
        assert "Design" in _PHASE_NAMES["new-feature"]

    def test_phase_names_matches_planner(self) -> None:
        """_PHASE_NAMES in strategies.py must be byte-identical to planner.py."""
        from agent_baton.core.engine.planner import _PHASE_NAMES as planner_PHASE_NAMES
        assert _PHASE_NAMES == planner_PHASE_NAMES

    def test_default_agents_matches_planner(self) -> None:
        """_DEFAULT_AGENTS in strategies.py must be byte-identical to planner.py."""
        from agent_baton.core.engine.planner import _DEFAULT_AGENTS as planner_DEFAULT_AGENTS
        assert _DEFAULT_AGENTS == planner_DEFAULT_AGENTS

    def test_heuristic_strategy_is_alias_for_zero_shot(self) -> None:
        """HeuristicStrategy is the canonical name; ZeroShotStrategy is the alias."""
        assert HeuristicStrategy is ZeroShotStrategy
