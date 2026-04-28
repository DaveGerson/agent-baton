"""Unit tests for ``agent_baton.core.engine._executor_helpers``.

Each test builds minimal dataclass fixtures inline — no planner, no engine,
no I/O.  The module under test is pure: it only reads data inputs and returns
data outputs.
"""
from __future__ import annotations

import os

import pytest

from agent_baton.core.engine._executor_helpers import (
    approval_passed_for_phase,
    effective_timeout,
    feedback_resolved_for_phase,
    find_step,
    gate_passed_for_phase,
    is_phase_complete,
)
from agent_baton.models.execution import (
    ApprovalResult,
    ExecutionState,
    FeedbackQuestion,
    FeedbackResult,
    GateResult,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_plan(*phases: PlanPhase) -> MachinePlan:
    return MachinePlan(
        task_id="t-test",
        task_summary="test task",
        phases=list(phases),
    )


def _make_phase(phase_id: int, *steps: PlanStep, **kwargs) -> PlanPhase:
    return PlanPhase(phase_id=phase_id, name=f"Phase{phase_id}", steps=list(steps), **kwargs)


def _make_step(step_id: str, *, timeout_seconds: int = 0) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer",
        task_description="do something",
        timeout_seconds=timeout_seconds,
    )


def _make_state(plan: MachinePlan, **kwargs) -> ExecutionState:
    return ExecutionState(task_id="t-test", plan=plan, **kwargs)


def _step_result(step_id: str, status: str) -> StepResult:
    return StepResult(step_id=step_id, agent_name="backend-engineer", status=status)


# ---------------------------------------------------------------------------
# find_step
# ---------------------------------------------------------------------------

class TestFindStep:
    def test_find_step_present(self):
        step = _make_step("1.1")
        state = _make_state(_make_plan(_make_phase(1, step)))
        assert find_step(state, "1.1") is step

    def test_find_step_absent(self):
        step = _make_step("1.1")
        state = _make_state(_make_plan(_make_phase(1, step)))
        assert find_step(state, "9.9") is None

    def test_find_step_searches_all_phases(self):
        step_a = _make_step("1.1")
        step_b = _make_step("2.1")
        step_c = _make_step("2.2")
        state = _make_state(
            _make_plan(
                _make_phase(1, step_a),
                _make_phase(2, step_b, step_c),
            )
        )
        assert find_step(state, "2.2") is step_c

    def test_find_step_returns_first_match(self):
        # If two steps somehow share an ID (shouldn't happen in practice),
        # we still return the first encountered.
        step_a = _make_step("1.1")
        step_b = _make_step("1.1")
        state = _make_state(
            _make_plan(
                _make_phase(1, step_a, step_b),
            )
        )
        assert find_step(state, "1.1") is step_a

    def test_find_step_empty_plan(self):
        state = _make_state(_make_plan())
        assert find_step(state, "1.1") is None


# ---------------------------------------------------------------------------
# effective_timeout
# ---------------------------------------------------------------------------

class TestEffectiveTimeout:
    def test_effective_timeout_uses_step_override(self):
        step = _make_step("1.1", timeout_seconds=120)
        assert effective_timeout(step) == 120

    def test_effective_timeout_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("BATON_DEFAULT_STEP_TIMEOUT_S", "300")
        step = _make_step("1.1", timeout_seconds=0)
        assert effective_timeout(step) == 300

    def test_effective_timeout_step_override_beats_env(self, monkeypatch):
        monkeypatch.setenv("BATON_DEFAULT_STEP_TIMEOUT_S", "300")
        step = _make_step("1.1", timeout_seconds=60)
        assert effective_timeout(step) == 60

    def test_effective_timeout_handles_missing_attr(self, monkeypatch):
        # When no step override and no env var, must return 0.
        monkeypatch.delenv("BATON_DEFAULT_STEP_TIMEOUT_S", raising=False)
        step = _make_step("1.1", timeout_seconds=0)
        assert effective_timeout(step) == 0

    def test_effective_timeout_ignores_non_positive_env(self, monkeypatch):
        monkeypatch.setenv("BATON_DEFAULT_STEP_TIMEOUT_S", "0")
        step = _make_step("1.1", timeout_seconds=0)
        assert effective_timeout(step) == 0

    def test_effective_timeout_ignores_invalid_env(self, monkeypatch):
        monkeypatch.setenv("BATON_DEFAULT_STEP_TIMEOUT_S", "not-a-number")
        step = _make_step("1.1", timeout_seconds=0)
        assert effective_timeout(step) == 0

    def test_effective_timeout_ignores_negative_env(self, monkeypatch):
        monkeypatch.setenv("BATON_DEFAULT_STEP_TIMEOUT_S", "-10")
        step = _make_step("1.1", timeout_seconds=0)
        assert effective_timeout(step) == 0


# ---------------------------------------------------------------------------
# gate_passed_for_phase
# ---------------------------------------------------------------------------

def _gate_result(phase_id: int, passed: bool) -> GateResult:
    return GateResult(phase_id=phase_id, gate_type="tests", passed=passed)


class TestGatePassedForPhase:
    def test_gate_passed_for_phase_no_results(self):
        state = _make_state(_make_plan())
        assert gate_passed_for_phase(state, 1) is False

    def test_gate_passed_for_phase_passing(self):
        state = _make_state(
            _make_plan(),
            gate_results=[_gate_result(1, passed=True)],
        )
        assert gate_passed_for_phase(state, 1) is True

    def test_gate_passed_for_phase_failing(self):
        state = _make_state(
            _make_plan(),
            gate_results=[_gate_result(1, passed=False)],
        )
        assert gate_passed_for_phase(state, 1) is False

    def test_gate_passed_for_phase_wrong_phase(self):
        # A passing gate for phase 2 must not satisfy phase 1.
        state = _make_state(
            _make_plan(),
            gate_results=[_gate_result(2, passed=True)],
        )
        assert gate_passed_for_phase(state, 1) is False

    def test_gate_passed_for_phase_latest_wins(self):
        # Multiple results for the same phase: if ANY is passing, returns True.
        state = _make_state(
            _make_plan(),
            gate_results=[
                _gate_result(1, passed=False),
                _gate_result(1, passed=True),
            ],
        )
        assert gate_passed_for_phase(state, 1) is True

    def test_gate_passed_for_phase_all_failing_latest_wins(self):
        # All failing — must return False.
        state = _make_state(
            _make_plan(),
            gate_results=[
                _gate_result(1, passed=False),
                _gate_result(1, passed=False),
            ],
        )
        assert gate_passed_for_phase(state, 1) is False


# ---------------------------------------------------------------------------
# approval_passed_for_phase
# ---------------------------------------------------------------------------

def _approval_result(phase_id: int, result: str) -> ApprovalResult:
    return ApprovalResult(phase_id=phase_id, result=result)


class TestApprovalPassedForPhase:
    def test_approval_passed_for_phase_no_results(self):
        state = _make_state(_make_plan())
        assert approval_passed_for_phase(state, 1) is False

    def test_approval_passed_for_phase_passing(self):
        state = _make_state(
            _make_plan(),
            approval_results=[_approval_result(1, "approve")],
        )
        assert approval_passed_for_phase(state, 1) is True

    def test_approval_passed_for_phase_approve_with_feedback(self):
        state = _make_state(
            _make_plan(),
            approval_results=[_approval_result(1, "approve-with-feedback")],
        )
        assert approval_passed_for_phase(state, 1) is True

    def test_approval_passed_for_phase_failing(self):
        state = _make_state(
            _make_plan(),
            approval_results=[_approval_result(1, "reject")],
        )
        assert approval_passed_for_phase(state, 1) is False

    def test_approval_passed_for_phase_wrong_phase(self):
        state = _make_state(
            _make_plan(),
            approval_results=[_approval_result(2, "approve")],
        )
        assert approval_passed_for_phase(state, 1) is False

    def test_approval_passed_for_phase_latest_wins(self):
        # reject followed by approve → True (any approve is sufficient).
        state = _make_state(
            _make_plan(),
            approval_results=[
                _approval_result(1, "reject"),
                _approval_result(1, "approve"),
            ],
        )
        assert approval_passed_for_phase(state, 1) is True


# ---------------------------------------------------------------------------
# feedback_resolved_for_phase
# ---------------------------------------------------------------------------

def _fq(question_id: str) -> FeedbackQuestion:
    return FeedbackQuestion(
        question_id=question_id,
        question="Which direction?",
        options=["A", "B"],
    )


def _fr(phase_id: int, question_id: str) -> FeedbackResult:
    return FeedbackResult(
        phase_id=phase_id,
        question_id=question_id,
        chosen_option="A",
        chosen_index=0,
    )


class TestFeedbackResolvedForPhase:
    def test_feedback_resolved_no_current_phase(self):
        # No phases → current_phase_obj is None → resolved by default.
        state = _make_state(_make_plan())
        assert feedback_resolved_for_phase(state, 1) is True

    def test_feedback_resolved_no_questions(self):
        # Phase with no feedback questions → resolved.
        phase = _make_phase(1, _make_step("1.1"))
        state = _make_state(_make_plan(phase), current_phase=0)
        assert feedback_resolved_for_phase(state, 1) is True

    def test_feedback_resolved_all_answered(self):
        phase = _make_phase(
            1, _make_step("1.1"),
            feedback_questions=[_fq("q1"), _fq("q2")],
        )
        state = _make_state(
            _make_plan(phase),
            current_phase=0,
            feedback_results=[_fr(1, "q1"), _fr(1, "q2")],
        )
        assert feedback_resolved_for_phase(state, 1) is True

    def test_feedback_resolved_partial_answers(self):
        phase = _make_phase(
            1, _make_step("1.1"),
            feedback_questions=[_fq("q1"), _fq("q2")],
        )
        state = _make_state(
            _make_plan(phase),
            current_phase=0,
            feedback_results=[_fr(1, "q1")],  # q2 unanswered
        )
        assert feedback_resolved_for_phase(state, 1) is False

    def test_feedback_resolved_no_answers(self):
        phase = _make_phase(
            1, _make_step("1.1"),
            feedback_questions=[_fq("q1")],
        )
        state = _make_state(_make_plan(phase), current_phase=0)
        assert feedback_resolved_for_phase(state, 1) is False

    def test_feedback_resolved_answer_for_wrong_phase_does_not_count(self):
        # Answer recorded for phase 2, but question is in phase 1.
        phase = _make_phase(
            1, _make_step("1.1"),
            feedback_questions=[_fq("q1")],
        )
        state = _make_state(
            _make_plan(phase),
            current_phase=0,
            feedback_results=[_fr(2, "q1")],  # wrong phase_id
        )
        assert feedback_resolved_for_phase(state, 1) is False
class TestIsPhaseComplete:
    def test_all_steps_complete(self):
        step_a = _make_step("1.1")
        step_b = _make_step("1.2")
        phase = _make_phase(1, step_a, step_b)
        state = _make_state(
            _make_plan(phase),
            step_results=[
                _step_result("1.1", "complete"),
                _step_result("1.2", "complete"),
            ],
        )
        assert is_phase_complete(state, 1) is True

    def test_one_step_still_dispatched(self):
        step_a = _make_step("1.1")
        step_b = _make_step("1.2")
        phase = _make_phase(1, step_a, step_b)
        state = _make_state(
            _make_plan(phase),
            step_results=[
                _step_result("1.1", "complete"),
                _step_result("1.2", "dispatched"),  # in-flight
            ],
        )
        assert is_phase_complete(state, 1) is False

    def test_step_interacting_not_terminal(self):
        step = _make_step("1.1")
        phase = _make_phase(1, step)
        state = _make_state(
            _make_plan(phase),
            step_results=[_step_result("1.1", "interacting")],
        )
        assert is_phase_complete(state, 1) is False

    def test_step_interact_dispatched_not_terminal(self):
        step = _make_step("1.1")
        phase = _make_phase(1, step)
        state = _make_state(
            _make_plan(phase),
            step_results=[_step_result("1.1", "interact_dispatched")],
        )
        assert is_phase_complete(state, 1) is False

    def test_failed_steps_are_terminal(self):
        step = _make_step("1.1")
        phase = _make_phase(1, step)
        state = _make_state(
            _make_plan(phase),
            step_results=[_step_result("1.1", "failed")],
        )
        assert is_phase_complete(state, 1) is True

    def test_interrupted_steps_are_terminal(self):
        step = _make_step("1.1")
        phase = _make_phase(1, step)
        state = _make_state(
            _make_plan(phase),
            step_results=[_step_result("1.1", "interrupted")],
        )
        assert is_phase_complete(state, 1) is True

    def test_mixed_terminal_statuses_all_done(self):
        step_a = _make_step("1.1")
        step_b = _make_step("1.2")
        step_c = _make_step("1.3")
        phase = _make_phase(1, step_a, step_b, step_c)
        state = _make_state(
            _make_plan(phase),
            step_results=[
                _step_result("1.1", "complete"),
                _step_result("1.2", "failed"),
                _step_result("1.3", "interrupted"),
            ],
        )
        assert is_phase_complete(state, 1) is True

    def test_missing_phase_id_returns_false(self):
        state = _make_state(_make_plan())
        assert is_phase_complete(state, 99) is False

    def test_empty_phase_vacuously_complete(self):
        # A phase with no steps is trivially complete (all([]) is True).
        phase = _make_phase(1)  # no steps
        state = _make_state(_make_plan(phase))
        assert is_phase_complete(state, 1) is True

    def test_no_results_recorded_yet(self):
        step = _make_step("1.1")
        phase = _make_phase(1, step)
        state = _make_state(_make_plan(phase))
        # No step_results → step_id not in any terminal set
        assert is_phase_complete(state, 1) is False
