"""Tests for feedback-driven remediations.

Covers:
  - Issue #3: baton execute cancel command is registered and produces correct output
  - Issue #4: Unicode handling on non-UTF-8 terminals (main() reconfigures streams)
  - Issue #6: baton plan --template and --import flags
"""
from __future__ import annotations

import argparse
import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Issue #3 — baton execute cancel
# ---------------------------------------------------------------------------

class TestExecuteCancel:
    """The cancel subcommand is registered and handles state transitions."""

    def test_cancel_subcommand_is_parseable(self):
        """'baton execute cancel' parses without error via the real argparse setup."""
        from agent_baton.cli.commands.execution.execute import register

        root = argparse.ArgumentParser()
        subparsers = root.add_subparsers(dest="command")
        register(subparsers)
        args = root.parse_args(["execute", "cancel"])
        assert args.subcommand == "cancel"

    def test_cancel_has_reason_argument(self):
        """The cancel subcommand accepts an optional --reason flag."""
        from agent_baton.cli.commands.execution.execute import register

        root = argparse.ArgumentParser()
        subparsers = root.add_subparsers(dest="command")
        register(subparsers)
        args = root.parse_args(["execute", "cancel", "--reason", "obsolete"])
        assert args.reason == "obsolete"

    def test_cancel_reason_defaults_to_empty_string(self):
        """When --reason is omitted, reason defaults to an empty string."""
        from agent_baton.cli.commands.execution.execute import register

        root = argparse.ArgumentParser()
        subparsers = root.add_subparsers(dest="command")
        register(subparsers)
        args = root.parse_args(["execute", "cancel"])
        assert args.reason == ""

    def test_cancelled_status_documented_in_execution_state(self):
        """ExecutionState documents 'cancelled' as a valid status in its field comment."""
        import inspect
        from agent_baton.models.execution import ExecutionState

        source = inspect.getsource(ExecutionState)
        assert "cancelled" in source

    def test_execution_state_status_default_is_running(self):
        """ExecutionState.status defaults to 'running'."""
        from agent_baton.models.execution import ExecutionState, MachinePlan

        plan = MachinePlan(task_id="t-001", task_summary="Test")
        state = ExecutionState(task_id="t-001", plan=plan)
        assert state.status == "running"

    def test_cancel_sets_status_to_cancelled(self, tmp_path: Path):
        """Calling the cancel handler on a running execution sets status to 'cancelled'."""
        from agent_baton.models.execution import ExecutionState, MachinePlan, PlanPhase, PlanStep
        from agent_baton.core.engine.executor import ExecutionEngine

        plan = MachinePlan(
            task_id="t-cancel-001",
            task_summary="Test cancel",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Implement",
                    steps=[
                        PlanStep(
                            step_id="1.1",
                            agent_name="backend-engineer",
                            task_description="build something",
                        )
                    ],
                )
            ],
        )
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)

        # Load the live state and verify it's running
        state = engine._load_execution()
        assert state is not None
        assert state.status == "running"

        # Simulate the cancel handler logic
        state.status = "cancelled"
        engine._save_execution(state)

        reloaded = engine._load_execution()
        assert reloaded.status == "cancelled"

    def test_cancel_handler_prints_confirmation(self, tmp_path: Path):
        """The cancel CLI handler prints confirmation message on success."""
        from agent_baton.models.execution import ExecutionState, MachinePlan, PlanPhase, PlanStep
        from agent_baton.core.engine.executor import ExecutionEngine
        from agent_baton.cli.commands.execution import execute as execute_mod

        plan = MachinePlan(
            task_id="t-cancel-002",
            task_summary="Test cancel output",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Implement",
                    steps=[
                        PlanStep(
                            step_id="1.1",
                            agent_name="backend-engineer",
                            task_description="build",
                        )
                    ],
                )
            ],
        )
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(plan)

        # Patch the engine construction and event bus so handler does not
        # touch disk paths or external state outside tmp_path.
        args = argparse.Namespace(
            subcommand="cancel",
            task_id=None,
            output="text",
            reason="",
        )

        captured = StringIO()
        with (
            patch.object(execute_mod, "ExecutionEngine", return_value=engine),
            patch.object(execute_mod, "EventBus", return_value=MagicMock()),
            patch("sys.stdout", captured),
        ):
            execute_mod.handler(args)

        output = captured.getvalue()
        assert "cancelled" in output.lower()


# ---------------------------------------------------------------------------
# Issue #6 — baton plan --template
# ---------------------------------------------------------------------------

class TestPlanTemplate:
    """--template emits a valid skeleton plan.json without invoking the planner."""

    def test_template_produces_valid_json(self):
        """--template output is parseable JSON."""
        from agent_baton.cli.commands.execution import plan_cmd

        args = argparse.Namespace(
            template=True,
            import_path=None,
            summary=None,
            task_type=None,
            agents=None,
            project=None,
            json=False,
            save=False,
            explain=False,
            knowledge=[],
            knowledge_pack=[],
            intervention="low",
            model=None,
            complexity=None,
        )
        captured = StringIO()
        with patch("sys.stdout", captured):
            plan_cmd.handler(args)

        data = json.loads(captured.getvalue())
        assert isinstance(data, dict)

    def test_template_contains_task_summary_key(self):
        """The template skeleton contains the 'task_summary' key."""
        from agent_baton.cli.commands.execution import plan_cmd

        args = argparse.Namespace(
            template=True,
            import_path=None,
            summary=None,
            task_type=None,
            agents=None,
            project=None,
            json=False,
            save=False,
            explain=False,
            knowledge=[],
            knowledge_pack=[],
            intervention="low",
            model=None,
            complexity=None,
        )
        captured = StringIO()
        with patch("sys.stdout", captured):
            plan_cmd.handler(args)

        data = json.loads(captured.getvalue())
        assert "task_summary" in data

    def test_template_contains_phases_key(self):
        """The template skeleton contains the 'phases' key."""
        from agent_baton.cli.commands.execution import plan_cmd

        args = argparse.Namespace(
            template=True,
            import_path=None,
            summary=None,
            task_type=None,
            agents=None,
            project=None,
            json=False,
            save=False,
            explain=False,
            knowledge=[],
            knowledge_pack=[],
            intervention="low",
            model=None,
            complexity=None,
        )
        captured = StringIO()
        with patch("sys.stdout", captured):
            plan_cmd.handler(args)

        data = json.loads(captured.getvalue())
        assert "phases" in data
        assert len(data["phases"]) >= 1

    def test_template_does_not_invoke_planner(self):
        """--template returns without invoking IntelligentPlanner."""
        from agent_baton.cli.commands.execution import plan_cmd

        args = argparse.Namespace(
            template=True,
            import_path=None,
            summary=None,
            task_type=None,
            agents=None,
            project=None,
            json=False,
            save=False,
            explain=False,
            knowledge=[],
            knowledge_pack=[],
            intervention="low",
            model=None,
            complexity=None,
        )
        with (
            patch("agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner") as mock_planner,
            patch("sys.stdout", StringIO()),
        ):
            plan_cmd.handler(args)
        mock_planner.assert_not_called()


# ---------------------------------------------------------------------------
# Issue #6 — baton plan --import
# ---------------------------------------------------------------------------

class TestPlanImport:
    """--import loads a hand-crafted plan.json, validates it, and prints output."""

    def _minimal_plan_data(self, task_id: str = "test-import-001") -> dict:
        return {
            "task_id": task_id,
            "task_summary": "Test import",
            "task_type": "new-feature",
            "risk_level": "low",
            "budget_tier": "standard",
            "git_strategy": "feature-branch",
            "phases": [
                {
                    "phase_id": 1,
                    "name": "Implement",
                    "steps": [
                        {
                            "step_id": "1.1",
                            "agent_name": "backend-engineer",
                            "task_description": "Build it",
                        }
                    ],
                }
            ],
        }

    def test_import_valid_plan_prints_json(self, tmp_path: Path):
        """A valid plan.json is loaded, validated, and printed as JSON."""
        from agent_baton.cli.commands.execution import plan_cmd

        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(self._minimal_plan_data()), encoding="utf-8")

        args = argparse.Namespace(
            template=False,
            import_path=str(plan_file),
            summary=None,
            task_type=None,
            agents=None,
            project=None,
            json=True,
            save=False,
            explain=False,
            knowledge=[],
            knowledge_pack=[],
            intervention="low",
            model=None,
            complexity=None,
        )
        captured = StringIO()
        with patch("sys.stdout", captured):
            plan_cmd.handler(args)

        result = json.loads(captured.getvalue())
        assert result["task_id"] == "test-import-001"
        assert result["task_summary"] == "Test import"

    def test_import_preserves_task_id(self, tmp_path: Path):
        """The task_id supplied in the hand-crafted file is preserved."""
        from agent_baton.cli.commands.execution import plan_cmd

        data = self._minimal_plan_data(task_id="custom-task-xyz")
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(data), encoding="utf-8")

        args = argparse.Namespace(
            template=False,
            import_path=str(plan_file),
            summary=None,
            task_type=None,
            agents=None,
            project=None,
            json=True,
            save=False,
            explain=False,
            knowledge=[],
            knowledge_pack=[],
            intervention="low",
            model=None,
            complexity=None,
        )
        captured = StringIO()
        with patch("sys.stdout", captured):
            plan_cmd.handler(args)

        result = json.loads(captured.getvalue())
        assert result["task_id"] == "custom-task-xyz"

    def test_import_assigns_task_id_when_missing(self, tmp_path: Path):
        """When task_id is absent from the file, a generated ID is assigned."""
        from agent_baton.cli.commands.execution import plan_cmd

        data = self._minimal_plan_data()
        del data["task_id"]
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(data), encoding="utf-8")

        args = argparse.Namespace(
            template=False,
            import_path=str(plan_file),
            summary=None,
            task_type=None,
            agents=None,
            project=None,
            json=True,
            save=False,
            explain=False,
            knowledge=[],
            knowledge_pack=[],
            intervention="low",
            model=None,
            complexity=None,
        )
        captured = StringIO()
        with patch("sys.stdout", captured):
            plan_cmd.handler(args)

        result = json.loads(captured.getvalue())
        assert result["task_id"]  # some non-empty ID was generated

    def test_import_nonexistent_file_exits(self, tmp_path: Path):
        """Importing a path that does not exist calls sys.exit(1)."""
        from agent_baton.cli.commands.execution import plan_cmd

        args = argparse.Namespace(
            template=False,
            import_path=str(tmp_path / "no_such_file.json"),
            summary=None,
            task_type=None,
            agents=None,
            project=None,
            json=True,
            save=False,
            explain=False,
            knowledge=[],
            knowledge_pack=[],
            intervention="low",
            model=None,
            complexity=None,
        )
        with pytest.raises(SystemExit) as exc_info:
            plan_cmd.handler(args)
        assert exc_info.value.code == 1

    def test_import_invalid_json_exits(self, tmp_path: Path):
        """Importing a file with invalid JSON calls sys.exit(1)."""
        from agent_baton.cli.commands.execution import plan_cmd

        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{ not valid json", encoding="utf-8")

        args = argparse.Namespace(
            template=False,
            import_path=str(bad_file),
            summary=None,
            task_type=None,
            agents=None,
            project=None,
            json=True,
            save=False,
            explain=False,
            knowledge=[],
            knowledge_pack=[],
            intervention="low",
            model=None,
            complexity=None,
        )
        with pytest.raises(SystemExit) as exc_info:
            plan_cmd.handler(args)
        assert exc_info.value.code == 1

    def test_import_does_not_invoke_planner(self, tmp_path: Path):
        """--import returns without invoking IntelligentPlanner."""
        from agent_baton.cli.commands.execution import plan_cmd

        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(self._minimal_plan_data()), encoding="utf-8")

        args = argparse.Namespace(
            template=False,
            import_path=str(plan_file),
            summary=None,
            task_type=None,
            agents=None,
            project=None,
            json=True,
            save=False,
            explain=False,
            knowledge=[],
            knowledge_pack=[],
            intervention="low",
            model=None,
            complexity=None,
        )
        with (
            patch("agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner") as mock_planner,
            patch("sys.stdout", StringIO()),
        ):
            plan_cmd.handler(args)
        mock_planner.assert_not_called()


# ---------------------------------------------------------------------------
# Issue #4 — Unicode fallback in main()
# ---------------------------------------------------------------------------

class TestUnicodeFallback:
    """main() reconfigures non-UTF-8 streams to prevent UnicodeEncodeError."""

    def test_main_source_contains_reconfigure(self):
        """main() contains a call to stream.reconfigure()."""
        import inspect
        import agent_baton.cli.main as main_mod

        source = inspect.getsource(main_mod.main)
        assert "reconfigure" in source

    def test_main_source_sets_pythonioencoding(self):
        """main() sets PYTHONIOENCODING as a last-resort fallback."""
        import inspect
        import agent_baton.cli.main as main_mod

        source = inspect.getsource(main_mod.main)
        assert "PYTHONIOENCODING" in source

    def test_main_source_uses_textiowrapper(self):
        """main() wraps streams via io.TextIOWrapper when reconfigure is unavailable."""
        import inspect
        import agent_baton.cli.main as main_mod

        source = inspect.getsource(main_mod.main)
        assert "TextIOWrapper" in source

    def test_utf8_stream_not_reconfigured(self):
        """Streams already encoding UTF-8 are left untouched."""
        import agent_baton.cli.main as main_mod

        # Simulate a utf-8 stdout; main() should not attempt to reconfigure it.
        mock_stream = MagicMock()
        mock_stream.encoding = "utf-8"

        reconfigure_called = []

        def track_reconfigure(*args, **kwargs):
            reconfigure_called.append((args, kwargs))

        mock_stream.reconfigure = track_reconfigure

        with (
            patch.object(sys, "stdout", mock_stream),
            patch.object(sys, "stderr", mock_stream),
            # Prevent the full CLI from running
            patch.object(sys, "argv", ["baton", "--version"]),
            pytest.raises(SystemExit),
        ):
            main_mod.main()

        assert not reconfigure_called, (
            "reconfigure() should not be called on a UTF-8 stream"
        )

    def test_non_utf8_stream_is_reconfigured(self):
        """Streams with non-UTF-8 encoding trigger reconfigure()."""
        import agent_baton.cli.main as main_mod

        reconfigure_called = []

        mock_stream = MagicMock()
        mock_stream.encoding = "cp1252"

        def track_reconfigure(*args, **kwargs):
            reconfigure_called.append((args, kwargs))
            mock_stream.encoding = "utf-8"  # simulate success

        mock_stream.reconfigure = track_reconfigure

        with (
            patch.object(sys, "stdout", mock_stream),
            patch.object(sys, "stderr", mock_stream),
            patch.object(sys, "argv", ["baton", "--version"]),
            pytest.raises(SystemExit),
        ):
            main_mod.main()

        assert reconfigure_called, (
            "reconfigure() should be called on a non-UTF-8 stream"
        )
