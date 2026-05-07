"""Unit tests for ExecutionPhaseState classes (005b step 3.3b).

Covers every :class:`DecisionKind` value routed through each state class
as specified in ``docs/internal/005b-phase3-design.md`` §2.3.

Design contract under test:
- :class:`PlanningState` — all decisions are no-ops (status == 'pending').
- :class:`ExecutingPhaseState` — status flips for gate/approval/feedback/
  failure kinds; no-ops for dispatch/advance; raises on illegal kinds.
- :class:`AwaitingApprovalState` — pass-through for blocked-state kinds;
  raises on DISPATCH-class; TERMINAL_FAILED flips to 'failed'.
- :class:`TerminalState` — pass-through for terminal kinds; raises on all
  non-terminal kinds.

No engine instance is constructed. All fixtures are inline.
"""

from __future__ import annotations

import pytest

from agent_baton.core.engine.resolver import DecisionKind, ResolverDecision
from agent_baton.core.engine.states import (
    AwaitingApprovalState,
    ExecutingPhaseState,
    ExecutionPhaseStateProtocol,
    PlanningState,
    TerminalState,
)
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Minimal fixture builders — no conftest dependency.
# ---------------------------------------------------------------------------


def _step(step_id: str = "1.1") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="developer",
        task_description="do work",
        depends_on=[],
        team=[],
    )


def _phase(phase_id: int = 0, *, steps: list[PlanStep] | None = None) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=f"phase-{phase_id}",
        steps=steps if steps is not None else [_step()],
    )


def _plan(phases: list[PlanPhase] | None = None) -> MachinePlan:
    return MachinePlan(
        task_id="task-test",
        task_summary="test",
        phases=phases if phases is not None else [_phase()],
    )


def _state(status: str = "running") -> ExecutionState:
    # Slice 13's I1/I2/I9 model_validator forbids constructing torn
    # states; the fixture must produce a state that satisfies the
    # invariants for the requested status.
    from agent_baton.models.execution import PendingApprovalRequest
    kwargs: dict = {
        "task_id": "task-test",
        "plan": _plan(),
        "status": status,
    }
    if status == "approval_pending":
        kwargs["pending_approval_request"] = PendingApprovalRequest(
            phase_id=0, requester="test",
        )
    if status in {"complete", "failed", "cancelled"}:
        kwargs["completed_at"] = "2026-05-07T00:00:00+00:00"
    if status == "paused-takeover":
        kwargs["takeover_records"] = [
            {"takeover_id": "t-1", "started_at": "2026-05-07T00:00:00+00:00",
             "started_by": "test", "scope": "phase", "reason": "test",
             "resumed_at": ""},
        ]
    return ExecutionState(**kwargs)


def _decision(kind: DecisionKind, **kwargs) -> ResolverDecision:
    return ResolverDecision(kind=kind, **kwargs)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """Each concrete class must satisfy ExecutionPhaseStateProtocol."""

    def test_planning_state_satisfies_protocol(self) -> None:
        state_obj: ExecutionPhaseStateProtocol = PlanningState()
        assert callable(state_obj.handle)

    def test_executing_phase_state_satisfies_protocol(self) -> None:
        state_obj: ExecutionPhaseStateProtocol = ExecutingPhaseState()
        assert callable(state_obj.handle)

    def test_awaiting_approval_state_satisfies_protocol(self) -> None:
        state_obj: ExecutionPhaseStateProtocol = AwaitingApprovalState()
        assert callable(state_obj.handle)

    def test_terminal_state_satisfies_protocol(self) -> None:
        state_obj: ExecutionPhaseStateProtocol = TerminalState()
        assert callable(state_obj.handle)


# ---------------------------------------------------------------------------
# PlanningState
# ---------------------------------------------------------------------------


class TestPlanningState:
    """state.status == 'pending'. All decisions are no-ops — the engine
    drives the first transition via start()."""

    def setup_method(self) -> None:
        self.handler = PlanningState()

    def _state(self) -> ExecutionState:
        return _state(status="pending")

    def test_dispatch_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.DISPATCH, step_id="1.1"))
        assert state.status == "pending"

    def test_team_dispatch_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.TEAM_DISPATCH, step_id="1.1"))
        assert state.status == "pending"

    def test_wait_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.WAIT))
        assert state.status == "pending"

    def test_terminal_complete_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.TERMINAL_COMPLETE))
        assert state.status == "pending"

    def test_terminal_failed_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.TERMINAL_FAILED))
        assert state.status == "pending"

    def test_no_phases_left_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.NO_PHASES_LEFT))
        assert state.status == "pending"

    def test_phase_needs_approval_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.PHASE_NEEDS_APPROVAL))
        assert state.status == "pending"

    def test_phase_needs_gate_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.PHASE_NEEDS_GATE))
        assert state.status == "pending"


# ---------------------------------------------------------------------------
# ExecutingPhaseState
# ---------------------------------------------------------------------------


class TestExecutingPhaseState:
    """state.status == 'running'. The primary state class — owns the
    largest slice of mutation epilogues."""

    def setup_method(self) -> None:
        self.handler = ExecutingPhaseState()

    def _state(self) -> ExecutionState:
        return _state(status="running")

    # ── No-op arms ──────────────────────────────────────────────────────────

    def test_dispatch_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.DISPATCH, step_id="1.1"))
        assert state.status == "running"

    def test_team_dispatch_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.TEAM_DISPATCH, step_id="1.1"))
        assert state.status == "running"

    def test_interact_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.INTERACT, step_id="1.1"))
        assert state.status == "running"

    def test_interact_continue_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.INTERACT_CONTINUE, step_id="1.1"))
        assert state.status == "running"

    def test_wait_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.WAIT))
        assert state.status == "running"

    def test_no_phases_left_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.NO_PHASES_LEFT))
        assert state.status == "running"

    def test_empty_phase_advance_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.EMPTY_PHASE_ADVANCE, phase_id=0))
        assert state.status == "running"

    def test_phase_advance_ok_is_noop(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.PHASE_ADVANCE_OK, phase_id=0))
        assert state.status == "running"

    # ── Gate-pending flips ───────────────────────────────────────────────────

    def test_empty_phase_gate_flips_to_gate_pending(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.EMPTY_PHASE_GATE, phase_id=0))
        assert state.status == "gate_pending"

    def test_phase_needs_gate_flips_to_gate_pending(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.PHASE_NEEDS_GATE, phase_id=0))
        assert state.status == "gate_pending"

    # ── Approval / feedback flips ────────────────────────────────────────────

    def test_phase_needs_approval_is_noop(self) -> None:
        """Slice 2: I1-coupled flip moved to executor's _approval_action.

        ExecutingPhaseState used to flip state.status for
        PHASE_NEEDS_APPROVAL but that left the
        pending_approval_request stamping to a different code path
        (different file, different commit), risking I1 violations on
        a save in between. Slice 2 made the state-class arm a no-op
        and gave _approval_action ownership of the
        transition_to_approval_pending call so the two writes are
        atomic.
        """
        state = self._state()
        # The handler must NOT flip status — that's the engine's job
        # via transition_to_approval_pending in _approval_action.
        self.handler.handle(state, _decision(DecisionKind.PHASE_NEEDS_APPROVAL, phase_id=0))
        assert state.status == "running"

    def test_phase_needs_feedback_flips_to_feedback_pending(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.PHASE_NEEDS_FEEDBACK, phase_id=0))
        assert state.status == "feedback_pending"

    # ── Failure flips ────────────────────────────────────────────────────────

    def test_step_failed_in_phase_flips_to_failed(self) -> None:
        state = self._state()
        self.handler.handle(
            state,
            _decision(
                DecisionKind.STEP_FAILED_IN_PHASE,
                phase_id=0,
                step_id="1.1",
                failed_step_ids=("1.1",),
            ),
        )
        assert state.status == "failed"

    def test_terminal_failed_flips_to_failed(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.TERMINAL_FAILED, fail_count=3))
        assert state.status == "failed"

    def test_timeout_flips_to_failed(self) -> None:
        state = self._state()
        self.handler.handle(state, _decision(DecisionKind.TIMEOUT, step_id="1.1"))
        assert state.status == "failed"

    # ── Illegal kinds raise ──────────────────────────────────────────────────

    def test_approval_pending_raises(self) -> None:
        state = self._state()
        with pytest.raises(RuntimeError, match="ExecutingPhaseState"):
            self.handler.handle(state, _decision(DecisionKind.APPROVAL_PENDING))

    def test_feedback_pending_raises(self) -> None:
        state = self._state()
        with pytest.raises(RuntimeError, match="ExecutingPhaseState"):
            self.handler.handle(state, _decision(DecisionKind.FEEDBACK_PENDING))

    def test_gate_pending_raises(self) -> None:
        state = self._state()
        with pytest.raises(RuntimeError, match="ExecutingPhaseState"):
            self.handler.handle(state, _decision(DecisionKind.GATE_PENDING))

    def test_gate_failed_raises(self) -> None:
        state = self._state()
        with pytest.raises(RuntimeError, match="ExecutingPhaseState"):
            self.handler.handle(state, _decision(DecisionKind.GATE_FAILED))

    def test_paused_takeover_raises(self) -> None:
        state = self._state()
        with pytest.raises(RuntimeError, match="ExecutingPhaseState"):
            self.handler.handle(state, _decision(DecisionKind.PAUSED_TAKEOVER))

    def test_budget_exceeded_raises(self) -> None:
        state = self._state()
        with pytest.raises(RuntimeError, match="ExecutingPhaseState"):
            self.handler.handle(state, _decision(DecisionKind.BUDGET_EXCEEDED))

    def test_terminal_complete_raises(self) -> None:
        state = self._state()
        with pytest.raises(RuntimeError, match="ExecutingPhaseState"):
            self.handler.handle(state, _decision(DecisionKind.TERMINAL_COMPLETE))


# ---------------------------------------------------------------------------
# AwaitingApprovalState
# ---------------------------------------------------------------------------


class TestAwaitingApprovalState:
    """state.status in blocked-on-input cluster. Pass-through for blocked
    kinds; raises on DISPATCH-class; TERMINAL_FAILED flips to 'failed'."""

    def setup_method(self) -> None:
        self.handler = AwaitingApprovalState()

    # ── Pass-through blocked-state kinds ────────────────────────────────────

    def test_approval_pending_is_noop(self) -> None:
        state = _state(status="approval_pending")
        self.handler.handle(state, _decision(DecisionKind.APPROVAL_PENDING, phase_id=0))
        assert state.status == "approval_pending"

    def test_feedback_pending_is_noop(self) -> None:
        state = _state(status="feedback_pending")
        self.handler.handle(state, _decision(DecisionKind.FEEDBACK_PENDING, phase_id=0))
        assert state.status == "feedback_pending"

    def test_gate_pending_is_noop(self) -> None:
        state = _state(status="gate_pending")
        self.handler.handle(state, _decision(DecisionKind.GATE_PENDING, phase_id=0))
        assert state.status == "gate_pending"

    def test_gate_failed_is_noop(self) -> None:
        state = _state(status="gate_failed")
        self.handler.handle(state, _decision(DecisionKind.GATE_FAILED, phase_id=0, fail_count=1))
        assert state.status == "gate_failed"

    def test_paused_takeover_is_noop(self) -> None:
        state = _state(status="paused-takeover")
        self.handler.handle(state, _decision(DecisionKind.PAUSED_TAKEOVER))
        assert state.status == "paused-takeover"

    def test_wait_is_noop(self) -> None:
        state = _state(status="gate_pending")
        self.handler.handle(state, _decision(DecisionKind.WAIT))
        assert state.status == "gate_pending"

    def test_no_phases_left_is_noop(self) -> None:
        state = _state(status="approval_pending")
        self.handler.handle(state, _decision(DecisionKind.NO_PHASES_LEFT))
        assert state.status == "approval_pending"

    # ── Terminal decisions from blocked states ───────────────────────────────

    def test_terminal_failed_flips_to_failed(self) -> None:
        state = _state(status="gate_failed")
        self.handler.handle(state, _decision(DecisionKind.TERMINAL_FAILED, fail_count=3))
        assert state.status == "failed"

    def test_terminal_complete_is_noop(self) -> None:
        state = _state(status="approval_pending")
        self.handler.handle(state, _decision(DecisionKind.TERMINAL_COMPLETE))
        assert state.status == "approval_pending"

    def test_budget_exceeded_is_noop(self) -> None:
        state = _state(status="paused")
        self.handler.handle(state, _decision(DecisionKind.BUDGET_EXCEEDED))
        assert state.status == "paused"

    # ── Cluster-boundary: DISPATCH-class raises ──────────────────────────────

    def test_dispatch_raises(self) -> None:
        state = _state(status="approval_pending")
        with pytest.raises(RuntimeError, match="AwaitingApprovalState"):
            self.handler.handle(state, _decision(DecisionKind.DISPATCH, step_id="1.1"))

    def test_team_dispatch_raises(self) -> None:
        state = _state(status="gate_pending")
        with pytest.raises(RuntimeError, match="AwaitingApprovalState"):
            self.handler.handle(state, _decision(DecisionKind.TEAM_DISPATCH, step_id="1.1"))

    def test_interact_raises(self) -> None:
        state = _state(status="feedback_pending")
        with pytest.raises(RuntimeError, match="AwaitingApprovalState"):
            self.handler.handle(state, _decision(DecisionKind.INTERACT, step_id="1.1"))

    def test_interact_continue_raises(self) -> None:
        state = _state(status="approval_pending")
        with pytest.raises(RuntimeError, match="AwaitingApprovalState"):
            self.handler.handle(state, _decision(DecisionKind.INTERACT_CONTINUE, step_id="1.1"))

    # ── Other unexpected kinds from blocked states ───────────────────────────

    def test_phase_needs_gate_raises(self) -> None:
        state = _state(status="gate_pending")
        with pytest.raises(RuntimeError, match="AwaitingApprovalState"):
            self.handler.handle(state, _decision(DecisionKind.PHASE_NEEDS_GATE))

    def test_step_failed_in_phase_raises(self) -> None:
        state = _state(status="approval_pending")
        with pytest.raises(RuntimeError, match="AwaitingApprovalState"):
            self.handler.handle(
                state,
                _decision(
                    DecisionKind.STEP_FAILED_IN_PHASE,
                    step_id="1.1",
                    failed_step_ids=("1.1",),
                ),
            )


# ---------------------------------------------------------------------------
# TerminalState
# ---------------------------------------------------------------------------


class TestTerminalState:
    """state.status in ('complete', 'failed', 'budget_exceeded').
    Terminal pass-throughs are no-ops; everything else raises."""

    def setup_method(self) -> None:
        self.handler = TerminalState()

    # ── Pass-through terminal kinds ──────────────────────────────────────────

    def test_terminal_complete_is_noop_from_complete(self) -> None:
        state = _state(status="complete")
        self.handler.handle(state, _decision(DecisionKind.TERMINAL_COMPLETE))
        assert state.status == "complete"

    def test_terminal_failed_is_noop_from_failed(self) -> None:
        state = _state(status="failed")
        self.handler.handle(state, _decision(DecisionKind.TERMINAL_FAILED))
        assert state.status == "failed"

    def test_budget_exceeded_is_noop_from_budget_exceeded(self) -> None:
        state = _state(status="budget_exceeded")
        self.handler.handle(state, _decision(DecisionKind.BUDGET_EXCEEDED))
        assert state.status == "budget_exceeded"

    def test_no_phases_left_is_noop_from_complete(self) -> None:
        state = _state(status="complete")
        self.handler.handle(state, _decision(DecisionKind.NO_PHASES_LEFT))
        assert state.status == "complete"

    # ── Cluster-boundary: non-terminal kinds all raise ────────────────────────

    def test_dispatch_raises(self) -> None:
        state = _state(status="complete")
        with pytest.raises(RuntimeError, match="TerminalState"):
            self.handler.handle(state, _decision(DecisionKind.DISPATCH, step_id="1.1"))

    def test_team_dispatch_raises(self) -> None:
        state = _state(status="failed")
        with pytest.raises(RuntimeError, match="TerminalState"):
            self.handler.handle(state, _decision(DecisionKind.TEAM_DISPATCH, step_id="1.1"))

    def test_wait_raises(self) -> None:
        state = _state(status="complete")
        with pytest.raises(RuntimeError, match="TerminalState"):
            self.handler.handle(state, _decision(DecisionKind.WAIT))

    def test_gate_pending_raises(self) -> None:
        state = _state(status="failed")
        with pytest.raises(RuntimeError, match="TerminalState"):
            self.handler.handle(state, _decision(DecisionKind.GATE_PENDING))

    def test_phase_needs_approval_raises(self) -> None:
        state = _state(status="complete")
        with pytest.raises(RuntimeError, match="TerminalState"):
            self.handler.handle(state, _decision(DecisionKind.PHASE_NEEDS_APPROVAL))

    def test_phase_needs_gate_raises(self) -> None:
        state = _state(status="complete")
        with pytest.raises(RuntimeError, match="TerminalState"):
            self.handler.handle(state, _decision(DecisionKind.PHASE_NEEDS_GATE))

    def test_step_failed_in_phase_raises(self) -> None:
        state = _state(status="complete")
        with pytest.raises(RuntimeError, match="TerminalState"):
            self.handler.handle(
                state,
                _decision(
                    DecisionKind.STEP_FAILED_IN_PHASE,
                    step_id="1.1",
                    failed_step_ids=("1.1",),
                ),
            )

    def test_timeout_raises(self) -> None:
        state = _state(status="failed")
        with pytest.raises(RuntimeError, match="TerminalState"):
            self.handler.handle(state, _decision(DecisionKind.TIMEOUT, step_id="1.1"))

    def test_interact_raises(self) -> None:
        state = _state(status="complete")
        with pytest.raises(RuntimeError, match="TerminalState"):
            self.handler.handle(state, _decision(DecisionKind.INTERACT, step_id="1.1"))

    def test_empty_phase_advance_raises(self) -> None:
        state = _state(status="complete")
        with pytest.raises(RuntimeError, match="TerminalState"):
            self.handler.handle(state, _decision(DecisionKind.EMPTY_PHASE_ADVANCE, phase_id=0))

    def test_phase_advance_ok_raises(self) -> None:
        state = _state(status="complete")
        with pytest.raises(RuntimeError, match="TerminalState"):
            self.handler.handle(state, _decision(DecisionKind.PHASE_ADVANCE_OK, phase_id=0))


# ---------------------------------------------------------------------------
# State mutation does not change the decision object (immutability guard)
# ---------------------------------------------------------------------------


class TestDecisionImmutability:
    """Confirm handle() never mutates the ResolverDecision (frozen dataclass)."""

    def test_executing_state_does_not_mutate_decision(self) -> None:
        handler = ExecutingPhaseState()
        state = _state(status="running")
        decision = _decision(DecisionKind.PHASE_NEEDS_GATE, phase_id=0)
        handler.handle(state, decision)
        assert decision.kind == DecisionKind.PHASE_NEEDS_GATE
        assert decision.phase_id == 0

    def test_terminal_state_does_not_mutate_decision(self) -> None:
        handler = TerminalState()
        state = _state(status="complete")
        decision = _decision(DecisionKind.TERMINAL_COMPLETE)
        handler.handle(state, decision)
        assert decision.kind == DecisionKind.TERMINAL_COMPLETE
