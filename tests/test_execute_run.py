"""Tests for the ``baton execute run`` subcommand (_handle_run).

Strategy:
- Parser registration: parse the registered subparsers and inspect resulting
  Namespace to verify argument names and defaults.
- Dry-run path: write a real plan.json in tmp_path, wire a real ExecutionEngine
  using team_context_root (avoids SQLite UNIQUE constraint issues), patch
  get_project_storage, ContextManager, and the ClaudeCodeLauncher import so
  no real claude binary is needed.
- Missing plan file: call _handle_run with a path that does not exist and
  assert SystemExit(1).
- Plan loading: write a plan.json and confirm the plan is loaded and drives
  the engine to COMPLETE in dry-run mode.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution import execute as _mod
from agent_baton.cli.commands.execution.execute import _handle_run, register
from agent_baton.core.engine.executor import ExecutionEngine


# ---------------------------------------------------------------------------
# Autouse isolation: bd-7444 made `_handle_run` consult the active-task
# marker (SQLite + file).  Tests that don't pass --task-id and don't set
# BATON_TASK_ID must not pick up the surrounding project's real active task,
# or they will fall into the resume branch and crash on the test's
# _FakeStorage stub.  This fixture forces the marker lookup to return None
# for every test in this module.
# ---------------------------------------------------------------------------

_EXECUTE_MOD_CONST = "agent_baton.cli.commands.execution.execute"


@pytest.fixture(autouse=True)
def _isolate_active_task_marker(monkeypatch):
    """Force active-task lookup to find nothing, so tests don't see real state."""
    import os
    monkeypatch.delenv("BATON_TASK_ID", raising=False)
    monkeypatch.setattr(
        f"{_EXECUTE_MOD_CONST}.detect_backend",
        lambda _root: "file",
    )
    monkeypatch.setattr(
        f"{_EXECUTE_MOD_CONST}.StatePersistence.get_active_task_id",
        staticmethod(lambda _root: None),
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXECUTE_MOD = "agent_baton.cli.commands.execution.execute"

# A minimal valid plan dict with one phase and one step.
_MINIMAL_PLAN = {
    "task_id": "test-run-task",
    "task_summary": "Test execute run",
    "risk_level": "LOW",
    "budget_tier": "lean",
    "execution_mode": "phased",
    "git_strategy": "commit-per-agent",
    "phases": [
        {
            "phase_id": 1,
            "name": "Implementation",
            "steps": [
                {
                    "step_id": "1.1",
                    "agent_name": "backend-engineer",
                    "task_description": "Implement the feature",
                    "model": "sonnet",
                }
            ],
        }
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(
    plan: str,
    *,
    model: str = "sonnet",
    max_steps: int = 50,
    dry_run: bool = False,
    task_id: str | None = None,
    output: str = "text",
) -> argparse.Namespace:
    """Build a Namespace that mimics what argparse produces for ``execute run``."""
    return argparse.Namespace(
        subcommand="run",
        plan=plan,
        model=model,
        max_steps=max_steps,
        dry_run=dry_run,
        task_id=task_id,
        output=output,
    )


class _FakeStorage:
    """Minimal storage stub: no active task, set_active_task is a no-op."""

    def get_active_task(self) -> None:
        return None

    def set_active_task(self, task_id: str) -> None:
        pass


def _patched_run(
    args: argparse.Namespace,
    *,
    tmp_path: Path,
    plan_path: Path,
) -> None:
    """Run _handle_run with all external side-effects mocked out.

    Uses a real ExecutionEngine backed by tmp_path (file-only, no SQLite
    constraint conflicts) so the full state machine exercises real logic.

    Patches:
      - get_project_storage → _FakeStorage (suppresses SQLite path)
      - ExecutionEngine constructor → real engine with team_context_root=tmp_path
      - ContextManager → MagicMock (suppresses file I/O)
      - ClaudeCodeLauncher import inside the function → not needed for dry_run
      - auto_sync_current_project → MagicMock (suppresses DB sync)
      - detect_backend / StatePersistence.get_active_task_id → return file/None
        so the test does NOT pick up the real project's active task marker
        (added for bd-7444; _handle_run now consults the active marker).
    """
    storage = _FakeStorage()
    real_engine = ExecutionEngine(team_context_root=tmp_path)

    with (
        patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=storage),
        patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=real_engine),
        patch(f"{_EXECUTE_MOD}.ContextManager"),
        patch("agent_baton.core.storage.sync.auto_sync_current_project", return_value=None),
        patch(f"{_EXECUTE_MOD}.detect_backend", return_value="file"),
        patch(
            f"{_EXECUTE_MOD}.StatePersistence.get_active_task_id",
            return_value=None,
        ),
    ):
        _handle_run(args)


# ===========================================================================
# Parser registration
# ===========================================================================

class TestRunParserRegistration:
    """Verify that `execute run` registers the expected arguments."""

    def _parse_run(self, argv: list[str]) -> argparse.Namespace:
        """Build the parser via register() and parse the given argv."""
        root = argparse.ArgumentParser()
        sub = root.add_subparsers(dest="cmd")
        register(sub)
        return root.parse_args(["execute"] + argv)

    def test_run_subcommand_registered(self) -> None:
        args = self._parse_run(["run"])
        assert args.subcommand == "run"

    def test_plan_default(self) -> None:
        args = self._parse_run(["run"])
        assert args.plan == ".claude/team-context/plan.json"

    def test_plan_custom(self) -> None:
        args = self._parse_run(["run", "--plan", "/tmp/myplan.json"])
        assert args.plan == "/tmp/myplan.json"

    def test_model_default(self) -> None:
        args = self._parse_run(["run"])
        assert args.model == "sonnet"

    def test_model_custom(self) -> None:
        args = self._parse_run(["run", "--model", "opus"])
        assert args.model == "opus"

    def test_max_steps_default(self) -> None:
        args = self._parse_run(["run"])
        assert args.max_steps == 50

    def test_max_steps_custom(self) -> None:
        args = self._parse_run(["run", "--max-steps", "10"])
        assert args.max_steps == 10

    def test_dry_run_default_is_false(self) -> None:
        args = self._parse_run(["run"])
        assert args.dry_run is False

    def test_dry_run_flag_sets_true(self) -> None:
        args = self._parse_run(["run", "--dry-run"])
        assert args.dry_run is True

    def test_task_id_default_is_none(self) -> None:
        args = self._parse_run(["run"])
        assert args.task_id is None

    def test_task_id_custom(self) -> None:
        args = self._parse_run(["run", "--task-id", "my-task-123"])
        assert args.task_id == "my-task-123"


# ===========================================================================
# Missing plan file
# ===========================================================================

class TestMissingPlanFile:
    def test_exits_when_plan_file_not_found(self, tmp_path: Path) -> None:
        missing = str(tmp_path / "nonexistent-plan.json")
        args = _make_args(missing)
        storage = _FakeStorage()

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=storage),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            pytest.raises(SystemExit) as exc_info,
        ):
            _handle_run(args)

        assert exc_info.value.code != 0

    def test_exit_code_is_nonzero(self, tmp_path: Path) -> None:
        missing = str(tmp_path / "no-plan.json")
        args = _make_args(missing)
        storage = _FakeStorage()

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=storage),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            pytest.raises(SystemExit) as exc_info,
        ):
            _handle_run(args)

        # user_error exits with code 1
        assert exc_info.value.code == 1

    def test_error_message_references_plan_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        missing = str(tmp_path / "gone.json")
        args = _make_args(missing)
        storage = _FakeStorage()

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=storage),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            pytest.raises(SystemExit),
        ):
            _handle_run(args)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "gone.json" in output or "plan" in output.lower()


# ===========================================================================
# Plan loading
# ===========================================================================

class TestPlanLoading:
    def test_invalid_json_exits(
        self, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text("this is not json", encoding="utf-8")
        args = _make_args(str(plan_path), dry_run=True)
        storage = _FakeStorage()

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=storage),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            pytest.raises(SystemExit) as exc_info,
        ):
            _handle_run(args)

        assert exc_info.value.code != 0

    def test_json_with_invalid_structure_exits(
        self, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        # Valid JSON but missing required MachinePlan fields (task_id, task_summary)
        plan_path.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
        args = _make_args(str(plan_path), dry_run=True)
        storage = _FakeStorage()

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=storage),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            pytest.raises(SystemExit) as exc_info,
        ):
            _handle_run(args)

        assert exc_info.value.code != 0

    def test_valid_plan_loaded_and_task_id_used(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_MINIMAL_PLAN), encoding="utf-8")
        args = _make_args(str(plan_path), dry_run=True)

        _patched_run(args, tmp_path=tmp_path, plan_path=plan_path)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "test-run-task" in output


# ===========================================================================
# Dry-run mode
# ===========================================================================

class TestDryRun:
    def test_dry_run_completes_without_launching_agent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_MINIMAL_PLAN), encoding="utf-8")
        args = _make_args(str(plan_path), dry_run=True)

        _patched_run(args, tmp_path=tmp_path, plan_path=plan_path)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "DRY RUN" in output
        assert "COMPLETE" in output

    def test_dry_run_prints_agent_name(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_MINIMAL_PLAN), encoding="utf-8")
        args = _make_args(str(plan_path), dry_run=True)

        _patched_run(args, tmp_path=tmp_path, plan_path=plan_path)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "backend-engineer" in output

    def test_dry_run_shows_step_id(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_MINIMAL_PLAN), encoding="utf-8")
        args = _make_args(str(plan_path), dry_run=True)

        _patched_run(args, tmp_path=tmp_path, plan_path=plan_path)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "1.1" in output

    def test_dry_run_does_not_invoke_claude_code_launcher(
        self, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_MINIMAL_PLAN), encoding="utf-8")
        args = _make_args(str(plan_path), dry_run=True)

        storage = _FakeStorage()
        real_engine = ExecutionEngine(team_context_root=tmp_path)

        mock_launcher_cls = MagicMock()

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=storage),
            patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=real_engine),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            patch(
                "agent_baton.core.storage.sync.auto_sync_current_project",
                return_value=None,
            ),
            # The launcher import is inside _handle_run; patch at the module level
            # it would import from.
            patch(
                "agent_baton.core.runtime.claude_launcher.ClaudeCodeLauncher",
                mock_launcher_cls,
            ),
        ):
            _handle_run(args)

        # In dry_run mode, the launcher is never instantiated.
        mock_launcher_cls.assert_not_called()

    def test_dry_run_max_steps_one_aborts_after_first_dispatch(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """With max_steps=1 and two steps, execution aborts after step 1."""
        two_step_plan = {
            **_MINIMAL_PLAN,
            "task_id": "two-step-task",
            "phases": [
                {
                    "phase_id": 1,
                    "name": "Phase 1",
                    "steps": [
                        {
                            "step_id": "1.1",
                            "agent_name": "backend-engineer",
                            "task_description": "Step one",
                            "model": "sonnet",
                        },
                        {
                            "step_id": "1.2",
                            "agent_name": "test-engineer",
                            "task_description": "Step two",
                            "model": "sonnet",
                        },
                    ],
                }
            ],
        }
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(two_step_plan), encoding="utf-8")
        args = _make_args(str(plan_path), dry_run=True, max_steps=1)

        storage = _FakeStorage()
        real_engine = ExecutionEngine(team_context_root=tmp_path)

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=storage),
            patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=real_engine),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            patch("agent_baton.core.storage.sync.auto_sync_current_project", return_value=None),
            pytest.raises(SystemExit) as exc_info,
        ):
            _handle_run(args)

        # max_steps exceeded → exit(1)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "ABORTED" in output or "max" in output.lower()

    def test_dry_run_multi_phase_plan_completes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """A plan with two phases completes in dry-run mode."""
        multi_phase_plan = {
            **_MINIMAL_PLAN,
            "task_id": "multi-phase-task",
            "phases": [
                {
                    "phase_id": 1,
                    "name": "Phase 1",
                    "steps": [
                        {
                            "step_id": "1.1",
                            "agent_name": "backend-engineer",
                            "task_description": "Backend work",
                            "model": "sonnet",
                        }
                    ],
                },
                {
                    "phase_id": 2,
                    "name": "Phase 2",
                    "steps": [
                        {
                            "step_id": "2.1",
                            "agent_name": "test-engineer",
                            "task_description": "Test work",
                            "model": "sonnet",
                        }
                    ],
                },
            ],
        }
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(multi_phase_plan), encoding="utf-8")
        args = _make_args(str(plan_path), dry_run=True)

        _patched_run(args, tmp_path=tmp_path, plan_path=plan_path)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "COMPLETE" in output
        # Both agents should be mentioned
        assert "backend-engineer" in output
        assert "test-engineer" in output
