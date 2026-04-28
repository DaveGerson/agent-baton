"""Tests for the DX.5 dry-run testing harness.

The dry-run harness lets developers walk a plan end-to-end with mock
dispatchers — no Claude API calls, no file writes by agents — to validate
plan routing, gates, and the action protocol before incurring real cost.

Surface under test:

- ``agent_baton.core.engine.dry_run_launcher.DryRunLauncher`` — re-export
  of the runtime mock plus a thin tracing wrapper.
- ``agent_baton.core.engine.gates.DryRunGateRunner`` — gate runner that
  always returns pass and records the command.
- ``baton execute start --dry-run`` — flag prints the dry-run banner.
- ``baton execute dry-run`` — single-shot convenience: load → start → run
  → complete → write report.
- The dry-run report at ``.claude/team-context/dry-run-report.md``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution import execute as _exec_mod
from agent_baton.cli.commands.execution.execute import register
from agent_baton.core.engine.dry_run_launcher import DryRunLauncher as EngineDryRunLauncher
from agent_baton.core.engine.gates import DryRunGateRunner
from agent_baton.core.runtime.launcher import (
    DryRunLauncher as RuntimeDryRunLauncher,
    LaunchResult,
)
from agent_baton.models.execution import GateResult, PlanGate


_EXECUTE_MOD = "agent_baton.cli.commands.execution.execute"


_MINIMAL_PLAN: dict = {
    "task_id": "dry-run-task-001",
    "task_summary": "Dry-run smoke test",
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


_TWO_PHASE_PLAN: dict = {
    **_MINIMAL_PLAN,
    "task_id": "dry-run-task-002",
    "phases": [
        {
            "phase_id": 1,
            "name": "Implementation",
            "steps": [
                {
                    "step_id": "1.1",
                    "agent_name": "backend-engineer",
                    "task_description": "Backend",
                    "model": "sonnet",
                }
            ],
        },
        {
            "phase_id": 2,
            "name": "Testing",
            "steps": [
                {
                    "step_id": "2.1",
                    "agent_name": "test-engineer",
                    "task_description": "Tests",
                    "model": "sonnet",
                }
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# DryRunLauncher
# ---------------------------------------------------------------------------


class TestDryRunLauncherSurface:
    """The engine-facing launcher must be importable and back-compat."""

    def test_dry_run_launcher_importable_from_engine(self) -> None:
        # Promoted to core.engine but must remain back-compat with runtime path.
        assert EngineDryRunLauncher is not None

    def test_engine_and_runtime_classes_share_behaviour(self) -> None:
        # Either the same class or a subclass — we don't care which, but the
        # runtime export must satisfy the Launcher protocol the engine expects.
        engine_inst = EngineDryRunLauncher()
        runtime_inst = RuntimeDryRunLauncher()
        for inst in (engine_inst, runtime_inst):
            assert hasattr(inst, "launch")
            assert hasattr(inst, "launches")

    def test_default_returns_complete(self) -> None:
        launcher = EngineDryRunLauncher()
        result = asyncio.run(
            launcher.launch(
                agent_name="backend-engineer",
                model="sonnet",
                prompt="hello",
                step_id="1.1",
            )
        )
        assert isinstance(result, LaunchResult)
        assert result.status == "complete"
        assert result.step_id == "1.1"
        assert result.agent_name == "backend-engineer"

    def test_records_trace_of_launches(self) -> None:
        launcher = EngineDryRunLauncher()
        asyncio.run(launcher.launch("a1", "sonnet", "p1", step_id="1.1"))
        asyncio.run(launcher.launch("a2", "opus", "p2-longer", step_id="1.2"))
        assert len(launcher.launches) == 2
        assert launcher.launches[0]["agent_name"] == "a1"
        assert launcher.launches[1]["agent_name"] == "a2"
        assert launcher.launches[1]["model"] == "opus"

    def test_per_step_override_returns_configured_result(self) -> None:
        launcher = EngineDryRunLauncher()
        custom = LaunchResult(
            step_id="1.1",
            agent_name="backend-engineer",
            status="failed",
            error="forced failure",
        )
        launcher.set_result("1.1", custom)
        result = asyncio.run(
            launcher.launch(
                agent_name="backend-engineer",
                model="sonnet",
                prompt="x",
                step_id="1.1",
            )
        )
        assert result.status == "failed"
        assert result.error == "forced failure"


# ---------------------------------------------------------------------------
# DryRunGateRunner
# ---------------------------------------------------------------------------


class TestDryRunGateRunner:
    def test_evaluate_always_passes(self) -> None:
        runner = DryRunGateRunner()
        gate = PlanGate(gate_type="test", command="pytest -q", description="run tests")
        result = runner.evaluate_output(gate, command_output="", exit_code=99)
        assert isinstance(result, GateResult)
        assert result.passed is True
        assert result.gate_type == "test"

    def test_records_commands(self) -> None:
        runner = DryRunGateRunner()
        gate1 = PlanGate(gate_type="test", command="pytest -q")
        gate2 = PlanGate(gate_type="lint", command="ruff check .")
        runner.evaluate_output(gate1, "", 0)
        runner.evaluate_output(gate2, "", 0)
        assert len(runner.gates_run) == 2
        assert runner.gates_run[0]["gate_type"] == "test"
        assert runner.gates_run[0]["command"] == "pytest -q"
        assert runner.gates_run[1]["command"] == "ruff check ."


# ---------------------------------------------------------------------------
# CLI: --dry-run flag wiring on `baton execute start`
# ---------------------------------------------------------------------------


class TestExecuteStartDryRunFlag:
    def _parse(self, argv: list[str]) -> argparse.Namespace:
        root = argparse.ArgumentParser()
        sub = root.add_subparsers(dest="cmd")
        register(sub)
        return root.parse_args(["execute"] + argv)

    def test_start_accepts_dry_run_flag(self) -> None:
        args = self._parse(["start", "--dry-run"])
        assert args.dry_run is True

    def test_start_dry_run_default_is_false(self) -> None:
        args = self._parse(["start"])
        assert args.dry_run is False


class TestExecuteDryRunSubcommand:
    """`baton execute dry-run` is the convenience all-in-one entrypoint."""

    def _parse(self, argv: list[str]) -> argparse.Namespace:
        root = argparse.ArgumentParser()
        sub = root.add_subparsers(dest="cmd")
        register(sub)
        return root.parse_args(["execute"] + argv)

    def test_dry_run_subcommand_registered(self) -> None:
        args = self._parse(["dry-run"])
        assert args.subcommand == "dry-run"

    def test_dry_run_subcommand_accepts_plan_arg(self) -> None:
        args = self._parse(["dry-run", "--plan", "/tmp/p.json"])
        assert args.plan == "/tmp/p.json"


# ---------------------------------------------------------------------------
# End-to-end dry-run: no real subprocess, COMPLETE reached, report written
# ---------------------------------------------------------------------------


class _FakeStorage:
    def get_active_task(self) -> None:
        return None

    def set_active_task(self, task_id: str) -> None:
        pass


def _run_dry_run_subcommand(plan_path: Path, tmp_path: Path) -> None:
    """Invoke the new ``baton execute dry-run`` handler in isolation."""
    from agent_baton.cli.commands.execution.execute import _handle_dry_run
    from agent_baton.core.engine.executor import ExecutionEngine

    args = argparse.Namespace(
        subcommand="dry-run",
        plan=str(plan_path),
        task_id=None,
        output="text",
        max_steps=50,
    )
    storage = _FakeStorage()
    real_engine = ExecutionEngine(team_context_root=tmp_path)

    with (
        patch(f"{_EXECUTE_MOD}._resolve_context_root", return_value=tmp_path),
        patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=storage),
        patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=real_engine),
        patch(f"{_EXECUTE_MOD}.ContextManager"),
        patch(
            "agent_baton.core.storage.sync.auto_sync_current_project",
            return_value=None,
        ),
    ):
        _handle_dry_run(args)


class TestDryRunEndToEnd:
    def test_completes_without_real_launcher(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_MINIMAL_PLAN), encoding="utf-8")

        # Hard guard: any subprocess.run call would mean we tried to invoke
        # a real gate or claude binary.  Mock it so the test fails noisily
        # if the dry-run path regresses.
        with patch("subprocess.run") as sp_run:
            sp_run.side_effect = AssertionError(
                "subprocess.run must not be called during a dry-run"
            )
            _run_dry_run_subcommand(plan_path, tmp_path)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "DRY RUN" in output
        assert "COMPLETE" in output

    def test_banner_printed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_MINIMAL_PLAN), encoding="utf-8")
        _run_dry_run_subcommand(plan_path, tmp_path)
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "no API calls" in output
        assert "no file writes" in output

    def test_report_file_written(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_TWO_PHASE_PLAN), encoding="utf-8")
        _run_dry_run_subcommand(plan_path, tmp_path)

        report_path = tmp_path / "dry-run-report.md"
        assert report_path.exists()
        text = report_path.read_text(encoding="utf-8")
        # Report should mention the agents and step IDs.
        assert "backend-engineer" in text
        assert "test-engineer" in text
        assert "1.1" in text
        assert "2.1" in text

    def test_report_contains_summary_table_headings(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_MINIMAL_PLAN), encoding="utf-8")
        _run_dry_run_subcommand(plan_path, tmp_path)

        text = (tmp_path / "dry-run-report.md").read_text(encoding="utf-8")
        # Summary metrics surfaced.
        assert "dispatches" in text.lower()
        assert "wall-clock" in text.lower() or "wall clock" in text.lower()
        assert "step" in text.lower()

    def test_no_real_claude_launcher_imported_or_called(
        self, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_MINIMAL_PLAN), encoding="utf-8")

        with patch(
            "agent_baton.core.runtime.claude_launcher.ClaudeCodeLauncher"
        ) as mock_launcher_cls:
            _run_dry_run_subcommand(plan_path, tmp_path)
            mock_launcher_cls.assert_not_called()
