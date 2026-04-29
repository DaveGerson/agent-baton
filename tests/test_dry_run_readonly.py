"""Regression test for bd-29bf: ``baton execute run --dry-run`` must be read-only.

Before the fix, ``--dry-run`` called ``engine.record_step_result``,
``engine.record_gate_result``, and ``engine.complete()`` on every loop
iteration, permanently marking the task ``status=complete``.  A subsequent
``baton execute run`` (without ``--dry-run``) then failed with
"execution already complete".

After the fix, ``--dry-run`` prints the same preview output but leaves the
execution state entirely untouched.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution.execute import _handle_run
from agent_baton.core.engine.executor import ExecutionEngine


_EXECUTE_MOD = "agent_baton.cli.commands.execution.execute"

# A minimal valid plan with one phase and one step.
_PLAN = {
    "task_id": "bd-29bf-regression-task",
    "task_summary": "dry-run read-only regression",
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


class _FakeStorage:
    """Minimal storage stub that never returns an active task."""

    def get_active_task(self) -> None:
        return None

    def set_active_task(self, task_id: str) -> None:
        pass


def _make_args(
    plan: str,
    *,
    dry_run: bool = False,
    task_id: str | None = None,
    max_steps: int = 50,
    model: str = "sonnet",
    output: str = "text",
) -> argparse.Namespace:
    return argparse.Namespace(
        subcommand="run",
        plan=plan,
        model=model,
        max_steps=max_steps,
        dry_run=dry_run,
        task_id=task_id,
        output=output,
    )


@pytest.fixture(autouse=True)
def _isolate_active_task(monkeypatch):
    """Prevent tests from picking up the real project's active task marker."""
    import os
    monkeypatch.delenv("BATON_TASK_ID", raising=False)
    monkeypatch.setattr(f"{_EXECUTE_MOD}.detect_backend", lambda _root: "file")
    monkeypatch.setattr(
        f"{_EXECUTE_MOD}.StatePersistence.get_active_task_id",
        staticmethod(lambda _root: None),
    )


# ---------------------------------------------------------------------------
# Core regression: dry-run on a fresh plan must not create any execution state
# ---------------------------------------------------------------------------

class TestDryRunIsReadOnly:
    """bd-29bf: --dry-run must not mutate execution state."""

    def test_dry_run_does_not_start_execution(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """After --dry-run, no execution row exists for the task."""
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_PLAN), encoding="utf-8")
        args = _make_args(str(plan_path), dry_run=True)

        real_engine = ExecutionEngine(team_context_root=tmp_path)

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=_FakeStorage()),
            patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=real_engine),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            patch("agent_baton.core.storage.sync.auto_sync_current_project", return_value=None),
        ):
            _handle_run(args)

        # No execution state should have been written.
        status = real_engine.status()
        assert status.get("status") == "no_active_execution", (
            f"Expected no_active_execution after dry-run, got {status.get('status')!r}"
        )

    def test_dry_run_leaves_no_completed_steps(
        self, tmp_path: Path
    ) -> None:
        """After --dry-run, no step_results are recorded."""
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_PLAN), encoding="utf-8")
        args = _make_args(str(plan_path), dry_run=True)

        real_engine = ExecutionEngine(team_context_root=tmp_path)

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=_FakeStorage()),
            patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=real_engine),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            patch("agent_baton.core.storage.sync.auto_sync_current_project", return_value=None),
        ):
            _handle_run(args)

        status = real_engine.status()
        steps_complete = status.get("steps_complete", 0)
        assert steps_complete == 0, (
            f"Expected 0 steps complete after dry-run, got {steps_complete}"
        )

    def test_dry_run_prints_preview_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """--dry-run still prints the action preview."""
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_PLAN), encoding="utf-8")
        args = _make_args(str(plan_path), dry_run=True)

        real_engine = ExecutionEngine(team_context_root=tmp_path)

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=_FakeStorage()),
            patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=real_engine),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            patch("agent_baton.core.storage.sync.auto_sync_current_project", return_value=None),
        ):
            _handle_run(args)

        # Capture once — readouterr() is destructive; calling it twice yields empty strings.
        captured = capsys.readouterr()
        output = captured.out + captured.err
        # DRY RUN banner and COMPLETE marker must appear.
        assert "DRY RUN" in output
        assert "COMPLETE" in output
        # The agent name from the plan must appear.
        assert "backend-engineer" in output

    def test_real_run_succeeds_after_dry_run(
        self, tmp_path: Path
    ) -> None:
        """A real run (no --dry-run) can start successfully after a prior dry-run.

        This is the exact repro from bd-29bf: without the fix, the real run
        fails with "execution already complete".
        """
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_PLAN), encoding="utf-8")

        # --- Step 1: dry-run preview ---
        dry_run_engine = ExecutionEngine(team_context_root=tmp_path)

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=_FakeStorage()),
            patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=dry_run_engine),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            patch("agent_baton.core.storage.sync.auto_sync_current_project", return_value=None),
        ):
            _handle_run(_make_args(str(plan_path), dry_run=True))

        # Engine status must still be no_active_execution (not complete).
        status_after_dry = dry_run_engine.status().get("status")
        assert status_after_dry == "no_active_execution", (
            f"dry-run mutated status to {status_after_dry!r}; bug is not fixed"
        )

        # --- Step 2: real run should be able to start (not crash with "already complete") ---
        # We verify that engine.start() is called (not user_error'd).
        real_engine = ExecutionEngine(team_context_root=tmp_path)
        start_called = []

        original_start = real_engine.start

        def _spy_start(plan):
            start_called.append(True)
            return original_start(plan)

        real_engine.start = _spy_start  # type: ignore[method-assign]

        mock_context_mgr = MagicMock()

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=_FakeStorage()),
            patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=real_engine),
            patch(f"{_EXECUTE_MOD}.ContextManager", return_value=mock_context_mgr),
            patch("agent_baton.core.storage.sync.auto_sync_current_project", return_value=None),
            # ClaudeCodeLauncher is imported with a local `from ... import` inside
            # _handle_run, so patch it at the source module, not at execute.
            patch(
                "agent_baton.core.runtime.claude_launcher.ClaudeCodeLauncher",
                side_effect=RuntimeError("no claude binary in test"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _handle_run(_make_args(str(plan_path), dry_run=False))

        # engine.start() must have been called — meaning the run got past the
        # "already complete" guard and actually tried to launch.
        assert start_called, (
            "engine.start() was never called on the real run — "
            "execution may have been blocked as 'already complete'"
        )
        # The exit is from the launcher stub (RuntimeError → user_error → sys.exit(1)),
        # not from the "already complete" guard (which exits with code 1 too, but
        # start_called being True above already proves we got past it).
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Resume path: dry-run on an already-started execution leaves it as "running"
# ---------------------------------------------------------------------------

class TestDryRunOnStartedExecution:
    """bd-29bf repro path: start → dry-run → status must remain 'running'."""

    def test_dry_run_on_running_execution_leaves_status_running(
        self, tmp_path: Path
    ) -> None:
        """After 'baton execute start' then '--dry-run', status stays 'running'."""
        from agent_baton.models.execution import MachinePlan

        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_PLAN), encoding="utf-8")

        # Start a real execution to put the engine in 'running' state.
        engine = ExecutionEngine(team_context_root=tmp_path)
        plan = MachinePlan.from_dict(_PLAN)
        engine.start(plan)

        status_before = engine.status().get("status")
        assert status_before == "running", (
            f"Expected 'running' after start, got {status_before!r}"
        )

        # Now simulate 'baton execute run --dry-run' on that running execution.
        # The engine already has state; _handle_run will find it via task_id.
        args = _make_args(str(plan_path), dry_run=True, task_id=_PLAN["task_id"])

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=_FakeStorage()),
            patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=engine),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            patch("agent_baton.core.storage.sync.auto_sync_current_project", return_value=None),
        ):
            _handle_run(args)

        # Status must still be 'running', not 'complete'.
        status_after = engine.status().get("status")
        assert status_after == "running", (
            f"dry-run on a running execution changed status to {status_after!r}; "
            "bug bd-29bf is not fixed"
        )

    def test_real_run_can_proceed_after_dry_run_on_started_execution(
        self, tmp_path: Path
    ) -> None:
        """After start → dry-run, a real run can resume and call next_action."""
        from agent_baton.models.execution import MachinePlan

        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_PLAN), encoding="utf-8")

        engine = ExecutionEngine(team_context_root=tmp_path)
        plan = MachinePlan.from_dict(_PLAN)
        engine.start(plan)

        # Dry-run preview.
        args_dry = _make_args(str(plan_path), dry_run=True, task_id=_PLAN["task_id"])
        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=_FakeStorage()),
            patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=engine),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            patch("agent_baton.core.storage.sync.auto_sync_current_project", return_value=None),
        ):
            _handle_run(args_dry)

        # Confirm still running.
        assert engine.status().get("status") == "running"

        # Real run: next_action must return DISPATCH (not FAILED or COMPLETE).
        next_act = engine.next_action()
        assert next_act.action_type.value == "dispatch", (
            f"Expected dispatch after start+dry-run, got {next_act.action_type!r}"
        )


# ---------------------------------------------------------------------------
# bd-ae75: automation steps in dry-run must show COMPLETE, not exhaust max_steps
# ---------------------------------------------------------------------------

_PLAN_WITH_AUTOMATION = {
    "task_id": "bd-ae75-automation-dry-run",
    "task_summary": "dry-run automation step regression",
    "risk_level": "LOW",
    "budget_tier": "lean",
    "execution_mode": "phased",
    "git_strategy": "commit-per-agent",
    "phases": [
        {
            "phase_id": 1,
            "name": "Automation Phase",
            "steps": [
                {
                    "step_id": "1.1",
                    "agent_name": "automation",
                    "task_description": "Run linter",
                    "step_type": "automation",
                    "command": "echo hello",
                    "model": "sonnet",
                }
            ],
        }
    ],
}


class TestDryRunAutomationSteps:
    """bd-ae75: automation steps must not loop until max_steps in dry-run mode."""

    def test_dry_run_completes_cleanly_with_automation_steps(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Dry-run a plan with an automation step exits with COMPLETE, not exit 1."""
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_PLAN_WITH_AUTOMATION), encoding="utf-8")
        # Small max_steps so the old bug (looping until limit) fails fast.
        args = _make_args(str(plan_path), dry_run=True, max_steps=5)

        real_engine = ExecutionEngine(team_context_root=tmp_path)

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=_FakeStorage()),
            patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=real_engine),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            patch("agent_baton.core.storage.sync.auto_sync_current_project", return_value=None),
        ):
            # Must NOT raise SystemExit(1) from max-steps exhaustion.
            _handle_run(args)

        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "COMPLETE" in output, (
            "Expected COMPLETE banner in dry-run output for automation plan; "
            f"got: {output[:500]}"
        )
        assert "ABORTED" not in output, (
            "Dry-run hit max-steps limit on automation plan — bd-ae75 not fixed"
        )

    def test_dry_run_automation_leaves_no_state(
        self, tmp_path: Path
    ) -> None:
        """Dry-run with an automation step must not mutate execution state."""
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_PLAN_WITH_AUTOMATION), encoding="utf-8")
        args = _make_args(str(plan_path), dry_run=True, max_steps=5)

        real_engine = ExecutionEngine(team_context_root=tmp_path)

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=_FakeStorage()),
            patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=real_engine),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            patch("agent_baton.core.storage.sync.auto_sync_current_project", return_value=None),
        ):
            _handle_run(args)

        status = real_engine.status()
        assert status.get("status") == "no_active_execution", (
            f"automation dry-run mutated state to {status.get('status')!r}"
        )


# ---------------------------------------------------------------------------
# bd-145f: duplicate (phase_id, gate_type) gate keys must not collide
# ---------------------------------------------------------------------------

class TestDryRunDuplicateGateKeys:
    """bd-145f: gates with same phase_id+gate_type but different commands are distinct."""

    def test_dry_run_handles_duplicate_gate_keys(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Two gates sharing phase_id and gate_type but different commands both appear."""
        from agent_baton.cli.commands.execution.execute import _run_loop
        from agent_baton.models.execution import ActionType

        # Build a sequence of actions that simulates the engine returning two
        # GATE actions with the same phase_id + gate_type but different commands,
        # then COMPLETE.  We drive _run_loop directly with a mock engine whose
        # next_action() pops from this queue.
        gate_cmd_a = "pytest tests/unit"
        gate_cmd_b = "pytest tests/integration"

        actions = [
            {
                "action_type": ActionType.GATE.value,
                "phase_id": 1,
                "gate_type": "test",
                "gate_command": gate_cmd_a,
            },
            {
                "action_type": ActionType.GATE.value,
                "phase_id": 1,
                "gate_type": "test",
                "gate_command": gate_cmd_b,
            },
            {
                "action_type": ActionType.COMPLETE.value,
                "summary": "done",
            },
        ]
        action_iter = iter(actions)

        mock_engine = MagicMock()
        mock_action = MagicMock()
        mock_action.to_dict.side_effect = lambda: next(action_iter)
        mock_engine.next_action.return_value = mock_action

        # _run_loop receives the first action_dict directly, then calls
        # engine.next_action() for subsequent ones.
        first_action = next(iter(actions))  # reuse reference — but iter already advanced

        # Re-build iterator so _run_loop gets all three actions in order.
        action_queue = [
            {
                "action_type": ActionType.GATE.value,
                "phase_id": 1,
                "gate_type": "test",
                "gate_command": gate_cmd_a,
            },
            {
                "action_type": ActionType.GATE.value,
                "phase_id": 1,
                "gate_type": "test",
                "gate_command": gate_cmd_b,
            },
            {
                "action_type": ActionType.COMPLETE.value,
                "summary": "done",
            },
        ]
        q_iter = iter(action_queue)
        first_dict = next(q_iter)

        subsequent = MagicMock()
        subsequent.to_dict.side_effect = lambda: next(q_iter)
        mock_engine.next_action.return_value = subsequent

        _run_loop(
            engine=mock_engine,
            launcher=None,
            action_dict=first_dict,
            max_steps=20,
            dry_run=True,
            model_override="sonnet",
            task_id="bd-145f-test",
        )

        captured = capsys.readouterr()
        output = captured.out + captured.err
        # Both gate commands must appear in the preview output.
        assert gate_cmd_a in output, (
            f"First gate command missing from dry-run output: {output[:500]}"
        )
        assert gate_cmd_b in output, (
            f"Second gate command (same phase_id+gate_type) silently dropped — "
            f"bd-145f not fixed. Output: {output[:500]}"
        )
        assert "COMPLETE" in output
