"""Unit tests for the I1 transition methods on ``ExecutionState``.

Slice 2 of the migration plan — Hole-1-class structural close.  These tests
verify that the three new transition methods enforce I1 (``status ==
"approval_pending"`` ⇔ ``pending_approval_request is not None``) and refuse
illegal source statuses.

See docs/internal/state-mutation-proposal.md §6 (first slice proof) and
docs/internal/migration-review-summary.md §5 slice 2.
"""
from __future__ import annotations

import pytest

from agent_baton.core.engine.errors import IllegalStateTransition
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PendingApprovalRequest,
    PlanPhase,
    PlanStep,
)


def _state(status: str = "running") -> ExecutionState:
    """Build a minimal ExecutionState in the given status."""
    plan = MachinePlan(
        task_id="t-transitions",
        task_summary="test transitions",
        phases=[
            PlanPhase(
                phase_id=0,
                name="phase",
                steps=[PlanStep(
                    step_id="0.1", agent_name="x", task_description="t",
                )],
            ),
        ],
    )
    state = ExecutionState(
        task_id="t-transitions",
        plan=plan,
        status=status,
    )
    return state


class TestTransitionToApprovalPending:
    """``transition_to_approval_pending`` enforces I1 atomically."""

    def test_flips_status_and_stamps_request_atomically(self) -> None:
        state = _state(status="running")
        state.transition_to_approval_pending(
            phase_id=0, requester="alice", requested_at="2026-05-07T00:00:00+00:00",
        )
        assert state.status == "approval_pending"
        assert state.pending_approval_request is not None
        assert state.pending_approval_request.phase_id == 0
        assert state.pending_approval_request.requester == "alice"
        assert state.pending_approval_request.requested_at == "2026-05-07T00:00:00+00:00"

    def test_auto_stamps_requested_at_when_blank(self) -> None:
        state = _state(status="running")
        state.transition_to_approval_pending(phase_id=0, requester="bob")
        assert state.pending_approval_request is not None
        assert state.pending_approval_request.requested_at != ""

    def test_idempotent_from_approval_pending(self) -> None:
        """Allowed from approval_pending so resume can re-emit cleanly."""
        state = _state(status="approval_pending")
        state.pending_approval_request = PendingApprovalRequest(
            phase_id=0, requester="orig", requested_at="2026-05-07T00:00:00+00:00",
        )
        state.transition_to_approval_pending(phase_id=0, requester="now")
        assert state.status == "approval_pending"
        # Most-recent stamp wins; idempotent-replay logic in the caller
        # owns the "preserve original requester" decision.
        assert state.pending_approval_request.requester == "now"

    def test_allowed_from_gate_pending(self) -> None:
        state = _state(status="gate_pending")
        state.transition_to_approval_pending(phase_id=0, requester="alice")
        assert state.status == "approval_pending"

    def test_rejects_from_failed(self) -> None:
        state = _state(status="failed")
        with pytest.raises(IllegalStateTransition) as exc:
            state.transition_to_approval_pending(phase_id=0, requester="alice")
        assert exc.value.from_status == "failed"
        assert exc.value.to_status == "approval_pending"
        # Side effects must NOT have happened.
        assert state.pending_approval_request is None

    def test_rejects_from_complete(self) -> None:
        state = _state(status="complete")
        with pytest.raises(IllegalStateTransition):
            state.transition_to_approval_pending(phase_id=0, requester="alice")


class TestClearApprovalPending:
    """``clear_approval_pending`` drops the audit row without flipping status."""

    def test_clears_request_only(self) -> None:
        state = _state(status="approval_pending")
        state.pending_approval_request = PendingApprovalRequest(
            phase_id=0, requester="alice",
        )
        state.clear_approval_pending()
        # Status is intentionally unchanged — caller follows up with a
        # transition_to_running / transition_to_failed.
        assert state.status == "approval_pending"
        assert state.pending_approval_request is None

    def test_idempotent_when_request_already_none(self) -> None:
        state = _state(status="approval_pending")
        assert state.pending_approval_request is None
        state.clear_approval_pending()
        assert state.pending_approval_request is None


class TestTransitionToRunning:
    """``transition_to_running`` enforces from_status and clears I1."""

    def test_from_approval_pending_clears_request(self) -> None:
        state = _state(status="approval_pending")
        state.pending_approval_request = PendingApprovalRequest(
            phase_id=0, requester="alice",
        )
        state.transition_to_running(from_status="approval_pending")
        assert state.status == "running"
        # I1 belt-and-suspenders: leaving approval_pending must clear the row.
        assert state.pending_approval_request is None

    def test_from_running_is_allowed_idempotent(self) -> None:
        state = _state(status="running")
        state.transition_to_running(from_status="running")
        assert state.status == "running"

    def test_from_gate_pending(self) -> None:
        state = _state(status="gate_pending")
        state.transition_to_running(from_status="gate_pending")
        assert state.status == "running"

    def test_from_budget_exceeded(self) -> None:
        state = _state(status="budget_exceeded")
        state.transition_to_running(from_status="budget_exceeded")
        assert state.status == "running"

    def test_rejects_when_actual_status_does_not_match(self) -> None:
        state = _state(status="running")
        with pytest.raises(IllegalStateTransition) as exc:
            state.transition_to_running(from_status="approval_pending")
        assert exc.value.from_status == "running"
        assert exc.value.to_status == "running"
        # Status unchanged.
        assert state.status == "running"

    def test_rejects_from_terminal(self) -> None:
        state = _state(status="failed")
        with pytest.raises(IllegalStateTransition):
            state.transition_to_running(from_status="failed")  # type: ignore[arg-type]


class TestI1InvariantHolds:
    """End-to-end: every transition path leaves I1 satisfied."""

    @staticmethod
    def _i1_holds(state: ExecutionState) -> bool:
        is_pending = state.status == "approval_pending"
        has_row = state.pending_approval_request is not None
        return is_pending == has_row

    def test_running_to_approval_pending_to_running(self) -> None:
        state = _state(status="running")
        assert self._i1_holds(state)
        state.transition_to_approval_pending(phase_id=0, requester="alice")
        assert self._i1_holds(state)
        state.transition_to_running(from_status="approval_pending")
        assert self._i1_holds(state)

    def test_clear_then_transition_running(self) -> None:
        """Approve-with-feedback shape: clear+transition leaves I1 intact."""
        state = _state(status="approval_pending")
        state.pending_approval_request = PendingApprovalRequest(
            phase_id=0, requester="alice",
        )
        assert self._i1_holds(state)
        # Note: clear_approval_pending intentionally violates I1 mid-call,
        # but the caller is expected to immediately follow with a
        # transition out of approval_pending. The end-state holds.
        state.clear_approval_pending()
        # In-flight: I1 is now violated (status=approval_pending, request=None).
        # This is the window the slice-3 record_approval_result fix avoids
        # by rearranging the call order so the save+amend happens BEFORE
        # the clear+transition rather than after.
        assert not self._i1_holds(state)
        state.transition_to_running(from_status="approval_pending")
        assert self._i1_holds(state)
