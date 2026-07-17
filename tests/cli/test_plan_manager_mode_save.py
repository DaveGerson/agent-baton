"""Tests for ``baton plan --manager-mode --save|--explain|--dry-run`` CLI
output (Wave 3 fix-cycle, PRD §20 "Artifacts:" block).

Follows ``tests/cli/test_config_cli.py``'s harness style (fake ``Path.home``
so ``ManagerConfig.load()``'s ``~/.baton/config.yaml`` check and
``KnowledgeRegistry.load_default_paths()``'s ``~/.claude/knowledge`` check
never touch a real developer machine) combined with
``tests/cli/test_plan_dry_run.py``'s pattern of mocking ``IntelligentPlanner``
so the real 7-stage planning pipeline never runs -- only
``KnowledgeRegistry``, ``RetrospectiveEngine``... no: only the heavy
classifier/policy/retro dependencies are mocked. ``KnowledgeRegistry`` is
deliberately left real (but empty, since the fake project root has no
``.claude/knowledge/``) because ``ManagerModePlanner`` needs a genuine
registry object, not a ``MagicMock``, to compose artifacts correctly.

Three behaviors are pinned in one file (per Wave 3 review Minor 3):

- ``--manager-mode --save`` (no ``--explain``) prints an ``Artifacts:``
  block with the charter/scope-map/team-blueprint/manager-brief paths
  (PRD §20 example shape) -- this is the new behavior (RED before the
  fix landed).
- ``--manager-mode --explain --save`` still produces the ``## Manager
  Mode`` section in ``explanation.md`` (pre-existing behavior, pinned).
- ``--manager-mode --dry-run`` still prints the "Manager Mode artifacts
  (preview only)" list and writes nothing to ``.claude/team-context/``
  (pre-existing behavior, pinned).
"""
from __future__ import annotations

import argparse
import contextlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution import plan_cmd
from agent_baton.core.config.manager import TalentFactoryConfig
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fake_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: Any) -> Path:
    """Redirect ``Path.home()`` so ``ManagerConfig.load()``'s
    ``~/.baton/config.yaml`` lookup and ``KnowledgeRegistry
    .load_default_paths()``'s ``~/.claude/knowledge`` lookup never read a
    real developer machine's files (mirrors ``tests/cli/test_config_cli
    .py``'s ``_fake_home``)."""
    fake_home_dir = tmp_path_factory.mktemp("fake_home_plan_manager_save")
    monkeypatch.setattr(Path, "home", lambda: fake_home_dir)
    return fake_home_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plan(task_id: str = "2026-07-02-reporting-endpoint-aaaa1111") -> MachinePlan:
    """Two-phase plan (backend-engineer then test-engineer) -- mirrors
    ``tests/manager/test_manager_mode_planner.py``'s ``_two_phase_plan``."""
    return MachinePlan(
        task_id=task_id,
        task_summary="Add a reporting endpoint with tests and docs",
        task_type="feature",
        complexity="medium",
        detected_stack="python",
        risk_level="MEDIUM",
        budget_tier="standard",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Implement the reporting endpoint.",
                        deliverables=["app/reporting/service.py"],
                        allowed_paths=["app/reporting/**"],
                        step_type="developing",
                    ),
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Test",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="test-engineer",
                        task_description="Add tests for the reporting endpoint.",
                        deliverables=["tests/reporting/test_service.py"],
                        allowed_paths=["tests/reporting/**"],
                        depends_on=["1.1"],
                        step_type="testing",
                    ),
                ],
            ),
        ],
    )


def _make_args(
    *,
    save: bool = False,
    dry_run: bool = False,
    explain: bool = False,
    manager_mode: bool = False,
    verbose: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        summary="Add a reporting endpoint with tests and docs",
        save=save,
        dry_run=dry_run,
        explain=explain,
        json=False,
        verbose=verbose,
        manager_mode=manager_mode,
        import_path=None,
        template=False,
        task_type=None,
        agents=None,
        project=None,
        knowledge=[],
        knowledge_pack=[],
        intervention="low",
        model=None,
        complexity=None,
        save_as_template=None,
        from_template=None,
        skip_init=False,
        release_id=None,
        gate_scope=None,
        goal=None,
        max_amend_cycles=3,
    )


def _run_handler(
    args: argparse.Namespace,
    plan: MachinePlan,
    capsys: pytest.CaptureFixture,
) -> str:
    """Invoke handler with the real 7-stage planner replaced by a mock that
    returns *plan* directly; ``KnowledgeRegistry`` is deliberately left
    real (see module docstring) so ``ManagerModePlanner`` composes for
    real."""
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = plan
    mock_planner.explain_plan.return_value = "Why this plan."

    patches = [
        patch(
            "agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner",
            return_value=mock_planner,
        ),
        patch(
            "agent_baton.cli.commands.execution.plan_cmd.RetrospectiveEngine",
            return_value=MagicMock(),
        ),
        patch(
            "agent_baton.cli.commands.execution.plan_cmd.DataClassifier",
            return_value=MagicMock(),
        ),
        patch(
            "agent_baton.cli.commands.execution.plan_cmd.PolicyEngine",
            return_value=MagicMock(),
        ),
    ]

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        plan_cmd.handler(args)

    return capsys.readouterr().out


def _run_handler_capturing_planner_mock(
    args: argparse.Namespace,
    plan: MachinePlan,
) -> MagicMock:
    """Like ``_run_handler`` but returns the mocked ``IntelligentPlanner``
    instance instead of stdout, so a test can inspect
    ``create_plan.call_args`` -- used for talent-factory config-wiring
    assertions (P5, docs/internal/talent-factory-contract.md)."""
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = plan
    mock_planner.explain_plan.return_value = "Why this plan."

    patches = [
        patch(
            "agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner",
            return_value=mock_planner,
        ),
        patch(
            "agent_baton.cli.commands.execution.plan_cmd.RetrospectiveEngine",
            return_value=MagicMock(),
        ),
        patch(
            "agent_baton.cli.commands.execution.plan_cmd.DataClassifier",
            return_value=MagicMock(),
        ),
        patch(
            "agent_baton.cli.commands.execution.plan_cmd.PolicyEngine",
            return_value=MagicMock(),
        ),
    ]

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        plan_cmd.handler(args)

    return mock_planner


# ---------------------------------------------------------------------------
# --manager-mode --save (no --explain): PRD §20 "Artifacts:" block
# ---------------------------------------------------------------------------


def test_manager_mode_save_prints_artifacts_block(
    tmp_path: Path, monkeypatch: Any, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    plan = _plan()

    out = _run_handler(_make_args(save=True, manager_mode=True), plan, capsys)

    assert "Artifacts:" in out
    assert "project-charter.md" in out
    assert "scope-map.json" in out
    assert "team-blueprint.json" in out
    assert "manager-brief.md" in out


def test_non_manager_save_output_unchanged(
    tmp_path: Path, monkeypatch: Any, capsys: pytest.CaptureFixture
) -> None:
    """Regression guard: a plain (non-manager-mode) --save run must never
    print an Artifacts: block -- Fix 1 is manager-mode only."""
    monkeypatch.chdir(tmp_path)
    plan = _plan(task_id="2026-07-02-reporting-endpoint-plain0000")

    out = _run_handler(_make_args(save=True, manager_mode=False), plan, capsys)

    assert "Artifacts:" not in out
    assert f"Plan saved: {(tmp_path / '.claude' / 'team-context' / 'plan.json')}" in out
    assert "Next: baton execute start" in out


# ---------------------------------------------------------------------------
# --manager-mode --explain --save: ## Manager Mode section (pre-existing)
# ---------------------------------------------------------------------------


def test_manager_mode_explain_produces_manager_mode_section(
    tmp_path: Path, monkeypatch: Any, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    plan = _plan(task_id="2026-07-02-reporting-endpoint-bbbb2222")

    out = _run_handler(_make_args(save=True, explain=True, manager_mode=True), plan, capsys)

    ctx_dir = tmp_path / ".claude" / "team-context"
    explanation_path = ctx_dir / "explanation.md"
    assert explanation_path.is_file()
    assert "## Manager Mode" in explanation_path.read_text(encoding="utf-8")

    # Fix 1 also applies to the --explain branch.
    assert "Artifacts:" in out
    assert "project-charter.md" in out


# ---------------------------------------------------------------------------
# --manager-mode --dry-run: preview list, nothing written (pre-existing)
# ---------------------------------------------------------------------------


def test_manager_mode_dry_run_preview_writes_nothing(
    tmp_path: Path, monkeypatch: Any, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    plan = _plan(task_id="2026-07-02-reporting-endpoint-cccc3333")

    out = _run_handler(_make_args(dry_run=True, manager_mode=True), plan, capsys)

    assert "Manager Mode artifacts (preview only -- nothing written):" in out
    assert "project-charter.md" in out

    ctx_dir = tmp_path / ".claude" / "team-context"
    assert not ctx_dir.exists()


# ---------------------------------------------------------------------------
# --manager-mode still threads talent-factory policy through create_plan()
# ---------------------------------------------------------------------------
#
# ManagerConfig loading was moved earlier in plan_cmd.handler() (P5.2, see
# docs/internal/talent-factory-contract.md §11 item 1) so `--skip-init` /
# `team.allow_talent_builder` / `talent_factory` could reach
# IntelligentPlanner.create_plan(). tests/cli/test_plan_cmd_talent_factory.py
# covers that wiring for a plain (non-manager-mode) `baton plan`; the tests
# below pin the same wiring when `--manager-mode` is also requested, since
# manager mode is the other consumer of the same (now earlier) config load
# and a regression here would silently re-order the two without either
# suite catching it.


class TestManagerModeStillThreadsTalentFactoryConfig:
    def test_manager_mode_save_passes_default_talent_factory_config(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.chdir(tmp_path)
        plan = _plan(task_id="2026-07-02-reporting-endpoint-tf000001")

        mock_planner = _run_handler_capturing_planner_mock(
            _make_args(save=True, manager_mode=True), plan,
        )

        kwargs = mock_planner.create_plan.call_args.kwargs
        assert kwargs["skip_init"] is False
        assert kwargs["allow_talent_builder"] is True
        assert isinstance(kwargs["talent_factory_config"], TalentFactoryConfig)
        assert kwargs["talent_factory_config"].retry_budget == 1

    def test_manager_mode_save_honors_project_talent_factory_overrides(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "baton.yaml").write_text(
            "team:\n  allow_talent_builder: false\n"
            "talent_factory:\n  retry_budget: 4\n  name_collision_policy: manual_review\n",
            encoding="utf-8",
        )
        plan = _plan(task_id="2026-07-02-reporting-endpoint-tf000002")

        mock_planner = _run_handler_capturing_planner_mock(
            _make_args(save=True, manager_mode=True), plan,
        )

        kwargs = mock_planner.create_plan.call_args.kwargs
        assert kwargs["allow_talent_builder"] is False
        config = kwargs["talent_factory_config"]
        assert config.retry_budget == 4
        assert config.name_collision_policy == "manual_review"

    def test_manager_mode_save_honors_skip_init(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.chdir(tmp_path)
        plan = _plan(task_id="2026-07-02-reporting-endpoint-tf000003")
        args = _make_args(save=True, manager_mode=True)
        args.skip_init = True

        mock_planner = _run_handler_capturing_planner_mock(args, plan)

        kwargs = mock_planner.create_plan.call_args.kwargs
        assert kwargs["skip_init"] is True
