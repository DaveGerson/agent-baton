"""Tests for WorkerSupervisor parallel/namespaced execution behaviour."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.core.runtime.launcher import DryRunLauncher
from agent_baton.core.runtime.supervisor import WorkerSupervisor
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _step(step_id: str = "1.1", agent: str = "backend") -> PlanStep:
    return PlanStep(step_id=step_id, agent_name=agent, task_description="task")


def _phase(phase_id: int = 0, steps: list[PlanStep] | None = None) -> PlanPhase:
    return PlanPhase(phase_id=phase_id, name="P", steps=steps or [_step()])


def _plan(task_id: str = "t1") -> MachinePlan:
    return MachinePlan(task_id=task_id, task_summary="test", phases=[_phase()])


# ===========================================================================
# Path namespacing
# ===========================================================================

class TestSupervisorPathNamespacing:
    def test_with_task_id_pid_path_is_under_executions_dir(
        self, tmp_path: Path
    ) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path, task_id="my-task")
        assert s.pid_path == tmp_path / "executions" / "my-task" / "worker.pid"

    def test_with_task_id_log_path_is_under_executions_dir(
        self, tmp_path: Path
    ) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path, task_id="my-task")
        assert s.log_path == tmp_path / "executions" / "my-task" / "worker.log"

    def test_with_task_id_status_path_is_under_executions_dir(
        self, tmp_path: Path
    ) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path, task_id="my-task")
        assert s.status_path == (
            tmp_path / "executions" / "my-task" / "worker-status.json"
        )

    def test_without_task_id_pid_path_is_legacy_flat(self, tmp_path: Path) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path)
        assert s.pid_path == tmp_path / "daemon.pid"

    def test_without_task_id_log_path_is_legacy_flat(self, tmp_path: Path) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path)
        assert s.log_path == tmp_path / "daemon.log"

    def test_without_task_id_status_path_is_legacy_flat(self, tmp_path: Path) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path)
        assert s.status_path == tmp_path / "daemon-status.json"

    def test_different_task_ids_produce_different_paths(
        self, tmp_path: Path
    ) -> None:
        s1 = WorkerSupervisor(team_context_root=tmp_path, task_id="task-one")
        s2 = WorkerSupervisor(team_context_root=tmp_path, task_id="task-two")
        assert s1.pid_path != s2.pid_path
        assert s1.log_path != s2.log_path
        assert s1.status_path != s2.status_path


# ===========================================================================
# start() creates files in the namespaced directory
# ===========================================================================

class TestSupervisorStartNamespaced:
    def test_start_with_task_id_creates_pid_in_executions_dir(
        self, tmp_path: Path
    ) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path, task_id="exec-001")
        s.start(plan=_plan(), launcher=DryRunLauncher())
        # After clean exit the PID file is removed; verify status file is in
        # the right directory.
        assert s.status_path.parent == tmp_path / "executions" / "exec-001"

    def test_start_with_task_id_writes_log_in_executions_dir(
        self, tmp_path: Path
    ) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path, task_id="exec-002")
        s.start(plan=_plan(), launcher=DryRunLauncher())
        assert s.log_path.parent == tmp_path / "executions" / "exec-002"
        assert s.log_path.exists()

    def test_start_with_task_id_writes_status_json(self, tmp_path: Path) -> None:
        s = WorkerSupervisor(team_context_root=tmp_path, task_id="exec-003")
        s.start(plan=_plan(), launcher=DryRunLauncher())
        assert s.status_path.exists()

    def test_two_supervisors_with_different_task_ids_do_not_share_files(
        self, tmp_path: Path
    ) -> None:
        s1 = WorkerSupervisor(team_context_root=tmp_path, task_id="exec-a")
        s2 = WorkerSupervisor(team_context_root=tmp_path, task_id="exec-b")
        s1.start(plan=_plan(task_id="task-a"), launcher=DryRunLauncher())
        s2.start(plan=_plan(task_id="task-b"), launcher=DryRunLauncher())
        # Both status files must exist and live in separate directories.
        assert s1.status_path.exists()
        assert s2.status_path.exists()
        assert s1.status_path != s2.status_path


# ===========================================================================
# list_workers — static method
# ===========================================================================

class TestListWorkers:
    def test_empty_dir_returns_empty_list(self, tmp_path: Path) -> None:
        assert WorkerSupervisor.list_workers(tmp_path) == []

    def test_missing_executions_dir_returns_empty_list(self, tmp_path: Path) -> None:
        non_existent = tmp_path / "no-such-dir"
        assert WorkerSupervisor.list_workers(non_existent) == []

    def test_no_pid_files_returns_empty_list(self, tmp_path: Path) -> None:
        # Create execution directories without any PID files.
        (tmp_path / "executions" / "orphan").mkdir(parents=True)
        assert WorkerSupervisor.list_workers(tmp_path) == []

    def test_finds_worker_with_valid_pid_file(self, tmp_path: Path) -> None:
        pid_dir = tmp_path / "executions" / "task-001"
        pid_dir.mkdir(parents=True)
        (pid_dir / "worker.pid").write_text(str(os.getpid()))

        workers = WorkerSupervisor.list_workers(tmp_path)
        assert len(workers) == 1
        assert workers[0]["task_id"] == "task-001"
        assert workers[0]["pid"] == os.getpid()

    def test_current_process_pid_is_alive(self, tmp_path: Path) -> None:
        pid_dir = tmp_path / "executions" / "running-task"
        pid_dir.mkdir(parents=True)
        (pid_dir / "worker.pid").write_text(str(os.getpid()))

        workers = WorkerSupervisor.list_workers(tmp_path)
        assert workers[0]["alive"] is True

    def test_dead_pid_is_marked_not_alive(self, tmp_path: Path) -> None:
        """A PID that doesn't correspond to a running process is reported
        as alive=False.  PID 999999999 is almost certainly not running."""
        dead_pid = 999999999
        pid_dir = tmp_path / "executions" / "dead-task"
        pid_dir.mkdir(parents=True)
        (pid_dir / "worker.pid").write_text(str(dead_pid))

        workers = WorkerSupervisor.list_workers(tmp_path)
        assert len(workers) == 1
        assert workers[0]["alive"] is False
        assert workers[0]["pid"] == dead_pid

    def test_finds_multiple_workers_across_execution_dirs(
        self, tmp_path: Path
    ) -> None:
        for i, task_id in enumerate(("alpha", "beta", "gamma")):
            pid_dir = tmp_path / "executions" / task_id
            pid_dir.mkdir(parents=True)
            (pid_dir / "worker.pid").write_text(str(os.getpid() + i))

        workers = WorkerSupervisor.list_workers(tmp_path)
        task_ids = {w["task_id"] for w in workers}
        assert task_ids == {"alpha", "beta", "gamma"}

    def test_includes_legacy_daemon_pid_when_present(self, tmp_path: Path) -> None:
        (tmp_path / "daemon.pid").write_text(str(os.getpid()))

        workers = WorkerSupervisor.list_workers(tmp_path)
        legacy = [w for w in workers if w["task_id"] == "(legacy)"]
        assert len(legacy) == 1
        assert legacy[0]["pid"] == os.getpid()

    def test_result_dict_has_required_keys(self, tmp_path: Path) -> None:
        pid_dir = tmp_path / "executions" / "task-xyz"
        pid_dir.mkdir(parents=True)
        (pid_dir / "worker.pid").write_text(str(os.getpid()))

        workers = WorkerSupervisor.list_workers(tmp_path)
        assert len(workers) == 1
        w = workers[0]
        assert "task_id" in w
        assert "pid" in w
        assert "alive" in w
        assert "pid_path" in w

    def test_pid_path_in_result_is_string(self, tmp_path: Path) -> None:
        pid_dir = tmp_path / "executions" / "t"
        pid_dir.mkdir(parents=True)
        pid_file = pid_dir / "worker.pid"
        pid_file.write_text(str(os.getpid()))

        workers = WorkerSupervisor.list_workers(tmp_path)
        assert isinstance(workers[0]["pid_path"], str)

    def test_ignores_pid_file_with_invalid_content(self, tmp_path: Path) -> None:
        """A PID file that can't be parsed as an integer is silently skipped."""
        pid_dir = tmp_path / "executions" / "bad-pid"
        pid_dir.mkdir(parents=True)
        (pid_dir / "worker.pid").write_text("not-a-number")

        workers = WorkerSupervisor.list_workers(tmp_path)
        assert workers == []

    def test_results_are_sorted_by_task_id(self, tmp_path: Path) -> None:
        for task_id in ("charlie", "alpha", "bravo"):
            pid_dir = tmp_path / "executions" / task_id
            pid_dir.mkdir(parents=True)
            (pid_dir / "worker.pid").write_text(str(os.getpid()))

        workers = WorkerSupervisor.list_workers(tmp_path)
        task_ids = [w["task_id"] for w in workers if w["task_id"] != "(legacy)"]
        assert task_ids == sorted(task_ids)

    def test_legacy_daemon_pid_dead_is_marked_not_alive(self, tmp_path: Path) -> None:
        (tmp_path / "daemon.pid").write_text("999999999")
        workers = WorkerSupervisor.list_workers(tmp_path)
        legacy = [w for w in workers if w["task_id"] == "(legacy)"]
        assert len(legacy) == 1
        assert legacy[0]["alive"] is False
