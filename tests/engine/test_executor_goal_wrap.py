"""Tests for the goal wrap-and-refine path in ExecutionEngine (G1.d).

Exercises ``ExecutionEngine._evaluate_goal_after_gate`` directly. The
helper is invoked from ``record_gate_result`` on the gate-passed branch
and is the integration seam between the goal evaluator and amend_plan.

We stub the evaluator (via monkeypatching ``select_evaluator``) so the
tests are hermetic and deterministic — no LLM calls.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import (
    ExecutionState,
    GoalCheck,
    MachinePlan,
    PlanPhase,
    PlanStep,
)


def _plan(condition: str | None = "all tests pass", max_amend: int = 3) -> MachinePlan:
    return MachinePlan(
        task_id="t1",
        task_summary="goal task",
        completion_condition=condition,
        max_amend_cycles=max_amend,
        phases=[
            PlanPhase(
                phase_id=1, name="Implement",
                steps=[PlanStep(
                    step_id="1.1", agent_name="backend-engineer",
                    task_description="do the work",
                )],
            ),
        ],
    )


def _started_engine(tmp_path: Path, plan: MachinePlan) -> tuple[ExecutionEngine, ExecutionState]:
    engine = ExecutionEngine(team_context_root=tmp_path)
    engine.start(plan)
    state = engine._load_execution()
    assert state is not None
    return engine, state


class _FakeEvaluator:
    """Returns a canned GoalCheck. Used to drive the executor branches
    without invoking the real selector or any network."""

    def __init__(self, check: GoalCheck) -> None:
        self._check = check

    def evaluate(self, *, state, plan, last_gate_passed, check_id):  # noqa: ARG002
        # Stamp the check_id so the executor's monotonic counter holds.
        self._check.check_id = check_id
        self._check.last_gate_passed = last_gate_passed
        return self._check


def _patch_evaluator(monkeypatch: pytest.MonkeyPatch, check: GoalCheck) -> None:
    monkeypatch.setattr(
        "agent_baton.core.engine.goal_evaluator.select_evaluator",
        lambda: _FakeEvaluator(check),
    )


class TestGoalWrapAndRefine:
    def test_noop_when_no_completion_condition(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the plan has no goal, the helper does nothing — no
        goal_checks appended, no amendments, no status mutation."""
        plan = _plan(condition=None)
        engine, state = _started_engine(tmp_path, plan)
        engine._evaluate_goal_after_gate(
            state, passed_phase_id=1, last_gate_passed=True,
        )
        assert state.goal_checks == []
        assert state.amend_cycles_used == 0
        assert state.goal_status == ""

    def test_met_records_check_and_sets_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        plan = _plan()
        check = GoalCheck(
            check_id="will-be-overwritten",
            phase_id=1,
            completion_condition=plan.completion_condition or "",
            met=True, confidence=0.95,
        )
        _patch_evaluator(monkeypatch, check)
        engine, state = _started_engine(tmp_path, plan)

        engine._evaluate_goal_after_gate(
            state, passed_phase_id=1, last_gate_passed=True,
        )

        assert len(state.goal_checks) == 1
        assert state.goal_checks[0].check_id == "g1"
        assert state.goal_status == "met"
        # No amendment when met.
        assert state.amend_cycles_used == 0
        assert state.amendments == []

    def test_not_met_with_suggestions_amends_plan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        plan = _plan(max_amend=3)
        suggested = {
            "phase_id": 99,  # will be renumbered by helper
            "name": "Close-the-gap",
            "steps": [{
                "step_id": "99.1",
                "agent_name": "backend-engineer",
                "task_description": "address the gap",
            }],
        }
        check = GoalCheck(
            check_id="x", phase_id=1,
            completion_condition=plan.completion_condition or "",
            met=False, confidence=0.4,
            missing=["test coverage incomplete"],
            suggested_phases=[suggested],
            reasoning="needs more tests",
        )
        _patch_evaluator(monkeypatch, check)
        engine, state = _started_engine(tmp_path, plan)
        starting_phases = len(state.plan.phases)

        engine._evaluate_goal_after_gate(
            state, passed_phase_id=1, last_gate_passed=True,
        )

        assert state.amend_cycles_used == 1
        assert state.goal_status == "active"
        # An amendment was recorded with the goal_round_out trigger.
        assert len(state.amendments) == 1
        assert state.amendments[0].trigger == "goal_round_out"
        # And a new phase actually landed on the plan.
        assert len(state.plan.phases) == starting_phases + 1

    def test_not_met_without_suggestions_marks_active(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        plan = _plan()
        check = GoalCheck(
            check_id="x", phase_id=1,
            completion_condition=plan.completion_condition or "",
            met=False, confidence=0.4,
            missing=["something"],
            suggested_phases=[],
        )
        _patch_evaluator(monkeypatch, check)
        engine, state = _started_engine(tmp_path, plan)

        engine._evaluate_goal_after_gate(
            state, passed_phase_id=1, last_gate_passed=True,
        )

        assert state.goal_status == "active"
        assert state.amend_cycles_used == 0
        assert state.amendments == []

    def test_budget_exhausted_marks_exhausted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        plan = _plan(max_amend=1)
        suggested = {
            "phase_id": 50,
            "name": "Follow-up",
            "steps": [{
                "step_id": "50.1",
                "agent_name": "backend-engineer",
                "task_description": "more work",
            }],
        }
        check = GoalCheck(
            check_id="x", phase_id=1,
            completion_condition=plan.completion_condition or "",
            met=False, confidence=0.4,
            missing=["still gaps"],
            suggested_phases=[suggested],
        )
        _patch_evaluator(monkeypatch, check)
        engine, state = _started_engine(tmp_path, plan)
        # Pretend we already burned our one allowed cycle.
        state.amend_cycles_used = 1

        engine._evaluate_goal_after_gate(
            state, passed_phase_id=1, last_gate_passed=True,
        )

        assert state.goal_status == "exhausted"
        # No further amendment.
        assert state.amend_cycles_used == 1
        assert state.amendments == []
