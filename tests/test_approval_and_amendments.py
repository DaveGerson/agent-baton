"""Tests for HITL approval gates and plan amendment features.

Covers:
- TestApprovalGates  — end-to-end approval flow (approve, reject,
  approve-with-feedback, persistence, resume)
- TestPlanAmendments — amend_plan mutations (insert phases, add steps,
  renumbering, audit trail, multi-amendment accumulation)
- TestSerializationCompat — ExecutionState roundtrip and backward compat
  with old state files that pre-date the new fields
- TestApproveWithFeedbackRaceRegression — regression for the reload race fixed
  in Hole 5: _load_execution returning None after amend must raise
  ExecutionStateInconsistency rather than silently dropping the amendment.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from unittest.mock import patch

from agent_baton.models.execution import (
    ActionType,
    ApprovalResult,
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanAmendment,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)
from agent_baton.core.engine.errors import ExecutionStateInconsistency
from agent_baton.core.engine.executor import ExecutionEngine


# ---------------------------------------------------------------------------
# Shared factory helpers  (mirror the style in test_executor.py)
# ---------------------------------------------------------------------------

def _step(
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task: str = "Implement feature X",
    model: str = "sonnet",
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task,
        model=model,
    )


def _gate(gate_type: str = "test", command: str = "pytest") -> PlanGate:
    return PlanGate(gate_type=gate_type, command=command)


def _phase(
    phase_id: int = 0,
    name: str = "Implementation",
    steps: list[PlanStep] | None = None,
    gate: PlanGate | None = None,
    approval_required: bool = False,
    approval_description: str = "",
) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=name,
        steps=steps or [_step()],
        gate=gate,
        approval_required=approval_required,
        approval_description=approval_description,
    )


def _plan(
    task_id: str = "task-001",
    phases: list[PlanPhase] | None = None,
) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Build a thing",
        phases=phases if phases is not None else [_phase()],
    )


def _engine(tmp_path: Path) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path)


# ---------------------------------------------------------------------------
# Convenience: drive an engine to the point where all steps in the first
# phase are complete (approval not yet recorded).
# ---------------------------------------------------------------------------

def _reach_approval(tmp_path: Path, *, phase_kw: dict | None = None) -> ExecutionEngine:
    """Start an engine on a single-phase plan and complete its step.

    The phase has ``approval_required=True``.  Returns the engine positioned
    just before the APPROVAL action is consumed.
    """
    kw = dict(phase_kw or {})
    kw.setdefault("approval_required", True)
    engine = _engine(tmp_path)
    engine.start(_plan(phases=[_phase(**kw)]))
    engine.record_step_result("1.1", "backend-engineer")
    return engine


# ===========================================================================
# TestApprovalGates
# ===========================================================================

class TestApprovalGates:
    # ------------------------------------------------------------------ #
    # 1. Phase with approval_required=True returns APPROVAL after steps   #
    # ------------------------------------------------------------------ #

    def test_approval_required_returns_approval_action(self, tmp_path: Path) -> None:
        engine = _reach_approval(tmp_path)
        action = engine.next_action()
        assert action.action_type == ActionType.APPROVAL

    # ------------------------------------------------------------------ #
    # 2. Phase without approval_required skips straight to gate           #
    # ------------------------------------------------------------------ #

    def test_no_approval_required_skips_to_gate(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[_phase(steps=[_step("1.1")], gate=_gate(), approval_required=False)]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

    # ------------------------------------------------------------------ #
    # 3. APPROVAL action carries context and options                       #
    # ------------------------------------------------------------------ #

    def test_approval_action_carries_context(self, tmp_path: Path) -> None:
        engine = _reach_approval(
            tmp_path,
            phase_kw={"approval_description": "Review the output carefully"},
        )
        action = engine.next_action()
        assert action.action_type == ActionType.APPROVAL
        assert action.approval_context == "Review the output carefully"

    def test_approval_action_carries_options(self, tmp_path: Path) -> None:
        engine = _reach_approval(tmp_path)
        action = engine.next_action()
        assert set(action.approval_options) == {
            "approve", "reject", "approve-with-feedback"
        }

    def test_approval_action_carries_phase_id(self, tmp_path: Path) -> None:
        engine = _reach_approval(tmp_path, phase_kw={"phase_id": 0})
        action = engine.next_action()
        assert action.phase_id == 0

    # ------------------------------------------------------------------ #
    # 4. "approve" → continue to gate when gate exists                    #
    # ------------------------------------------------------------------ #

    def test_approve_continues_to_gate(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[
                _phase(
                    steps=[_step("1.1")],
                    gate=_gate("test", "pytest"),
                    approval_required=True,
                )
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # consumes APPROVAL, sets status=approval_pending
        engine.record_approval_result(phase_id=0, result="approve")
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

    # ------------------------------------------------------------------ #
    # 5. "approve" with no gate → dispatch next phase                     #
    # ------------------------------------------------------------------ #

    def test_approve_continues_to_next_phase_no_gate(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[
                _phase(phase_id=0, steps=[_step("1.1")], approval_required=True),
                _phase(phase_id=1, name="Phase 2", steps=[_step("2.1", agent_name="architect")]),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # APPROVAL
        engine.record_approval_result(phase_id=0, result="approve")
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "2.1"

    # ------------------------------------------------------------------ #
    # 6. "reject" → status=failed                                         #
    # ------------------------------------------------------------------ #

    def test_reject_fails_execution(self, tmp_path: Path) -> None:
        engine = _reach_approval(tmp_path)
        engine.next_action()  # APPROVAL
        engine.record_approval_result(phase_id=0, result="reject")
        assert engine._load_state().status == "failed"

    def test_reject_next_action_is_failed(self, tmp_path: Path) -> None:
        engine = _reach_approval(tmp_path)
        engine.next_action()  # APPROVAL
        engine.record_approval_result(phase_id=0, result="reject")
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    # ------------------------------------------------------------------ #
    # 7. "approve-with-feedback" inserts remediation phase                 #
    #    NOTE: this test exposes a bug — see module docstring.             #
    # ------------------------------------------------------------------ #

    def test_approve_with_feedback_amends_plan(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[
                _phase(phase_id=0, steps=[_step("1.1")], approval_required=True),
                _phase(phase_id=1, name="Phase 2", steps=[_step("2.1", agent_name="architect")]),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # APPROVAL
        engine.record_approval_result(
            phase_id=0, result="approve-with-feedback", feedback="Add error handling"
        )
        state = engine._load_state()
        # The plan should now have 3 phases: original 2 + 1 remediation phase.
        assert len(state.plan.phases) == 3
        # A remediation phase must have been inserted at position 1 (after phase 0).
        remediation = state.plan.phases[1]
        assert remediation.name == "Remediation"
        # The amendment must be durably recorded.
        assert len(state.amendments) == 1
        assert state.amendments[0].trigger == "approval_feedback"

    # ------------------------------------------------------------------ #
    # 8. Status is "approval_pending" on disk                             #
    # ------------------------------------------------------------------ #

    def test_approval_pending_persists_on_disk(self, tmp_path: Path) -> None:
        engine = _reach_approval(tmp_path)
        engine.next_action()  # triggers APPROVAL and saves status=approval_pending
        data = json.loads((tmp_path / "execution-state.json").read_text())
        assert data["status"] == "approval_pending"

    # ------------------------------------------------------------------ #
    # 9. Resume with approval_pending re-emits APPROVAL                   #
    # ------------------------------------------------------------------ #

    def test_resume_with_approval_pending(self, tmp_path: Path) -> None:
        engine = _reach_approval(tmp_path)
        engine.next_action()  # APPROVAL — state is now approval_pending on disk

        # Simulate crash+restart: fresh engine instance, same state dir.
        engine2 = _engine(tmp_path)
        action = engine2.resume()
        assert action.action_type == ActionType.APPROVAL

    def test_resume_approval_action_carries_context(self, tmp_path: Path) -> None:
        engine = _reach_approval(
            tmp_path,
            phase_kw={"approval_description": "Resumable review"},
        )
        engine.next_action()  # APPROVAL

        engine2 = _engine(tmp_path)
        action = engine2.resume()
        assert action.approval_context == "Resumable review"

    # ------------------------------------------------------------------ #
    # 10. Approval result stored in state.approval_results                #
    # ------------------------------------------------------------------ #

    def test_approval_result_in_state(self, tmp_path: Path) -> None:
        engine = _reach_approval(tmp_path)
        engine.next_action()  # APPROVAL
        engine.record_approval_result(phase_id=0, result="approve", feedback="")
        state = engine._load_state()
        assert len(state.approval_results) == 1
        ar = state.approval_results[0]
        assert ar.phase_id == 0
        assert ar.result == "approve"

    def test_approval_result_feedback_stored(self, tmp_path: Path) -> None:
        engine = _reach_approval(tmp_path)
        engine.next_action()  # APPROVAL
        engine.record_approval_result(
            phase_id=0, result="approve-with-feedback", feedback="Some concerns"
        )
        state = engine._load_state()
        assert state.approval_results[0].feedback == "Some concerns"

    # ------------------------------------------------------------------ #
    # Edge: invalid approval result raises ValueError                     #
    # ------------------------------------------------------------------ #

    def test_invalid_approval_result_raises(self, tmp_path: Path) -> None:
        engine = _reach_approval(tmp_path)
        engine.next_action()  # APPROVAL
        with pytest.raises(ValueError, match="Invalid approval result"):
            engine.record_approval_result(phase_id=0, result="maybe")

    # ------------------------------------------------------------------ #
    # Edge: record_approval_result without active state raises RuntimeError
    # ------------------------------------------------------------------ #

    def test_record_approval_without_state_raises(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with pytest.raises(RuntimeError):
            engine.record_approval_result(phase_id=0, result="approve")

    # ====================================================================== #
    # Hole 1 validation tests — record_approval_result must enforce that:    #
    #   (a) status is approval_pending                                       #
    #   (b) phase_id matches the current phase                               #
    #   (c) phase actually requested approval                                #
    #   (d) team-mode self-approval is rejected                              #
    # ====================================================================== #

    def test_reject_when_status_not_approval_pending(
        self, tmp_path: Path
    ) -> None:
        """A stray approval recorded mid-execution must be rejected.

        Without this guard, the approval would re-flip status to "running"
        and silently mask whatever phase the engine was actually in.
        """
        from agent_baton.core.engine.errors import InvalidApprovalState

        plan = _plan(
            phases=[
                _phase(phase_id=0, steps=[_step("1.1")], approval_required=True)
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        # Engine status is "running" — no approval has been requested yet.
        with pytest.raises(InvalidApprovalState) as exc_info:
            engine.record_approval_result(phase_id=0, result="approve")
        assert exc_info.value.reason == InvalidApprovalState.REASON_NOT_PENDING
        assert exc_info.value.current_status == "running"

    def test_reject_phase_id_mismatch(self, tmp_path: Path) -> None:
        """Approving phase_id=99 when phase 0 is the current must be rejected."""
        from agent_baton.core.engine.errors import InvalidApprovalState

        engine = _reach_approval(tmp_path)
        engine.next_action()  # APPROVAL — status becomes approval_pending
        with pytest.raises(InvalidApprovalState) as exc_info:
            engine.record_approval_result(phase_id=99, result="approve")
        assert exc_info.value.reason == InvalidApprovalState.REASON_PHASE_MISMATCH

    def test_reject_when_phase_did_not_request_approval(
        self, tmp_path: Path
    ) -> None:
        """A phase without approval_required cannot have an approval recorded.

        We synthesize this by flipping status to approval_pending on disk
        without setting approval_required on the phase — simulating a
        future code path that incorrectly transitions into approval_pending.
        """
        from agent_baton.core.engine.errors import InvalidApprovalState

        plan = _plan(
            phases=[
                _phase(phase_id=0, steps=[_step("1.1")], approval_required=False)
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        # Hack the persisted status into approval_pending without setting
        # approval_required on the phase.
        state = engine._load_state()
        state.status = "approval_pending"
        engine._save_execution(state)
        with pytest.raises(InvalidApprovalState) as exc_info:
            engine.record_approval_result(phase_id=0, result="approve")
        assert exc_info.value.reason == (
            InvalidApprovalState.REASON_NO_APPROVAL_REQUESTED
        )

    def test_team_mode_rejects_self_approval(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In BATON_APPROVAL_MODE=team the requester cannot self-approve."""
        from agent_baton.core.engine.errors import InvalidApprovalState

        monkeypatch.setenv("BATON_APPROVAL_MODE", "team")
        # Pin the actor identity so requester == approver in this test.
        monkeypatch.setenv("USER", "alice")
        monkeypatch.setenv("USERNAME", "alice")

        engine = _reach_approval(tmp_path)
        engine.next_action()  # APPROVAL — stamps requester
        # Verify the requester was captured.
        state = engine._load_state()
        assert state.pending_approval_request is not None
        assert state.pending_approval_request.requester.startswith("alice")
        # Now try to self-approve as the same actor.
        with pytest.raises(InvalidApprovalState) as exc_info:
            engine.record_approval_result(
                phase_id=0,
                result="approve",
                actor=state.pending_approval_request.requester,
            )
        assert exc_info.value.reason == InvalidApprovalState.REASON_SELF_APPROVAL

    def test_team_mode_accepts_different_actor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In team mode a different actor than the requester may approve."""
        monkeypatch.setenv("BATON_APPROVAL_MODE", "team")
        monkeypatch.setenv("USER", "alice")
        monkeypatch.setenv("USERNAME", "alice")

        engine = _reach_approval(tmp_path)
        engine.next_action()  # APPROVAL — requester captured as alice@...
        # Different actor approves.
        engine.record_approval_result(
            phase_id=0,
            result="approve",
            actor="bob@somewhere",
        )
        state = engine._load_state()
        assert state.status == "running"
        assert len(state.approval_results) == 1
        assert state.approval_results[0].actor == "bob@somewhere"
        # Pending request audit row must be cleared after a successful record.
        assert state.pending_approval_request is None

    def test_local_mode_self_approval_allowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In local mode the requester may self-approve (default behavior)."""
        # Default approval mode is "local"; assert it explicitly for clarity.
        monkeypatch.delenv("BATON_APPROVAL_MODE", raising=False)
        monkeypatch.setenv("USER", "alice")
        monkeypatch.setenv("USERNAME", "alice")

        engine = _reach_approval(tmp_path)
        engine.next_action()  # APPROVAL
        state = engine._load_state()
        requester = state.pending_approval_request.requester
        # Same actor records the approval — must succeed in local mode.
        engine.record_approval_result(
            phase_id=0, result="approve", actor=requester
        )
        state = engine._load_state()
        assert state.status == "running"
        assert state.pending_approval_request is None

    def test_pending_approval_request_persists_to_disk(
        self, tmp_path: Path
    ) -> None:
        """The pending-approval audit row must roundtrip through save/load."""
        engine = _reach_approval(tmp_path)
        engine.next_action()  # APPROVAL — stamps the request
        # Reload state from disk via a fresh engine.
        engine2 = _engine(tmp_path)
        action = engine2.resume()
        assert action.action_type == ActionType.APPROVAL
        state = engine2._load_state()
        assert state.pending_approval_request is not None
        assert state.pending_approval_request.phase_id == 0
        assert state.pending_approval_request.requester

    # ------------------------------------------------------------------ #
    # Context auto-generated when approval_description is empty           #
    # ------------------------------------------------------------------ #

    def test_approval_context_auto_generated_from_step_results(
        self, tmp_path: Path
    ) -> None:
        """When approval_description is empty, context is built from step results."""
        engine = _reach_approval(
            tmp_path,
            phase_kw={
                "approval_required": True,
                "approval_description": "",
                "steps": [_step("1.1")],
            },
        )
        engine.record_step_result("1.1", "backend-engineer", outcome="Feature done")
        action = engine.next_action()
        # Auto-generated context should mention the phase name and step.
        assert action.action_type == ActionType.APPROVAL
        assert action.approval_context  # non-empty


# ===========================================================================
# TestPlanAmendments
# ===========================================================================

class TestPlanAmendments:
    # ------------------------------------------------------------------ #
    # 1. New phase inserted after current phase by default                #
    # ------------------------------------------------------------------ #

    def test_amend_adds_phase_after_current(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[
                _phase(phase_id=0, name="Phase 0", steps=[_step("1.1")]),
                _phase(phase_id=1, name="Phase 1", steps=[_step("2.1", agent_name="architect")]),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)  # current_phase index = 0

        new_phase = PlanPhase(
            phase_id=99,
            name="Inserted",
            steps=[_step("99.1", agent_name="test-engineer")],
        )
        engine.amend_plan(description="Insert after current", new_phases=[new_phase])

        state = engine._load_state()
        # After renumber: Phase0=1, Inserted=2, Phase1=3
        assert len(state.plan.phases) == 3
        # Inserted must be at index 1 (between old phase 0 and phase 1).
        assert state.plan.phases[1].name == "Inserted"

    # ------------------------------------------------------------------ #
    # 2. insert_after_phase=N inserts at the correct position             #
    # ------------------------------------------------------------------ #

    def test_amend_adds_phase_at_specific_position(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[
                _phase(phase_id=0, name="P0", steps=[_step("1.1")]),
                _phase(phase_id=1, name="P1", steps=[_step("2.1", agent_name="b")]),
                _phase(phase_id=2, name="P2", steps=[_step("3.1", agent_name="c")]),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)

        new_phase = PlanPhase(
            phase_id=99,
            name="Between P1 and P2",
            steps=[_step("99.1", agent_name="d")],
        )
        engine.amend_plan(
            description="Insert after P1",
            new_phases=[new_phase],
            insert_after_phase=1,  # match by phase_id=1
        )

        state = engine._load_state()
        # Expected order: P0, P1, Between P1 and P2, P2
        names = [p.name for p in state.plan.phases]
        assert names == ["P0", "P1", "Between P1 and P2", "P2"]

    # ------------------------------------------------------------------ #
    # 3. add_steps_to_phase appends steps to an existing phase             #
    # ------------------------------------------------------------------ #

    def test_amend_adds_steps_to_existing_phase(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)

        extra_step = _step("1.2", agent_name="test-engineer", task="Run tests")
        engine.amend_plan(
            description="Add test step",
            new_steps=[extra_step],
            add_steps_to_phase=0,
        )

        state = engine._load_state()
        assert len(state.plan.phases[0].steps) == 2
        step_ids = [s.step_id for s in state.plan.phases[0].steps]
        assert "1.2" in step_ids

    # ------------------------------------------------------------------ #
    # 4. phase_ids are sequential (1-based) after insertion                #
    # ------------------------------------------------------------------ #

    def test_amend_renumbers_phases(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[
                _phase(phase_id=0, name="P0", steps=[_step("1.1")]),
                _phase(phase_id=1, name="P1", steps=[_step("2.1", agent_name="b")]),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)

        new_phase = PlanPhase(
            phase_id=99,
            name="Inserted",
            steps=[_step("99.1", agent_name="c")],
        )
        engine.amend_plan(description="Insert", new_phases=[new_phase])

        state = engine._load_state()
        phase_ids = [p.phase_id for p in state.plan.phases]
        assert phase_ids == list(range(1, len(state.plan.phases) + 1))

    def test_amend_renumbers_step_ids(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[
                _phase(phase_id=0, name="P0", steps=[_step("1.1")]),
                _phase(phase_id=1, name="P1", steps=[_step("2.1", agent_name="b")]),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)

        new_phase = PlanPhase(
            phase_id=99,
            name="Inserted",
            steps=[_step("99.1", agent_name="c")],
        )
        engine.amend_plan(description="Insert", new_phases=[new_phase])

        state = engine._load_state()
        # After renumber: phase_ids 1,2,3 → step_ids "1.1","2.1","3.1"
        for phase in state.plan.phases:
            for step in phase.steps:
                prefix = str(phase.phase_id)
                assert step.step_id.startswith(prefix + ".")

    # ------------------------------------------------------------------ #
    # 5. Amendment audit trail recorded in state.amendments               #
    # ------------------------------------------------------------------ #

    def test_amendment_audit_trail(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)

        new_phase = PlanPhase(
            phase_id=99,
            name="Extra",
            steps=[_step("99.1", agent_name="test-engineer")],
        )
        amendment = engine.amend_plan(
            description="Add extra phase",
            new_phases=[new_phase],
            trigger="manual",
            trigger_phase_id=0,
            feedback="Requested by reviewer",
        )

        state = engine._load_state()
        assert len(state.amendments) == 1
        saved = state.amendments[0]
        assert saved.amendment_id == amendment.amendment_id
        assert saved.trigger == "manual"
        assert saved.trigger_phase_id == 0
        assert saved.description == "Add extra phase"
        assert saved.feedback == "Requested by reviewer"

    def test_amendment_records_phases_added(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)

        new_phase = PlanPhase(phase_id=99, name="Extra",
                              steps=[_step("99.1", agent_name="c")])
        amendment = engine.amend_plan(description="Add", new_phases=[new_phase])

        # phases_added contains the *original* phase_id of the inserted phase
        # (before renumbering).
        assert len(amendment.phases_added) == 1

    # ------------------------------------------------------------------ #
    # 6. Engine picks up new work after amendment                         #
    # ------------------------------------------------------------------ #

    def test_execution_continues_after_amendment(self, tmp_path: Path) -> None:
        """After all original steps are done, amending adds dispatchable work."""
        plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # COMPLETE (no more phases at this point)

        # Now amend to add a new phase.
        new_phase = PlanPhase(
            phase_id=99,
            name="Extra Work",
            steps=[_step("99.1", agent_name="test-engineer", task="Write tests")],
        )
        engine.amend_plan(description="Add extra work", new_phases=[new_phase])

        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH

    # ------------------------------------------------------------------ #
    # 7. approve-with-feedback creates remediation phase with correct agent
    #    NOTE: this test exposes a known bug — see module docstring.       #
    # ------------------------------------------------------------------ #

    def test_amend_from_approval_feedback(self, tmp_path: Path) -> None:
        """approve-with-feedback triggers _amend_from_feedback on the right agent."""
        plan = _plan(
            phases=[
                _phase(
                    phase_id=0,
                    steps=[_step("1.1", agent_name="backend-engineer")],
                    approval_required=True,
                ),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # APPROVAL
        engine.record_approval_result(
            phase_id=0,
            result="approve-with-feedback",
            feedback="Fix the edge cases",
        )

        state = engine._load_state()
        # A remediation phase must be inserted.
        assert len(state.plan.phases) == 2
        remediation = state.plan.phases[1]
        assert remediation.name == "Remediation"
        # The remediation step should reference the feedback.
        assert "Fix the edge cases" in remediation.steps[0].task_description
        # The remediation agent should match the phase's first step agent.
        assert remediation.steps[0].agent_name == "backend-engineer"
        # The amendment must be durably persisted.
        assert len(state.amendments) == 1
        assert state.amendments[0].trigger == "approval_feedback"

    # ------------------------------------------------------------------ #
    # 8. Multiple amendments accumulate correctly                         #
    # ------------------------------------------------------------------ #

    def test_multiple_amendments(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)

        np1 = PlanPhase(phase_id=98, name="Extra1", steps=[_step("98.1", agent_name="b")])
        np2 = PlanPhase(phase_id=99, name="Extra2", steps=[_step("99.1", agent_name="c")])

        engine.amend_plan(description="First amendment", new_phases=[np1])
        engine.amend_plan(description="Second amendment", new_phases=[np2])

        state = engine._load_state()
        assert len(state.amendments) == 2
        assert state.amendments[0].amendment_id == "amend-1"
        assert state.amendments[1].amendment_id == "amend-2"

    def test_multiple_amendments_phase_count(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)

        np1 = PlanPhase(phase_id=98, name="Extra1", steps=[_step("98.1", agent_name="b")])
        np2 = PlanPhase(phase_id=99, name="Extra2", steps=[_step("99.1", agent_name="c")])

        engine.amend_plan(description="First", new_phases=[np1])
        engine.amend_plan(description="Second", new_phases=[np2])

        state = engine._load_state()
        assert len(state.plan.phases) == 3

    # ------------------------------------------------------------------ #
    # Edge: amend_plan without active state raises RuntimeError           #
    # ------------------------------------------------------------------ #

    def test_amend_plan_without_state_raises(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with pytest.raises(RuntimeError, match="amend_plan"):
            engine.amend_plan(description="No state")


# ===========================================================================
# TestSerializationCompat
# ===========================================================================

class TestSerializationCompat:
    # ------------------------------------------------------------------ #
    # 1. ExecutionState with approval_results roundtrips cleanly          #
    # ------------------------------------------------------------------ #

    def test_state_with_approvals_roundtrip(self, tmp_path: Path) -> None:
        state = ExecutionState(
            task_id="t1",
            plan=_plan(task_id="t1", phases=[]),
        )
        state.approval_results.append(
            ApprovalResult(phase_id=2, result="approve-with-feedback", feedback="ok")
        )

        d = state.to_dict()
        restored = ExecutionState.from_dict(d)

        assert len(restored.approval_results) == 1
        ar = restored.approval_results[0]
        assert ar.phase_id == 2
        assert ar.result == "approve-with-feedback"
        assert ar.feedback == "ok"

    def test_approval_result_decided_at_preserved(self, tmp_path: Path) -> None:
        state = ExecutionState(
            task_id="t1",
            plan=_plan(task_id="t1", phases=[]),
        )
        ar = ApprovalResult(phase_id=0, result="approve")
        state.approval_results.append(ar)
        original_ts = ar.decided_at

        restored = ExecutionState.from_dict(state.to_dict())
        assert restored.approval_results[0].decided_at == original_ts

    # ------------------------------------------------------------------ #
    # 2. ExecutionState with amendments roundtrips cleanly                #
    # ------------------------------------------------------------------ #

    def test_state_with_amendments_roundtrip(self, tmp_path: Path) -> None:
        state = ExecutionState(
            task_id="t1",
            plan=_plan(task_id="t1", phases=[]),
        )
        state.amendments.append(
            PlanAmendment(
                amendment_id="amend-1",
                trigger="manual",
                trigger_phase_id=3,
                description="Added extra tests",
                phases_added=[4, 5],
                steps_added=["4.1"],
                feedback="Reviewer asked for more coverage",
            )
        )

        d = state.to_dict()
        restored = ExecutionState.from_dict(d)

        assert len(restored.amendments) == 1
        am = restored.amendments[0]
        assert am.amendment_id == "amend-1"
        assert am.trigger == "manual"
        assert am.trigger_phase_id == 3
        assert am.phases_added == [4, 5]
        assert am.steps_added == ["4.1"]
        assert am.feedback == "Reviewer asked for more coverage"

    # ------------------------------------------------------------------ #
    # 3. Old state dict without new fields loads with empty defaults      #
    # ------------------------------------------------------------------ #

    def test_old_state_without_new_fields_loads(self, tmp_path: Path) -> None:
        old_dict: dict = {
            "task_id": "old-task",
            "plan": {
                "task_id": "old-task",
                "task_summary": "Legacy plan",
                "phases": [
                    {
                        "phase_id": 0,
                        "name": "Work",
                        "steps": [
                            {
                                "step_id": "1.1",
                                "agent_name": "backend-engineer",
                                "task_description": "Do it",
                            }
                        ],
                        # No approval_required / approval_description fields.
                    }
                ],
            },
            "current_phase": 0,
            "current_step_index": 0,
            "status": "running",
            "step_results": [],
            "gate_results": [],
            # Deliberately absent: "approval_results", "amendments"
        }

        state = ExecutionState.from_dict(old_dict)

        assert state.approval_results == []
        assert state.amendments == []
        # The phase must have sensible defaults for the new fields.
        assert state.plan.phases[0].approval_required is False
        assert state.plan.phases[0].approval_description == ""

    def test_old_state_without_new_fields_is_operational(self, tmp_path: Path) -> None:
        """An old state loaded from disk must still drive the engine normally."""
        old_dict: dict = {
            "task_id": "old-task",
            "plan": {
                "task_id": "old-task",
                "task_summary": "Legacy plan",
                "phases": [
                    {
                        "phase_id": 0,
                        "name": "Work",
                        "steps": [
                            {
                                "step_id": "1.1",
                                "agent_name": "backend-engineer",
                                "task_description": "Do it",
                            }
                        ],
                    }
                ],
            },
            "current_phase": 0,
            "current_step_index": 0,
            "status": "running",
            "step_results": [],
            "gate_results": [],
        }
        # Write to disk and let a fresh engine load it via resume().
        state_path = tmp_path / "execution-state.json"
        state_path.write_text(json.dumps(old_dict), encoding="utf-8")

        engine = _engine(tmp_path)
        action = engine.resume()
        assert action.action_type == ActionType.DISPATCH

    # ------------------------------------------------------------------ #
    # 4. PlanPhase with approval fields roundtrips through to_dict        #
    # ------------------------------------------------------------------ #

    def test_plan_phase_approval_fields_serialized(self) -> None:
        phase = PlanPhase(
            phase_id=1,
            name="Review Phase",
            steps=[_step()],
            approval_required=True,
            approval_description="Check the output",
        )
        d = phase.to_dict()
        assert d["approval_required"] is True
        assert d["approval_description"] == "Check the output"

    def test_plan_phase_approval_fields_deserialized(self) -> None:
        data = {
            "phase_id": 1,
            "name": "Review Phase",
            "steps": [
                {
                    "step_id": "1.1",
                    "agent_name": "backend-engineer",
                    "task_description": "Do it",
                }
            ],
            "approval_required": True,
            "approval_description": "Check the output",
        }
        phase = PlanPhase.from_dict(data)
        assert phase.approval_required is True
        assert phase.approval_description == "Check the output"

    def test_plan_phase_no_approval_fields_omitted_from_dict(self) -> None:
        """approval_required=False phases don't clutter the serialized form."""
        phase = PlanPhase(phase_id=1, name="Plain", steps=[_step()])
        d = phase.to_dict()
        # Per the to_dict implementation, these keys are only written when True.
        assert "approval_required" not in d
        assert "approval_description" not in d

    # ------------------------------------------------------------------ #
    # 5. ApprovalResult roundtrips through to_dict / from_dict            #
    # ------------------------------------------------------------------ #

    @pytest.mark.parametrize("result", ["approve", "reject", "approve-with-feedback"])
    def test_approval_result_all_variants_roundtrip(self, result: str) -> None:
        ar = ApprovalResult(phase_id=5, result=result, feedback="note")
        restored = ApprovalResult.from_dict(ar.to_dict())
        assert restored.phase_id == 5
        assert restored.result == result
        assert restored.feedback == "note"

    # ------------------------------------------------------------------ #
    # 6. PlanAmendment roundtrips through to_dict / from_dict             #
    # ------------------------------------------------------------------ #

    def test_plan_amendment_roundtrip(self) -> None:
        am = PlanAmendment(
            amendment_id="amend-7",
            trigger="gate_feedback",
            trigger_phase_id=2,
            description="Rewrite tests",
            phases_added=[3],
            steps_added=["3.1", "3.2"],
            feedback="Tests were broken",
        )
        restored = PlanAmendment.from_dict(am.to_dict())
        assert restored.amendment_id == "amend-7"
        assert restored.trigger == "gate_feedback"
        assert restored.phases_added == [3]
        assert restored.steps_added == ["3.1", "3.2"]


# ===========================================================================
# TestApproveWithFeedbackRaceRegression
# Regression tests for Hole 5: the reload-or-fall-back race in
# record_approval_result / approve-with-feedback.
# ===========================================================================

class TestApproveWithFeedbackRaceRegression:
    """Regression for: _load_execution returning None after amend must raise
    ExecutionStateInconsistency, not silently fall back to the pre-amendment
    in-memory state.
    """

    def test_load_failure_after_amend_raises_inconsistency_error(
        self, tmp_path: Path
    ) -> None:
        """Mocking _load_execution to return None after _amend_from_feedback must
        surface ExecutionStateInconsistency — never silently proceed with the
        pre-amendment state.

        Call sequence inside record_approval_result for approve-with-feedback:
          call 1: _require_execution (start of record_approval_result) — must succeed
          call 2: amend_plan -> _require_execution (inside _amend_from_feedback) — must succeed
          call 3: explicit reload after _amend_from_feedback returns — must fail (None)
                  → this is the call that exercises the Hole-5 fix
        """
        plan = _plan(
            phases=[
                _phase(
                    phase_id=0,
                    steps=[_step("1.1", agent_name="backend-engineer")],
                    approval_required=True,
                ),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # APPROVAL

        # After _amend_from_feedback writes to disk, the reload returns None
        # (simulates storage corruption / backend answering stale data).
        original_load = engine._load_execution

        call_count = {"n": 0}

        def _failing_load() -> ExecutionState | None:
            call_count["n"] += 1
            # Allow the first two calls (initial load and amend_plan's load)
            # to succeed.  The third call is the post-amendment reload that
            # Hole 5 guards — it should return None.
            if call_count["n"] <= 2:
                return original_load()
            return None

        with patch.object(engine, "_load_execution", side_effect=_failing_load):
            with pytest.raises(ExecutionStateInconsistency) as exc_info:
                engine.record_approval_result(
                    phase_id=0,
                    result="approve-with-feedback",
                    feedback="Please add error handling",
                )

        assert exc_info.value.task_id == "task-001"
        assert "approve-with-feedback" in exc_info.value.context

    def test_load_failure_error_carries_task_id(self, tmp_path: Path) -> None:
        """ExecutionStateInconsistency must expose the task_id for diagnostics."""
        plan = _plan(
            task_id="diag-task-99",
            phases=[
                _phase(
                    phase_id=0,
                    steps=[_step("1.1")],
                    approval_required=True,
                ),
            ],
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # APPROVAL

        original_load = engine._load_execution
        call_count = {"n": 0}

        def _failing_load() -> ExecutionState | None:
            call_count["n"] += 1
            if call_count["n"] <= 2:
                return original_load()
            return None

        with patch.object(engine, "_load_execution", side_effect=_failing_load):
            with pytest.raises(ExecutionStateInconsistency) as exc_info:
                engine.record_approval_result(
                    phase_id=0,
                    result="approve-with-feedback",
                    feedback="Needs review",
                )

        assert exc_info.value.task_id == "diag-task-99"

    def test_successful_reload_does_not_raise(self, tmp_path: Path) -> None:
        """When _load_execution succeeds (normal path), no exception must be raised."""
        plan = _plan(
            phases=[
                _phase(
                    phase_id=0,
                    steps=[_step("1.1", agent_name="backend-engineer")],
                    approval_required=True,
                ),
                _phase(phase_id=1, name="Phase 2", steps=[_step("2.1", agent_name="architect")]),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # APPROVAL

        # Normal path — must not raise.
        engine.record_approval_result(
            phase_id=0,
            result="approve-with-feedback",
            feedback="Minor nits",
        )
        state = engine._load_state()
        # Amendment must be durably saved.
        assert len(state.amendments) == 1
        assert state.amendments[0].trigger == "approval_feedback"
