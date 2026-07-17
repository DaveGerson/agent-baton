"""Tests for ``baton run`` -- the canonical-run delegation contract.

``baton run`` (this module) used to independently construct an
``ExecutionEngine`` + ``TaskWorker`` + ``BatonRunner`` stack -- a second,
divergent implementation of the autonomous-loop contract that
``baton execute run`` (``_handle_run`` in
``cli/commands/execution/execute.py``) already implements.  That duplicate
implementation called ``ExecutionEngine.start(plan, task_id=...)``, a
signature the engine has never had, so any ``baton run`` invocation that
started a fresh plan (including ``--dry-run``) crashed with a ``TypeError``.

These tests pin:

1. The CLI surface (registered flags) is unchanged -- no breaking CLI change.
2. ``handler()`` delegates to ``_handle_run`` instead of retaining its own
   engine/worker construction (no second state machine).
3. ``baton run --dry-run`` completes without crashing end-to-end (the
   regression the dry-run signature bug caused).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution import run as run_mod
from agent_baton.core.engine.executor import ExecutionEngine

_RUN_MOD = "agent_baton.cli.commands.execution.run"
_EXECUTE_MOD = "agent_baton.cli.commands.execution.execute"

_MINIMAL_PLAN: dict = {
    "task_id": "test-run-delegate-task",
    "task_summary": "Test baton run delegation",
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
    def get_active_task(self) -> None:
        return None

    def set_active_task(self, task_id: str) -> None:
        pass


# ---------------------------------------------------------------------------
# 1. Parser registration -- flags unchanged
# ---------------------------------------------------------------------------

class TestRunParserRegistration:
    def _parse(self, argv: list[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        run_mod.register(subparsers)
        return parser.parse_args(["run", *argv])

    def test_default_flags(self) -> None:
        args = self._parse([])
        assert args.plan == ".claude/team-context/plan.json"
        assert args.task_id is None
        assert args.max_parallel == 3
        assert args.dry_run is False
        assert args.resume is False

    def test_dry_run_flag(self) -> None:
        args = self._parse(["--dry-run"])
        assert args.dry_run is True

    def test_task_id_and_plan_flags(self) -> None:
        args = self._parse(["--plan", "custom.json", "--task-id", "abc-123"])
        assert args.plan == "custom.json"
        assert args.task_id == "abc-123"

    def test_max_steps_flag_has_a_default(self) -> None:
        # Not present on the pre-delegation CLI surface; added as an
        # additive, backward-compatible flag for the canonical runner.
        args = self._parse([])
        assert args.max_steps > 0


# ---------------------------------------------------------------------------
# 2. Delegation -- no second state machine
# ---------------------------------------------------------------------------

class TestHandlerDelegatesToHandleRun:
    def test_delegates_with_translated_namespace(self) -> None:
        args = argparse.Namespace(
            plan="my-plan.json",
            task_id="task-42",
            max_parallel=3,
            max_steps=500,
            dry_run=True,
            resume=False,
        )
        with patch(f"{_EXECUTE_MOD}._handle_run") as mock_handle_run:
            run_mod.handler(args)

        mock_handle_run.assert_called_once()
        (delegate_ns,), _ = mock_handle_run.call_args
        assert delegate_ns.subcommand == "run"
        assert delegate_ns.plan == "my-plan.json"
        assert delegate_ns.task_id == "task-42"
        assert delegate_ns.dry_run is True
        assert delegate_ns.max_steps == 500
        # The historic bug: nothing in the delegate path should carry a
        # task_id kwarg into ExecutionEngine.start() -- there simply is no
        # such call site left in run.py to make that mistake in.
        assert not hasattr(delegate_ns, "start_task_id")

    def test_does_not_construct_its_own_engine_or_worker(self) -> None:
        """run.py must not import/construct ExecutionEngine or TaskWorker
        directly anymore -- that would be retaining a second state machine."""
        import inspect

        source = inspect.getsource(run_mod)
        assert "ExecutionEngine(" not in source
        assert "TaskWorker(" not in source
        assert "BatonRunner(" not in source

    def test_max_parallel_warns_but_does_not_crash(self, capsys: pytest.CaptureFixture) -> None:
        args = argparse.Namespace(
            plan="my-plan.json", task_id=None, max_parallel=8,
            max_steps=50, dry_run=True, resume=False,
        )
        with patch(f"{_EXECUTE_MOD}._handle_run"):
            run_mod.handler(args)
        out = capsys.readouterr().out
        assert "max-parallel" in out.lower() or "max_parallel" in out.lower()


# ---------------------------------------------------------------------------
# 3. End-to-end: baton run --dry-run must not crash (the regression)
# ---------------------------------------------------------------------------

class TestDryRunEndToEndDoesNotCrash:
    def test_dry_run_completes_without_type_error(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_MINIMAL_PLAN), encoding="utf-8")

        storage = _FakeStorage()
        real_engine = ExecutionEngine(team_context_root=tmp_path)

        args = argparse.Namespace(
            plan=str(plan_path),
            task_id=None,
            max_parallel=3,
            max_steps=50,
            dry_run=True,
            resume=False,
        )

        with (
            patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=storage),
            patch(f"{_EXECUTE_MOD}.ExecutionEngine", return_value=real_engine),
            patch(f"{_EXECUTE_MOD}.ContextManager"),
            patch("agent_baton.core.storage.sync.auto_sync_current_project", return_value=None),
            patch(f"{_EXECUTE_MOD}.detect_backend", return_value="file"),
            patch(f"{_EXECUTE_MOD}.StatePersistence.get_active_task_id", return_value=None),
        ):
            # Must not raise -- this is the exact scenario that used to
            # raise TypeError: ExecutionEngine.start() got an unexpected
            # keyword argument 'task_id'.
            run_mod.handler(args)

        status = real_engine.status()
        assert status.get("status") == "no_active_execution", (
            "dry-run must not mutate persisted execution state"
        )
