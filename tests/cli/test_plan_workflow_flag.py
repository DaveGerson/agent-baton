"""Tests for ``baton plan --workflow`` (and the ``baton goal`` passthrough).

Spec: docs/internal/adversarial-tdd-workflow-design.md decision #9 and the
CLI interaction matrix in §5. Mirrors the ``--manager-mode`` wiring tests in
``tests/manager/test_manager_mode_flag.py``: build the real argparse parser
via ``plan_cmd.register``, stub ``IntelligentPlanner`` so no stack detection /
risk classification / knowledge scanning runs, then call ``plan_cmd.handler``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from agent_baton.cli.commands import goal_cmd
from agent_baton.cli.commands.execution import plan_cmd
from agent_baton.models.execution import MachinePlan

from tests.workflow._plans import build_base_plan

STAGE_PHASE_NAMES = [
    "Brainstorm & Spec",
    "Architecture",
    "Test Authoring",
    "Test Verification",
    "Implementation",
    "Implementation Verification",
    "Final Review",
]

STAGE_TIERS = {
    "Brainstorm & Spec": "fable",
    "Architecture": "fable",
    "Test Authoring": "opus",
    "Test Verification": "opus",
    "Implementation": "sonnet",
    "Implementation Verification": "opus",
    "Final Review": "fable",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="baton")
    subparsers = parser.add_subparsers()
    plan_cmd.register(subparsers)
    return parser


def _goal_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="baton")
    subparsers = parser.add_subparsers(dest="cmd")
    goal_cmd.register(subparsers)
    return parser


def _install_stub_planner(monkeypatch: Any, captured: dict[str, Any]) -> None:
    """Replace ``IntelligentPlanner`` with a stub returning a real base plan.

    The plan is the shared Design/Implement/Test/Review fixture from
    ``tests/workflow/_plans.py``, so the reshaped output exercised here is the
    same shape the applier unit tests pin.
    """

    class _StubPlanner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def create_plan(self, summary: str, **kwargs: Any) -> MachinePlan:
            plan = build_base_plan()
            plan.task_id = "task-workflow-cli"
            plan.task_summary = summary
            captured["plan"] = plan
            return plan

        def explain_plan(self, plan: MachinePlan) -> str:
            return "stub explanation"

    monkeypatch.setattr(plan_cmd, "IntelligentPlanner", _StubPlanner)


def _run_plan(monkeypatch: Any, tmp_path: Path, argv: list[str]) -> dict[str, Any]:
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}
    _install_stub_planner(monkeypatch, captured)
    plan_cmd.handler(_build_parser().parse_args(argv))
    return captured


def _step_models(payload: dict[str, Any], phase_name: str) -> set[str]:
    phase = next(p for p in payload["phases"] if p["name"] == phase_name)
    models: set[str] = set()
    for step in phase["steps"]:
        if step.get("team"):
            models.update(member["model"] for member in step["team"])
        else:
            models.add(step["model"])
    return models


# ---------------------------------------------------------------------------
# Flag parsing
# ---------------------------------------------------------------------------

class TestWorkflowFlagParsing:
    def test_default_is_unset(self) -> None:
        args = _build_parser().parse_args(["plan", "do the thing"])
        assert getattr(args, "workflow", None) is None

    def test_accepts_a_preset_name(self) -> None:
        args = _build_parser().parse_args(
            ["plan", "do the thing", "--workflow", "adversarial-tdd"]
        )
        assert args.workflow == "adversarial-tdd"


# ---------------------------------------------------------------------------
# Unknown workflow (decision #9)
# ---------------------------------------------------------------------------

class TestUnknownWorkflow:
    def test_exits_with_validation_code(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _run_plan(
                monkeypatch,
                tmp_path,
                ["plan", "do the thing", "--workflow", "no-such-workflow", "--json"],
            )
        assert exc_info.value.code == 2
        capsys.readouterr()

    def test_error_lists_available_presets(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        with pytest.raises(SystemExit):
            _run_plan(
                monkeypatch,
                tmp_path,
                ["plan", "do the thing", "--workflow", "no-such-workflow", "--json"],
            )

        stderr = capsys.readouterr().err
        assert "no-such-workflow" in stderr
        assert "adversarial-tdd" in stderr
        assert "Traceback (most recent call last)" not in stderr


# ---------------------------------------------------------------------------
# Mutual exclusion (decision #7)
# ---------------------------------------------------------------------------

class TestMutualExclusion:
    def test_manager_mode_is_rejected(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _run_plan(
                monkeypatch,
                tmp_path,
                [
                    "plan",
                    "do the thing",
                    "--workflow",
                    "adversarial-tdd",
                    "--manager-mode",
                    "--json",
                ],
            )

        assert exc_info.value.code == 2
        stderr = capsys.readouterr().err.lower()
        assert "--workflow" in stderr
        assert "--manager-mode" in stderr

    def test_import_is_rejected(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        # A *valid* plan file, so that a missing guard would succeed (exit 0)
        # rather than fail for an unrelated reason.
        import_path = tmp_path / "handmade.json"
        import_path.write_text(
            json.dumps(
                {
                    "task_id": "task-imported",
                    "task_summary": "hand-crafted plan",
                    "phases": [],
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(SystemExit) as exc_info:
            _run_plan(
                monkeypatch,
                tmp_path,
                [
                    "plan",
                    "do the thing",
                    "--workflow",
                    "adversarial-tdd",
                    "--import",
                    str(import_path),
                    "--json",
                ],
            )

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "--workflow" in captured.err.lower()
        assert "--import" in captured.err.lower()
        # The import branch must not have run.
        assert "task-imported" not in captured.out


# ---------------------------------------------------------------------------
# Staged plan output (acceptance criterion #1)
# ---------------------------------------------------------------------------

class TestStagedPlanOutput:
    def _payload(self, monkeypatch: Any, tmp_path: Path, capsys: Any) -> dict[str, Any]:
        _run_plan(
            monkeypatch,
            tmp_path,
            ["plan", "add rate limiting", "--workflow", "adversarial-tdd", "--json"],
        )
        return json.loads(capsys.readouterr().out)

    def test_plan_root_records_the_workflow(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        payload = self._payload(monkeypatch, tmp_path, capsys)
        assert payload["workflow"] == "adversarial-tdd"

    def test_phases_are_the_seven_stages(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        payload = self._payload(monkeypatch, tmp_path, capsys)
        assert [p["name"] for p in payload["phases"]] == STAGE_PHASE_NAMES

    def test_model_tiers_match_the_stage_table(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        payload = self._payload(monkeypatch, tmp_path, capsys)
        for phase_name, tier in STAGE_TIERS.items():
            assert _step_models(payload, phase_name) == {tier}, phase_name

    def test_every_step_carries_a_workflow_stage(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        payload = self._payload(monkeypatch, tmp_path, capsys)
        for phase in payload["phases"]:
            for step in phase["steps"]:
                assert step.get("workflow_stage")

    def test_workflow_decisions_are_recorded(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        payload = self._payload(monkeypatch, tmp_path, capsys)
        assert payload["plan_diagnostics"]["workflow"]["workflow"] == (
            "adversarial-tdd"
        )

    def test_without_the_flag_the_plan_is_unchanged(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        # Acceptance criterion #5: plans without --workflow stay byte-identical
        # (both new fields absent from the JSON).
        _run_plan(monkeypatch, tmp_path, ["plan", "add rate limiting", "--json"])
        payload = json.loads(capsys.readouterr().out)

        assert "workflow" not in payload
        assert [p["name"] for p in payload["phases"]] == [
            "Design",
            "Implement",
            "Test",
            "Review",
        ]
        for phase in payload["phases"]:
            for step in phase["steps"]:
                assert "workflow_stage" not in step

    def test_saved_plan_json_carries_the_workflow(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        _run_plan(
            monkeypatch,
            tmp_path,
            ["plan", "add rate limiting", "--workflow", "adversarial-tdd", "--save"],
        )
        saved = json.loads(
            (tmp_path / ".claude" / "team-context" / "plan.json").read_text(
                encoding="utf-8"
            )
        )
        assert saved["workflow"] == "adversarial-tdd"
        assert [p["name"] for p in saved["phases"]] == STAGE_PHASE_NAMES


# ---------------------------------------------------------------------------
# `baton goal` passthrough
# ---------------------------------------------------------------------------

class TestGoalPassthrough:
    def test_goal_accepts_the_flag(self) -> None:
        args = _goal_parser().parse_args(
            ["goal", "all tests pass", "--workflow", "adversarial-tdd"]
        )
        assert args.workflow == "adversarial-tdd"

    def test_goal_delegates_the_workflow_to_plan(self, monkeypatch: Any) -> None:
        args = _goal_parser().parse_args(
            ["goal", "all tests pass", "--workflow", "adversarial-tdd", "--no-execute"]
        )
        delegated: dict[str, Any] = {}
        monkeypatch.setattr(
            plan_cmd, "handler", lambda ns: delegated.update(namespace=ns)
        )

        goal_cmd.handler(args)

        assert delegated["namespace"].workflow == "adversarial-tdd"

    def test_goal_without_the_flag_passes_none(self, monkeypatch: Any) -> None:
        args = _goal_parser().parse_args(["goal", "all tests pass", "--no-execute"])
        delegated: dict[str, Any] = {}
        monkeypatch.setattr(
            plan_cmd, "handler", lambda ns: delegated.update(namespace=ns)
        )

        goal_cmd.handler(args)

        assert getattr(delegated["namespace"], "workflow", None) is None
