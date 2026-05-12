"""Unit tests for the goal evaluator (G1).

Covers the stub evaluator's decision logic and the universal safety
rail that overrides ``met=True`` when ``last_gate_passed=False``.

The LLM evaluator's network path is not exercised here (hermetic
constraint per tests/CLAUDE.md); we only confirm it falls back to the
stub when the API key is missing.
"""
from __future__ import annotations

import os

from agent_baton.core.engine.goal_evaluator import (
    LLMGoalEvaluator,
    StubGoalEvaluator,
    _apply_safety_rail,
    select_evaluator,
)
from agent_baton.models.execution import (
    ExecutionState,
    GoalCheck,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
    StepStatus,
)


def _plan_with_goal(condition: str = "all tests pass") -> MachinePlan:
    return MachinePlan(
        task_id="t1",
        task_summary="goal-driven task",
        completion_condition=condition,
        max_amend_cycles=3,
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[PlanStep(
                    step_id="1.1", agent_name="backend-engineer",
                    task_description="do the work",
                )],
            ),
        ],
    )


def _completed_state(plan: MachinePlan) -> ExecutionState:
    """An ExecutionState with every step marked complete."""
    state = ExecutionState(task_id="t1", plan=plan)
    for phase in plan.phases:
        for step in phase.steps:
            state.step_results.append(StepResult(
                step_id=step.step_id,
                agent_name=step.agent_name,
                model="sonnet",
                status=StepStatus.COMPLETE,
                outcome="done",
            ))
    return state


class TestStubEvaluator:
    def test_not_met_when_steps_incomplete(self) -> None:
        plan = _plan_with_goal()
        state = ExecutionState(task_id="t1", plan=plan)
        chk = StubGoalEvaluator().evaluate(
            state=state, plan=plan,
            last_gate_passed=True, check_id="g1",
        )
        assert not chk.met
        assert chk.missing  # at least one phase listed as incomplete
        assert chk.evaluator_source == "stub"

    def test_met_when_done_and_gate_passed(self) -> None:
        plan = _plan_with_goal()
        state = _completed_state(plan)
        chk = StubGoalEvaluator().evaluate(
            state=state, plan=plan,
            last_gate_passed=True, check_id="g1",
        )
        assert chk.met
        assert chk.last_gate_passed
        assert chk.confidence >= 0.9

    def test_safety_rail_forces_not_met_when_gate_failed(self) -> None:
        """Even with the safety rail short-circuited (stub never says met
        when gate fails), confirm the helper itself enforces the rule."""
        check = GoalCheck(
            check_id="g1", phase_id=1,
            completion_condition="x", met=True,
            confidence=0.99,
        )
        result = _apply_safety_rail(check, last_gate_passed=False)
        assert not result.met
        assert any("last gate" in m for m in result.missing)

    def test_safety_rail_passthrough_when_gate_passed(self) -> None:
        check = GoalCheck(
            check_id="g1", phase_id=1,
            completion_condition="x", met=True,
            confidence=0.99,
        )
        result = _apply_safety_rail(check, last_gate_passed=True)
        assert result.met
        assert result.last_gate_passed


class TestSelector:
    def test_stub_explicit_selection(self, monkeypatch) -> None:
        monkeypatch.setenv("BATON_GOAL_EVALUATOR", "stub")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-but-ignored")
        assert isinstance(select_evaluator(), StubGoalEvaluator)

    def test_default_falls_back_to_stub_without_api_key(
        self, monkeypatch,
    ) -> None:
        monkeypatch.delenv("BATON_GOAL_EVALUATOR", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert isinstance(select_evaluator(), StubGoalEvaluator)

    def test_llm_selected_when_key_present(self, monkeypatch) -> None:
        monkeypatch.setenv("BATON_GOAL_EVALUATOR", "haiku")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
        assert isinstance(select_evaluator(), LLMGoalEvaluator)

    def test_llm_evaluator_falls_back_to_stub_without_sdk(
        self, monkeypatch,
    ) -> None:
        """Without a usable anthropic SDK or with no API key, evaluate()
        returns a stub-derived GoalCheck rather than raising."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        ev = LLMGoalEvaluator()
        plan = _plan_with_goal()
        state = ExecutionState(task_id="t1", plan=plan)
        chk = ev.evaluate(
            state=state, plan=plan,
            last_gate_passed=False, check_id="g1",
        )
        # Stub fallback was used → evaluator_source == "stub".
        assert chk.evaluator_source == "stub"
        assert not chk.met
