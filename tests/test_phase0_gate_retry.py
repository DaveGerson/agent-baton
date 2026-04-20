"""Regression tests for Fix 0.5 — gate retry mechanism.

Verifies:
  1. record_gate_result(passed=False) sets state.status == "gate_failed"
     (NOT "failed").
  2. _determine_action() returns a GATE action when status == "gate_failed".
  3. reset_gate_failed(phase_id) transitions gate_failed → gate_pending,
     removes the failed GateResult, and next_action() re-issues GATE.
  4. fail_gate(phase_id) transitions gate_failed → failed.
  5. CLI retry-gate and fail subcommands exist and call the right engine methods.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _plan_with_gate(
    task_id: str = "gate-retry",
    gate_command: str = "pytest",
) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Gate retry test",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Build feature",
                    )
                ],
                gate=PlanGate(gate_type="test", command=gate_command),
            )
        ],
    )


def _run_to_gate(engine: ExecutionEngine, plan: MachinePlan) -> None:
    """Start the engine and complete all steps so it reaches the gate."""
    engine.start(plan)
    for phase in plan.phases:
        for step in phase.steps:
            engine.record_step_result(
                step_id=step.step_id,
                agent_name=step.agent_name,
                status="complete",
                outcome="done",
            )


# ---------------------------------------------------------------------------
# Fix 0.5a: record_gate_result(passed=False) → status == "gate_failed"
# ---------------------------------------------------------------------------

class TestGateFailedStatus:
    def test_failed_gate_sets_gate_failed_not_failed(self, tmp_path: Path) -> None:
        """record_gate_result(passed=False) must set status='gate_failed', not 'failed'."""
        plan = _plan_with_gate()
        engine = ExecutionEngine(team_context_root=tmp_path)
        _run_to_gate(engine, plan)

        engine.record_gate_result(phase_id=1, passed=False, output="tests broke")

        state = engine._load_state()
        assert state.status == "gate_failed", (
            f"Expected status='gate_failed' after a failed gate, got '{state.status}'. "
            "Fix 0.5: record_gate_result(passed=False) must use 'gate_failed' status."
        )

    def test_failed_gate_does_not_set_status_failed(self, tmp_path: Path) -> None:
        """Confirm the old bug (status='failed') no longer occurs."""
        plan = _plan_with_gate()
        engine = ExecutionEngine(team_context_root=tmp_path)
        _run_to_gate(engine, plan)

        engine.record_gate_result(phase_id=1, passed=False, output="flaky test")

        state = engine._load_state()
        assert state.status != "failed", (
            "status must NOT be 'failed' after a gate fails — "
            "that was the bug; it should now be 'gate_failed'."
        )

    def test_passed_gate_sets_running_status(self, tmp_path: Path) -> None:
        """Passing gate still advances to running/next phase — no regression."""
        plan = _plan_with_gate()
        engine = ExecutionEngine(team_context_root=tmp_path)
        _run_to_gate(engine, plan)

        engine.record_gate_result(phase_id=1, passed=True, output="all green")

        state = engine._load_state()
        # After a successful gate with no more phases, engine completes.
        assert state.status != "gate_failed", (
            "A passing gate must not set status='gate_failed'"
        )


# ---------------------------------------------------------------------------
# Fix 0.5b: _determine_action() returns GATE when status == "gate_failed"
# ---------------------------------------------------------------------------

class TestDetermineActionOnGateFailed:
    def test_next_action_returns_gate_when_gate_failed(self, tmp_path: Path) -> None:
        """next_action() must return a GATE action when status is 'gate_failed'."""
        plan = _plan_with_gate()
        engine = ExecutionEngine(team_context_root=tmp_path)
        _run_to_gate(engine, plan)

        engine.record_gate_result(phase_id=1, passed=False, output="flaky")

        action = engine.next_action()
        assert action.action_type == ActionType.GATE, (
            f"next_action() must return GATE when status='gate_failed', "
            f"got {action.action_type}. Fix 0.5 regression."
        )

    def test_gate_failed_action_references_correct_phase(self, tmp_path: Path) -> None:
        """The re-issued GATE action must reference the same phase that failed."""
        plan = _plan_with_gate()
        engine = ExecutionEngine(team_context_root=tmp_path)
        _run_to_gate(engine, plan)

        engine.record_gate_result(phase_id=1, passed=False, output="still failing")

        action = engine.next_action()
        assert action.phase_id == 1, (
            "The re-issued GATE action must reference phase_id=1"
        )


# ---------------------------------------------------------------------------
# Fix 0.5c: reset_gate_failed() transitions gate_failed → gate_pending
# ---------------------------------------------------------------------------

class TestResetGateFailed:
    def test_reset_gate_failed_transitions_to_gate_pending(self, tmp_path: Path) -> None:
        """reset_gate_failed(phase_id) must change status from gate_failed to gate_pending."""
        plan = _plan_with_gate()
        engine = ExecutionEngine(team_context_root=tmp_path)
        _run_to_gate(engine, plan)

        engine.record_gate_result(phase_id=1, passed=False, output="broke")
        assert engine._load_state().status == "gate_failed"

        engine.reset_gate_failed(phase_id=1)

        state = engine._load_state()
        assert state.status == "gate_pending", (
            f"reset_gate_failed() must set status='gate_pending', got '{state.status}'"
        )

    def test_reset_gate_failed_removes_failed_gate_result(self, tmp_path: Path) -> None:
        """reset_gate_failed() must remove the failed GateResult so the gate is re-runnable."""
        plan = _plan_with_gate()
        engine = ExecutionEngine(team_context_root=tmp_path)
        _run_to_gate(engine, plan)

        engine.record_gate_result(phase_id=1, passed=False, output="FAIL")
        state = engine._load_state()
        failed_gates_before = [g for g in state.gate_results if not g.passed]
        assert len(failed_gates_before) == 1, "Pre-condition: one failed gate result"

        engine.reset_gate_failed(phase_id=1)

        state_after = engine._load_state()
        failed_gates_after = [g for g in state_after.gate_results if not g.passed]
        assert len(failed_gates_after) == 0, (
            "reset_gate_failed() must remove the failed GateResult"
        )

    def test_reset_gate_failed_then_next_action_returns_gate(self, tmp_path: Path) -> None:
        """After reset_gate_failed(), next_action() must return GATE again."""
        plan = _plan_with_gate()
        engine = ExecutionEngine(team_context_root=tmp_path)
        _run_to_gate(engine, plan)

        engine.record_gate_result(phase_id=1, passed=False, output="broken")
        engine.reset_gate_failed(phase_id=1)

        action = engine.next_action()
        assert action.action_type == ActionType.GATE, (
            "After reset_gate_failed(), next_action() must return GATE for retry"
        )

    def test_reset_gate_failed_raises_on_wrong_status(self, tmp_path: Path) -> None:
        """reset_gate_failed() must raise ValueError when status is not 'gate_failed'."""
        plan = _plan_with_gate()
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        # Status is 'running' — not gate_failed.
        with pytest.raises(ValueError, match="gate_failed"):
            engine.reset_gate_failed(phase_id=1)


# ---------------------------------------------------------------------------
# Fix 0.5d: fail_gate() transitions gate_failed → failed
# ---------------------------------------------------------------------------

class TestFailGate:
    def test_fail_gate_transitions_to_failed(self, tmp_path: Path) -> None:
        """fail_gate(phase_id) must set status='failed' from 'gate_failed'."""
        plan = _plan_with_gate()
        engine = ExecutionEngine(team_context_root=tmp_path)
        _run_to_gate(engine, plan)

        engine.record_gate_result(phase_id=1, passed=False, output="still broken")
        assert engine._load_state().status == "gate_failed"

        engine.fail_gate(phase_id=1)

        state = engine._load_state()
        assert state.status == "failed", (
            f"fail_gate() must set status='failed', got '{state.status}'"
        )

    def test_fail_gate_raises_on_wrong_status(self, tmp_path: Path) -> None:
        """fail_gate() must raise ValueError when status is not 'gate_failed'."""
        plan = _plan_with_gate()
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        with pytest.raises(ValueError, match="gate_failed"):
            engine.fail_gate(phase_id=1)


# ---------------------------------------------------------------------------
# Fix 0.5e: CLI retry-gate and fail subcommands exist and wire engine methods
# ---------------------------------------------------------------------------

_EXECUTE_MOD = "agent_baton.cli.commands.execution.execute"


def _build_execute_parser() -> argparse.ArgumentParser:
    """Construct the top-level parser with the 'execute' subcommand registered."""
    import argparse as _ap
    from agent_baton.cli.commands.execution.execute import register
    root = _ap.ArgumentParser()
    sub = root.add_subparsers(dest="cmd")
    register(sub)
    return root


class TestCliGateRetrySubcommands:
    def test_retry_gate_subcommand_is_registered(self) -> None:
        """baton execute retry-gate must be a registered subcommand."""
        parser = _build_execute_parser()
        args = parser.parse_args(["execute", "retry-gate", "--phase-id", "1"])
        assert args.subcommand == "retry-gate"
        assert args.phase_id == 1

    def test_fail_subcommand_is_registered(self) -> None:
        """baton execute fail must be a registered subcommand."""
        parser = _build_execute_parser()
        args = parser.parse_args(["execute", "fail", "--phase-id", "2"])
        assert args.subcommand == "fail"
        assert args.phase_id == 2

    def test_retry_gate_handler_calls_reset_gate_failed(self, tmp_path: Path) -> None:
        """The retry-gate handler must call engine.reset_gate_failed(phase_id)."""
        from agent_baton.cli.commands.execution import execute as _mod

        plan = _plan_with_gate(task_id="cli-retry-gate")
        real_engine = ExecutionEngine(team_context_root=tmp_path)
        _run_to_gate(real_engine, plan)
        real_engine.record_gate_result(phase_id=1, passed=False, output="broke")

        reset_calls: list[int] = []
        original_reset = real_engine.reset_gate_failed

        def spy_reset(phase_id: int) -> None:
            reset_calls.append(phase_id)
            original_reset(phase_id)

        real_engine.reset_gate_failed = spy_reset  # type: ignore[method-assign]

        args = argparse.Namespace(
            subcommand="retry-gate",
            phase_id=1,
            task_id=None,
            output="text",
        )

        # The handler constructs ExecutionEngine directly — patch the class so
        # it returns our pre-seeded real_engine instead.
        class _FakeStorage:
            def get_active_task(self):
                return plan.task_id
            def set_active_task(self, tid):
                pass

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=_FakeStorage()),
            patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=real_engine),
            patch(f"{_EXECUTE_MOD}.detect_backend", return_value="file"),
        ):
            try:
                _mod.handler(args)
            except SystemExit:
                pass  # user_error() exits — but the reset call should have happened

        assert 1 in reset_calls, (
            "retry-gate handler must call engine.reset_gate_failed(phase_id=1)"
        )

    def test_fail_handler_calls_fail_gate(self, tmp_path: Path) -> None:
        """The fail handler must call engine.fail_gate(phase_id)."""
        from agent_baton.cli.commands.execution import execute as _mod

        plan = _plan_with_gate(task_id="cli-fail-gate")
        real_engine = ExecutionEngine(team_context_root=tmp_path)
        _run_to_gate(real_engine, plan)
        real_engine.record_gate_result(phase_id=1, passed=False, output="broke")

        fail_calls: list[int] = []
        original_fail = real_engine.fail_gate

        def spy_fail(phase_id: int) -> None:
            fail_calls.append(phase_id)
            original_fail(phase_id)

        real_engine.fail_gate = spy_fail  # type: ignore[method-assign]

        args = argparse.Namespace(
            subcommand="fail",
            phase_id=1,
            task_id=None,
            output="text",
        )

        class _FakeStorage:
            def get_active_task(self):
                return plan.task_id
            def set_active_task(self, tid):
                pass

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=_FakeStorage()),
            patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=real_engine),
            patch(f"{_EXECUTE_MOD}.detect_backend", return_value="file"),
        ):
            try:
                _mod.handler(args)
            except SystemExit:
                pass

        assert 1 in fail_calls, (
            "fail handler must call engine.fail_gate(phase_id=1)"
        )
