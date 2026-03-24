"""Tests for daemon CLI task_id threading and daemon list subcommand."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution import daemon as daemon_module
from agent_baton.cli.commands.execution.daemon import handler, register


_MOD = "agent_baton.cli.commands.execution.daemon"


# ---------------------------------------------------------------------------
# Helpers to build Namespace objects mimicking argparse output
# ---------------------------------------------------------------------------

def _start_args(
    task_id: str | None = None,
    plan: str | None = "/tmp/plan.json",
    dry_run: bool = True,
    foreground: bool = True,
    resume: bool = False,
    max_parallel: int = 3,
    project_dir: str | None = None,
    serve: bool = False,
    host: str = "127.0.0.1",
    port: int = 8741,
    token: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        daemon_action="start",
        task_id=task_id,
        plan=plan,
        dry_run=dry_run,
        foreground=foreground,
        resume=resume,
        max_parallel=max_parallel,
        project_dir=project_dir,
        serve=serve,
        host=host,
        port=port,
        token=token,
    )


def _status_args(task_id: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(daemon_action="status", task_id=task_id)


def _stop_args(task_id: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(daemon_action="stop", task_id=task_id)


def _list_args(project_dir: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(daemon_action="list", project_dir=project_dir)


# ---------------------------------------------------------------------------
# register() — argument parsing
# ---------------------------------------------------------------------------

class TestDaemonRegister:
    def test_start_accepts_task_id(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        args = parser.parse_args(["daemon", "start", "--plan", "p.json", "--task-id", "my-task"])
        assert args.task_id == "my-task"

    def test_status_accepts_task_id(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        args = parser.parse_args(["daemon", "status", "--task-id", "t123"])
        assert args.task_id == "t123"

    def test_stop_accepts_task_id(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        args = parser.parse_args(["daemon", "stop", "--task-id", "t456"])
        assert args.task_id == "t456"

    def test_list_subcommand_is_registered(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        args = parser.parse_args(["daemon", "list"])
        assert args.daemon_action == "list"

    def test_list_accepts_project_dir(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        args = parser.parse_args(["daemon", "list", "--project-dir", "/some/dir"])
        assert args.project_dir == "/some/dir"

    def test_start_task_id_defaults_to_none(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        args = parser.parse_args(["daemon", "start", "--plan", "p.json"])
        assert args.task_id is None


# ---------------------------------------------------------------------------
# handler — task_id is threaded to WorkerSupervisor
# ---------------------------------------------------------------------------

class TestHandlerTaskIdThreading:
    def test_start_passes_task_id_to_supervisor(self, tmp_path: Path) -> None:
        plan_file = tmp_path / "plan.json"
        # Write a minimal valid plan JSON.
        plan_file.write_text(
            '{"task_id": "t1", "task_summary": "Test", "phases": [], '
            '"risk_level": "LOW", "budget_tier": "lean"}',
            encoding="utf-8",
        )
        args = _start_args(task_id="exec-99", plan=str(plan_file))

        captured_supervisors: list[MagicMock] = []

        def fake_supervisor(task_id=None, **kwargs):
            sv = MagicMock()
            sv.task_id = task_id
            sv._task_id = task_id
            sv.pid_path = tmp_path / "fake.pid"
            sv.start.return_value = "Completed."
            captured_supervisors.append(sv)
            return sv

        with patch(f"{_MOD}.WorkerSupervisor", side_effect=fake_supervisor):
            handler(args)

        assert len(captured_supervisors) == 1
        assert captured_supervisors[0].task_id == "exec-99"

    def test_start_without_task_id_uses_none(self, tmp_path: Path) -> None:
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(
            '{"task_id": "t1", "task_summary": "Test", "phases": [], '
            '"risk_level": "LOW", "budget_tier": "lean"}',
            encoding="utf-8",
        )
        args = _start_args(task_id=None, plan=str(plan_file))

        captured_supervisors: list[MagicMock] = []

        def fake_supervisor(task_id=None, **kwargs):
            sv = MagicMock()
            sv.task_id = task_id
            sv._task_id = task_id
            sv.pid_path = tmp_path / "fake.pid"
            sv.start.return_value = "Completed."
            captured_supervisors.append(sv)
            return sv

        with patch(f"{_MOD}.WorkerSupervisor", side_effect=fake_supervisor):
            handler(args)

        assert captured_supervisors[0].task_id is None

    def test_status_passes_task_id_to_supervisor(self, capsys: pytest.CaptureFixture) -> None:
        mock_sv = MagicMock()
        mock_sv.status.return_value = {"running": False}

        with patch(f"{_MOD}.WorkerSupervisor", return_value=mock_sv) as mock_cls:
            handler(_status_args(task_id="my-exec"))

        _, kwargs = mock_cls.call_args
        assert kwargs.get("task_id") == "my-exec"

    def test_stop_passes_task_id_to_supervisor(self, capsys: pytest.CaptureFixture) -> None:
        mock_sv = MagicMock()
        mock_sv.stop.return_value = True

        with patch(f"{_MOD}.WorkerSupervisor", return_value=mock_sv) as mock_cls:
            handler(_stop_args(task_id="exec-to-stop"))

        _, kwargs = mock_cls.call_args
        assert kwargs.get("task_id") == "exec-to-stop"


# ---------------------------------------------------------------------------
# handler — daemon list
# ---------------------------------------------------------------------------

class TestHandlerDaemonList:
    def test_list_calls_list_workers(self, capsys: pytest.CaptureFixture) -> None:
        with patch(f"{_MOD}.WorkerSupervisor.list_workers", return_value=[]) as mock_lw:
            handler(_list_args())
        mock_lw.assert_called_once()

    def test_list_prints_no_workers_message_when_empty(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        with patch(f"{_MOD}.WorkerSupervisor.list_workers", return_value=[]):
            handler(_list_args())
        out = capsys.readouterr().out
        assert "No daemon workers found." in out

    def test_list_prints_worker_table_header(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        workers = [
            {
                "task_id": "exec-001",
                "pid": 12345,
                "alive": True,
                "pid_path": "/tmp/exec-001/worker.pid",
            }
        ]
        with patch(f"{_MOD}.WorkerSupervisor.list_workers", return_value=workers):
            handler(_list_args())
        out = capsys.readouterr().out
        assert "TASK ID" in out
        assert "PID" in out
        assert "ALIVE" in out

    def test_list_shows_task_id_and_pid(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        workers = [
            {
                "task_id": "exec-abc",
                "pid": 99999,
                "alive": True,
                "pid_path": "/tmp/x/worker.pid",
            }
        ]
        with patch(f"{_MOD}.WorkerSupervisor.list_workers", return_value=workers):
            handler(_list_args())
        out = capsys.readouterr().out
        assert "exec-abc" in out
        assert "99999" in out

    def test_list_shows_alive_yes_for_running_worker(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        workers = [
            {"task_id": "t", "pid": 1, "alive": True, "pid_path": "/x"}
        ]
        with patch(f"{_MOD}.WorkerSupervisor.list_workers", return_value=workers):
            handler(_list_args())
        out = capsys.readouterr().out
        assert "yes" in out

    def test_list_shows_alive_no_for_dead_worker(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        workers = [
            {"task_id": "t", "pid": 999999999, "alive": False, "pid_path": "/x"}
        ]
        with patch(f"{_MOD}.WorkerSupervisor.list_workers", return_value=workers):
            handler(_list_args())
        out = capsys.readouterr().out
        assert "no" in out

    def test_list_uses_project_dir_when_specified(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()

        with patch(f"{_MOD}.WorkerSupervisor.list_workers", return_value=[]) as mock_lw:
            handler(_list_args(project_dir=str(project_dir)))

        called_root = mock_lw.call_args[0][0]
        assert called_root == project_dir.resolve() / ".claude" / "team-context"

    def test_list_uses_cwd_based_path_when_no_project_dir(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        with patch(f"{_MOD}.WorkerSupervisor.list_workers", return_value=[]) as mock_lw:
            handler(_list_args(project_dir=None))

        called_root = mock_lw.call_args[0][0]
        # The path must end in .claude/team-context
        assert str(called_root).endswith(".claude/team-context")


# ---------------------------------------------------------------------------
# Legacy behaviour preserved when task_id is absent
# ---------------------------------------------------------------------------

class TestLegacyBehaviourPreserved:
    def test_status_with_no_task_id_queries_legacy_pid(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        mock_sv = MagicMock()
        mock_sv.status.return_value = {"running": False}

        with patch(f"{_MOD}.WorkerSupervisor", return_value=mock_sv) as mock_cls:
            handler(_status_args(task_id=None))

        _, kwargs = mock_cls.call_args
        assert kwargs.get("task_id") is None

    def test_stop_with_no_task_id_targets_legacy_daemon(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        mock_sv = MagicMock()
        mock_sv.stop.return_value = False

        with patch(f"{_MOD}.WorkerSupervisor", return_value=mock_sv) as mock_cls:
            handler(_stop_args(task_id=None))

        _, kwargs = mock_cls.call_args
        assert kwargs.get("task_id") is None
