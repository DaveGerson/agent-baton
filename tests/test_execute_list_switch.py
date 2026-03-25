"""Tests for execute list and execute switch CLI helpers.

Both _handle_list() and _handle_switch() resolve the context root as
Path(".claude/team-context").  We patch StatePersistence, WorkerSupervisor,
and sys.exit at the module level to isolate tests from the filesystem and from
real process termination.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from agent_baton.cli.commands.execution.execute import _handle_list, _handle_switch


# Patch target for the module under test.
_MOD = "agent_baton.cli.commands.execution.execute"


# ---------------------------------------------------------------------------
# _handle_list — no executions
# ---------------------------------------------------------------------------

class TestHandleListEmpty:
    def test_prints_no_executions_message_when_list_is_empty(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        # Build a mock class that carries both class-method mocks and behaves
        # as a factory for per-task-id StatePersistence instances.
        mock_sp_instance = MagicMock()
        mock_sp_instance.load.return_value = None

        mock_cls = MagicMock(return_value=mock_sp_instance)
        mock_cls.list_executions = MagicMock(return_value=[])
        mock_cls.get_active_task_id = MagicMock(return_value=None)

        with (
            patch(f"{_MOD}.StatePersistence", mock_cls),
            patch(f"{_MOD}.WorkerSupervisor.list_workers", return_value=[]),
            # Prevent the SQLite backend from returning real rows from baton.db
            patch(f"{_MOD}.detect_backend", return_value="file"),
        ):
            _handle_list()

        out = capsys.readouterr().out
        assert "No executions found." in out


# ---------------------------------------------------------------------------
# _handle_list — multiple executions
# ---------------------------------------------------------------------------

def _make_fake_state(
    task_id: str,
    summary: str = "A task",
    status: str = "running",
    risk_level: str = "LOW",
    budget_tier: str = "lean",
    total_steps: int = 2,
    step_results: list | None = None,
) -> MagicMock:
    """Build a minimal MagicMock that mimics ExecutionState."""
    state = MagicMock()
    state.task_id = task_id
    state.status = status
    state.started_at = "2026-01-15T10:00:00+00:00"
    state.completed_at = ""
    state.step_results = step_results or []

    plan = MagicMock()
    plan.task_summary = summary
    plan.risk_level = risk_level
    plan.budget_tier = budget_tier
    plan.total_steps = total_steps
    state.plan = plan

    return state


class TestHandleListMultiple:
    def _run_list_with_tasks(
        self,
        task_ids: list[str],
        states_by_id: dict[str, MagicMock],
        active_task_id: str | None = None,
        workers: list[dict] | None = None,
    ) -> str:
        """Run _handle_list() with faked StatePersistence and return stdout.

        We patch the StatePersistence CLASS with a side_effect that handles
        both instance creation AND the static method calls.  The mock class
        also carries the static-method mocks as class-level attributes so
        that ``StatePersistence.list_executions(...)`` and
        ``StatePersistence.get_active_task_id(...)`` resolve correctly.
        """

        def fake_sp_factory(context_root, task_id=None):
            sp = MagicMock()
            if task_id is not None:
                sp.load.return_value = states_by_id.get(task_id)
            else:
                sp.load.return_value = None  # no legacy flat file
            sp.exists.return_value = task_id in states_by_id
            return sp

        mock_cls = MagicMock(side_effect=fake_sp_factory)
        mock_cls.list_executions = MagicMock(return_value=task_ids)
        mock_cls.get_active_task_id = MagicMock(return_value=active_task_id)

        with (
            patch(f"{_MOD}.StatePersistence", mock_cls),
            patch(f"{_MOD}.WorkerSupervisor.list_workers", return_value=workers or []),
        ):
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                _handle_list()
            return buf.getvalue()

    def test_shows_all_task_ids(self) -> None:
        states = {
            "task-alpha": _make_fake_state("task-alpha", summary="Alpha work"),
            "task-beta": _make_fake_state("task-beta", summary="Beta work"),
        }
        out = self._run_list_with_tasks(
            task_ids=["task-alpha", "task-beta"],
            states_by_id=states,
        )
        assert "task-alpha" in out
        assert "task-beta" in out

    def test_marks_active_execution_with_asterisk(self) -> None:
        states = {
            "task-alpha": _make_fake_state("task-alpha"),
            "task-beta": _make_fake_state("task-beta"),
        }
        out = self._run_list_with_tasks(
            task_ids=["task-alpha", "task-beta"],
            states_by_id=states,
            active_task_id="task-alpha",
        )
        # The active line starts with "* task-alpha"
        assert "* task-alpha" in out
        # The inactive line starts with "  task-beta" (space, not asterisk)
        lines = out.splitlines()
        beta_lines = [l for l in lines if "task-beta" in l]
        assert beta_lines, "task-beta should appear in output"
        assert not beta_lines[0].lstrip().startswith("*")

    def test_shows_status_column(self) -> None:
        states = {
            "task-x": _make_fake_state("task-x", status="complete"),
        }
        out = self._run_list_with_tasks(
            task_ids=["task-x"],
            states_by_id=states,
        )
        assert "complete" in out

    def test_shows_plan_summary_truncated_to_40_chars(self) -> None:
        long_summary = "A" * 60
        states = {
            "task-s": _make_fake_state("task-s", summary=long_summary),
        }
        out = self._run_list_with_tasks(
            task_ids=["task-s"],
            states_by_id=states,
        )
        # Should appear (truncated to 40 chars)
        assert "A" * 40 in out
        # The full 60-char version should NOT appear
        assert "A" * 60 not in out

    def test_shows_steps_progress(self) -> None:
        complete_result = MagicMock()
        complete_result.status = "complete"
        states = {
            "task-p": _make_fake_state(
                "task-p",
                total_steps=3,
                step_results=[complete_result, complete_result],
            ),
        }
        out = self._run_list_with_tasks(
            task_ids=["task-p"],
            states_by_id=states,
        )
        assert "2/3" in out

    def test_shows_worker_pid_when_alive(self) -> None:
        states = {
            "task-w": _make_fake_state("task-w"),
        }
        workers = [{"task_id": "task-w", "pid": 12345, "alive": True}]
        out = self._run_list_with_tasks(
            task_ids=["task-w"],
            states_by_id=states,
            workers=workers,
        )
        assert "12345" in out

    def test_shows_dash_when_no_worker_running(self) -> None:
        states = {
            "task-nw": _make_fake_state("task-nw"),
        }
        out = self._run_list_with_tasks(
            task_ids=["task-nw"],
            states_by_id=states,
            workers=[],
        )
        lines = out.splitlines()
        task_line = [l for l in lines if "task-nw" in l]
        assert task_line
        assert "-" in task_line[0]

    def test_header_row_is_present(self) -> None:
        states = {
            "task-h": _make_fake_state("task-h"),
        }
        out = self._run_list_with_tasks(
            task_ids=["task-h"],
            states_by_id=states,
        )
        assert "TASK ID" in out
        assert "STATUS" in out


# ---------------------------------------------------------------------------
# _handle_switch
# ---------------------------------------------------------------------------

class TestHandleSwitch:
    def test_valid_task_id_sets_active_and_prints_confirmation(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        mock_sp = MagicMock()
        mock_sp.exists.return_value = True

        with patch(f"{_MOD}.StatePersistence", return_value=mock_sp):
            _handle_switch("task-valid")

        mock_sp.set_active.assert_called_once()
        out = capsys.readouterr().out
        assert "task-valid" in out
        assert "switched" in out.lower()

    def test_invalid_task_id_prints_error_and_exits(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        mock_sp = MagicMock()
        mock_sp.exists.return_value = False

        with (
            patch(f"{_MOD}.StatePersistence", return_value=mock_sp),
            pytest.raises(SystemExit) as exc_info,
        ):
            _handle_switch("no-such-task")

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "error" in output.lower()
        assert "no-such-task" in output

    def test_set_active_not_called_for_invalid_task_id(self) -> None:
        mock_sp = MagicMock()
        mock_sp.exists.return_value = False

        with (
            patch(f"{_MOD}.StatePersistence", return_value=mock_sp),
            pytest.raises(SystemExit),
        ):
            _handle_switch("ghost-task")

        mock_sp.set_active.assert_not_called()

    def test_switch_constructs_persistence_with_correct_task_id(self) -> None:
        mock_sp = MagicMock()
        mock_sp.exists.return_value = True

        with patch(f"{_MOD}.StatePersistence") as mock_cls:
            mock_cls.return_value = mock_sp
            _handle_switch("precise-task-id")

        # First positional arg is context_root, task_id is the kwarg.
        args, kwargs = mock_cls.call_args
        assert kwargs.get("task_id") == "precise-task-id" or (
            len(args) > 1 and args[1] == "precise-task-id"
        )
