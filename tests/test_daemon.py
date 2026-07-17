"""Tests for WorkerSupervisor (daemon mode) and SignalHandler.

Also includes E2E subprocess smoke tests for:
  baton daemon start --dry-run --foreground --plan <file>
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

if sys.platform != "win32":
    import fcntl
else:
    import msvcrt

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.runtime.decisions import DecisionManager
from agent_baton.core.runtime.launcher import DryRunLauncher, LaunchResult
from agent_baton.core.runtime.supervisor import WorkerSupervisor
from agent_baton.core.runtime.worker import TaskWorker
from agent_baton.models.execution import (
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Plan factories
# ---------------------------------------------------------------------------

def _step(step_id: str = "1.1", agent: str = "backend") -> PlanStep:
    return PlanStep(step_id=step_id, agent_name=agent, task_description="task")


def _gate(gate_type: str = "test") -> PlanGate:
    return PlanGate(gate_type=gate_type)


def _phase(phase_id: int = 0, steps=None, gate=None) -> PlanPhase:
    return PlanPhase(phase_id=phase_id, name="P", steps=steps or [_step()], gate=gate)


def _plan(task_id: str = "t1", phases=None) -> MachinePlan:
    return MachinePlan(
        task_id=task_id, task_summary="test plan",
        phases=phases or [_phase()],
    )


# ===========================================================================
# WorkerSupervisor — paths
# ===========================================================================

class TestSupervisorPaths:
    # DECISION: removed trivial test_default_paths (hard-coded filename assertions
    # against "daemon.pid" etc.). The names are constants; this adds no behavioural
    # coverage. test_custom_root exercises the meaningful path composition logic.
    def test_custom_root(self, tmp_path: Path) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path)
        assert s.pid_path.parent == tmp_path


# ===========================================================================
# WorkerSupervisor — start()
# ===========================================================================

class TestSupervisorStart:
    def test_simple_plan_completes(self, tmp_path: Path) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path)
        summary = s.start(plan=_plan(), launcher=DryRunLauncher())
        assert "completed" in summary.lower() or "complete" in summary.lower()

    def test_pid_file_cleaned_up(self, tmp_path: Path) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path)
        s.start(plan=_plan(), launcher=DryRunLauncher())
        assert not s.pid_path.exists()

    def test_daemon_log_created(self, tmp_path: Path) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path)
        s.start(plan=_plan(), launcher=DryRunLauncher())
        assert s.log_path.exists()
        content = s.log_path.read_text()
        assert "Daemon starting" in content

    def test_status_file_written(self, tmp_path: Path) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path)
        s.start(plan=_plan(), launcher=DryRunLauncher())
        assert s.status_path.exists()
        data = json.loads(s.status_path.read_text())
        assert "timestamp" in data

    def test_multi_phase_plan(self, tmp_path: Path) -> None:
        plan = _plan(phases=[
            _phase(phase_id=0, steps=[_step("1.1")], gate=_gate()),
            _phase(phase_id=1, steps=[_step("2.1", agent="tester")]),
        ])
        s = WorkerSupervisor(team_context_root=tmp_path)
        summary = s.start(plan=plan, launcher=DryRunLauncher())
        assert "completed" in summary.lower() or "complete" in summary.lower()

    def test_failed_step(self, tmp_path: Path) -> None:
        launcher = DryRunLauncher()
        launcher.set_result(
            "1.1",
            LaunchResult(step_id="1.1", agent_name="backend", status="failed", error="crash"),
        )
        s = WorkerSupervisor(team_context_root=tmp_path)
        summary = s.start(plan=_plan(), launcher=launcher)
        assert "failed" in summary.lower()


# ===========================================================================
# WorkerSupervisor — status()
# ===========================================================================

class TestSupervisorStatus:
    # DECISION: removed test_no_pid_means_not_running — exact duplicate of
    # test_no_execution_returns_not_running (same object, same assertion, same code path).
    def test_no_execution_returns_not_running(self, tmp_path: Path) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path)
        status = s.status()
        assert status["running"] is False

    def test_after_completion_has_status(self, tmp_path: Path) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path)
        s.start(plan=_plan(), launcher=DryRunLauncher())
        status = s.status()
        assert status.get("task_id") == "t1"
        assert "last_update" in status


# ===========================================================================
# WorkerSupervisor — stop()
# ===========================================================================

class TestSupervisorStop:
    def test_stop_without_pid_returns_false(self, tmp_path: Path) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path)
        assert s.stop() is False

    def test_stop_with_stale_pid_returns_false_or_true(self, tmp_path: Path) -> None:
        """A stale PID (process no longer running) — stop() may still send
        the signal or fail gracefully."""
        s = WorkerSupervisor(team_context_root=tmp_path)
        tmp_path.mkdir(parents=True, exist_ok=True)
        s.pid_path.write_text("999999999")  # likely doesn't exist
        # Should not raise
        s.stop()


# ===========================================================================
# WorkerSupervisor — PID locking
# ===========================================================================

class TestSupervisorPIDLocking:
    def test_pid_file_contains_pid(self, tmp_path: Path) -> None:
        """After start(), PID file is removed (clean exit) but was written
        during execution.  We verify that the PID written to disk during
        execution is the current process PID by inspecting immediately after
        _write_pid()."""
        import os
        s = WorkerSupervisor(team_context_root=tmp_path)
        tmp_path.mkdir(parents=True, exist_ok=True)
        s._write_pid()
        try:
            # On Windows, msvcrt lock blocks other handles from reading
            # the locked byte range, so read via the same FD.
            s._pid_fd.seek(0)
            content = s._pid_fd.read().strip()
            assert content == str(os.getpid())
        finally:
            s._remove_pid()

    def test_flock_prevents_second_instance(self, tmp_path: Path) -> None:
        """A second supervisor on the same directory cannot acquire the lock
        while the first holds it."""
        s1 = WorkerSupervisor(team_context_root=tmp_path)
        tmp_path.mkdir(parents=True, exist_ok=True)
        s1._write_pid()
        try:
            s2 = WorkerSupervisor(team_context_root=tmp_path)
            with pytest.raises(RuntimeError, match="Another daemon is already running"):
                s2._write_pid()
        finally:
            s1._remove_pid()

    def test_stale_pid_cleaned_on_restart(self, tmp_path: Path) -> None:
        """A PID file left on disk (without a live flock) does not block a
        new supervisor from starting."""
        # Write a file that looks like a stale PID file — no flock held.
        tmp_path.mkdir(parents=True, exist_ok=True)
        stale_pid_path = tmp_path / "daemon.pid"
        stale_pid_path.write_text("999999999")

        # A new supervisor should be able to acquire the lock.
        s = WorkerSupervisor(team_context_root=tmp_path)
        # _write_pid opens the file for writing (overwriting), so the stale
        # content is gone and the lock is now ours.
        s._write_pid()
        try:
            # Read via the held FD to avoid lock conflicts on Windows.
            s._pid_fd.seek(0)
            content = s._pid_fd.read().strip()
            assert content != "999999999"
        finally:
            s._remove_pid()


# ===========================================================================
# WorkerSupervisor — log rotation
# ===========================================================================

class TestSupervisorLogRotation:
    # DECISION: merged test_uses_rotating_handler + test_rotating_handler_max_bytes
    # into one comprehensive test. Both call _setup_logging() and inspect the same
    # RotatingFileHandler object; splitting adds no independent coverage.
    def test_rotating_handler_attached_with_correct_max_bytes(self, tmp_path: Path) -> None:
        """After _setup_logging(), the baton.daemon logger has a RotatingFileHandler
        with maxBytes == 10 MiB."""
        s = WorkerSupervisor(team_context_root=tmp_path)
        s._setup_logging()
        logger = logging.getLogger("baton.daemon")
        rotating_handlers = [
            h for h in logger.handlers if isinstance(h, RotatingFileHandler)
        ]
        assert len(rotating_handlers) >= 1
        assert rotating_handlers[-1].maxBytes == 10 * 1024 * 1024

    def test_log_file_created(self, tmp_path: Path) -> None:
        """daemon.log exists on disk after start() completes."""
        s = WorkerSupervisor(team_context_root=tmp_path)
        s.start(plan=_plan(), launcher=DryRunLauncher())
        assert s.log_path.exists()


# ===========================================================================
# WorkerSupervisor — atomic status writes
# ===========================================================================

class TestSupervisorAtomicWrites:
    def test_status_file_written_atomically(self, tmp_path: Path) -> None:
        """daemon-status.json exists and contains valid JSON after start()."""
        s = WorkerSupervisor(team_context_root=tmp_path)
        s.start(plan=_plan(), launcher=DryRunLauncher())
        assert s.status_path.exists()
        data = json.loads(s.status_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "timestamp" in data

    def test_no_tmp_file_left_behind(self, tmp_path: Path) -> None:
        """After start(), the .tmp intermediate file is gone."""
        s = WorkerSupervisor(team_context_root=tmp_path)
        s.start(plan=_plan(), launcher=DryRunLauncher())
        tmp_file = s.status_path.with_suffix(".tmp")
        assert not tmp_file.exists()

    def test_status_file_has_summary_field(self, tmp_path: Path) -> None:
        """The status JSON written atomically includes a 'summary' key."""
        s = WorkerSupervisor(team_context_root=tmp_path)
        s.start(plan=_plan(), launcher=DryRunLauncher())
        data = json.loads(s.status_path.read_text(encoding="utf-8"))
        assert "summary" in data


# ===========================================================================
# WorkerSupervisor — resume
# ===========================================================================

class TestSupervisorResume:
    def test_resume_flag_calls_engine_resume(self, tmp_path: Path) -> None:
        """When resume=True, the engine loads state from disk rather than
        starting fresh.  We verify by first writing state with start() and
        then resuming — the result should still complete successfully."""
        # Phase 1: run to completion to create persisted state.
        from agent_baton.core.engine.executor import ExecutionEngine
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend", status="complete")
        # state is now on disk but not yet marked complete.

        # Phase 2: supervisor.start(resume=True) should pick up the existing
        # state and drive it to completion.
        s = WorkerSupervisor(team_context_root=tmp_path)
        summary = s.start(plan=_plan(), launcher=DryRunLauncher(), resume=True)
        assert "completed" in summary.lower() or "complete" in summary.lower()

    def test_resume_after_partial_execution(self, tmp_path: Path) -> None:
        """Start a two-step plan, complete only step 1, then resume —
        step 2 should be dispatched and the plan should complete."""
        plan = _plan(phases=[
            _phase(phase_id=0, steps=[_step("1.1"), _step("1.2", agent="tester")]),
        ])
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend", status="complete")
        # step 1.2 is still pending — state is on disk.

        launcher = DryRunLauncher()
        s = WorkerSupervisor(team_context_root=tmp_path)
        summary = s.start(plan=plan, launcher=launcher, resume=True)
        assert "completed" in summary.lower() or "complete" in summary.lower()
        # Step 1.2 must have been launched during resume.
        launched_ids = {l["step_id"] for l in launcher.launches}
        assert "1.2" in launched_ids


# ===========================================================================
# ExecutionEngine — recover_dispatched_steps
# ===========================================================================

class TestRecoverDispatchedSteps:
    def test_recover_clears_dispatched_markers(self, tmp_path: Path) -> None:
        """Steps with status='dispatched' are removed so they can be
        re-dispatched after a crash."""
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_plan())
        # Mark the step as dispatched (simulates a crash mid-flight).
        engine.mark_dispatched("1.1", "backend")
        recovered = engine.recover_dispatched_steps()
        assert recovered == 1
        # The step should now be dispatchable again (not in dispatched_step_ids).
        state_path = tmp_path / "execution-state.json"
        data = json.loads(state_path.read_text(encoding="utf-8"))
        dispatched = [r for r in data["step_results"] if r["status"] == "dispatched"]
        assert dispatched == []

    def test_recover_preserves_completed_steps(self, tmp_path: Path) -> None:
        """Completed steps are not touched by recover_dispatched_steps()."""
        plan = _plan(phases=[
            _phase(steps=[_step("1.1"), _step("1.2", agent="tester")]),
        ])
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend", status="complete")
        engine.mark_dispatched("1.2", "tester")
        recovered = engine.recover_dispatched_steps()
        assert recovered == 1
        # Step 1.1 must still be complete.
        state_path = tmp_path / "execution-state.json"
        data = json.loads(state_path.read_text(encoding="utf-8"))
        complete_ids = {r["step_id"] for r in data["step_results"] if r["status"] == "complete"}
        assert "1.1" in complete_ids

    def test_recover_returns_count(self, tmp_path: Path) -> None:
        """Return value equals the number of dispatched steps removed."""
        plan = _plan(phases=[
            _phase(steps=[_step("1.1"), _step("1.2", agent="a2"), _step("1.3", agent="a3")]),
        ])
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend")
        engine.mark_dispatched("1.2", "a2")
        # 1.3 left as pending.
        count = engine.recover_dispatched_steps()
        assert count == 2

    # DECISION: parameterized test_recover_no_state_returns_zero +
    # test_recover_no_dispatched_steps_returns_zero into one test. Both assert
    # recover_dispatched_steps() == 0 and differ only in whether a state file
    # exists. The same boundary (return 0) is exercised in both.
    @pytest.mark.parametrize("create_state", [False, True], ids=["no_state_file", "state_but_nothing_dispatched"])
    def test_recover_returns_zero_when_nothing_to_recover(
        self, tmp_path: Path, create_state: bool
    ) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        if create_state:
            engine.start(_plan())  # creates state file with no dispatched steps
        assert engine.recover_dispatched_steps() == 0


# ===========================================================================
# TaskWorker — DecisionManager integration
# ===========================================================================

class TestWorkerDecisionIntegration:
    def test_auto_approve_for_test_gate(self, tmp_path: Path) -> None:
        """Gate type 'test' is auto-approved without consulting DecisionManager."""
        plan = _plan(phases=[
            _phase(phase_id=0, steps=[_step("1.1")], gate=_gate("test")),
            _phase(phase_id=1, steps=[_step("2.1", agent="tester")]),
        ])
        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            dm = DecisionManager(decisions_dir=tmp_path / "decisions")
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher(), decision_manager=dm)
            summary = await worker.run()
            # No decisions should have been requested.
            assert dm.pending() == []
            assert "completed" in summary.lower() or "complete" in summary.lower()
        asyncio.run(_run())

    def test_review_gate_creates_decision_request(self, tmp_path: Path) -> None:
        """Gate type 'review' with a DecisionManager creates a DecisionRequest
        file on disk and populates pending()."""
        plan = _plan(phases=[
            _phase(phase_id=0, steps=[_step("1.1")], gate=_gate("review")),
            _phase(phase_id=1, steps=[_step("2.1", agent="tester")]),
        ])
        decisions_dir = tmp_path / "decisions"
        dm = DecisionManager(decisions_dir=decisions_dir)

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher(), decision_manager=dm)

            # We need the worker to reach the gate and write the decision
            # request, but then we resolve it so it doesn't hang.
            async def _resolve_and_run():
                # Wait until a pending decision appears.
                for _ in range(50):
                    if dm.pending():
                        break
                    await asyncio.sleep(0.05)
                assert dm.pending(), "DecisionManager should have a pending request"
                req = dm.pending()[0]
                dm.resolve(req.request_id, chosen_option="approve")

            worker_task = asyncio.create_task(worker.run())
            resolver_task = asyncio.create_task(_resolve_and_run())
            await asyncio.gather(resolver_task, worker_task)

        asyncio.run(_run())

    def test_review_gate_polls_for_resolution(self, tmp_path: Path) -> None:
        """Worker blocks on a 'review' gate until the decision is resolved,
        then completes normally."""
        plan = _plan(phases=[
            _phase(phase_id=0, steps=[_step("1.1")], gate=_gate("review")),
            _phase(phase_id=1, steps=[_step("2.1", agent="tester")]),
        ])
        decisions_dir = tmp_path / "decisions"
        dm = DecisionManager(decisions_dir=decisions_dir)

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            launcher = DryRunLauncher()
            worker = TaskWorker(engine=engine, launcher=launcher, decision_manager=dm)

            async def _resolver():
                # Poll until the decision request file appears.
                for _ in range(100):
                    if dm.pending():
                        break
                    await asyncio.sleep(0.02)
                req = dm.pending()[0]
                dm.resolve(req.request_id, chosen_option="approve")

            worker_task = asyncio.create_task(worker.run())
            resolver_task = asyncio.create_task(_resolver())
            results = await asyncio.gather(resolver_task, worker_task)
            summary = results[1]
            assert "completed" in summary.lower() or "complete" in summary.lower()
            # Both phases should have been executed.
            launched_ids = {l["step_id"] for l in launcher.launches}
            assert "1.1" in launched_ids
            assert "2.1" in launched_ids

        asyncio.run(_run())

    def test_review_gate_reject_marks_failed(self, tmp_path: Path) -> None:
        """Resolving a review gate with 'reject' should fail the execution."""
        plan = _plan(phases=[
            _phase(phase_id=0, steps=[_step("1.1")], gate=_gate("review")),
            _phase(phase_id=1, steps=[_step("2.1", agent="tester")]),
        ])
        decisions_dir = tmp_path / "decisions"
        dm = DecisionManager(decisions_dir=decisions_dir)

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            launcher = DryRunLauncher()
            worker = TaskWorker(engine=engine, launcher=launcher, decision_manager=dm)

            async def _reject():
                for _ in range(100):
                    if dm.pending():
                        break
                    await asyncio.sleep(0.02)
                req = dm.pending()[0]
                dm.resolve(req.request_id, chosen_option="reject")

            worker_task = asyncio.create_task(worker.run())
            await asyncio.gather(asyncio.create_task(_reject()), worker_task)
            summary = worker_task.result()
            # Gate failed → execution should report failure.
            assert "failed" in summary.lower()

        asyncio.run(_run())

    def test_no_decision_manager_review_gate_auto_approves(self, tmp_path: Path) -> None:
        """Without a DecisionManager, a 'review' gate falls back to auto-approval."""
        plan = _plan(phases=[
            _phase(phase_id=0, steps=[_step("1.1")], gate=_gate("review")),
            _phase(phase_id=1, steps=[_step("2.1", agent="tester")]),
        ])
        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())  # no DM
            summary = await worker.run()
            assert "completed" in summary.lower() or "complete" in summary.lower()
        asyncio.run(_run())

    def test_feedback_action_routes_through_decision_manager(self, tmp_path: Path) -> None:
        """A FEEDBACK action is routed to the DecisionManager (not busy-looped)
        and, once resolved, dispatches the chosen option's step."""
        from agent_baton.models.execution import FeedbackQuestion

        plan = _plan(phases=[
            _phase(
                phase_id=0,
                steps=[_step("1.1")],
            ),
        ])
        plan.phases[0].feedback_questions = [
            FeedbackQuestion(
                question_id="q1",
                question="Which layout?",
                context="",
                options=["Grid", "List"],
                option_agents=["frontend-engineer", "frontend-engineer"],
                option_prompts=["Build grid for {task}", "Build list for {task}"],
            )
        ]
        decisions_dir = tmp_path / "decisions"
        dm = DecisionManager(decisions_dir=decisions_dir)

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            engine.record_step_result("1.1", "backend", status="complete")
            launcher = DryRunLauncher()
            worker = TaskWorker(engine=engine, launcher=launcher, decision_manager=dm)

            async def _resolver():
                for _ in range(100):
                    if dm.pending():
                        break
                    await asyncio.sleep(0.02)
                assert dm.pending(), "DecisionManager should have a pending feedback request"
                req = dm.pending()[0]
                assert req.decision_type == "feedback_response"
                dm.resolve(req.request_id, chosen_option="0")

            worker_task = asyncio.create_task(worker.run())
            await asyncio.wait_for(
                asyncio.gather(asyncio.create_task(_resolver()), worker_task),
                timeout=10.0,
            )
            summary = worker_task.result()
            assert "completed" in summary.lower() or "complete" in summary.lower()

        asyncio.run(_run())

    def test_feedback_no_decision_manager_picks_first_option(self, tmp_path: Path) -> None:
        """Without a DecisionManager, FEEDBACK auto-selects option 0 instead
        of busy-looping forever."""
        from agent_baton.models.execution import FeedbackQuestion

        plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")])])
        plan.phases[0].feedback_questions = [
            FeedbackQuestion(
                question_id="q1",
                question="Which layout?",
                context="",
                options=["Grid", "List"],
                option_agents=["frontend-engineer", "frontend-engineer"],
                option_prompts=["Build grid for {task}", "Build list for {task}"],
            )
        ]

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            engine.record_step_result("1.1", "backend", status="complete")
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())  # no DM
            summary = await asyncio.wait_for(worker.run(), timeout=10.0)
            assert "completed" in summary.lower() or "complete" in summary.lower()

        asyncio.run(_run())

    def test_interact_stale_resolution_is_not_replayed_on_next_turn(
        self, tmp_path: Path,
    ) -> None:
        """One resolved interact answer must resume exactly one turn.

        Regression (phase 2 review): the interact decision ID was keyed on
        ``(task, step)`` only, so on turn 2 the worker found turn 1's
        already-resolved decision under the same ID and re-applied the same
        input on every subsequent turn without ever asking the human again.
        """
        from unittest.mock import MagicMock

        dm = DecisionManager(decisions_dir=tmp_path / "decisions")
        engine = MagicMock()
        engine.status.return_value = {"task_id": "task-i"}
        shutdown = asyncio.Event()
        shutdown.set()  # let _handle_interact return instead of polling
        worker = TaskWorker(
            engine=engine, launcher=DryRunLauncher(),
            decision_manager=dm, shutdown_event=shutdown,
            gate_poll_interval=0.01,
        )

        def _action(turn: int):
            a = MagicMock()
            a.interact_step_id = "1.1"
            a.interact_turn = turn
            a.message = "agent asks a question"
            return a

        async def _run():
            # Turn 1: a pending decision is recorded (shutdown set → returns).
            await worker._handle_interact(_action(1))
            pending = [r for r in dm.pending() if r.task_id == "task-i"]
            assert len(pending) == 1
            turn1_id = pending[0].request_id
            dm.resolve(turn1_id, chosen_option="reply", rationale="answer-one")

            # Turn 1 re-entry (e.g. after restart): applies the answer once.
            await worker._handle_interact(_action(1))
            engine.provide_interact_input.assert_called_once_with(
                step_id="1.1", input_text="answer-one",
            )

            # Turn 2: the stale turn-1 resolution must NOT be replayed —
            # a fresh pending decision must be recorded instead.
            await worker._handle_interact(_action(2))
            engine.provide_interact_input.assert_called_once()
            pending2 = [r for r in dm.pending() if r.task_id == "task-i"]
            assert len(pending2) == 1
            assert pending2[0].request_id != turn1_id

        asyncio.run(_run())

    def test_interact_no_decision_manager_completes_immediately(self, tmp_path: Path) -> None:
        """Without a DecisionManager, INTERACT finalizes via
        complete_interaction() instead of busy-looping forever."""
        plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")])])

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            state = engine._load_execution()
            state.step_results.append(
                __import__("agent_baton.models.execution", fromlist=["StepResult"]).StepResult(
                    step_id="1.1", agent_name="backend", status="interacting",
                )
            )
            engine._save_execution(state)

            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())  # no DM
            summary = await asyncio.wait_for(worker.run(), timeout=10.0)
            assert "completed" in summary.lower() or "complete" in summary.lower()

        asyncio.run(_run())


# ===========================================================================
# WorkerSupervisor / daemon.py — persistent decision routing (mandatory)
#
# Regression coverage for: "Ensure daemon and supervisor always receive a
# persistent DecisionManager when human-required actions are possible,
# never auto-approve merely because dependency injection was omitted."
# ===========================================================================

class TestSupervisorAlwaysInjectsDecisionManager:
    def test_review_gate_is_not_auto_approved_by_bare_supervisor_start(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """WorkerSupervisor.start() must inject its own persistent
        DecisionManager -- callers that don't pass one explicitly must not
        get TaskWorker's "no decision manager configured" auto-approve
        fallback for a human-required gate."""
        import threading

        # signal.set_wakeup_fd() (used by SignalHandler.install()) only
        # works on the main thread; this test drives supervisor.start() from
        # a worker thread so it can poll for the pending decision while the
        # supervisor's own asyncio.run() blocks, so the signal handling
        # (irrelevant to what's under test here) is stubbed out.
        monkeypatch.setattr(
            "agent_baton.core.runtime.signals.SignalHandler.install", lambda self: None,
        )
        monkeypatch.setattr(
            "agent_baton.core.runtime.signals.SignalHandler.uninstall", lambda self: None,
        )

        plan = _plan(phases=[
            _phase(phase_id=0, steps=[_step("1.1")], gate=_gate("review")),
            _phase(phase_id=1, steps=[_step("2.1", agent="tester")]),
        ])
        s = WorkerSupervisor(team_context_root=tmp_path)
        summary_box: dict = {}

        def _run_supervisor():
            summary_box["summary"] = s.start(plan=plan, launcher=DryRunLauncher())

        thread = threading.Thread(target=_run_supervisor, daemon=True)
        thread.start()

        decisions_dir = tmp_path / "decisions"
        dm = DecisionManager(decisions_dir=decisions_dir)
        for _ in range(200):
            if dm.pending():
                break
            __import__("time").sleep(0.05)
        pending = dm.pending()
        assert pending, (
            "Expected a durable pending decision under "
            f"{decisions_dir} -- the gate must not have been auto-approved."
        )
        assert pending[0].decision_type == "gate_approval"
        dm.resolve(pending[0].request_id, chosen_option="approve")

        thread.join(timeout=10.0)
        assert not thread.is_alive(), "supervisor.start() did not finish after resolution"
        assert "completed" in summary_box.get("summary", "").lower() or \
            "complete" in summary_box.get("summary", "").lower()

    def test_run_daemon_with_api_injects_decision_manager_into_worker(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """_run_daemon_with_api must build TaskWorker with a non-None
        DecisionManager rather than leaving dependency injection to chance."""
        import agent_baton.cli.commands.execution.daemon as daemon_mod

        captured: dict = {}
        real_task_worker = TaskWorker

        class _CapturingTaskWorker(real_task_worker):
            def __init__(self, *args, **kwargs):
                captured["decision_manager"] = kwargs.get("decision_manager")
                super().__init__(*args, **kwargs)

            async def run(self):  # type: ignore[override]
                return "completed (stub)"

        monkeypatch.setattr(
            "agent_baton.core.runtime.worker.TaskWorker", _CapturingTaskWorker,
        )

        class _StubServer:
            def __init__(self, *a, **k):
                self.should_exit = False

            async def serve(self):
                await asyncio.sleep(3600)

        monkeypatch.setattr(
            "uvicorn.Server", lambda config: _StubServer(),
        )
        monkeypatch.setattr("uvicorn.Config", lambda *a, **k: object())

        summary = asyncio.run(daemon_mod._run_daemon_with_api(
            plan=_plan(),
            launcher=DryRunLauncher(),
            supervisor=WorkerSupervisor(team_context_root=tmp_path),
            max_parallel=1,
            resume=False,
            host="127.0.0.1",
            port=0,
            token=None,
            team_context_root=tmp_path,
        ))
        assert "completed" in summary.lower()
        assert captured.get("decision_manager") is not None


# ===========================================================================
# Shared execution lifecycle contract
#
# Characterization tests for docs/internal/execution-runtime-contract.md:
#   §6 (pause-and-resume contract) and §8 (state-transition test matrix,
#   stage 5 "Pause" and stage 8 "Complete" cross-surface equivalence).
# These pin down the CURRENT contract, including the documented gap that
# process-level pause does not (yet) have an engine-owned status value.
# ===========================================================================

class TestSharedLifecycleContract:
    def test_pause_does_not_mutate_persisted_status(self, tmp_path: Path) -> None:
        """SIGSTOP/SIGCONT via WorkerSupervisor.pause_worker/resume_worker
        operate purely at the OS process level today — per contract §6 they
        must not change ExecutionState.status (there is no engine-owned
        'paused' transition yet; durability comes only from the fact that
        state was already saved at the last completed engine call)."""
        import subprocess
        import time

        task_id = "pause-contract-task"

        engine = ExecutionEngine(team_context_root=tmp_path, task_id=task_id)
        engine.start(_plan(task_id=task_id))
        status_before = engine.status().get("status")
        assert status_before == "running"

        # A real, unrelated long-lived subprocess stands in for the daemon
        # worker process so real SIGSTOP/SIGCONT can be sent without
        # touching the test process itself.
        proc = subprocess.Popen(["sleep", "5"])
        try:
            pid_dir = tmp_path / "executions" / task_id
            pid_dir.mkdir(parents=True, exist_ok=True)
            (pid_dir / "worker.pid").write_text(str(proc.pid))

            s = WorkerSupervisor(team_context_root=tmp_path, task_id=task_id)
            paused_pid = s.pause_worker(task_id)
            assert paused_pid == proc.pid

            if sys.platform.startswith("linux"):
                for _ in range(20):
                    state_line = next(
                        line for line in Path(f"/proc/{proc.pid}/status").read_text().splitlines()
                        if line.startswith("State:")
                    )
                    if state_line.split()[1] == "T":
                        break
                    time.sleep(0.05)
                else:
                    pytest.fail("worker process never entered SIGSTOP state 'T'")

            # The persisted execution status is untouched by the OS-level pause.
            assert engine.status().get("status") == status_before == "running"

            resumed_pid = s.resume_worker(task_id)
            assert resumed_pid == proc.pid
        finally:
            proc.terminate()
            proc.wait(timeout=5)

        # And still untouched after resume.
        assert engine.status().get("status") == "running"

    def test_worker_and_direct_engine_calls_reach_equivalent_terminal_state(
        self, tmp_path: Path
    ) -> None:
        """Driving the same plan (a) directly via ExecutionEngine calls (the
        shape every CLI action-loop command performs one at a time) and
        (b) via the async TaskWorker (the shape the daemon and BatonRunner
        use) must persist an equivalent terminal ExecutionState — same
        status, same set of completed step ids — because both surfaces are
        required to drive the same ExecutionDriver methods (contract §3)."""
        plan = _plan(phases=[
            _phase(phase_id=0, steps=[_step("1.1"), _step("1.2", agent="tester")]),
        ])

        # (a) CLI-action-loop shape: one direct engine call per stage.
        direct_engine = ExecutionEngine(team_context_root=tmp_path / "direct")
        action = direct_engine.start(plan)
        assert action.action_type.value == "dispatch"
        direct_engine.mark_dispatched("1.1", "backend")
        direct_engine.record_step_result("1.1", "backend", status="complete")
        direct_engine.mark_dispatched("1.2", "tester")
        direct_engine.record_step_result("1.2", "tester", status="complete")
        final_action = direct_engine.next_action()
        assert final_action.action_type.value == "complete"
        direct_engine.complete()
        direct_state = direct_engine._load_execution()
        assert direct_state is not None

        # (b) Daemon/TaskWorker shape: async loop drives the same plan.
        worker_engine = ExecutionEngine(team_context_root=tmp_path / "worker")
        worker_engine.start(plan)
        worker = TaskWorker(engine=worker_engine, launcher=DryRunLauncher())
        asyncio.run(worker.run())
        worker_state = worker_engine._load_execution()
        assert worker_state is not None

        assert direct_state.status == worker_state.status == "complete"
        direct_complete_ids = {r.step_id for r in direct_state.step_results if r.status == "complete"}
        worker_complete_ids = {r.step_id for r in worker_state.step_results if r.status == "complete"}
        assert direct_complete_ids == worker_complete_ids == {"1.1", "1.2"}


# ===========================================================================
# Daemon restart while a decision is pending
#
# docs/internal/execution-runtime-contract.md §5 (restart semantics) + §7.1
# (no duplicate-call guard on record_step_result -- the mid-dispatch gap)
# describe a worker crash as the one blind spot in "state is durable at
# every transition boundary": a step left `dispatched` on a crash is stuck
# until an operator (or WorkerSupervisor.start(resume=True)) clears it. This
# test drives a real crash-and-restart across a human-required gate: the
# first worker creates the durable decision request and then "dies" (its
# asyncio task is cancelled) before the decision is resolved; a brand-new
# engine + worker pair -- exactly what WorkerSupervisor.start(resume=True)
# constructs -- must resume without duplicating the pending decision, and
# the task must reach COMPLETE exactly once after the (single) decision is
# eventually resolved.
# ===========================================================================

class TestDaemonRestartWithPendingDecision:
    def test_worker_crash_while_gate_pending_then_restart_completes_once(
        self, tmp_path: Path,
    ) -> None:
        from agent_baton.core.events.bus import EventBus

        task_id = "restart-pending-decision-task"
        plan = _plan(
            task_id=task_id,
            phases=[
                _phase(phase_id=0, steps=[_step("1.1")], gate=_gate("review")),
                _phase(phase_id=1, steps=[_step("2.1", agent="tester")]),
            ],
        )
        decisions_dir = tmp_path / "decisions"
        bus = EventBus()
        dm = DecisionManager(decisions_dir=decisions_dir, bus=bus)

        needed_events: list[dict] = []
        bus.subscribe(
            "human.decision_needed",
            lambda event: needed_events.append(event.payload),
        )

        async def _run() -> str:
            # --- "Crash" worker: reaches the review gate, records the
            # durable decision request, then the process dies (the
            # asyncio task is cancelled) before anyone resolves it. ---
            engine1 = ExecutionEngine(team_context_root=tmp_path, task_id=task_id)
            engine1.start(plan)
            worker1 = TaskWorker(
                engine=engine1, launcher=DryRunLauncher(), decision_manager=dm, bus=bus,
            )
            worker1_task = asyncio.create_task(worker1.run())
            for _ in range(100):
                if dm.pending():
                    break
                await asyncio.sleep(0.02)
            assert dm.pending(), "worker1 should have created a pending gate decision"
            pending_before_crash = dm.pending()[0]

            worker1_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker1_task

            # The crash must not have silently mutated state: the task is
            # still waiting on the gate, not failed/complete.
            crashed_status = engine1.status().get("status")
            assert crashed_status not in ("complete", "failed", "cancelled"), crashed_status
            # Exactly one decision request exists so far -- crashing did
            # not spawn a second one.
            assert len(dm.list_all()) == 1
            assert len(needed_events) == 1

            # --- "Restart": WorkerSupervisor.start(resume=True) would
            # build exactly this pair -- a fresh engine that resumes from
            # disk and clears any stuck `dispatched` steps, plus a fresh
            # worker sharing the SAME DecisionManager (same decisions_dir
            # on disk -- the durable queue every surface reads). ---
            engine2 = ExecutionEngine(team_context_root=tmp_path, task_id=task_id)
            engine2.resume()
            engine2.recover_dispatched_steps()
            launcher2 = DryRunLauncher()
            worker2 = TaskWorker(
                engine=engine2, launcher=launcher2, decision_manager=dm, bus=bus,
            )

            async def _resolve_after_reentry() -> None:
                # The restarted worker re-enters the same gate. The
                # deterministic request_id must make it reuse the SAME
                # pending request rather than mint a duplicate.
                for _ in range(100):
                    if dm.pending():
                        break
                    await asyncio.sleep(0.02)
                assert len(dm.pending()) == 1, "restart must not duplicate the pending decision"
                assert dm.pending()[0].request_id == pending_before_crash.request_id
                dm.resolve(dm.pending()[0].request_id, chosen_option="approve")

            resolver_task = asyncio.create_task(_resolve_after_reentry())
            results = await asyncio.gather(resolver_task, worker2.run())
            return results[1]

        summary = asyncio.run(_run())
        assert "complete" in summary.lower()

        # Exactly one decision request/resolution ever existed for this
        # gate across the crash + restart -- no duplicate -- and the
        # human_decision_needed event fired exactly once (the restarted
        # worker found the pending request already on disk and did not
        # re-publish it).
        all_decisions = dm.list_all()
        assert len(all_decisions) == 1
        assert all_decisions[0].status == "resolved"
        assert len(needed_events) == 1

        final_status = ExecutionEngine(
            team_context_root=tmp_path, task_id=task_id,
        ).status()
        assert final_status.get("status") == "complete"


# ===========================================================================
# TaskWorker — shutdown_event
# ===========================================================================

class TestWorkerShutdownEvent:
    def test_worker_exits_on_shutdown_event_set_before_start(self, tmp_path: Path) -> None:
        """If shutdown_event is already set when run() is called, the worker
        returns immediately without dispatching anything."""
        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(_plan())
            shutdown = asyncio.Event()
            shutdown.set()  # pre-set before run()
            launcher = DryRunLauncher()
            worker = TaskWorker(engine=engine, launcher=launcher, shutdown_event=shutdown)
            summary = await worker.run()
            assert "shutdown" in summary.lower()
            assert launcher.launches == []
        asyncio.run(_run())

    def test_shutdown_event_set_during_gate_aborts_gate(self, tmp_path: Path) -> None:
        """Setting shutdown_event while a 'review' gate is polling causes the
        gate to be recorded as failed and the worker to exit."""
        plan = _plan(phases=[
            _phase(phase_id=0, steps=[_step("1.1")], gate=_gate("review")),
            _phase(phase_id=1, steps=[_step("2.1", agent="tester")]),
        ])
        decisions_dir = tmp_path / "decisions"
        dm = DecisionManager(decisions_dir=decisions_dir)

        async def _run():
            shutdown = asyncio.Event()
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            worker = TaskWorker(
                engine=engine,
                launcher=DryRunLauncher(),
                decision_manager=dm,
                shutdown_event=shutdown,
            )

            async def _set_shutdown():
                # Wait until the decision request is created (gate is polling).
                for _ in range(100):
                    if dm.pending():
                        break
                    await asyncio.sleep(0.02)
                shutdown.set()

            worker_task = asyncio.create_task(worker.run())
            await asyncio.gather(asyncio.create_task(_set_shutdown()), worker_task)
            summary = worker_task.result()
            # After shutdown the gate should be recorded as failed (aborted).
            state_path = tmp_path / "execution-state.json"
            data = json.loads(state_path.read_text(encoding="utf-8"))
            gate_results = data.get("gate_results", [])
            assert gate_results, "Gate result should have been recorded"
            assert gate_results[-1]["passed"] is False

        asyncio.run(_run())

    # DECISION: removed test_worker_without_shutdown_event_runs_normally — it only
    # asserts the absence of a shutdown_event does not crash. This is already
    # exercised by test_single_step_completes and every other test that constructs
    # a TaskWorker without the shutdown_event argument.


# ===========================================================================
# daemonize() function
# ===========================================================================

class TestDaemonizeFunction:
    # test_windows_raises_runtime_error runs on all platforms (it monkeypatches sys.platform).
    def test_windows_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Windows, daemonize() raises RuntimeError immediately."""
        from agent_baton.core.runtime import daemon
        monkeypatch.setattr(daemon.sys, "platform", "win32")
        with pytest.raises(RuntimeError, match="POSIX"):
            daemon.daemonize()

    # DECISION: merged test_calls_fork_twice + test_calls_setsid + (implicitly)
    # test_calls_dup2 into one comprehensive test that checks all three OS-level
    # syscalls with a single monkeypatched daemonize() invocation. Each was
    # patching the same functions and running the same setup; splitting them
    # produced three identical mock setups with one different assertion each.
    @pytest.mark.skipif(sys.platform == "win32", reason="os.fork/os.setsid not available on Windows")
    def test_daemonize_calls_fork_twice_setsid_once_and_dup2(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """daemonize() calls os.fork() exactly twice, os.setsid() once, and
        os.dup2() to redirect stdio to /dev/null."""
        import agent_baton.core.runtime.daemon as daemon_mod

        fork_calls: list[int] = []
        setsid_calls: list[int] = []
        dup2_calls: list[tuple] = []

        def fake_fork() -> int:
            fork_calls.append(1)
            return 0  # always child to avoid os._exit()

        monkeypatch.setattr(daemon_mod.sys, "platform", "linux")
        monkeypatch.setattr(daemon_mod.os, "fork", fake_fork)
        monkeypatch.setattr(daemon_mod.os, "setsid", lambda: setsid_calls.append(1))
        monkeypatch.setattr(daemon_mod.os, "open", lambda *a, **kw: 0)
        monkeypatch.setattr(daemon_mod.os, "dup2", lambda fd, target: dup2_calls.append((fd, target)))
        monkeypatch.setattr(daemon_mod.os, "close", lambda *a: None)
        monkeypatch.setattr(daemon_mod.sys.stdout, "flush", lambda: None)
        monkeypatch.setattr(daemon_mod.sys.stderr, "flush", lambda: None)

        daemon_mod.daemonize()

        assert len(fork_calls) == 2, f"Expected 2 fork calls, got {len(fork_calls)}"
        assert len(setsid_calls) == 1, f"Expected 1 setsid call, got {len(setsid_calls)}"
        # dup2 must have been called to redirect at least stdout (fd 1) and stderr (fd 2)
        redirected_fds = {target for _, target in dup2_calls}
        assert {1, 2}.issubset(redirected_fds), (
            f"dup2 must redirect fd 1 and fd 2; got targets: {redirected_fds}"
        )

    @pytest.mark.skipif(sys.platform == "win32", reason="os.fork not available on Windows")
    def test_first_fork_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the first os.fork() raises OSError, daemonize() re-raises as RuntimeError."""
        import agent_baton.core.runtime.daemon as daemon_mod

        call_count = 0

        def failing_fork() -> int:
            nonlocal call_count
            call_count += 1
            raise OSError("fork failed")

        monkeypatch.setattr(daemon_mod.sys, "platform", "linux")
        monkeypatch.setattr(daemon_mod.os, "fork", failing_fork)

        with pytest.raises(RuntimeError, match="First fork failed"):
            daemon_mod.daemonize()

    @pytest.mark.skipif(sys.platform == "win32", reason="os.fork not available on Windows")
    def test_second_fork_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the second os.fork() raises OSError, daemonize() re-raises as RuntimeError."""
        import agent_baton.core.runtime.daemon as daemon_mod

        fork_calls = [0]

        def failing_second_fork() -> int:
            fork_calls[0] += 1
            if fork_calls[0] == 1:
                return 0  # first fork: child
            raise OSError("second fork failed")

        monkeypatch.setattr(daemon_mod.sys, "platform", "linux")
        monkeypatch.setattr(daemon_mod.os, "fork", failing_second_fork)
        monkeypatch.setattr(daemon_mod.os, "setsid", lambda: None)

        with pytest.raises(RuntimeError, match="Second fork failed"):
            daemon_mod.daemonize()


# ===========================================================================
# TODO-4: daemon CLI handler wraps supervisor.start() RuntimeError cleanly
# ===========================================================================

class TestDaemonHandlerRuntimeErrorIsCaught:
    """TODO-4: TOCTOU race — supervisor.start() can raise RuntimeError when
    another daemon acquires the lock between the PID-probe check and the flock.
    The handler must catch that error and print a clean message rather than
    letting the traceback propagate to the user.
    """

    def _make_args(self, tmp_path: Path, resume: bool = False, plan: str | None = None) -> argparse.Namespace:
        """Build a minimal Namespace that the daemon handler's 'start' branch expects."""
        import argparse as _ap
        return _ap.Namespace(
            daemon_action="start",
            plan=plan,
            resume=resume,
            dry_run=True,
            foreground=True,
            project_dir=None,
            serve=False,
            host="127.0.0.1",
            port=8741,
            token=None,
            task_id=None,
            max_parallel=1,
        )

    def test_runtime_error_from_supervisor_prints_clean_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """When supervisor.start() raises RuntimeError the handler prints
        'Error: ...' and returns rather than propagating the exception."""
        import agent_baton.cli.commands.execution.daemon as daemon_mod

        def boom(*args, **kwargs):
            raise RuntimeError("Another daemon is already running")

        monkeypatch.setattr(daemon_mod.WorkerSupervisor, "start", boom)

        # Provide a real plan file so we get past the plan-loading step.
        import json
        from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
        plan = MachinePlan(
            task_id="t1",
            task_summary="test",
            phases=[PlanPhase(phase_id=0, name="P", steps=[
                PlanStep(step_id="1.1", agent_name="be", task_description="do")
            ])],
        )
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan.to_dict()), encoding="utf-8")

        args = self._make_args(tmp_path, plan=str(plan_file))
        # Must not raise.
        daemon_mod.handler(args)

        captured = capsys.readouterr()
        assert "Error:" in captured.out
        assert "Already" in captured.out or "already running" in captured.out.lower()


# ===========================================================================
# TODO-6: --plan is optional when --resume is set
# ===========================================================================

class TestDaemonPlanOptionalWithResume:
    """TODO-6: --plan must be optional when --resume is set.

    Before the fix, --plan was required=True on the 'start' subparser so
    users had to provide a dummy --plan argument when resuming.

    After the fix, --plan defaults to None and the handler checks
    ``not args.resume and not args.plan`` to produce the error only when
    both are absent.
    """

    def test_plan_argument_not_required(self) -> None:
        """Argparse must accept 'baton daemon start --resume' without --plan."""
        from agent_baton.cli.commands.execution.daemon import register
        import argparse as _ap

        root = _ap.ArgumentParser()
        subs = root.add_subparsers(dest="cmd")
        register(subs)

        # Should parse without error — no --plan provided, --resume present.
        args = root.parse_args(["daemon", "start", "--resume"])
        assert args.plan is None
        assert args.resume is True

    def test_plan_and_resume_both_absent_triggers_error(
        self, capsys
    ) -> None:
        """Without --plan and without --resume the handler must print an error
        and return rather than crashing."""
        import agent_baton.cli.commands.execution.daemon as daemon_mod
        import argparse as _ap

        args = _ap.Namespace(
            daemon_action="start",
            plan=None,
            resume=False,
            dry_run=True,
            foreground=True,
            project_dir=None,
            serve=False,
            host="127.0.0.1",
            port=8741,
            token=None,
            task_id=None,
            max_parallel=1,
        )
        # Must not raise — just print the error message.
        daemon_mod.handler(args)

        captured = capsys.readouterr()
        assert "--plan is required" in captured.out or "--plan" in captured.out

    def test_resume_without_plan_does_not_trigger_plan_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """--resume without --plan must not print the '--plan is required' error."""
        import agent_baton.cli.commands.execution.daemon as daemon_mod
        import argparse as _ap

        # Stub supervisor.start so we don't actually run execution.
        monkeypatch.setattr(daemon_mod.WorkerSupervisor, "start", lambda *a, **kw: "resumed ok")

        args = _ap.Namespace(
            daemon_action="start",
            plan=None,
            resume=True,
            dry_run=True,
            foreground=True,
            project_dir=None,
            serve=False,
            host="127.0.0.1",
            port=8741,
            token=None,
            task_id=None,
            max_parallel=1,
        )
        daemon_mod.handler(args)

        captured = capsys.readouterr()
        # The plan-required error must NOT appear.
        assert "--plan is required" not in captured.out


# ===========================================================================
# E2E smoke tests — subprocess invocation of "baton daemon start"
#
# These tests exercise the full CLI entry point through the OS process
# boundary.  They use --dry-run so no real Claude calls are made and
# --foreground so the process stays attached to the test process and
# terminates after the plan completes.
# ===========================================================================


def _write_smoke_plan(tmp_path: Path, task_id: str = "e2e-smoke") -> Path:
    """Write a minimal MachinePlan JSON and return its path."""
    plan = MachinePlan(
        task_id=task_id,
        task_summary="E2E smoke test plan",
        phases=[
            PlanPhase(
                phase_id=0,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer--python",
                        task_description="Implement smoke feature",
                    )
                ],
                gate=PlanGate(gate_type="test", command="echo ok"),
            )
        ],
    )
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    return plan_file


def _run_daemon_e2e(
    plan_file: Path,
    project_dir: Path,
    extra_args: list[str] | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess:
    """Invoke ``baton daemon start`` in foreground dry-run mode via subprocess."""
    cmd = [
        sys.executable, "-m", "agent_baton.cli.main",
        "daemon", "start",
        "--plan", str(plan_file),
        "--dry-run",
        "--foreground",
        "--project-dir", str(project_dir),
    ]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


class TestDaemonE2ESmokeTests:
    """End-to-end subprocess smoke tests for ``baton daemon start``."""

    def test_exits_cleanly(self, tmp_path: Path) -> None:
        """The daemon must exit with return code 0 when using DryRunLauncher."""
        plan_file = _write_smoke_plan(tmp_path, task_id="e2e-clean")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        result = _run_daemon_e2e(plan_file, project_dir)
        assert result.returncode == 0, (
            f"Daemon exited with code {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_prints_startup_message_with_task_id(self, tmp_path: Path) -> None:
        """Daemon must emit a startup message that includes the task ID."""
        plan_file = _write_smoke_plan(tmp_path, task_id="e2e-task-id-check")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        result = _run_daemon_e2e(plan_file, project_dir)
        combined = result.stdout + result.stderr
        assert "e2e-task-id-check" in combined, (
            f"Expected task ID in output.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_completes_within_timeout(self, tmp_path: Path) -> None:
        """DryRunLauncher finishes instantly — the daemon must not hang."""
        plan_file = _write_smoke_plan(tmp_path, task_id="e2e-timeout-check")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        # If this raises subprocess.TimeoutExpired the daemon hung.
        _run_daemon_e2e(plan_file, project_dir, timeout=30.0)

    def test_missing_plan_flag_prints_error_and_exits(self, tmp_path: Path) -> None:
        """Invoking daemon start without --plan must print an error and exit."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        cmd = [
            sys.executable, "-m", "agent_baton.cli.main",
            "daemon", "start",
            "--dry-run",
            "--foreground",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10.0)
        combined = result.stdout + result.stderr
        assert "plan" in combined.lower() or "required" in combined.lower(), (
            f"Expected error about missing --plan.\n{combined}"
        )

    def test_nonexistent_plan_file_prints_error(self, tmp_path: Path) -> None:
        """A plan file path that does not exist must produce a clear error message."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        cmd = [
            sys.executable, "-m", "agent_baton.cli.main",
            "daemon", "start",
            "--plan", "/tmp/nonexistent-e2e-plan-xyz.json",
            "--dry-run",
            "--foreground",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10.0)
        combined = result.stdout + result.stderr
        assert "not found" in combined.lower() or "error" in combined.lower(), (
            f"Expected 'not found' error.\n{combined}"
        )

    def test_multi_phase_plan_completes(self, tmp_path: Path) -> None:
        """A plan with two phases (including a gate) must also complete cleanly."""
        plan = MachinePlan(
            task_id="e2e-multi-phase",
            task_summary="Multi-phase E2E smoke",
            phases=[
                PlanPhase(
                    phase_id=0,
                    name="Phase 1",
                    steps=[PlanStep(step_id="1.1", agent_name="backend", task_description="step one")],
                    gate=PlanGate(gate_type="test", command="echo ok"),
                ),
                PlanPhase(
                    phase_id=1,
                    name="Phase 2",
                    steps=[PlanStep(step_id="2.1", agent_name="tester", task_description="step two")],
                ),
            ],
        )
        plan_file = tmp_path / "multi.json"
        plan_file.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        result = _run_daemon_e2e(plan_file, project_dir)
        assert result.returncode == 0, (
            f"Multi-phase E2E failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
