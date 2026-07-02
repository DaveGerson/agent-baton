"""Tests for the ``--manager-mode`` flag, ``MachinePlan.manager_mode``, and
the Wave 0 ``ManagerModePlanner`` skeleton.

See docs/internal/manager-mode-pmo-plan.md Wave 0 / Task 4.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from agent_baton.cli.commands.execution import plan_cmd
from agent_baton.models.execution import MachinePlan


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="baton")
    subparsers = parser.add_subparsers()
    plan_cmd.register(subparsers)
    return parser


def _install_stub_planner(monkeypatch: Any, captured: dict[str, MachinePlan]) -> None:
    """Replace ``IntelligentPlanner`` with a lightweight stub.

    Returns a minimal, valid ``MachinePlan`` without doing real stack
    detection, risk classification, or knowledge-registry scanning, and
    stashes the returned (mutable) plan object into *captured* so the
    test can inspect it after ``handler()`` mutates it in place (e.g.
    ``plan.manager_mode = True``).
    """

    class _StubPlanner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def create_plan(self, summary: str, **kwargs: Any) -> MachinePlan:
            plan = MachinePlan(
                task_id="task-manager-flag-test",
                task_summary=summary,
            )
            captured["plan"] = plan
            return plan

        def explain_plan(self, plan: MachinePlan) -> str:
            return "stub explanation"

    monkeypatch.setattr(plan_cmd, "IntelligentPlanner", _StubPlanner)


def test_machine_plan_manager_mode_round_trips() -> None:
    plan = MachinePlan(task_id="task-1", task_summary="Do the thing", manager_mode=True)
    assert MachinePlan.from_dict(plan.to_dict()).manager_mode is True


def test_plan_cmd_flag_sets_manager_mode(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.chdir(tmp_path)
    captured: dict[str, MachinePlan] = {}
    _install_stub_planner(monkeypatch, captured)

    parser = _build_parser()
    args = parser.parse_args(["plan", "do the thing", "--manager-mode", "--dry-run"])

    plan_cmd.handler(args)

    assert captured["plan"].manager_mode is True


def test_enabled_by_default_config_turns_on_manager_mode(
    monkeypatch: Any, tmp_path: Any
) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(
        "manager_mode:\n  enabled_by_default: true\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    captured: dict[str, MachinePlan] = {}
    _install_stub_planner(monkeypatch, captured)

    parser = _build_parser()
    # No --manager-mode flag: config alone should turn it on.
    args = parser.parse_args(["plan", "do the thing", "--dry-run"])

    plan_cmd.handler(args)

    assert captured["plan"].manager_mode is True


def test_non_manager_plan_unchanged(monkeypatch: Any, tmp_path: Any) -> None:
    # Force a clean slate for this specific submodule so this test proves
    # the OFF code path itself never triggers the import, independent of
    # whatever other tests in this session already imported it.
    monkeypatch.delitem(sys.modules, "agent_baton.core.manager.planner", raising=False)
    monkeypatch.chdir(tmp_path)
    captured: dict[str, MachinePlan] = {}
    _install_stub_planner(monkeypatch, captured)

    parser = _build_parser()
    # --save (not --dry-run) so this exercises the branch that would
    # otherwise call ManagerModePlanner.build_and_write.
    args = parser.parse_args(["plan", "do the thing", "--save"])

    plan_cmd.handler(args)

    plan = captured["plan"]
    assert plan.manager_mode is False
    assert "agent_baton.core.manager.planner" not in sys.modules

    # The only behavioral delta introduced by this milestone for a
    # non-manager plan is the presence of `manager_mode: False` in
    # to_dict() -- everything else about the shape is unchanged.
    on_plan = MachinePlan(
        task_id=plan.task_id,
        task_summary=plan.task_summary,
        created_at=plan.created_at,
        manager_mode=True,
    )
    off_dict = plan.to_dict()
    on_dict = on_plan.to_dict()
    diff_keys = {k for k in off_dict if off_dict.get(k) != on_dict.get(k)}
    assert diff_keys == {"manager_mode"}


def test_manager_mode_planner_skeleton_builds_and_writes_nothing(tmp_path: Any) -> None:
    """Wave 0 contract: ManagerModePlanner builds nothing yet.

    ``build()`` returns an empty ``ManagerArtifacts``; ``build_and_write()``
    persists nothing to disk (write_all on an empty container is a no-op).
    Wave 3 fills in the real composition.
    """
    from agent_baton.core.config.manager import ManagerConfig
    from agent_baton.core.manager.artifacts import ManagerArtifacts
    from agent_baton.core.manager.planner import ManagerModePlanner

    plan = MachinePlan(
        task_id="task-skeleton", task_summary="Do the thing", manager_mode=True
    )
    team_context_dir = tmp_path / ".claude" / "team-context"

    planner = ManagerModePlanner(
        ManagerConfig(), project_root=tmp_path, team_context_dir=team_context_dir
    )

    artifacts = planner.build(plan, plan.task_summary)
    assert artifacts == ManagerArtifacts()

    written = planner.build_and_write(plan, plan.task_summary)
    assert written == ManagerArtifacts()
    assert not (team_context_dir / "executions" / plan.task_id).exists()
