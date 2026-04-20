"""Tests for WorkerSupervisor (daemon mode) and SignalHandler.

Also includes E2E subprocess smoke tests for:
  baton daemon start --dry-run --foreground --plan <file>
"""
from __future__ import annotations

import argparse
import asyncio
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
