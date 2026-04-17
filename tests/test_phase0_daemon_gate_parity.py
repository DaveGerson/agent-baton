"""Regression tests for Fix 0.1 — daemon gate execution parity.

Verifies that TaskWorker._handle_gate():
  1. Calls asyncio.create_subprocess_shell with the gate command for programmatic gates.
  2. Falls back to auto-approve when gate_command is empty/missing.
  3. Records pass when subprocess returncode == 0.
  4. Records fail when subprocess returncode != 0.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.runtime.launcher import DryRunLauncher
from agent_baton.core.runtime.worker import TaskWorker
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
    task_id: str = "gate-parity",
    gate_type: str = "test",
    gate_command: str = "pytest",
) -> MachinePlan:
    """One-phase plan whose phase has a programmatic gate."""
    return MachinePlan(
        task_id=task_id,
        task_summary="Gate parity test",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Do the work",
                    )
                ],
                gate=PlanGate(gate_type=gate_type, command=gate_command),
            )
        ],
    )


def _make_action(gate_type: str = "test", gate_command: str = "pytest", phase_id: int = 1):
    """Build a minimal ExecutionAction-like object for _handle_gate."""
    action = MagicMock()
    action.action_type = ActionType.GATE
    action.gate_type = gate_type
    action.gate_command = gate_command
    action.phase_id = phase_id
    action.message = f"Run {gate_type} gate"
    return action


def _make_worker(tmp_path: Path, plan: MachinePlan) -> TaskWorker:
    engine = ExecutionEngine(team_context_root=tmp_path)
    engine.start(plan)
    # Complete the single step so the worker reaches the gate.
    engine.record_step_result(
        step_id="1.1",
        agent_name="backend-engineer",
        status="complete",
        outcome="done",
    )
    return TaskWorker(engine=engine, launcher=DryRunLauncher())


# ---------------------------------------------------------------------------
# Fix 0.1: subprocess execution for programmatic gates
# ---------------------------------------------------------------------------

class TestHandleGateSubprocessExecution:
    @pytest.mark.parametrize("gate_type", ["test", "build", "lint", "spec"])
    def test_programmatic_gate_calls_create_subprocess_shell(
        self, tmp_path: Path, gate_type: str
    ) -> None:
        """_handle_gate() must call asyncio.create_subprocess_shell for programmatic gates."""
        plan = _plan_with_gate(
            task_id=f"gate-{gate_type}",
            gate_type=gate_type,
            gate_command=f"run-{gate_type}",
        )
        worker = _make_worker(tmp_path, plan)
        action = _make_action(gate_type=gate_type, gate_command=f"run-{gate_type}")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))

        shell_calls: list[str] = []

        async def _fake_subprocess_shell(cmd, **kwargs):
            shell_calls.append(cmd)
            return mock_proc

        async def _run():
            with patch("asyncio.create_subprocess_shell", side_effect=_fake_subprocess_shell):
                await worker._handle_gate(action)

        asyncio.run(_run())

        assert len(shell_calls) == 1, (
            f"asyncio.create_subprocess_shell must be called once for gate_type={gate_type!r}"
        )
        assert shell_calls[0] == f"run-{gate_type}", (
            "Gate command must be passed to create_subprocess_shell"
        )

    def test_gate_auto_approves_when_command_is_empty(self, tmp_path: Path) -> None:
        """_handle_gate() must auto-approve (pass=True) when gate_command is empty."""
        plan = _plan_with_gate(task_id="gate-empty-cmd", gate_type="test", gate_command="")
        worker = _make_worker(tmp_path, plan)
        action = _make_action(gate_type="test", gate_command="")

        shell_calls: list[str] = []

        async def _fake_subprocess_shell(cmd, **kwargs):
            shell_calls.append(cmd)
            raise AssertionError("create_subprocess_shell must NOT be called for empty command")

        async def _run():
            with patch("asyncio.create_subprocess_shell", side_effect=_fake_subprocess_shell):
                await worker._handle_gate(action)

        asyncio.run(_run())

        # Verify subprocess was never called.
        assert len(shell_calls) == 0

        # Verify the gate was recorded as passed (auto-approve).
        state = worker._engine._load_state()
        gate_results = state.gate_results if state else []
        assert any(g.passed for g in gate_results), (
            "Empty gate command must result in an auto-approved (passed) gate result"
        )

    def test_gate_passes_when_returncode_zero(self, tmp_path: Path) -> None:
        """_handle_gate() must record passed=True when the subprocess exits with 0."""
        plan = _plan_with_gate(task_id="gate-pass", gate_type="test", gate_command="pytest")
        worker = _make_worker(tmp_path, plan)
        action = _make_action(gate_type="test", gate_command="pytest")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"1 passed", b""))

        async def _run():
            with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
                await worker._handle_gate(action)

        asyncio.run(_run())

        state = worker._engine._load_state()
        gate_results = [g for g in state.gate_results if g.phase_id == 1]
        assert gate_results, "A gate result must be recorded"
        assert gate_results[-1].passed is True, (
            "Gate must be recorded as passed when subprocess returncode == 0"
        )

    def test_gate_fails_when_returncode_nonzero(self, tmp_path: Path) -> None:
        """_handle_gate() must record passed=False when the subprocess exits with non-zero."""
        plan = _plan_with_gate(task_id="gate-fail", gate_type="test", gate_command="pytest")
        worker = _make_worker(tmp_path, plan)
        action = _make_action(gate_type="test", gate_command="pytest")

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"FAILED"))

        async def _run():
            with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
                await worker._handle_gate(action)

        asyncio.run(_run())

        state = worker._engine._load_state()
        gate_results = [g for g in state.gate_results if g.phase_id == 1]
        assert gate_results, "A gate result must be recorded even on failure"
        assert gate_results[-1].passed is False, (
            "Gate must be recorded as failed when subprocess returncode != 0"
        )

    def test_human_gate_does_not_call_subprocess(self, tmp_path: Path) -> None:
        """Human-required gate types (review, approval) must NOT call create_subprocess_shell."""
        plan = _plan_with_gate(task_id="gate-human", gate_type="review", gate_command="")
        # Replace gate so it's a human gate type.
        plan.phases[0].gate = PlanGate(gate_type="review", command="")
        worker = _make_worker(tmp_path, plan)
        action = _make_action(gate_type="review", gate_command="")

        shell_calls: list[str] = []

        async def _unexpected_shell(cmd, **kwargs):
            shell_calls.append(cmd)
            raise AssertionError("create_subprocess_shell must NOT be called for human gates")

        async def _run():
            with patch("asyncio.create_subprocess_shell", side_effect=_unexpected_shell):
                await worker._handle_gate(action)

        asyncio.run(_run())

        assert len(shell_calls) == 0, (
            "Human-required gates must not call create_subprocess_shell"
        )
