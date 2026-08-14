"""RW-2.4 — the daemon loop must survive an ``InvalidGateState``.

``TaskWorker._execution_loop`` calls ``_handle_gate`` with no exception
handling, and ``_handle_gate`` calls ``engine.record_gate_result`` at eight
sites with none either.  Since the F002 pre-conditions landed, that call can
raise :class:`~agent_baton.core.engine.errors.InvalidGateState` — and it can
do so for reasons the daemon cannot prevent: the worker holds a GATE action
across an ``await`` while a CLI invocation, the REST API, or a second worker
advances the same execution's phase.  The action it is holding is then stale,
the recording is rejected, and the exception unwinds all the way out of
``run()``, taking the whole daemon down with every other task it was
supervising.

A rejected gate recording is a per-task problem.  It must not be a
process-level fatality.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_baton.core.engine.errors import InvalidGateState
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


def _plan(task_id: str, gate_type: str = "test", command: str = "") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Daemon gate-rejection fixture",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Build it",
                    ),
                ],
                gate=PlanGate(gate_type=gate_type, command=command),
            ),
        ],
    )


def _stale_gate_action(gate_type: str, phase_id: int, command: str = ""):
    """A GATE action whose phase is no longer the engine's current phase."""
    action = MagicMock()
    action.action_type = ActionType.GATE
    action.gate_type = gate_type
    action.gate_command = command
    action.phase_id = phase_id
    action.message = f"Run {gate_type} gate"
    return action


def _worker(tmp_path: Path, plan: MachinePlan) -> TaskWorker:
    engine = ExecutionEngine(team_context_root=tmp_path)
    engine.start(plan)
    engine.record_step_result(
        step_id="1.1",
        agent_name="backend-engineer",
        status="complete",
        outcome="done",
    )
    return TaskWorker(engine=engine, launcher=DryRunLauncher())


class TestHandleGateSurvivesRejection:
    """``_handle_gate`` must absorb a rejected recording."""

    @pytest.mark.parametrize(
        ("label", "gate_type"),
        [
            # No command → the "auto-approve, nothing to run" branch.
            ("programmatic_no_command", "test"),
            # No DecisionManager → the human-gate auto-approve branch.
            ("human_gate_autoapprove", "review"),
        ],
    )
    def test_stale_phase_does_not_propagate(
        self, tmp_path: Path, label: str, gate_type: str
    ) -> None:
        worker = _worker(tmp_path, _plan(f"worker-gate-{label}", gate_type=gate_type))
        action = _stale_gate_action(gate_type, phase_id=99)

        try:
            asyncio.run(worker._handle_gate(action))
        except InvalidGateState as exc:  # pragma: no cover - the defect
            pytest.fail(
                "TaskWorker._handle_gate let InvalidGateState escape "
                f"({label}); the daemon supervising this task dies: {exc}"
            )

    def test_rejected_recording_leaves_state_intact(self, tmp_path: Path) -> None:
        """Absorbing the rejection must not also corrupt the execution."""
        worker = _worker(tmp_path, _plan("worker-gate-intact"))
        engine = worker._engine
        before = engine._load_state()
        assert before is not None

        try:
            asyncio.run(worker._handle_gate(_stale_gate_action("test", phase_id=99)))
        except InvalidGateState as exc:  # pragma: no cover - the defect
            pytest.fail(f"InvalidGateState escaped _handle_gate: {exc}")

        after = engine._load_state()
        assert after is not None
        assert after.gate_results == [], (
            "a rejected gate recording must not leave a forged gate row: "
            f"{[(g.phase_id, g.gate_type) for g in after.gate_results]}"
        )
        assert after.current_phase == before.current_phase


class TestExecutionLoopSurvivesRejection:
    """``run()`` must return, not raise and not livelock.

    Swallowing the exception inside ``_handle_gate`` alone is not enough: the
    loop asks the engine for the next action again, gets the same GATE, and
    spins.  The guard has to end the task one way or another.
    """

    def test_run_returns_instead_of_dying(self, tmp_path: Path) -> None:
        worker = _worker(tmp_path, _plan("worker-gate-loop"))
        engine = worker._engine

        def _always_rejected(**kwargs):
            raise InvalidGateState(
                reason=InvalidGateState.REASON_PHASE_MISMATCH,
                message="record_gate_result: phase_id=1 is not the current phase",
                phase_id=kwargs.get("phase_id"),
                current_phase_id=2,
            )

        engine.record_gate_result = _always_rejected  # type: ignore[method-assign]

        async def _drive() -> str:
            return await asyncio.wait_for(worker.run(), timeout=10)

        try:
            summary = asyncio.run(_drive())
        except InvalidGateState as exc:  # pragma: no cover - the defect
            pytest.fail(
                "TaskWorker.run() died on a rejected gate recording; every "
                f"other task this daemon supervises dies with it: {exc}"
            )
        except asyncio.TimeoutError:  # pragma: no cover - the livelock defect
            pytest.fail(
                "TaskWorker.run() never returned after a permanently rejected "
                "gate recording — the daemon spins on the same GATE action "
                "forever instead of ending the task."
            )

        assert isinstance(summary, str) and summary, (
            "run() must return a summary describing how the task ended"
        )
