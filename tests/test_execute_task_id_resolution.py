"""Tests for task-id resolution priority chain in execute.py.

Resolution order (highest to lowest priority):
    --task-id flag  →  BATON_TASK_ID env var  →  active-task-id.txt  →  None

Also covers the export hint printed after `baton execute start` and the
`Bound:` field printed by `baton execute status`.
"""
from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution.execute import handler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOD = "agent_baton.cli.commands.execution.execute"


def _make_args(
    subcommand: str,
    task_id: str | None = None,
    **extra,
) -> argparse.Namespace:
    """Build a minimal Namespace that the execute handler accepts."""
    attrs: dict = {"subcommand": subcommand, "task_id": task_id}
    attrs.update(extra)
    return argparse.Namespace(**attrs)


def _capture_handler(args: argparse.Namespace) -> str:
    """Run handler(args) and return combined stdout as a string."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        handler(args)
    return buf.getvalue()


def _fake_engine(task_id_seen: list[str | None]) -> MagicMock:
    """Return a mock ExecutionEngine that records the task_id it was given."""
    engine = MagicMock()
    engine.status.return_value = {
        "task_id": task_id_seen[0] if task_id_seen else "?",
        "status": "running",
        "current_phase": 1,
        "steps_complete": 0,
        "steps_total": 2,
        "gates_passed": 0,
        "gates_failed": 0,
        "elapsed_seconds": 0,
    }
    return engine


# ---------------------------------------------------------------------------
# Test 1: --task-id flag beats BATON_TASK_ID env var
# ---------------------------------------------------------------------------

class TestFlagBeatsEnvVar:
    """The explicit --task-id flag must win over BATON_TASK_ID."""

    def test_engine_receives_flag_task_id_not_env_var(self) -> None:
        received: list[str | None] = []

        def fake_engine_factory(bus, task_id, storage):
            received.append(task_id)
            e = MagicMock()
            e.status.return_value = {
                "task_id": task_id,
                "status": "running",
                "current_phase": 1,
                "steps_complete": 0,
                "steps_total": 2,
                "gates_passed": 0,
                "gates_failed": 0,
                "elapsed_seconds": 0,
            }
            return e

        args = _make_args("status", task_id="task-B")

        with (
            patch(f"{_MOD}.ExecutionEngine", side_effect=fake_engine_factory),
            patch(f"{_MOD}.EventBus"),
            patch(f"{_MOD}.get_project_storage"),
            patch.dict("os.environ", {"BATON_TASK_ID": "task-A"}),
        ):
            _capture_handler(args)

        assert received == ["task-B"], (
            f"Expected engine to receive 'task-B' (from --task-id), got {received}"
        )

    def test_engine_does_not_receive_env_var_value_when_flag_set(self) -> None:
        received: list[str | None] = []

        def fake_engine_factory(bus, task_id, storage):
            received.append(task_id)
            e = MagicMock()
            e.status.return_value = {
                "task_id": task_id,
                "status": "running",
                "current_phase": 1,
                "steps_complete": 0,
                "steps_total": 2,
                "gates_passed": 0,
                "gates_failed": 0,
                "elapsed_seconds": 0,
            }
            return e

        args = _make_args("status", task_id="explicit-override")

        with (
            patch(f"{_MOD}.ExecutionEngine", side_effect=fake_engine_factory),
            patch(f"{_MOD}.EventBus"),
            patch(f"{_MOD}.get_project_storage"),
            patch.dict("os.environ", {"BATON_TASK_ID": "env-task"}),
        ):
            _capture_handler(args)

        assert "env-task" not in received


# ---------------------------------------------------------------------------
# Test 2: BATON_TASK_ID env var beats active-task-id.txt
# ---------------------------------------------------------------------------

class TestEnvVarBeatsActiveMarker:
    """BATON_TASK_ID must be used before falling back to the active marker."""

    def test_engine_receives_env_var_not_active_marker(self) -> None:
        received: list[str | None] = []

        def fake_engine_factory(bus, task_id, storage):
            received.append(task_id)
            e = MagicMock()
            e.status.return_value = {
                "task_id": task_id,
                "status": "running",
                "current_phase": 1,
                "steps_complete": 0,
                "steps_total": 2,
                "gates_passed": 0,
                "gates_failed": 0,
                "elapsed_seconds": 0,
            }
            return e

        # No --task-id flag; env var set; active marker would return "task-B"
        args = _make_args("status", task_id=None)

        with (
            patch(f"{_MOD}.ExecutionEngine", side_effect=fake_engine_factory),
            patch(f"{_MOD}.EventBus"),
            patch(f"{_MOD}.get_project_storage"),
            patch(f"{_MOD}.StatePersistence.get_active_task_id", return_value="task-B"),
            patch.dict("os.environ", {"BATON_TASK_ID": "task-A"}),
        ):
            _capture_handler(args)

        assert received == ["task-A"], (
            f"Expected engine to receive 'task-A' (from env var), got {received}"
        )

    def test_active_marker_not_consulted_when_env_var_set(self) -> None:
        """get_active_task_id must not be called when BATON_TASK_ID is present."""
        args = _make_args("status", task_id=None)
        mock_engine = MagicMock()
        mock_engine.status.return_value = {
            "task_id": "task-A",
            "status": "running",
            "current_phase": 1,
            "steps_complete": 0,
            "steps_total": 2,
            "gates_passed": 0,
            "gates_failed": 0,
            "elapsed_seconds": 0,
        }

        with (
            patch(f"{_MOD}.ExecutionEngine", return_value=mock_engine),
            patch(f"{_MOD}.EventBus"),
            patch(f"{_MOD}.get_project_storage"),
            patch(
                f"{_MOD}.StatePersistence.get_active_task_id", return_value="task-B"
            ) as mock_active,
            patch.dict("os.environ", {"BATON_TASK_ID": "task-A"}),
        ):
            _capture_handler(args)

        mock_active.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: Fallback to active-task-id.txt when env var is unset
# ---------------------------------------------------------------------------

class TestFallbackToActiveMarker:
    """When BATON_TASK_ID is absent and no --task-id flag, the active marker is used."""

    def test_engine_receives_active_marker_task_id(self) -> None:
        received: list[str | None] = []

        def fake_engine_factory(bus, task_id, storage):
            received.append(task_id)
            e = MagicMock()
            e.status.return_value = {
                "task_id": task_id,
                "status": "running",
                "current_phase": 1,
                "steps_complete": 0,
                "steps_total": 2,
                "gates_passed": 0,
                "gates_failed": 0,
                "elapsed_seconds": 0,
            }
            return e

        args = _make_args("status", task_id=None)

        # Remove BATON_TASK_ID from env if present
        env_without_baton = {
            k: v for k, v in __import__("os").environ.items()
            if k != "BATON_TASK_ID"
        }

        with (
            patch(f"{_MOD}.ExecutionEngine", side_effect=fake_engine_factory),
            patch(f"{_MOD}.EventBus"),
            patch(f"{_MOD}.get_project_storage"),
            patch(
                f"{_MOD}.StatePersistence.get_active_task_id",
                return_value="marker-task",
            ),
            patch("os.environ", env_without_baton),
        ):
            _capture_handler(args)

        assert received == ["marker-task"]

    def test_get_active_task_id_called_when_no_env_var(self) -> None:
        args = _make_args("status", task_id=None)
        mock_engine = MagicMock()
        mock_engine.status.return_value = {
            "task_id": "marker-task",
            "status": "running",
            "current_phase": 1,
            "steps_complete": 0,
            "steps_total": 2,
            "gates_passed": 0,
            "gates_failed": 0,
            "elapsed_seconds": 0,
        }

        env_without_baton = {
            k: v for k, v in __import__("os").environ.items()
            if k != "BATON_TASK_ID"
        }

        with (
            patch(f"{_MOD}.ExecutionEngine", return_value=mock_engine),
            patch(f"{_MOD}.EventBus"),
            patch(f"{_MOD}.get_project_storage"),
            patch(
                f"{_MOD}.StatePersistence.get_active_task_id",
                return_value="marker-task",
            ) as mock_active,
            patch("os.environ", env_without_baton),
        ):
            _capture_handler(args)

        mock_active.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4: Export hint is printed after start
# ---------------------------------------------------------------------------

class TestExportHintOnStart:
    """After baton execute start, stdout must contain the export hint."""

    def _run_start(
        self,
        plan_task_id: str = "new-plan-abc",
        env_task_id: str | None = None,
        tmp_path: Path | None = None,
    ) -> str:
        import tempfile, json as _json
        if tmp_path is None:
            tmp_path = Path(tempfile.mkdtemp())

        plan_file = tmp_path / "plan.json"
        plan_data = {
            "task_id": plan_task_id,
            "task_summary": "Test task",
            "phases": [],
            "risk_level": "LOW",
            "budget_tier": "lean",
        }
        plan_file.write_text(_json.dumps(plan_data), encoding="utf-8")

        args = _make_args("start", task_id=None, plan=str(plan_file))

        mock_plan = MagicMock()
        mock_plan.task_id = plan_task_id
        mock_plan.task_summary = "Test task"
        mock_plan.risk_level = "LOW"

        mock_action = MagicMock()
        mock_action.to_dict.return_value = {
            "action_type": "dispatch",  # matches ActionType.DISPATCH.value
            "agent_name": "backend-engineer--python",
            "agent_model": "sonnet",
            "step_id": "1.1",
            "message": "Dispatch step 1.1",
            "delegation_prompt": "Do the work.",
        }

        mock_engine = MagicMock()
        mock_engine.start.return_value = mock_action
        mock_engine._persistence = None

        env = dict(__import__("os").environ)
        if env_task_id is not None:
            env["BATON_TASK_ID"] = env_task_id
        else:
            env.pop("BATON_TASK_ID", None)

        with (
            patch(f"{_MOD}.MachinePlan") as mock_plan_cls,
            patch(f"{_MOD}.ExecutionEngine", return_value=mock_engine),
            patch(f"{_MOD}.EventBus"),
            patch(f"{_MOD}.get_project_storage"),
            patch(f"{_MOD}.ContextManager"),
            patch("os.environ", env),
        ):
            mock_plan_cls.from_dict.return_value = mock_plan
            return _capture_handler(args)

    def test_export_hint_is_present_in_stdout(self, tmp_path: Path) -> None:
        out = self._run_start(plan_task_id="plan-xyz-123", tmp_path=tmp_path)
        assert "export BATON_TASK_ID=" in out

    def test_export_hint_contains_plan_task_id(self, tmp_path: Path) -> None:
        out = self._run_start(plan_task_id="exact-plan-id", tmp_path=tmp_path)
        assert "export BATON_TASK_ID=exact-plan-id" in out

    def test_export_hint_appears_after_end_prompt_delimiter(self, tmp_path: Path) -> None:
        out = self._run_start(plan_task_id="plan-seq", tmp_path=tmp_path)
        end_prompt_pos = out.find("--- End Prompt ---")
        hint_pos = out.find("export BATON_TASK_ID=")
        assert end_prompt_pos != -1, "--- End Prompt --- delimiter not found"
        assert hint_pos != -1, "export hint not found"
        assert hint_pos > end_prompt_pos, (
            "Export hint should appear after --- End Prompt --- delimiter"
        )

    def test_export_hint_uses_plan_task_id_not_stale_env_var(
        self, tmp_path: Path
    ) -> None:
        """When BATON_TASK_ID is set to a previous task, the hint must show the new plan's id."""
        out = self._run_start(
            plan_task_id="brand-new-task",
            env_task_id="old-stale-task",
            tmp_path=tmp_path,
        )
        assert "export BATON_TASK_ID=brand-new-task" in out
        assert "old-stale-task" not in out.split("export BATON_TASK_ID=", 1)[-1].split("\n")[0]


# ---------------------------------------------------------------------------
# Test 5: baton execute status shows Bound: field
# ---------------------------------------------------------------------------

class TestStatusShowsBindingSource:
    """The status subcommand must print a Bound: line indicating resolution path."""

    def _run_status(
        self,
        task_id_flag: str | None,
        env_task_id: str | None,
    ) -> str:
        args = _make_args("status", task_id=task_id_flag)

        mock_engine = MagicMock()
        mock_engine.status.return_value = {
            "task_id": task_id_flag or env_task_id or "marker-task",
            "status": "running",
            "current_phase": 1,
            "steps_complete": 1,
            "steps_total": 4,
            "gates_passed": 0,
            "gates_failed": 0,
            "elapsed_seconds": 0,
        }

        env = dict(__import__("os").environ)
        if env_task_id is not None:
            env["BATON_TASK_ID"] = env_task_id
        else:
            env.pop("BATON_TASK_ID", None)

        with (
            patch(f"{_MOD}.ExecutionEngine", return_value=mock_engine),
            patch(f"{_MOD}.EventBus"),
            patch(f"{_MOD}.get_project_storage"),
            patch(f"{_MOD}.StatePersistence.get_active_task_id", return_value="marker-task"),
            patch("os.environ", env),
        ):
            return _capture_handler(args)

    def test_bound_via_env_var_when_env_set(self) -> None:
        out = self._run_status(task_id_flag=None, env_task_id="env-abc")
        assert "Bound:   BATON_TASK_ID" in out

    def test_bound_via_flag_when_flag_set(self) -> None:
        out = self._run_status(task_id_flag="flagged-task", env_task_id=None)
        assert "Bound:   --task-id" in out

    def test_bound_via_active_marker_when_neither_set(self) -> None:
        out = self._run_status(task_id_flag=None, env_task_id=None)
        assert "Bound:   active-task-id.txt" in out

    def test_bound_via_flag_beats_env_var_in_status_label(self) -> None:
        """When both flag and env var are present, label should reflect --task-id."""
        out = self._run_status(task_id_flag="flag-task", env_task_id="env-task")
        assert "Bound:   --task-id" in out
        assert "BATON_TASK_ID" not in out.split("Bound:")[1].split("\n")[0]

    def test_bound_field_appears_after_task_field(self) -> None:
        out = self._run_status(task_id_flag=None, env_task_id="some-env-task")
        lines = out.splitlines()
        task_idx = next((i for i, l in enumerate(lines) if l.startswith("Task:")), -1)
        bound_idx = next((i for i, l in enumerate(lines) if l.startswith("Bound:")), -1)
        assert task_idx != -1, "Task: field not found"
        assert bound_idx != -1, "Bound: field not found"
        assert bound_idx == task_idx + 1, "Bound: must immediately follow Task:"

    def test_status_still_shows_other_fields(self) -> None:
        out = self._run_status(task_id_flag=None, env_task_id=None)
        assert "Status:" in out
        assert "Phase:" in out
        assert "Steps:" in out
