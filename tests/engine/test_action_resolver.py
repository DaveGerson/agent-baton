"""Unit tests for ActionResolver / ResolverDecision (005b step 2.2C).

Covers every :class:`DecisionKind` value with a minimal inline
``ExecutionState`` fixture.  No engine instance is constructed — these
tests prove the resolver is stateless and depends only on
``ExecutionState`` plus its ``max_gate_retries`` constructor argument.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from agent_baton.core.engine.resolver import (
    ActionResolver,
    DecisionKind,
    ResolverDecision,
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
# Builder helpers — keep tests dense and readable.
# ---------------------------------------------------------------------------


def _step(
    step_id: str = "1.1",
    agent_name: str = "developer",
    *,
    depends_on: list[str] | None = None,
    team: list | None = None,
    interactive: bool = False,
    timeout_seconds: int = 0,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description="do work",
        depends_on=depends_on or [],
        team=team or [],
        interactive=interactive,
        timeout_seconds=timeout_seconds,
    )


def _phase(
    phase_id: int = 0,
    *,
    steps: list[PlanStep] | None = None,
    gate: PlanGate | None = None,
    approval_required: bool = False,
    feedback_questions: list[FeedbackQuestion] | None = None,
) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=f"phase-{phase_id}",
        steps=steps if steps is not None else [_step()],
        gate=gate,
        approval_required=approval_required,
        feedback_questions=feedback_questions or [],
    )


def _plan(phases: list[PlanPhase] | None = None) -> MachinePlan:
    return MachinePlan(
        task_id="task-test",
        task_summary="test",
        phases=phases if phases is not None else [_phase()],
    )


def _state(
    *,
    plan: MachinePlan | None = None,
    status: str = "running",
    current_phase: int = 0,
    step_results: list[StepResult] | None = None,
    gate_results: list[GateResult] | None = None,
    approval_results: list[ApprovalResult] | None = None,
    feedback_results: list[FeedbackResult] | None = None,
    takeover_records: list[dict] | None = None,
) -> ExecutionState:
    return ExecutionState(
        task_id="task-test",
        plan=plan if plan is not None else _plan(),
        current_phase=current_phase,
        status=status,
        step_results=step_results or [],
        gate_results=gate_results or [],
        approval_results=approval_results or [],
        feedback_results=feedback_results or [],
        takeover_records=takeover_records or [],
    )


# ---------------------------------------------------------------------------
# Constructor / dataclass invariants
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_default_max_gate_retries_is_three(self) -> None:
        resolver = ActionResolver()
        assert resolver._max_gate_retries == 3

    def test_custom_max_gate_retries(self) -> None:
        resolver = ActionResolver(max_gate_retries=7)
        assert resolver._max_gate_retries == 7


class TestResolverDecisionShape:
    def test_resolver_decision_is_frozen(self) -> None:
        assert dataclasses.is_dataclass(ResolverDecision)
        decision = ResolverDecision(kind=DecisionKind.WAIT)
        with pytest.raises(dataclasses.FrozenInstanceError):
            decision.kind = DecisionKind.DISPATCH  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Terminal states
# ---------------------------------------------------------------------------


class TestTerminal:
    def test_terminal_complete_returns_terminal_complete(self) -> None:
        state = _state(status="complete")
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.TERMINAL_COMPLETE
        assert "complete" in decision.message.lower()

    def test_terminal_failed_returns_terminal_failed(self) -> None:
        sr = StepResult(step_id="1.1", agent_name="dev", status="failed")
        state = _state(status="failed", step_results=[sr])
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.TERMINAL_FAILED
        assert "1.1" in decision.message

    def test_terminal_failed_message_uses_approval_rejection(self) -> None:
        rej = ApprovalResult(phase_id=0, result="reject", feedback="no")
        state = _state(status="failed", approval_results=[rej])
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.TERMINAL_FAILED
        assert "approval was rejected" in decision.message


# ---------------------------------------------------------------------------
# Pending statuses
# ---------------------------------------------------------------------------


class TestPendingStatuses:
    def test_approval_pending(self) -> None:
        phase = _phase(approval_required=True)
        state = _state(plan=_plan([phase]), status="approval_pending")
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.APPROVAL_PENDING
        assert decision.phase_id == 0

    def test_feedback_pending(self) -> None:
        q = FeedbackQuestion(question_id="q1", question="why?")
        phase = _phase(feedback_questions=[q])
        state = _state(plan=_plan([phase]), status="feedback_pending")
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.FEEDBACK_PENDING
        assert decision.phase_id == 0

    def test_gate_pending(self) -> None:
        gate = PlanGate(gate_type="test", command="pytest")
        phase = _phase(gate=gate)
        state = _state(plan=_plan([phase]), status="gate_pending")
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.GATE_PENDING
        assert decision.phase_id == 0


class TestGateFailed:
    def test_gate_failed_under_retries(self) -> None:
        gate = PlanGate(gate_type="test", command="pytest")
        phase = _phase(gate=gate)
        gr = GateResult(phase_id=0, gate_type="test", passed=False)
        state = _state(
            plan=_plan([phase]),
            status="gate_failed",
            gate_results=[gr],
        )
        decision = ActionResolver(max_gate_retries=3).determine_next(state)
        assert decision.kind is DecisionKind.GATE_FAILED
        assert decision.fail_count == 1
        assert decision.phase_id == 0

    def test_gate_failed_at_terminal_after_max_retries(self) -> None:
        gate = PlanGate(gate_type="test", command="pytest")
        phase = _phase(gate=gate)
        # 3 failed gate results, max_gate_retries=3 → terminal.
        gates = [
            GateResult(phase_id=0, gate_type="test", passed=False)
            for _ in range(3)
        ]
        state = _state(
            plan=_plan([phase]),
            status="gate_failed",
            gate_results=gates,
        )
        decision = ActionResolver(max_gate_retries=3).determine_next(state)
        assert decision.kind is DecisionKind.TERMINAL_FAILED
        assert decision.fail_count == 3
        assert decision.phase_id == 0


class TestPausedAndBudget:
    def test_paused_takeover(self) -> None:
        records = [{"step_id": "1.1", "resumed_at": ""}]
        state = _state(status="paused-takeover", takeover_records=records)
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.PAUSED_TAKEOVER
        assert decision.step_id == "1.1"

    def test_budget_exceeded(self) -> None:
        sr = StepResult(
            step_id="1.1",
            agent_name="dev",
            status="complete",
            estimated_tokens=42_000,
        )
        state = _state(status="budget_exceeded", step_results=[sr])
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.BUDGET_EXCEEDED
        assert "42,000" in decision.message


# ---------------------------------------------------------------------------
# Phase boundaries — empty phases, no phases left
# ---------------------------------------------------------------------------


class TestPhaseBoundaries:
    def test_no_phases_returns_no_phases_left(self) -> None:
        state = _state(plan=_plan([]), status="running")
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.NO_PHASES_LEFT

    def test_empty_phase_with_gate_pending_returns_empty_phase_gate(
        self,
    ) -> None:
        gate = PlanGate(gate_type="test", command="pytest")
        phase = _phase(steps=[], gate=gate)
        state = _state(plan=_plan([phase]))
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.EMPTY_PHASE_GATE
        assert decision.phase_id == 0

    def test_empty_phase_no_gate_returns_empty_phase_advance(self) -> None:
        phase = _phase(steps=[], gate=None)
        state = _state(plan=_plan([phase]))
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.EMPTY_PHASE_ADVANCE
        assert decision.phase_id == 0

    def test_empty_phase_with_passing_gate_returns_advance(self) -> None:
        gate = PlanGate(gate_type="test", command="pytest")
        phase = _phase(steps=[], gate=gate)
        gr = GateResult(phase_id=0, gate_type="test", passed=True)
        state = _state(plan=_plan([phase]), gate_results=[gr])
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.EMPTY_PHASE_ADVANCE


# ---------------------------------------------------------------------------
# Dispatch decisions
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_dispatch_when_next_step_ready(self) -> None:
        state = _state()  # default: one pending step
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.DISPATCH
        assert decision.step_id == "1.1"
        assert decision.phase_id == 0

    def test_team_dispatch_for_team_step(self) -> None:
        # Use a non-empty list to flag the step as a team step; the
        # resolver only checks truthiness of step.team.  Slice 11
        # converted PlanStep to Pydantic, so the placeholder must now be
        # a real TeamMember instance.
        from agent_baton.models.execution import TeamMember
        team_step = _step(team=[TeamMember(member_id="x", agent_name="dev")])
        phase = _phase(steps=[team_step])
        state = _state(plan=_plan([phase]))
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.TEAM_DISPATCH
        assert decision.step_id == "1.1"

    def test_dispatch_skips_dispatched_step(self) -> None:
        s1 = _step(step_id="1.1")
        s2 = _step(step_id="1.2")
        phase = _phase(steps=[s1, s2])
        sr = StepResult(step_id="1.1", agent_name="dev", status="dispatched")
        state = _state(plan=_plan([phase]), step_results=[sr])
        decision = ActionResolver().determine_next(state)
        # 1.1 is in-flight, 1.2 should be the next dispatch.
        assert decision.kind is DecisionKind.DISPATCH
        assert decision.step_id == "1.2"

    def test_dispatch_respects_depends_on(self) -> None:
        s1 = _step(step_id="1.1")
        s2 = _step(step_id="1.2", depends_on=["1.1"])
        phase = _phase(steps=[s1, s2])
        # 1.1 is still running → 1.2 must NOT be dispatched, WAIT instead.
        sr = StepResult(step_id="1.1", agent_name="dev", status="dispatched")
        state = _state(plan=_plan([phase]), step_results=[sr])
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.WAIT


# ---------------------------------------------------------------------------
# INTERACT decisions
# ---------------------------------------------------------------------------


class TestInteract:
    def test_interact_when_step_in_interacting(self) -> None:
        s = _step(interactive=True)
        phase = _phase(steps=[s])
        sr = StepResult(step_id="1.1", agent_name="dev", status="interacting")
        state = _state(plan=_plan([phase]), step_results=[sr])
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.INTERACT
        assert decision.step_id == "1.1"

    def test_interact_continue_when_human_input_provided(self) -> None:
        s = _step(interactive=True)
        phase = _phase(steps=[s])
        sr = StepResult(
            step_id="1.1",
            agent_name="dev",
            status="interact_dispatched",
        )
        state = _state(plan=_plan([phase]), step_results=[sr])
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.INTERACT_CONTINUE
        assert decision.step_id == "1.1"


# ---------------------------------------------------------------------------
# WAIT, FAIL, TIMEOUT
# ---------------------------------------------------------------------------


class TestWaitFailTimeout:
    def test_wait_when_steps_dispatched(self) -> None:
        s1 = _step(step_id="1.1")
        s2 = _step(step_id="1.2")
        phase = _phase(steps=[s1, s2])
        sr1 = StepResult(step_id="1.1", agent_name="dev", status="dispatched")
        sr2 = StepResult(step_id="1.2", agent_name="dev", status="dispatched")
        state = _state(plan=_plan([phase]), step_results=[sr1, sr2])
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.WAIT
        assert decision.phase_id == 0

    def test_step_failed_in_phase_returns_failed(self) -> None:
        s1 = _step(step_id="1.1")
        phase = _phase(steps=[s1])
        sr = StepResult(step_id="1.1", agent_name="dev", status="failed")
        state = _state(plan=_plan([phase]), step_results=[sr])
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.STEP_FAILED_IN_PHASE
        assert "1.1" in decision.failed_step_ids
        assert decision.step_id == "1.1"

    def test_timeout_when_dispatched_step_exceeds_timeout(self) -> None:
        # Step with a 1s timeout, started 60s ago → timed out.
        s = _step(timeout_seconds=1)
        phase = _phase(steps=[s])
        old = (
            datetime.now(timezone.utc) - timedelta(seconds=60)
        ).isoformat(timespec="seconds")
        sr = StepResult(
            step_id="1.1",
            agent_name="dev",
            status="dispatched",
            step_started_at=old,
        )
        state = _state(plan=_plan([phase]), step_results=[sr])
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.TIMEOUT
        assert decision.step_id == "1.1"
        assert "timed out" in decision.message


# ---------------------------------------------------------------------------
# Phase-complete progression: approval > feedback > gate > advance
# ---------------------------------------------------------------------------


class TestPhaseCompletion:
    def _phase_with_one_complete_step(self, **phase_kw) -> ExecutionState:
        s = _step(step_id="1.1")
        phase = _phase(steps=[s], **phase_kw)
        sr = StepResult(step_id="1.1", agent_name="dev", status="complete")
        return _state(plan=_plan([phase]), step_results=[sr])

    def test_phase_needs_approval(self) -> None:
        state = self._phase_with_one_complete_step(approval_required=True)
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.PHASE_NEEDS_APPROVAL
        assert decision.phase_id == 0

    def test_phase_needs_feedback(self) -> None:
        q = FeedbackQuestion(question_id="q1", question="why?")
        state = self._phase_with_one_complete_step(feedback_questions=[q])
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.PHASE_NEEDS_FEEDBACK
        assert decision.phase_id == 0

    def test_phase_needs_gate_after_all_steps_complete(self) -> None:
        gate = PlanGate(gate_type="test", command="pytest")
        state = self._phase_with_one_complete_step(gate=gate)
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.PHASE_NEEDS_GATE
        assert decision.phase_id == 0

    def test_phase_advance_ok_after_gate_and_approval(self) -> None:
        gate = PlanGate(gate_type="test", command="pytest")
        s = _step(step_id="1.1")
        phase = _phase(steps=[s], gate=gate, approval_required=True)
        sr = StepResult(step_id="1.1", agent_name="dev", status="complete")
        gr = GateResult(phase_id=0, gate_type="test", passed=True)
        ar = ApprovalResult(phase_id=0, result="approve")
        state = _state(
            plan=_plan([phase]),
            step_results=[sr],
            gate_results=[gr],
            approval_results=[ar],
        )
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.PHASE_ADVANCE_OK
        assert decision.phase_id == 0

    def test_phase_advance_ok_with_no_gate_or_approval(self) -> None:
        s = _step(step_id="1.1")
        phase = _phase(steps=[s])
        sr = StepResult(step_id="1.1", agent_name="dev", status="complete")
        state = _state(plan=_plan([phase]), step_results=[sr])
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.PHASE_ADVANCE_OK

    def test_phase_advance_with_feedback_resolved(self) -> None:
        q = FeedbackQuestion(question_id="q1", question="why?")
        s = _step(step_id="1.1")
        phase = _phase(steps=[s], feedback_questions=[q])
        sr = StepResult(step_id="1.1", agent_name="dev", status="complete")
        fr = FeedbackResult(
            phase_id=0,
            question_id="q1",
            chosen_option="opt-a",
            chosen_index=0,
        )
        state = _state(
            plan=_plan([phase]),
            step_results=[sr],
            feedback_results=[fr],
        )
        decision = ActionResolver().determine_next(state)
        assert decision.kind is DecisionKind.PHASE_ADVANCE_OK
