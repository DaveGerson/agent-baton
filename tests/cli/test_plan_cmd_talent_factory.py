"""``baton plan`` -- talent-factory policy wiring (P5.2).

Verifies that ``--skip-init`` and the project's ``team.allow_talent_builder`` /
``talent_factory`` manager-config sections are actually threaded into
``IntelligentPlanner.create_plan()`` -- not just parsed and dropped. See
docs/internal/talent-factory-contract.md §11 item 1 and
agent_baton/core/engine/planning/talent_factory.py.
"""
from __future__ import annotations

import argparse
import contextlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution import plan_cmd
from agent_baton.core.config.manager import TalentFactoryConfig
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


def _make_minimal_plan() -> MachinePlan:
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer",
        task_description="Implement the thing",
        model="sonnet",
        depends_on=[],
        deliverables=[],
        allowed_paths=[],
        blocked_paths=[],
        context_files=[],
    )
    phase = PlanPhase(phase_id=1, name="Implement", steps=[step], approval_required=False)
    return MachinePlan(
        task_id="2026-01-01-talent-factory-wiring-aabb0011",
        task_summary="Talent-factory wiring test",
        risk_level="LOW",
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[phase],
        shared_context="",
        pattern_source=None,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _make_args(project_root: Path, *, skip_init: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        summary="do the thing",
        save=False,
        explain=False,
        json=False,
        verbose=False,
        import_path=None,
        template=False,
        task_type=None,
        agents=None,
        project=str(project_root),
        knowledge=[],
        knowledge_pack=[],
        intervention="low",
        model=None,
        complexity=None,
        skip_init=skip_init,
    )


def _run_handler_capturing_create_plan_call(args: argparse.Namespace, plan: MachinePlan) -> MagicMock:
    """Run handler() with heavy deps mocked; return the mock IntelligentPlanner."""
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = plan

    patches = [
        patch("agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner", return_value=mock_planner),
        patch("agent_baton.cli.commands.execution.plan_cmd.KnowledgeRegistry", return_value=MagicMock()),
        patch("agent_baton.cli.commands.execution.plan_cmd.RetrospectiveEngine", return_value=MagicMock()),
        patch("agent_baton.cli.commands.execution.plan_cmd.DataClassifier", return_value=MagicMock()),
        patch("agent_baton.cli.commands.execution.plan_cmd.PolicyEngine", return_value=MagicMock()),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        plan_cmd.handler(args)
    return mock_planner


class TestSkipInitWiring:
    def test_skip_init_flag_reaches_create_plan(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _make_minimal_plan()
        args = _make_args(tmp_path, skip_init=True)

        mock_planner = _run_handler_capturing_create_plan_call(args, plan)

        kwargs = mock_planner.create_plan.call_args.kwargs
        assert kwargs["skip_init"] is True

    def test_skip_init_defaults_false(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _make_minimal_plan()
        args = _make_args(tmp_path, skip_init=False)

        mock_planner = _run_handler_capturing_create_plan_call(args, plan)

        kwargs = mock_planner.create_plan.call_args.kwargs
        assert kwargs["skip_init"] is False


class TestAllowTalentBuilderWiring:
    def test_allow_talent_builder_defaults_true_with_no_config(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _make_minimal_plan()
        args = _make_args(tmp_path)

        mock_planner = _run_handler_capturing_create_plan_call(args, plan)

        kwargs = mock_planner.create_plan.call_args.kwargs
        assert kwargs["allow_talent_builder"] is True
        assert isinstance(kwargs["talent_factory_config"], TalentFactoryConfig)

    def test_project_baton_yaml_disables_talent_builder(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        (tmp_path / "baton.yaml").write_text(
            "team:\n  allow_talent_builder: false\n",
            encoding="utf-8",
        )
        plan = _make_minimal_plan()
        args = _make_args(tmp_path)

        mock_planner = _run_handler_capturing_create_plan_call(args, plan)

        kwargs = mock_planner.create_plan.call_args.kwargs
        assert kwargs["allow_talent_builder"] is False

    def test_project_baton_yaml_talent_factory_section_threaded(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        (tmp_path / "baton.yaml").write_text(
            "talent_factory:\n  retry_budget: 3\n  name_collision_policy: version_suffix\n",
            encoding="utf-8",
        )
        plan = _make_minimal_plan()
        args = _make_args(tmp_path)

        mock_planner = _run_handler_capturing_create_plan_call(args, plan)

        kwargs = mock_planner.create_plan.call_args.kwargs
        config = kwargs["talent_factory_config"]
        assert isinstance(config, TalentFactoryConfig)
        assert config.retry_budget == 3
        assert config.name_collision_policy == "version_suffix"
