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

from tests.workflow._plans import build_base_plan, build_gateless_plan

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


def _install_stub_planner(
    monkeypatch: Any,
    captured: dict[str, Any],
    plan_factory: Any = build_base_plan,
) -> None:
    """Replace ``IntelligentPlanner`` with a stub returning a real base plan.

    The default plan is the shared Design/Implement/Test/Review fixture from
    ``tests/workflow/_plans.py``, so the reshaped output exercised here is the
    same shape the applier unit tests pin. *plan_factory* lets a test swap in
    a different base shape (e.g. a gateless plan for the fallback-gate test).
    """

    class _StubPlanner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def create_plan(self, summary: str, **kwargs: Any) -> MachinePlan:
            plan = plan_factory()
            plan.task_id = "task-workflow-cli"
            plan.task_summary = summary
            captured["plan"] = plan
            return plan

        def explain_plan(self, plan: MachinePlan) -> str:
            return "stub explanation"

    monkeypatch.setattr(plan_cmd, "IntelligentPlanner", _StubPlanner)


def _run_plan(
    monkeypatch: Any,
    tmp_path: Path,
    argv: list[str],
    *,
    plan_factory: Any = build_base_plan,
) -> dict[str, Any]:
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}
    _install_stub_planner(monkeypatch, captured, plan_factory)
    plan_cmd.handler(_build_parser().parse_args(argv))
    return captured


def _write_baton_yaml(tmp_path: Path, text: str) -> None:
    config_dir = tmp_path / ".claude"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "baton.yaml").write_text(text, encoding="utf-8")


def _phase(payload: dict[str, Any], name: str) -> dict[str, Any]:
    return next(p for p in payload["phases"] if p["name"] == name)


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
    def test_flag_exists_and_defaults_to_none(self) -> None:
        # `vars()`, not getattr-with-default: the attribute must actually be
        # declared by register(), which getattr(..., None) cannot distinguish
        # from the flag not existing at all.
        args = _build_parser().parse_args(["plan", "do the thing"])
        assert "workflow" in vars(args)
        assert args.workflow is None

    def test_accepts_a_preset_name(self) -> None:
        args = _build_parser().parse_args(
            ["plan", "do the thing", "--workflow", "adversarial-tdd"]
        )
        assert args.workflow == "adversarial-tdd"


# ---------------------------------------------------------------------------
# Unknown workflow (decision #9)
# ---------------------------------------------------------------------------

class TestUnknownWorkflow:
    def test_handled_error_exits_2_and_lists_available_presets(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        """A *handled* ``UnknownWorkflowError``, not an argparse rejection.

        Exit code 2 alone proves nothing here: before ``--workflow`` exists,
        argparse itself exits 2 with "unrecognized arguments". The message
        assertions below are what distinguish the two — argparse never names
        the available presets.
        """
        with pytest.raises(SystemExit) as exc_info:
            _run_plan(
                monkeypatch,
                tmp_path,
                ["plan", "do the thing", "--workflow", "no-such-workflow", "--json"],
            )

        assert exc_info.value.code == 2

        stderr = capsys.readouterr().err
        assert "unrecognized arguments" not in stderr
        assert "no-such-workflow" in stderr
        assert "adversarial-tdd" in stderr
        assert "Traceback (most recent call last)" not in stderr

    def test_unknown_workflow_is_rejected_before_any_planning_runs(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        # MINOR-1: get_workflow_preset() is resolved in the top-of-handler
        # guard block, before create_plan() is ever invoked -- an unknown
        # preset name must not waste a planning run (stack detection, risk
        # classification, knowledge scanning).
        monkeypatch.chdir(tmp_path)
        captured: dict[str, Any] = {}
        _install_stub_planner(monkeypatch, captured)

        with pytest.raises(SystemExit) as exc_info:
            plan_cmd.handler(
                _build_parser().parse_args(
                    ["plan", "do the thing", "--workflow", "no-such-workflow", "--json"]
                )
            )

        assert exc_info.value.code == 2
        assert "plan" not in captured, "create_plan() must not have run"

        stderr = capsys.readouterr().err
        assert "Planning..." not in stderr
        assert "Analyzing patterns" not in stderr


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
# baton.yaml wiring (acceptance criterion #4)
# ---------------------------------------------------------------------------

class TestBatonYamlWiring:
    """Proves ``plan_cmd`` actually calls ``load_workflow_settings``.

    Every other CLI assertion here passes with hardcoded preset defaults;
    these two only pass if project config reaches the applier.
    """

    _CONFIG = (
        "workflow:\n"
        "  stages:\n"
        "    spec:\n"
        "      model: haiku\n"
        '  external_command: "codex verify"\n'
    )

    def _payload(self, monkeypatch: Any, tmp_path: Path, capsys: Any) -> dict[str, Any]:
        _write_baton_yaml(tmp_path, self._CONFIG)
        _run_plan(
            monkeypatch,
            tmp_path,
            ["plan", "add rate limiting", "--workflow", "adversarial-tdd", "--json"],
        )
        return json.loads(capsys.readouterr().out)

    def test_stage_model_override_reaches_the_plan(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        payload = self._payload(monkeypatch, tmp_path, capsys)
        # Preset default for this stage is fable; only config can make it haiku.
        assert _step_models(payload, "Brainstorm & Spec") == {"haiku"}

    def test_external_command_reaches_the_plan(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        payload = self._payload(monkeypatch, tmp_path, capsys)
        steps = _phase(payload, "Implementation Verification")["steps"]
        automation = [s for s in steps if s.get("step_type") == "automation"]
        assert len(automation) == 1
        assert automation[0]["command"] == "codex verify"
        assert automation[0]["agent_name"] == "task-runner"

    def test_absent_config_leaves_preset_defaults(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        _run_plan(
            monkeypatch,
            tmp_path,
            ["plan", "add rate limiting", "--workflow", "adversarial-tdd", "--json"],
        )
        payload = json.loads(capsys.readouterr().out)
        assert _step_models(payload, "Brainstorm & Spec") == {"fable"}
        steps = _phase(payload, "Implementation Verification")["steps"]
        assert all(s.get("step_type") != "automation" for s in steps)


# ---------------------------------------------------------------------------
# fallback-gate wiring (decision #4: IO happens in the CLI, not the applier)
# ---------------------------------------------------------------------------

class TestFallbackGateWiring:
    def test_gateless_base_plan_gets_a_gate_on_implementation(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        # The applier cannot call default_gate (it does filesystem IO), so a
        # gateless base plan only ends up gated if plan_cmd computed
        # fallback_gate itself and passed it in.
        _run_plan(
            monkeypatch,
            tmp_path,
            ["plan", "add rate limiting", "--workflow", "adversarial-tdd", "--json"],
            plan_factory=build_gateless_plan,
        )
        payload = json.loads(capsys.readouterr().out)

        gate = _phase(payload, "Implementation").get("gate")
        assert gate is not None, "plan_cmd must pass fallback_gate=default_gate(...)"
        assert gate.get("command")


# ---------------------------------------------------------------------------
# --dry-run (interaction matrix: applier runs before the forecast)
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_forecast_reflects_the_reshaped_plan(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        _run_plan(
            monkeypatch,
            tmp_path,
            [
                "plan",
                "add rate limiting",
                "--workflow",
                "adversarial-tdd",
                "--dry-run",
            ],
        )
        out = capsys.readouterr().out

        # 7 stage phases, not the base plan's 4.
        assert "Phases: 7" in out
        assert "Brainstorm & Spec" in out
        # fable-tier steps must be priced, i.e. the forecast saw the re-tiered
        # plan rather than the pre-reshape draft.
        assert "fable" in out

    def test_dry_run_writes_nothing(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        _run_plan(
            monkeypatch,
            tmp_path,
            [
                "plan",
                "add rate limiting",
                "--workflow",
                "adversarial-tdd",
                "--dry-run",
            ],
        )
        capsys.readouterr()
        assert not (tmp_path / ".claude" / "team-context" / "plan.json").exists()


# ---------------------------------------------------------------------------
# --explain renders WorkflowDecisions
# ---------------------------------------------------------------------------

class TestExplain:
    def _explanation(self, monkeypatch: Any, tmp_path: Path, capsys: Any) -> str:
        _run_plan(
            monkeypatch,
            tmp_path,
            [
                "plan",
                "add rate limiting",
                "--workflow",
                "adversarial-tdd",
                "--save",
                "--explain",
            ],
        )
        capsys.readouterr()
        return (tmp_path / ".claude" / "team-context" / "explanation.md").read_text(
            encoding="utf-8"
        )

    def test_explanation_names_the_workflow(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        assert "adversarial-tdd" in self._explanation(monkeypatch, tmp_path, capsys)

    def test_explanation_renders_the_stage_table(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        explanation = self._explanation(monkeypatch, tmp_path, capsys)
        for phase_name in STAGE_PHASE_NAMES:
            assert phase_name in explanation, phase_name
        for tier in ("fable", "opus", "sonnet"):
            assert tier in explanation, tier

    def test_planner_explanation_is_still_included(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        assert "stub explanation" in self._explanation(monkeypatch, tmp_path, capsys)

    def test_explanation_lists_dropped_steps(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        # The shared base plan's Design/Test/Review steps are neither
        # harvested nor carried over -- §5.3 requires the drop to be visible
        # in --explain, not only in plan_diagnostics["workflow"].
        explanation = self._explanation(monkeypatch, tmp_path, capsys)
        assert "Dropped base steps" in explanation
        assert "architect" in explanation  # the dropped Design step's agent

    def test_explanation_renders_auditor_drop_warning(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        from tests.workflow._plans import build_auditor_in_implement_phase_plan

        _run_plan(
            monkeypatch,
            tmp_path,
            [
                "plan",
                "add rate limiting",
                "--workflow",
                "adversarial-tdd",
                "--save",
                "--explain",
            ],
            plan_factory=build_auditor_in_implement_phase_plan,
        )
        capsys.readouterr()
        explanation = (
            tmp_path / ".claude" / "team-context" / "explanation.md"
        ).read_text(encoding="utf-8")
        assert "Workflow warnings" in explanation
        assert "auditor" in explanation


# ---------------------------------------------------------------------------
# §5.12 WorkflowReshapeError → exit 2 with an actionable message
# ---------------------------------------------------------------------------

class TestReshapeErrorHandling:
    _BAD_CONFIG = 'workflow:\n  stages:\n    spec: {agent: ""}\n'

    def test_invalid_override_exits_2_not_traceback(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        # `stages.spec.agent: ""` passes config validation (str | None) but
        # produces a step with an empty agent_name, which MachinePlan's own
        # validators reject -- the applier raises WorkflowReshapeError and
        # the CLI must turn that into a validation error, not a traceback.
        _write_baton_yaml(tmp_path, self._BAD_CONFIG)
        with pytest.raises(SystemExit) as excinfo:
            _run_plan(
                monkeypatch,
                tmp_path,
                ["plan", "add rate limiting", "--workflow", "adversarial-tdd"],
            )
        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        assert "baton.yaml" in err
        assert "adversarial-tdd" in err


# ---------------------------------------------------------------------------
# Config-default manager mode is suppressed, not an error (interaction matrix)
# ---------------------------------------------------------------------------

class TestConfigDefaultManagerModeSuppressed:
    _CONFIG = "manager_mode:\n  enabled_by_default: true\n"

    def _run(self, monkeypatch: Any, tmp_path: Path) -> None:
        _write_baton_yaml(tmp_path, self._CONFIG)
        _run_plan(
            monkeypatch,
            tmp_path,
            ["plan", "add rate limiting", "--workflow", "adversarial-tdd", "--save"],
        )

    def _saved(self, tmp_path: Path) -> dict[str, Any]:
        return json.loads(
            (tmp_path / ".claude" / "team-context" / "plan.json").read_text(
                encoding="utf-8"
            )
        )

    def test_does_not_error(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        # Only the explicit --manager-mode + --workflow combination is fatal.
        self._run(monkeypatch, tmp_path)
        capsys.readouterr()
        assert self._saved(tmp_path)["workflow"] == "adversarial-tdd"

    def test_warns_that_manager_mode_was_suppressed(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        self._run(monkeypatch, tmp_path)
        stderr = capsys.readouterr().err.lower()
        assert "manager mode" in stderr or "manager_mode" in stderr
        assert "workflow" in stderr

    def test_plan_is_not_flagged_manager_mode(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        self._run(monkeypatch, tmp_path)
        capsys.readouterr()
        assert self._saved(tmp_path)["manager_mode"] is False

    def test_no_adversarial_review_steps_are_injected(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        # PhasePolicyApplier stamps injected reviews with a "review-" step-id
        # prefix; decision #7 exists precisely to keep them out of the seven
        # workflow phases (three of which already ARE reviews).
        self._run(monkeypatch, tmp_path)
        capsys.readouterr()
        saved = self._saved(tmp_path)

        step_ids = [s["step_id"] for p in saved["phases"] for s in p["steps"]]
        assert not [sid for sid in step_ids if sid.startswith("review-")]
        assert [p["name"] for p in saved["phases"]] == STAGE_PHASE_NAMES

    def test_no_manager_artifacts_are_written(
        self, monkeypatch: Any, tmp_path: Path, capsys: Any
    ) -> None:
        self._run(monkeypatch, tmp_path)
        capsys.readouterr()
        executions = tmp_path / ".claude" / "team-context" / "executions"
        assert not list(executions.glob("*/project-charter.md"))


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
