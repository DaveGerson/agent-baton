"""Regression tests for Fix 0.4 — recover_dispatched_steps() wired into resume().

Verifies:
  1. engine.resume() clears steps stuck in "dispatched" and returns a DISPATCH
     action (not WAIT or FAILED).
  2. WorkerSupervisor.start(resume=True) also calls recover_dispatched_steps()
     before handing off to the worker loop.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.runtime.launcher import DryRunLauncher
from agent_baton.core.runtime.supervisor import WorkerSupervisor
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_plan(task_id: str = "resume-test") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Resume recovery test",
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
            )
        ],
    )


# ---------------------------------------------------------------------------
# Fix 0.4a: engine.resume() clears dispatched steps
# ---------------------------------------------------------------------------

class TestEngineResumeRecovery:
    def test_resume_clears_dispatched_step_and_redispatches(self, tmp_path: Path) -> None:
        """After a crash, a step stuck in 'dispatched' must be cleared on resume().

        The engine starts a plan, marks step 1.1 as dispatched (simulating a crash
        before the result was recorded), then a fresh engine instance resumes.
        The resumed engine must return DISPATCH (not WAIT) for the same step.
        """
        plan = _simple_plan()
        engine = ExecutionEngine(team_context_root=tmp_path)
        action = engine.start(plan)
        assert action.action_type == ActionType.DISPATCH

        # Simulate a crash: mark the step as "dispatched" but never complete it.
        engine.mark_dispatched(step_id="1.1", agent_name="backend-engineer")

        # Confirm the step is stuck in dispatched.
        state = engine._load_state()
        assert any(
            r.step_id == "1.1" and r.status == "dispatched"
            for r in state.step_results
        ), "Pre-condition: step 1.1 should be in 'dispatched' state"

        # A new engine instance resumes (simulates daemon restart).
        new_engine = ExecutionEngine(team_context_root=tmp_path)
        new_engine._task_id = plan.task_id
        new_engine.resume()

        # The dispatched step must have been cleared from persisted state.
        state_after = new_engine._load_state()
        dispatched_ids = {r.step_id for r in state_after.step_results if r.status == "dispatched"}
        assert "1.1" not in dispatched_ids, (
            "resume() must clear steps stuck in 'dispatched' status"
        )

        # The next action must be DISPATCH — the step is re-dispatchable.
        # next_action() loads the cleaned state saved by recover_dispatched_steps().
        next_act = new_engine.next_action()
        assert next_act.action_type == ActionType.DISPATCH, (
            f"Expected DISPATCH after resume, got {next_act.action_type}. "
            "recover_dispatched_steps() may not be wired into resume()."
        )
        assert next_act.step_id == "1.1"

    def test_resume_does_not_affect_completed_steps(self, tmp_path: Path) -> None:
        """recover_dispatched_steps() must leave completed steps untouched."""
        plan = _simple_plan(task_id="resume-no-clobber")
        plan.phases[0].steps.append(
            PlanStep(
                step_id="1.2",
                agent_name="test-engineer",
                task_description="Write tests",
            )
        )

        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)

        # Complete step 1.1, leave 1.2 dispatched.
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome="done",
        )
        engine.mark_dispatched(step_id="1.2", agent_name="test-engineer")

        new_engine = ExecutionEngine(team_context_root=tmp_path)
        new_engine._task_id = plan.task_id
        new_engine.resume()

        state = new_engine._load_state()
        # 1.1 must remain complete.
        completed = {r.step_id for r in state.step_results if r.status == "complete"}
        assert "1.1" in completed, "resume() must not remove completed step results"
        # 1.2 must have been cleared (was dispatched).
        dispatched = {r.step_id for r in state.step_results if r.status == "dispatched"}
        assert "1.2" not in dispatched, "resume() must clear dispatched step 1.2"

    def test_resume_no_op_when_no_dispatched_steps(self, tmp_path: Path) -> None:
        """Resume on a clean state (no dispatched steps) must not raise or corrupt state."""
        plan = _simple_plan(task_id="resume-noop")
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)

        new_engine = ExecutionEngine(team_context_root=tmp_path)
        new_engine._task_id = plan.task_id
        action = new_engine.resume()

        # Should return DISPATCH for the first step — normal operation.
        assert action.action_type == ActionType.DISPATCH


# ---------------------------------------------------------------------------
# Fix 0.4b: WorkerSupervisor.start(resume=True) calls recover_dispatched_steps
# ---------------------------------------------------------------------------

class TestSupervisorResumeCallsRecovery:
    def test_supervisor_resume_calls_recover_dispatched_steps(self, tmp_path: Path) -> None:
        """WorkerSupervisor.start(resume=True) must call engine.recover_dispatched_steps().

        We patch ExecutionEngine to spy on recover_dispatched_steps and verify
        it is invoked before the worker loop runs.
        """
        plan = _simple_plan(task_id="supervisor-resume")

        # Pre-create a persisted execution so resume() can load state.
        seed_engine = ExecutionEngine(team_context_root=tmp_path)
        seed_engine.start(plan)

        recovery_calls: list[str] = []

        original_recover = ExecutionEngine.recover_dispatched_steps

        def spy_recover(self_engine: ExecutionEngine) -> int:
            recovery_calls.append("called")
            return original_recover(self_engine)

        supervisor = WorkerSupervisor(team_context_root=tmp_path, task_id=plan.task_id)

        with patch.object(ExecutionEngine, "recover_dispatched_steps", spy_recover):
            # DryRunLauncher completes immediately so the supervisor won't hang.
            supervisor.start(plan=plan, launcher=DryRunLauncher(), resume=True)

        assert len(recovery_calls) >= 1, (
            "WorkerSupervisor.start(resume=True) must call recover_dispatched_steps(). "
            "It was not called — regression in supervisor.py Fix 0.4."
        )
