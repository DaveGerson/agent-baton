"""Tests for ``baton learn run-cycle`` — plan invocation and --dry-run path.

Verifies:
1. The argparse subparser registers ``--plan`` on ``baton execute run``.
2. ``_cmd_run_cycle`` with ``--dry-run`` writes the plan file and prints the
   ``baton execute run --plan <path>`` command without calling subprocess.run.
3. ``_cmd_run_cycle`` without ``--run`` or ``--dry-run`` prints the manual
   instructions and returns without writing a plan file or calling subprocess.
4. ``_cmd_run_cycle`` with ``--run`` passes ``--plan <resolved path>`` as the
   first non-flag argument to subprocess.run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helper: minimal template that _cmd_run_cycle accepts
# ---------------------------------------------------------------------------

_MINIMAL_TEMPLATE: dict = {
    "_template_meta": {"name": "learning-cycle", "version": "1"},
    "task": "Learning cycle",
    "phases": [
        {
            "phase_id": 1,
            "name": "Collect",
            "description": "Gather data",
            "steps": [{"agent": "test-engineer", "description": "collect"}],
        }
    ],
}


def _write_template(tmp_path: Path) -> Path:
    tpl = tmp_path / "learning-cycle-plan.json"
    tpl.write_text(json.dumps(_MINIMAL_TEMPLATE), encoding="utf-8")
    return tpl


def _make_args(
    *,
    run: bool = False,
    dry_run: bool = False,
    template: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(run=run, dry_run=dry_run, template=template)


# ---------------------------------------------------------------------------
# 1. Argparse: --plan flag exists on ``baton execute run``
# ---------------------------------------------------------------------------

class TestExecuteRunArgparse:
    """Confirm --plan is registered on the ``baton execute run`` subparser."""

    def test_plan_flag_registered(self) -> None:
        from agent_baton.cli.commands.execution import execute

        root = argparse.ArgumentParser()
        subs = root.add_subparsers(dest="command")
        execute.register(subs)

        args = root.parse_args(
            ["execute", "run", "--plan", "/tmp/my-plan.json", "--dry-run"]
        )
        assert args.plan == "/tmp/my-plan.json"
        assert args.dry_run is True

    def test_plan_default_value(self) -> None:
        from agent_baton.cli.commands.execution import execute

        root = argparse.ArgumentParser()
        subs = root.add_subparsers(dest="command")
        execute.register(subs)

        args = root.parse_args(["execute", "run"])
        assert args.plan == ".claude/team-context/plan.json"


# ---------------------------------------------------------------------------
# 2. --dry-run: prints command, does NOT call subprocess.run
# ---------------------------------------------------------------------------

class TestRunCycleDryRun:
    def test_dry_run_prints_command_with_plan_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        from agent_baton.cli.commands.improve import learn_cmd

        tpl = _write_template(tmp_path)

        # Redirect team-context root so plan_dest lands in tmp_path
        monkeypatch.setattr(
            learn_cmd, "_team_context_root", lambda: tmp_path
        )

        args = _make_args(dry_run=True, template=str(tpl))

        with patch("subprocess.run") as mock_sub:
            learn_cmd._cmd_run_cycle(args)
            mock_sub.assert_not_called()

        out = capsys.readouterr().out
        assert "baton execute run" in out
        assert "--plan" in out
        # The path printed must point at the plan written in team-context
        plan_dest = tmp_path / "learning-cycle-plan.json"
        assert str(plan_dest) in out

    def test_dry_run_writes_plan_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        from agent_baton.cli.commands.improve import learn_cmd

        tpl = _write_template(tmp_path)
        monkeypatch.setattr(learn_cmd, "_team_context_root", lambda: tmp_path)

        args = _make_args(dry_run=True, template=str(tpl))
        with patch("subprocess.run"):
            learn_cmd._cmd_run_cycle(args)

        plan_dest = tmp_path / "learning-cycle-plan.json"
        assert plan_dest.exists(), "plan file must be written even in dry-run mode"


# ---------------------------------------------------------------------------
# 3. No --run, no --dry-run: just prints instructions, no side-effects
# ---------------------------------------------------------------------------

class TestRunCycleNoFlags:
    def test_prints_manual_instructions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        from agent_baton.cli.commands.improve import learn_cmd

        tpl = _write_template(tmp_path)
        monkeypatch.setattr(learn_cmd, "_team_context_root", lambda: tmp_path)

        args = _make_args(template=str(tpl))
        with patch("subprocess.run") as mock_sub:
            learn_cmd._cmd_run_cycle(args)
            mock_sub.assert_not_called()

        out = capsys.readouterr().out
        assert "baton learn run-cycle --run" in out

    def test_no_plan_file_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        from agent_baton.cli.commands.improve import learn_cmd

        tpl = _write_template(tmp_path)
        # Use a separate subdirectory so we can check no plan was written
        dest_dir = tmp_path / "ctx"
        dest_dir.mkdir()
        monkeypatch.setattr(learn_cmd, "_team_context_root", lambda: dest_dir)

        args = _make_args(template=str(tpl))
        with patch("subprocess.run"):
            learn_cmd._cmd_run_cycle(args)

        assert not (dest_dir / "learning-cycle-plan.json").exists()


# ---------------------------------------------------------------------------
# 4. --run: subprocess.run is called with --plan <resolved-path>
# ---------------------------------------------------------------------------

class TestRunCycleWithRun:
    def test_subprocess_receives_plan_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        from agent_baton.cli.commands.improve import learn_cmd

        tpl = _write_template(tmp_path)
        monkeypatch.setattr(learn_cmd, "_team_context_root", lambda: tmp_path)

        # Suppress TriggerEvaluator side-effects
        monkeypatch.setattr(
            "agent_baton.core.improve.triggers.TriggerEvaluator",
            MagicMock(),
        )

        args = _make_args(run=True, template=str(tpl))

        fake_result = MagicMock()
        fake_result.returncode = 0

        with patch("subprocess.run", return_value=fake_result) as mock_sub:
            learn_cmd._cmd_run_cycle(args)

        assert mock_sub.call_count == 1
        invoked_cmd: list[str] = mock_sub.call_args[0][0]

        # Must be: ["baton", "execute", "run", "--plan", "<path>"]
        assert invoked_cmd[0] == "baton"
        assert invoked_cmd[1] == "execute"
        assert invoked_cmd[2] == "run"
        assert "--plan" in invoked_cmd
        plan_idx = invoked_cmd.index("--plan")
        plan_path = Path(invoked_cmd[plan_idx + 1])
        assert plan_path.name == "learning-cycle-plan.json"
        assert plan_path.parent == tmp_path

    def test_plan_file_content_matches_template(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        from agent_baton.cli.commands.improve import learn_cmd

        tpl = _write_template(tmp_path)
        dest_dir = tmp_path / "ctx"
        dest_dir.mkdir()
        monkeypatch.setattr(learn_cmd, "_team_context_root", lambda: dest_dir)
        monkeypatch.setattr(
            "agent_baton.core.improve.triggers.TriggerEvaluator",
            MagicMock(),
        )

        args = _make_args(run=True, template=str(tpl))
        fake_result = MagicMock()
        fake_result.returncode = 0

        with patch("subprocess.run", return_value=fake_result):
            learn_cmd._cmd_run_cycle(args)

        written = dest_dir / "learning-cycle-plan.json"
        assert written.exists()
        data = json.loads(written.read_text(encoding="utf-8"))
        assert data["task"] == _MINIMAL_TEMPLATE["task"]
