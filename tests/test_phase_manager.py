"""Unit tests for ``agent_baton.core.engine.phase_manager``.

Tests cover all five public methods of :class:`PhaseManager`:

- ``is_phase_complete``
- ``evaluate_phase_approval_gate``
- ``evaluate_phase_feedback_gate``
- ``evaluate_phase_gate``
- ``advance_phase``

Fixtures are built inline as minimal dataclasses — no conftest dependence,
no planner, no I/O.
"""
from __future__ import annotations

import pytest

from agent_baton.core.engine.phase_manager import (
    ApprovalGateOutcome,
    FeedbackGateOutcome,
    GateOutcome,
    PhaseManager,
)
from agent_baton.models.execution import (
    ApprovalResult,
    ExecutionState,
    FeedbackQuestion,
    FeedbackResult,
    GateResult,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_plan(*phases: PlanPhase) -> MachinePlan:
    return MachinePlan(task_id="t-pm", task_summary="phase manager test", phases=list(phases))


def _make_phase(
    phase_id: int,
    *steps: PlanStep,
    gate: PlanGate | None = None,
    approval_required: bool = False,
    approval_description: str = "",
    feedback_questions: list[FeedbackQuestion] | None = None,
) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=f"Phase{phase_id}",
        steps=list(steps),
        gate=gate,
        approval_required=approval_required,
        approval_description=approval_description,
        feedback_questions=feedback_questions or [],
    )


def _make_step(step_id: str) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer",
        task_description="do something",
    )


def _make_gate(gate_type: str = "tests", command: str = "pytest") -> PlanGate:
    return PlanGate(gate_type=gate_type, command=command)


def _make_state(plan: MachinePlan, **kwargs) -> ExecutionState:
    return ExecutionState(task_id="t-pm", plan=plan, **kwargs)


def _step_result(step_id: str, status: str) -> StepResult:
    return StepResult(step_id=step_id, agent_name="backend-engineer", status=status)


def _gate_result(phase_id: int, passed: bool) -> GateResult:
    return GateResult(phase_id=phase_id, gate_type="tests", passed=passed)


def _approval_result(phase_id: int, result: str) -> ApprovalResult:
    return ApprovalResult(phase_id=phase_id, result=result)


def _fq(question_id: str) -> FeedbackQuestion:
    return FeedbackQuestion(
        question_id=question_id,
        question=f"Q: {question_id}?",
        options=["Yes", "No"],
    )


def _fr(phase_id: int, question_id: str) -> FeedbackResult:
    return FeedbackResult(
        phase_id=phase_id,
        question_id=question_id,
        chosen_option="Yes",
        chosen_index=0,
    )


# ---------------------------------------------------------------------------
# PhaseManager instantiation
# ---------------------------------------------------------------------------

class TestPhaseManagerInstantiation:
    def test_zero_arg_constructor(self):
        pm = PhaseManager()
        assert pm is not None

    def test_singleton_like_usage(self):
        # Multiple instances are fine; each is stateless.
        pm1 = PhaseManager()
        pm2 = PhaseManager()
        assert type(pm1) is type(pm2)


# ---------------------------------------------------------------------------
# is_phase_complete
# ---------------------------------------------------------------------------

class TestIsPhaseComplete:
    def setup_method(self):
        self.pm = PhaseManager()

    def test_happy_path_all_complete(self):
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
        assert self.pm.is_phase_complete(state, 1) is True

    def test_one_step_in_flight(self):
        step_a = _make_step("1.1")
        step_b = _make_step("1.2")
        phase = _make_phase(1, step_a, step_b)
        state = _make_state(
            _make_plan(phase),
            step_results=[
                _step_result("1.1", "complete"),
                _step_result("1.2", "dispatched"),
            ],
        )
        assert self.pm.is_phase_complete(state, 1) is False

    def test_interacting_step_is_not_terminal(self):
        step = _make_step("1.1")
        phase = _make_phase(1, step)
        state = _make_state(
            _make_plan(phase),
            step_results=[_step_result("1.1", "interacting")],
        )
        assert self.pm.is_phase_complete(state, 1) is False

    def test_interact_dispatched_is_not_terminal(self):
        step = _make_step("1.1")
        phase = _make_phase(1, step)
        state = _make_state(
            _make_plan(phase),
            step_results=[_step_result("1.1", "interact_dispatched")],
        )
        assert self.pm.is_phase_complete(state, 1) is False

    def test_failed_steps_satisfy_terminal(self):
        step = _make_step("1.1")
        phase = _make_phase(1, step)
        state = _make_state(
            _make_plan(phase),
            step_results=[_step_result("1.1", "failed")],
        )
        assert self.pm.is_phase_complete(state, 1) is True

    def test_interrupted_steps_satisfy_terminal(self):
        step = _make_step("1.1")
        phase = _make_phase(1, step)
        state = _make_state(
            _make_plan(phase),
            step_results=[_step_result("1.1", "interrupted")],
        )
        assert self.pm.is_phase_complete(state, 1) is True

    def test_missing_phase_id_returns_false(self):
        state = _make_state(_make_plan())
        assert self.pm.is_phase_complete(state, 99) is False

    def test_empty_phase_vacuously_complete(self):
        phase = _make_phase(1)  # no steps
        state = _make_state(_make_plan(phase))
        assert self.pm.is_phase_complete(state, 1) is True

    def test_no_results_yet(self):
        step = _make_step("1.1")
        phase = _make_phase(1, step)
        state = _make_state(_make_plan(phase))
        assert self.pm.is_phase_complete(state, 1) is False


# ---------------------------------------------------------------------------
# evaluate_phase_approval_gate
# ---------------------------------------------------------------------------

class TestEvalPhaseApprovalGate:
    def setup_method(self):
        self.pm = PhaseManager()

    def test_required_and_satisfied(self):
        phase = _make_phase(1, _make_step("1.1"), approval_required=True)
        state = _make_state(
            _make_plan(phase),
            approval_results=[_approval_result(1, "approve")],
        )
        outcome = self.pm.evaluate_phase_approval_gate(state, 1)
        assert outcome == ApprovalGateOutcome(required=True, satisfied=True, rejected=False)

    def test_required_and_satisfied_approve_with_feedback(self):
        phase = _make_phase(1, _make_step("1.1"), approval_required=True)
        state = _make_state(
            _make_plan(phase),
            approval_results=[_approval_result(1, "approve-with-feedback")],
        )
        outcome = self.pm.evaluate_phase_approval_gate(state, 1)
        assert outcome.required is True
        assert outcome.satisfied is True
        assert outcome.rejected is False

    def test_required_and_not_satisfied(self):
        phase = _make_phase(1, _make_step("1.1"), approval_required=True)
        state = _make_state(_make_plan(phase))
        outcome = self.pm.evaluate_phase_approval_gate(state, 1)
        assert outcome == ApprovalGateOutcome(required=True, satisfied=False, rejected=False)

    def test_not_required(self):
        phase = _make_phase(1, _make_step("1.1"), approval_required=False)
        state = _make_state(_make_plan(phase))
        outcome = self.pm.evaluate_phase_approval_gate(state, 1)
        assert outcome.required is False
        assert outcome.satisfied is False
        assert outcome.rejected is False

    def test_rejected(self):
        phase = _make_phase(1, _make_step("1.1"), approval_required=True)
        state = _make_state(
            _make_plan(phase),
            approval_results=[_approval_result(1, "reject")],
        )
        outcome = self.pm.evaluate_phase_approval_gate(state, 1)
        assert outcome.required is True
        assert outcome.satisfied is False
        assert outcome.rejected is True

    def test_rejected_then_approved(self):
        # Both a reject AND an approve are present — satisfied wins over rejected.
        phase = _make_phase(1, _make_step("1.1"), approval_required=True)
        state = _make_state(
            _make_plan(phase),
            approval_results=[
                _approval_result(1, "reject"),
                _approval_result(1, "approve"),
            ],
        )
        outcome = self.pm.evaluate_phase_approval_gate(state, 1)
        assert outcome.required is True
        assert outcome.satisfied is True
        assert outcome.rejected is True  # historical reject is still recorded

    def test_missing_phase_id_not_required(self):
        state = _make_state(_make_plan())
        outcome = self.pm.evaluate_phase_approval_gate(state, 99)
        assert outcome.required is False
        assert outcome.satisfied is False


# ---------------------------------------------------------------------------
# evaluate_phase_feedback_gate
# ---------------------------------------------------------------------------

class TestEvalPhaseFeedbackGate:
    def setup_method(self):
        self.pm = PhaseManager()

    def test_required_and_satisfied(self):
        phase = _make_phase(
            1, _make_step("1.1"),
            feedback_questions=[_fq("q1"), _fq("q2")],
        )
        state = _make_state(
            _make_plan(phase),
            current_phase=0,
            feedback_results=[_fr(1, "q1"), _fr(1, "q2")],
        )
        outcome = self.pm.evaluate_phase_feedback_gate(state, 1)
        assert outcome.required is True
        assert outcome.satisfied is True
        assert outcome.pending_question_ids == ()

    def test_required_with_pending_questions(self):
        phase = _make_phase(
            1, _make_step("1.1"),
            feedback_questions=[_fq("q1"), _fq("q2")],
        )
        state = _make_state(
            _make_plan(phase),
            current_phase=0,
            feedback_results=[_fr(1, "q1")],  # q2 unanswered
        )
        outcome = self.pm.evaluate_phase_feedback_gate(state, 1)
        assert outcome.required is True
        assert outcome.satisfied is False
        assert "q2" in outcome.pending_question_ids

    def test_not_required_no_feedback_questions(self):
        phase = _make_phase(1, _make_step("1.1"))
        state = _make_state(_make_plan(phase), current_phase=0)
        outcome = self.pm.evaluate_phase_feedback_gate(state, 1)
        assert outcome.required is False
        assert outcome.satisfied is True
        assert outcome.pending_question_ids == ()

    def test_missing_phase_id_not_required(self):
        state = _make_state(_make_plan())
        outcome = self.pm.evaluate_phase_feedback_gate(state, 99)
        assert outcome.required is False
        assert outcome.satisfied is True

    def test_bd_f4e3_latent_bug_non_current_phase(self):
        """bd-f4e3 (FIXED): querying a non-current phase returns the correct
        phase's feedback state.

        Before the fix the helper read state.current_phase_obj, ignoring
        phase_id and returning the wrong phase's resolution.  After the
        fix (commit bb83587) the helper looks up the phase by its
        phase_id field, so a non-current phase with unanswered questions
        correctly reports satisfied=False with the pending question IDs.
        """
        phase1 = _make_phase(1, _make_step("1.1"))  # no feedback questions
        phase2 = _make_phase(
            2, _make_step("2.1"),
            feedback_questions=[_fq("q1")],
        )
        state = _make_state(
            _make_plan(phase1, phase2),
            current_phase=0,
        )
        # Querying phase 2 (non-current) now correctly reports its
        # unanswered question — independent of state.current_phase.
        outcome = self.pm.evaluate_phase_feedback_gate(state, 2)
        assert outcome.satisfied is False
        assert outcome.required is True
        assert outcome.pending_question_ids == ("q1",)


# ---------------------------------------------------------------------------
# evaluate_phase_gate
# ---------------------------------------------------------------------------

class TestEvalPhaseGate:
    def setup_method(self):
        self.pm = PhaseManager()

    def test_required_and_satisfied(self):
        phase = _make_phase(1, _make_step("1.1"), gate=_make_gate())
        state = _make_state(
            _make_plan(phase),
            gate_results=[_gate_result(1, passed=True)],
        )
        outcome = self.pm.evaluate_phase_gate(state, 1)
        assert outcome == GateOutcome(required=True, satisfied=True, fail_count=0)

    def test_required_and_failing(self):
        phase = _make_phase(1, _make_step("1.1"), gate=_make_gate())
        state = _make_state(
            _make_plan(phase),
            gate_results=[_gate_result(1, passed=False)],
        )
        outcome = self.pm.evaluate_phase_gate(state, 1)
        assert outcome.required is True
        assert outcome.satisfied is False
        assert outcome.fail_count == 1

    def test_fail_count_multiple_failures(self):
        phase = _make_phase(1, _make_step("1.1"), gate=_make_gate())
        state = _make_state(
            _make_plan(phase),
            gate_results=[
                _gate_result(1, passed=False),
                _gate_result(1, passed=False),
                _gate_result(1, passed=True),
            ],
        )
        outcome = self.pm.evaluate_phase_gate(state, 1)
        assert outcome.required is True
        assert outcome.satisfied is True
        assert outcome.fail_count == 2

    def test_not_required_no_gate(self):
        phase = _make_phase(1, _make_step("1.1"), gate=None)
        state = _make_state(_make_plan(phase))
        outcome = self.pm.evaluate_phase_gate(state, 1)
        assert outcome.required is False
        assert outcome.satisfied is False
        assert outcome.fail_count == 0

    def test_wrong_phase_results_not_counted(self):
        phase1 = _make_phase(1, _make_step("1.1"), gate=_make_gate())
        phase2 = _make_phase(2, _make_step("2.1"))
        state = _make_state(
            _make_plan(phase1, phase2),
            gate_results=[_gate_result(2, passed=True)],  # phase 2 passed, not phase 1
        )
        outcome = self.pm.evaluate_phase_gate(state, 1)
        assert outcome.required is True
        assert outcome.satisfied is False
        assert outcome.fail_count == 0

    def test_missing_phase_id_not_required(self):
        state = _make_state(_make_plan())
        outcome = self.pm.evaluate_phase_gate(state, 99)
        assert outcome.required is False
        assert outcome.satisfied is False
        assert outcome.fail_count == 0


# ---------------------------------------------------------------------------
# advance_phase
# ---------------------------------------------------------------------------

class TestAdvancePhase:
    def setup_method(self):
        self.pm = PhaseManager()

    def _two_phase_state(self) -> ExecutionState:
        phase1 = _make_phase(1, _make_step("1.1"))
        phase2 = _make_phase(2, _make_step("2.1"))
        return _make_state(
            _make_plan(phase1, phase2),
            current_phase=0,
            current_step_index=3,
            status="gate_pending",
        )

    def test_increments_current_phase(self):
        state = self._two_phase_state()
        assert state.current_phase == 0
        self.pm.advance_phase(state)
        assert state.current_phase == 1

    def test_resets_step_index_to_zero(self):
        state = self._two_phase_state()
        state.current_step_index = 3
        self.pm.advance_phase(state)
        assert state.current_step_index == 0

    def test_set_status_running_true_flips_status(self):
        state = self._two_phase_state()
        state.status = "gate_pending"
        self.pm.advance_phase(state, set_status_running=True)
        assert state.status == "running"

    def test_set_status_running_false_does_not_flip_status(self):
        state = self._two_phase_state()
        state.status = "gate_pending"
        self.pm.advance_phase(state, set_status_running=False)
        assert state.status == "gate_pending"

    def test_default_does_not_flip_status(self):
        state = self._two_phase_state()
        state.status = "gate_pending"
        self.pm.advance_phase(state)
        assert state.status == "gate_pending"

    def test_multiple_consecutive_advances(self):
        phase1 = _make_phase(1, _make_step("1.1"))
        phase2 = _make_phase(2, _make_step("2.1"))
        phase3 = _make_phase(3, _make_step("3.1"))
        state = _make_state(
            _make_plan(phase1, phase2, phase3),
            current_phase=0,
            current_step_index=0,
        )
        self.pm.advance_phase(state)
        assert state.current_phase == 1
        assert state.current_step_index == 0
        self.pm.advance_phase(state)
        assert state.current_phase == 2
        assert state.current_step_index == 0

    def test_advance_past_last_phase_increments_beyond_bound(self):
        # Engine guards against running off the end; PhaseManager just bumps.
        phase = _make_phase(1, _make_step("1.1"))
        state = _make_state(
            _make_plan(phase),
            current_phase=0,
        )
        self.pm.advance_phase(state)
        # current_phase == 1 is out-of-bounds for a 1-phase plan; engine handles it.
        assert state.current_phase == 1

    def test_phase_advance_ok_arm_pattern(self):
        # Simulates the PHASE_ADVANCE_OK call site: set_status_running=True.
        state = self._two_phase_state()
        state.status = "approval_pending"
        self.pm.advance_phase(state, set_status_running=True)
        assert state.current_phase == 1
        assert state.current_step_index == 0
        assert state.status == "running"

    def test_empty_phase_advance_arm_pattern(self):
        # Simulates the EMPTY_PHASE_ADVANCE call site: set_status_running=False.
        state = self._two_phase_state()
        state.status = "running"
        self.pm.advance_phase(state, set_status_running=False)
        assert state.current_phase == 1
        assert state.current_step_index == 0
        assert state.status == "running"  # status unchanged
