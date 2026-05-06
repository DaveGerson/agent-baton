"""Tests for investigative phase retry in the resolver."""
from __future__ import annotations

import pytest

from agent_baton.core.engine.resolver import (
    ActionResolver,
    DecisionKind,
    ResolverDecision,
)
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step(step_id: str, agent: str = "general-purpose", task: str = "step") -> PlanStep:
    return PlanStep(step_id=step_id, agent_name=agent, task_description=task)


def _investigative_plan(max_retry_phases: int = 3) -> MachinePlan:
    return MachinePlan(
        task_id="test-debug",
        task_summary="debug login failure",
        archetype="investigative",
        max_retry_phases=max_retry_phases,
        phases=[
            PlanPhase(
                phase_id=1,
                name="Investigate",
                steps=[_step("1.1", task="investigate")],
            ),
            PlanPhase(
                phase_id=2,
                name="Hypothesize",
                steps=[_step("2.1", task="hypothesize")],
            ),
            PlanPhase(
                phase_id=3,
                name="Fix",
                steps=[_step("3.1", task="fix")],
            ),
            PlanPhase(
                phase_id=4,
                name="Verify",
                steps=[_step("4.1", task="verify")],
            ),
        ],
    )


class TestDecisionKindRetryPhase:
    def test_retry_phase_exists(self):
        assert hasattr(DecisionKind, 'RETRY_PHASE')
        assert DecisionKind.RETRY_PHASE.value == "retry_phase"

    def test_retry_phase_is_enum_member(self):
        assert DecisionKind.RETRY_PHASE in DecisionKind

    def test_existing_kinds_unchanged(self):
        """Adding RETRY_PHASE must not remove any existing decision kinds."""
        existing = {
            "terminal_complete", "terminal_failed", "dispatch", "gate_pending",
            "approval_pending", "feedback_pending", "phase_advance_ok",
        }
        current = {k.value for k in DecisionKind}
        assert existing.issubset(current)


class TestResolverRetryPhaseDetection:
    def test_non_investigative_never_retries(self):
        plan = MachinePlan(
            task_id="test-feat",
            task_summary="add feature",
            archetype="phased",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Implement",
                    steps=[_step("1.1", agent="backend-engineer", task="implement")],
                ),
            ],
        )
        state = ExecutionState(task_id="test-feat", plan=plan)
        resolver = ActionResolver()
        decision = resolver.determine_next(state)
        assert decision.kind != DecisionKind.RETRY_PHASE

    def test_investigative_plan_with_zero_retry_does_not_retry(self):
        """When max_retry_phases == 0, even investigative plans must not retry."""
        plan = _investigative_plan(max_retry_phases=0)
        state = ExecutionState(task_id="test-debug", plan=plan)
        resolver = ActionResolver()
        decision = resolver.determine_next(state)
        assert decision.kind != DecisionKind.RETRY_PHASE

    def test_direct_archetype_never_retries(self):
        plan = MachinePlan(
            task_id="test-direct",
            task_summary="rename foo to bar",
            archetype="direct",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Implement",
                    steps=[_step("1.1", agent="backend-engineer")],
                ),
                PlanPhase(
                    phase_id=2,
                    name="Review",
                    steps=[_step("2.1", agent="code-reviewer")],
                ),
            ],
        )
        state = ExecutionState(task_id="test-direct", plan=plan)
        resolver = ActionResolver()
        decision = resolver.determine_next(state)
        assert decision.kind != DecisionKind.RETRY_PHASE

    def test_resolver_decision_is_frozen(self):
        """ResolverDecision must be immutable (frozen dataclass)."""
        decision = ResolverDecision(kind=DecisionKind.DISPATCH, step_id="1.1")
        with pytest.raises((AttributeError, TypeError)):
            decision.kind = DecisionKind.RETRY_PHASE  # type: ignore[misc]

    def test_resolver_decision_kind_attribute(self):
        """RETRY_PHASE decision should carry phase context."""
        decision = ResolverDecision(
            kind=DecisionKind.RETRY_PHASE,
            phase_id=1,
            message="Hypothesis did not resolve the issue — retrying investigation.",
        )
        assert decision.kind == DecisionKind.RETRY_PHASE
        assert decision.phase_id == 1

    def test_resolver_decision_retry_phase_message(self):
        decision = ResolverDecision(
            kind=DecisionKind.RETRY_PHASE,
            phase_id=2,
            message="retry",
        )
        assert "retry" in decision.message

    def test_investigative_fresh_state_dispatches_first_step(self):
        """On a fresh investigative plan, the first action should be DISPATCH,
        not RETRY_PHASE — retry only happens after verify fails."""
        plan = _investigative_plan(max_retry_phases=3)
        state = ExecutionState(task_id="test-debug", plan=plan)
        resolver = ActionResolver()
        decision = resolver.determine_next(state)
        # First action of a fresh run is always DISPATCH
        assert decision.kind == DecisionKind.DISPATCH

    def test_phased_plan_with_completed_step_is_not_retry(self):
        """After a step completes normally, the resolver moves forward, not retry."""
        plan = MachinePlan(
            task_id="test-phased",
            task_summary="build feature",
            archetype="phased",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Implement",
                    steps=[
                        _step("1.1", agent="backend-engineer"),
                        _step("1.2", agent="test-engineer"),
                    ],
                ),
            ],
        )
        state = ExecutionState(task_id="test-phased", plan=plan)
        # Record step 1.1 as complete
        state.step_results.append(
            StepResult(step_id="1.1", agent_name="backend-engineer", status="complete")
        )
        resolver = ActionResolver()
        decision = resolver.determine_next(state)
        assert decision.kind != DecisionKind.RETRY_PHASE
