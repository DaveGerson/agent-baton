"""Tests for ``baton report`` (and a smoke pass over ``baton team``) (M7).

See docs/internal/manager-mode-pmo-plan.md Wave 2 / Task 10 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §8.3/§8.5/§16
Milestone 7.

Only ``tests/cli/test_report_cli.py`` is in the plan's required file list
for Task 10 (no dedicated ``test_team_cli.py`` is listed), so
``team_cmd.py``'s smoke coverage lives here too rather than as a new file.

``_resolve_context_root`` is patched directly (mirroring
``tests/cli/test_execute_run_resume.py``) rather than relying on
``monkeypatch.chdir`` + git-repo-detection failure, for determinism across
environments.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.cli.commands import report_cmd, team_cmd
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.manager.artifacts import write_json
from agent_baton.core.manager.decisions import DecisionPacketBuilder
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.models.execution import ExecutionState, MachinePlan, PlanPhase, PlanStep, StepResult
from agent_baton.models.manager import ManagerDecision, RoleCard, ScopeMap, TeamBlueprint, Workstream


@pytest.fixture(autouse=True)
def _fake_home(tmp_path_factory, monkeypatch):
    """Redirect ``Path.home()`` so ``ManagerConfig.load()``'s
    ``~/.baton/config.yaml`` check never reads a real developer machine's
    config (mirrors ``tests/manager/conftest.py``'s ``fake_home``, which
    does not apply here since this file lives under ``tests/cli/``)."""
    fake_home_dir = tmp_path_factory.mktemp("fake_home_report_cli")
    monkeypatch.setattr(Path, "home", lambda: fake_home_dir)
    return fake_home_dir


def _build_parser(mod) -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command")
    mod.register(sub)
    return root


def _sample_plan() -> MachinePlan:
    return MachinePlan(
        task_id="task-cli-report",
        task_summary="Add a reporting endpoint with tests",
        phases=[
            PlanPhase(phase_id=1, name="Implement", steps=[
                PlanStep(step_id="1.1", agent_name="backend-engineer",
                         task_description="Implement the endpoint",
                         deliverables=["endpoint handler"]),
            ]),
            PlanPhase(phase_id=2, name="Test", steps=[
                PlanStep(step_id="2.1", agent_name="test-engineer",
                         task_description="Add tests", depends_on=["1.1"],
                         deliverables=["endpoint tests"]),
            ]),
        ],
    )


def _seed_sidecars(context_root: Path, plan: MachinePlan) -> ManagerArtifactPaths:
    paths = ManagerArtifactPaths(context_root, plan.task_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    (paths.root / "plan.json").write_text(
        json.dumps(plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8",
    )

    scope_map = ScopeMap(
        task_id=plan.task_id,
        workstreams=[
            Workstream(id="ws-1", name="Implement", owner_role="backend-engineer",
                       allowed_paths=["app/reporting/**"], deliverables=["endpoint handler"]),
            Workstream(id="ws-2", name="Test", owner_role="test-engineer",
                       allowed_paths=["tests/reporting/**"], deliverables=["endpoint tests"],
                       dependencies=["ws-1"]),
        ],
    )
    write_json(paths.scope_map, scope_map)

    blueprint = TeamBlueprint(
        task_id=plan.task_id,
        team_name="Delivery Team",
        mission=plan.task_summary,
        roles=[
            RoleCard(role="backend-engineer", agent_name="backend-engineer",
                      mission="Own the reporting endpoint", owns=["endpoint handler"]),
            RoleCard(role="test-engineer", agent_name="test-engineer",
                      mission="Own the reporting tests", owns=["endpoint tests"]),
        ],
        workstream_assignments={"ws-1": "backend-engineer", "ws-2": "test-engineer"},
    )
    write_json(paths.team_blueprint, blueprint)
    return paths


# ---------------------------------------------------------------------------
# baton report -- brief-only state (no execution-state.json yet)
# ---------------------------------------------------------------------------


def test_report_cli_renders_for_active_task(tmp_path: Path, capsys, monkeypatch):
    plan = _sample_plan()
    _seed_sidecars(tmp_path, plan)
    monkeypatch.setenv("BATON_TASK_ID", plan.task_id)

    with patch.object(report_cmd, "_resolve_context_root", return_value=tmp_path):
        parser = _build_parser(report_cmd)
        args = parser.parse_args(["report"])
        report_cmd.handler(args)

    out = capsys.readouterr().out
    assert "Manager Report" in out
    assert "Implement" in out
    assert "backend-engineer" in out
    # Rendering also persists manager-report.md for later reads.
    paths = ManagerArtifactPaths(tmp_path, plan.task_id)
    assert paths.manager_report.is_file()


def test_report_json_machine_readable(tmp_path: Path, capsys, monkeypatch):
    plan = _sample_plan()
    _seed_sidecars(tmp_path, plan)
    monkeypatch.setenv("BATON_TASK_ID", plan.task_id)

    with patch.object(report_cmd, "_resolve_context_root", return_value=tmp_path):
        parser = _build_parser(report_cmd)
        args = parser.parse_args(["report", "--json"])
        report_cmd.handler(args)

    payload = json.loads(capsys.readouterr().out)
    assert "status" in payload
    assert "workstreams" in payload
    assert "open_decisions" in payload
    assert payload["task_id"] == plan.task_id
    assert payload["status"] == "planned"  # no execution-state.json seeded


def test_report_cli_reflects_running_execution_state(tmp_path: Path, capsys, monkeypatch):
    plan = _sample_plan()
    _seed_sidecars(tmp_path, plan)
    monkeypatch.setenv("BATON_TASK_ID", plan.task_id)

    state = ExecutionState(task_id=plan.task_id, plan=plan, status="running")
    state.step_results.append(
        StepResult(step_id="1.1", agent_name="backend-engineer", status="complete", outcome="done")
    )
    StatePersistence(tmp_path, task_id=plan.task_id).save(state)

    with patch.object(report_cmd, "_resolve_context_root", return_value=tmp_path):
        parser = _build_parser(report_cmd)
        args = parser.parse_args(["report", "--json"])
        report_cmd.handler(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "running"
    ws1 = next(w for w in payload["workstreams"] if w["id"] == "ws-1")
    assert ws1["owner"] == "backend-engineer"
    assert ws1["status"] == "complete"


def test_report_cli_explicit_task_id_overrides_env(tmp_path: Path, capsys, monkeypatch):
    plan = _sample_plan()
    _seed_sidecars(tmp_path, plan)
    monkeypatch.setenv("BATON_TASK_ID", "some-other-task")

    with patch.object(report_cmd, "_resolve_context_root", return_value=tmp_path):
        parser = _build_parser(report_cmd)
        args = parser.parse_args(["report", "--task-id", plan.task_id, "--json"])
        report_cmd.handler(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["task_id"] == plan.task_id


def test_report_cli_no_active_task_errors_cleanly(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("BATON_TASK_ID", raising=False)
    with patch.object(report_cmd, "_resolve_context_root", return_value=tmp_path):
        parser = _build_parser(report_cmd)
        args = parser.parse_args(["report"])
        try:
            report_cmd.handler(args)
            assert False, "expected SystemExit"
        except SystemExit as exc:
            assert exc.code != 0


def test_report_cli_open_decision_from_decision_packet(tmp_path: Path, capsys, monkeypatch):
    plan = _sample_plan()
    paths = _seed_sidecars(tmp_path, plan)
    monkeypatch.setenv("BATON_TASK_ID", plan.task_id)

    from agent_baton.core.config.manager import ManagerConfig

    decision = ManagerDecision(
        decision_type="scope_expansion", task_id=plan.task_id,
        summary="Needs to touch app/auth/session.py", created_at="2026-07-02T00:00:00Z",
    )
    DecisionPacketBuilder(ManagerConfig(), paths).create(decision)

    with patch.object(report_cmd, "_resolve_context_root", return_value=tmp_path):
        parser = _build_parser(report_cmd)
        args = parser.parse_args(["report", "--json"])
        report_cmd.handler(args)

    payload = json.loads(capsys.readouterr().out)
    assert len(payload["open_decisions"]) == 1
    assert payload["open_decisions"][0]["decision_id"] == decision.decision_id


# ---------------------------------------------------------------------------
# baton team status|show -- smoke coverage
# ---------------------------------------------------------------------------


def test_team_status_cli_smoke(tmp_path: Path, capsys, monkeypatch):
    plan = _sample_plan()
    _seed_sidecars(tmp_path, plan)
    monkeypatch.setenv("BATON_TASK_ID", plan.task_id)

    with patch.object(team_cmd, "_resolve_context_root", return_value=tmp_path):
        parser = _build_parser(team_cmd)
        args = parser.parse_args(["team", "status"])
        team_cmd.handler(args)

    out = capsys.readouterr().out
    assert "Team: Delivery Team" in out
    assert "backend-engineer" in out
    assert "test-engineer" in out
    assert "Workstream ownership:" in out
    assert "ws-1" in out and "backend-engineer" in out


def test_team_show_cli_includes_role_cards(tmp_path: Path, capsys, monkeypatch):
    plan = _sample_plan()
    _seed_sidecars(tmp_path, plan)
    monkeypatch.setenv("BATON_TASK_ID", plan.task_id)

    with patch.object(team_cmd, "_resolve_context_root", return_value=tmp_path):
        parser = _build_parser(team_cmd)
        args = parser.parse_args(["team", "show"])
        team_cmd.handler(args)

    out = capsys.readouterr().out
    assert "Role cards:" in out
    assert "# Role Card: backend-engineer" in out
    assert "# Role Card: test-engineer" in out


def test_team_cli_no_blueprint_errors_cleanly(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BATON_TASK_ID", "task-without-blueprint")
    with patch.object(team_cmd, "_resolve_context_root", return_value=tmp_path):
        parser = _build_parser(team_cmd)
        args = parser.parse_args(["team", "status"])
        try:
            team_cmd.handler(args)
            assert False, "expected SystemExit"
        except SystemExit as exc:
            assert exc.code != 0
