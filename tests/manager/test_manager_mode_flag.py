"""Tests for the ``--manager-mode`` flag, ``MachinePlan.manager_mode``, the
Wave 0 ``ManagerModePlanner`` skeleton, ``--gate-scope`` explicitness
recording, and fail-safe manager-config loading (review-fix follow-ups).

See docs/internal/manager-mode-pmo-plan.md Wave 0 / Task 4.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

import pytest

from agent_baton.cli.commands.execution import plan_cmd
from agent_baton.models.execution import MachinePlan


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="baton")
    subparsers = parser.add_subparsers()
    plan_cmd.register(subparsers)
    return parser


def _install_stub_planner(monkeypatch: Any, captured: dict[str, Any]) -> None:
    """Replace ``IntelligentPlanner`` with a lightweight stub.

    Returns a minimal, valid ``MachinePlan`` without doing real stack
    detection, risk classification, or knowledge-registry scanning, and
    stashes the returned (mutable) plan object into ``captured["plan"]``
    (so a test can inspect it after ``handler()`` mutates it in place, e.g.
    ``plan.manager_mode = True``) and the kwargs ``create_plan`` was called
    with into ``captured["create_plan_kwargs"]`` (so a test can assert on
    what the CLI actually resolved, e.g. ``gate_scope``).
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
            captured["create_plan_kwargs"] = kwargs
            return plan

        def explain_plan(self, plan: MachinePlan) -> str:
            return "stub explanation"

    monkeypatch.setattr(plan_cmd, "IntelligentPlanner", _StubPlanner)


def test_machine_plan_manager_mode_round_trips() -> None:
    plan = MachinePlan(task_id="task-1", task_summary="Do the thing", manager_mode=True)
    assert MachinePlan.from_dict(plan.to_dict()).manager_mode is True


def test_plan_cmd_flag_sets_manager_mode(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}
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
    captured: dict[str, Any] = {}
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
    captured: dict[str, Any] = {}
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


def test_manager_mode_planner_builds_full_composition_and_dry_run_writes_nothing(
    tmp_path: Any,
) -> None:
    """Wave 3 contract (supersedes the Wave 0 placeholder): ``build()`` runs
    the full composition in-memory without touching disk; a task with zero
    phases (e.g. this test's minimal plan, mirroring what a stub planner
    hands back in ``test_plan_cmd_flag_sets_manager_mode`` above) must not
    crash the composition. ``build_and_write()`` is where persistence
    actually happens -- see ``tests/manager/test_manager_mode_planner.py``
    for the full Wave 3 composition test suite (contracts/bundles over a
    real multi-phase plan, review-step integration, etc).
    """
    from agent_baton.core.config.manager import ManagerConfig
    from agent_baton.core.manager.planner import ManagerModePlanner
    from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry

    plan = MachinePlan(
        task_id="task-skeleton", task_summary="Do the thing", manager_mode=True
    )
    team_context_dir = tmp_path / ".claude" / "team-context"

    planner = ManagerModePlanner(
        ManagerConfig(),
        project_root=tmp_path,
        team_context_dir=team_context_dir,
        knowledge_registry=KnowledgeRegistry(),
    )

    artifacts = planner.build(plan, plan.task_summary)
    assert artifacts.charter is not None
    assert artifacts.scope_map is not None
    assert artifacts.blueprint is not None
    assert artifacts.brief_md
    # No phases -> no nontrivial steps -> no contracts/bundles.
    assert artifacts.scope_contracts == {}
    assert artifacts.context_bundles == {}
    assert not team_context_dir.exists()

    written = planner.build_and_write(plan, plan.task_summary)
    assert written.charter is not None
    assert (team_context_dir / "executions" / plan.task_id / "project-charter.md").is_file()


# ---------------------------------------------------------------------------
# --gate-scope explicitness (M6 prep; review-fix #1)
# ---------------------------------------------------------------------------

def test_gate_scope_omitted_uses_focused_default(monkeypatch: Any, tmp_path: Any) -> None:
    """Omitting --gate-scope must behave exactly as before: 'focused'.

    The argparse default sentinel changed from "focused" to None so the
    handler can record explicitness, but the *effective* value threaded
    into IntelligentPlanner.create_plan() must be unchanged.
    """
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}
    _install_stub_planner(monkeypatch, captured)

    parser = _build_parser()
    args = parser.parse_args(["plan", "do the thing", "--dry-run"])

    # Sentinel: argparse itself reports "not passed" as None, not "focused".
    assert args.gate_scope is None

    plan_cmd.handler(args)

    assert captured["create_plan_kwargs"]["gate_scope"] == "focused"


def test_gate_scope_explicit_full_is_honored(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}
    _install_stub_planner(monkeypatch, captured)

    parser = _build_parser()
    args = parser.parse_args(
        ["plan", "do the thing", "--gate-scope", "full", "--dry-run"]
    )

    assert args.gate_scope == "full"

    plan_cmd.handler(args)

    assert captured["create_plan_kwargs"]["gate_scope"] == "full"


# ---------------------------------------------------------------------------
# Fail-safe manager-config loading (review-fix #2)
# ---------------------------------------------------------------------------

_MALFORMED_BATON_YAML = (
    "policies:\n"
    "  phase_completion:\n"
    "    adversarial_review: sometimes\n"  # invalid Literal value
)


def test_malformed_config_non_manager_plan_succeeds_with_warning(
    monkeypatch: Any, tmp_path: Any, caplog: Any
) -> None:
    """A broken baton.yaml must never crash a plain `baton plan`.

    Manager mode is not requested (no --manager-mode, and the malformed
    file can't even be parsed far enough to know its own
    enabled_by_default), so the load failure is logged and swallowed;
    the plan still gets created.
    """
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(_MALFORMED_BATON_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}
    _install_stub_planner(monkeypatch, captured)

    parser = _build_parser()
    args = parser.parse_args(["plan", "do the thing", "--dry-run"])  # no --manager-mode

    with caplog.at_level(logging.WARNING):
        plan_cmd.handler(args)  # must not raise

    assert captured["plan"].manager_mode is False
    assert any(
        "invalid manager config" in rec.message.lower()
        for rec in caplog.records
    )


def test_malformed_config_manager_mode_flag_raises_typed_error(
    monkeypatch: Any, tmp_path: Any, capsys: Any
) -> None:
    """The same broken baton.yaml + --manager-mode is a hard, typed error.

    No raw traceback: a clean "error: ..." message on stderr and a
    semantic (EXIT_VALIDATION) exit code, matching the convention already
    used by sibling files in cli/commands/execution/ (execute.py,
    daemon.py, handoff.py -- see agent_baton/cli/errors.py).
    """
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(_MALFORMED_BATON_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}
    _install_stub_planner(monkeypatch, captured)

    parser = _build_parser()
    args = parser.parse_args(
        ["plan", "do the thing", "--manager-mode", "--dry-run"]
    )

    with pytest.raises(SystemExit) as exc_info:
        plan_cmd.handler(args)

    assert exc_info.value.code == 2  # EXIT_VALIDATION

    stderr = capsys.readouterr().err
    assert "error:" in stderr.lower()
    assert "manager config" in stderr.lower()
    # No raw Python traceback leaked to the user.
    assert "Traceback (most recent call last)" not in stderr
