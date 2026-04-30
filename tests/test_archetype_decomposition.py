"""Tests for archetype-aware phase decomposition."""
from __future__ import annotations

import pytest

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.stages.decomposition import DecompositionStage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def services():
    """Build a minimal services container from a fresh IntelligentPlanner."""
    from agent_baton.core.engine.planning.planner import IntelligentPlanner
    return IntelligentPlanner()._build_services()


def _draft_for_archetype(
    task_summary: str,
    archetype: str,
    task_type: str,
    complexity: str,
    agents: list[str],
    task_id: str,
) -> PlanDraft:
    """Build a pre-populated PlanDraft ready for DecompositionStage."""
    draft = PlanDraft.from_inputs(
        task_summary,
        task_type=task_type,
        complexity=complexity,
    )
    draft.planning_archetype = archetype
    draft.inferred_type = task_type
    draft.inferred_complexity = complexity
    draft.resolved_agents = agents
    draft.task_id = task_id
    return draft


# ---------------------------------------------------------------------------
# DIRECT archetype
# ---------------------------------------------------------------------------

class TestDirectArchetypeDecomposition:
    def test_direct_produces_two_phases(self, services):
        draft = _draft_for_archetype(
            "rename function foo to bar",
            archetype="direct",
            task_type="refactor",
            complexity="light",
            agents=["backend-engineer"],
            task_id="test-direct",
        )

        stage = DecompositionStage()
        result = stage.run(draft, services)

        assert len(result.plan_phases) == 2
        assert result.plan_phases[0].name == "Implement"
        assert result.plan_phases[1].name == "Review"

    def test_direct_has_single_implement_step(self, services):
        draft = _draft_for_archetype(
            "fix typo in README",
            archetype="direct",
            task_type="bug-fix",
            complexity="light",
            agents=["backend-engineer"],
            task_id="test-direct2",
        )

        stage = DecompositionStage()
        result = stage.run(draft, services)

        implement_phase = result.plan_phases[0]
        assert len(implement_phase.steps) == 1
        assert implement_phase.steps[0].agent_name == "backend-engineer"

    def test_direct_implement_phase_is_first(self, services):
        draft = _draft_for_archetype(
            "delete the unused constant",
            archetype="direct",
            task_type="refactor",
            complexity="light",
            agents=["backend-engineer"],
            task_id="test-direct3",
        )

        stage = DecompositionStage()
        result = stage.run(draft, services)

        phase_names = [p.name for p in result.plan_phases]
        assert phase_names.index("Implement") < phase_names.index("Review")

    def test_direct_uses_resolved_agent(self, services):
        draft = _draft_for_archetype(
            "update version number",
            archetype="direct",
            task_type="refactor",
            complexity="light",
            agents=["frontend-engineer"],
            task_id="test-direct4",
        )

        stage = DecompositionStage()
        result = stage.run(draft, services)

        implement_phase = result.plan_phases[0]
        # The resolved agent must be used, not a hardcoded default
        assert implement_phase.steps[0].agent_name == "frontend-engineer"


# ---------------------------------------------------------------------------
# INVESTIGATIVE archetype
# ---------------------------------------------------------------------------

class TestInvestigativeArchetypeDecomposition:
    def test_investigative_produces_four_phases(self, services):
        draft = _draft_for_archetype(
            "debug why login returns 500 error",
            archetype="investigative",
            task_type="bug-fix",
            complexity="medium",
            agents=["backend-engineer"],
            task_id="test-invest",
        )

        stage = DecompositionStage()
        result = stage.run(draft, services)

        assert len(result.plan_phases) == 4
        phase_names = [p.name for p in result.plan_phases]
        assert "Investigate" in phase_names
        assert "Hypothesize" in phase_names
        assert "Fix" in phase_names
        assert "Verify" in phase_names

    def test_investigative_phase_order(self, services):
        draft = _draft_for_archetype(
            "diagnose memory leak in the API server",
            archetype="investigative",
            task_type="bug-fix",
            complexity="medium",
            agents=["backend-engineer"],
            task_id="test-invest-order",
        )

        stage = DecompositionStage()
        result = stage.run(draft, services)

        phase_names = [p.name for p in result.plan_phases]
        assert phase_names.index("Investigate") < phase_names.index("Hypothesize")
        assert phase_names.index("Hypothesize") < phase_names.index("Fix")
        assert phase_names.index("Fix") < phase_names.index("Verify")

    def test_investigative_investigate_uses_opus(self, services):
        draft = _draft_for_archetype(
            "diagnose intermittent test failures",
            archetype="investigative",
            task_type="bug-fix",
            complexity="medium",
            agents=["backend-engineer"],
            task_id="test-invest2",
        )

        stage = DecompositionStage()
        result = stage.run(draft, services)

        investigate_phase = result.plan_phases[0]
        assert investigate_phase.steps[0].model == "opus"

    def test_investigative_fix_phase_has_test_step(self, services):
        draft = _draft_for_archetype(
            "fix race condition in worker pool",
            archetype="investigative",
            task_type="bug-fix",
            complexity="medium",
            agents=["backend-engineer"],
            task_id="test-invest3",
        )

        stage = DecompositionStage()
        result = stage.run(draft, services)

        fix_phase = next(p for p in result.plan_phases if p.name == "Fix")
        agent_names = [s.agent_name for s in fix_phase.steps]
        assert "test-engineer" in agent_names

    def test_investigative_all_phases_have_steps(self, services):
        draft = _draft_for_archetype(
            "debug why the scheduler misses jobs",
            archetype="investigative",
            task_type="bug-fix",
            complexity="medium",
            agents=["backend-engineer"],
            task_id="test-invest4",
        )

        stage = DecompositionStage()
        result = stage.run(draft, services)

        for phase in result.plan_phases:
            assert len(phase.steps) >= 1, (
                f"Phase '{phase.name}' has no steps"
            )

    def test_investigative_verify_phase_has_test_engineer(self, services):
        draft = _draft_for_archetype(
            "fix the broken authentication check",
            archetype="investigative",
            task_type="bug-fix",
            complexity="medium",
            agents=["backend-engineer"],
            task_id="test-invest5",
        )

        stage = DecompositionStage()
        result = stage.run(draft, services)

        verify_phase = next(p for p in result.plan_phases if p.name == "Verify")
        agent_names = [s.agent_name for s in verify_phase.steps]
        assert "test-engineer" in agent_names


# ---------------------------------------------------------------------------
# PHASED archetype (fallthrough)
# ---------------------------------------------------------------------------

class TestPhasedArchetypeFallthrough:
    def test_phased_uses_existing_logic(self, services):
        """PHASED archetype should fall through to existing decomposition."""
        draft = _draft_for_archetype(
            "build authentication system with OAuth2 and JWT",
            archetype="phased",
            task_type="new-feature",
            complexity="heavy",
            agents=["backend-engineer", "security-reviewer"],
            task_id="test-phased",
        )

        stage = DecompositionStage()
        result = stage.run(draft, services)

        # Phased falls through to existing logic, should have 3+ phases
        assert len(result.plan_phases) >= 3

    def test_phased_result_is_not_empty(self, services):
        draft = _draft_for_archetype(
            "add user profile management",
            archetype="phased",
            task_type="new-feature",
            complexity="medium",
            agents=["backend-engineer"],
            task_id="test-phased2",
        )

        stage = DecompositionStage()
        result = stage.run(draft, services)

        assert len(result.plan_phases) >= 1

    def test_phased_all_phases_have_steps(self, services):
        draft = _draft_for_archetype(
            "implement full user registration flow",
            archetype="phased",
            task_type="new-feature",
            complexity="medium",
            agents=["backend-engineer", "test-engineer"],
            task_id="test-phased3",
        )

        stage = DecompositionStage()
        result = stage.run(draft, services)

        for phase in result.plan_phases:
            assert len(phase.steps) >= 1, (
                f"Phase '{phase.name}' has no steps"
            )


# ---------------------------------------------------------------------------
# Unknown archetype — graceful fallback
# ---------------------------------------------------------------------------

class TestUnknownArchetypeFallback:
    def test_unknown_archetype_does_not_crash(self, services):
        """An unrecognised archetype value must not raise — fall through to phased."""
        draft = _draft_for_archetype(
            "add a button",
            archetype="future-archetype-not-yet-defined",
            task_type="new-feature",
            complexity="light",
            agents=["frontend-engineer"],
            task_id="test-unknown",
        )

        stage = DecompositionStage()
        result = stage.run(draft, services)

        # Must produce at least one phase with at least one step
        assert len(result.plan_phases) >= 1
        for phase in result.plan_phases:
            assert len(phase.steps) >= 1
