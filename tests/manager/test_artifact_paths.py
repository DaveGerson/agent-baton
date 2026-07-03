"""Tests for :mod:`agent_baton.core.manager.paths` and ``.artifacts`` (M1 — Wave 0 / Task 3).

See docs/internal/manager-mode-pmo-plan.md Wave 0 / Task 3 and the
sidecar tree in docs/internal/manager-mode-pmo-design.md.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.core.manager.artifacts import (
    append_decision_log,
    write_json,
    write_text,
)
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.models.manager import ManagerDecision, ScopeMap, Workstream


def test_paths_layout(tmp_path: Path) -> None:
    team_context_dir = tmp_path / ".claude" / "team-context"
    paths = ManagerArtifactPaths(team_context_dir, "task-1")

    root = team_context_dir / "executions" / "task-1"
    assert paths.root == root

    assert paths.charter == root / "project-charter.md"
    assert paths.scope_map == root / "scope-map.json"
    assert paths.team_blueprint == root / "team-blueprint.json"
    assert paths.role_cards_dir == root / "role-cards"
    assert paths.knowledge_plan == root / "knowledge-plan.json"
    assert paths.manager_brief == root / "manager-brief.md"
    assert paths.manager_report == root / "manager-report.md"
    assert paths.scope_contracts_dir == root / "scope-contracts"
    assert paths.context_bundles_dir == root / "context-bundles"
    assert paths.handoffs_dir == root / "handoffs"
    assert paths.decisions_dir == root / "decisions"
    assert paths.decision_log == root / "decision-log.jsonl"

    assert paths.role_card("backend-engineer") == root / "role-cards" / "backend-engineer.md"
    assert paths.scope_contract("2.1", ext="md") == root / "scope-contracts" / "2.1.md"
    assert paths.scope_contract("2.1", ext="json") == root / "scope-contracts" / "2.1.json"
    assert paths.context_bundle("2.1") == root / "context-bundles" / "2.1.json"
    assert paths.phase_handoff(1) == root / "handoffs" / "phase-1-handoff.md"
    assert paths.decision("dec-abc123") == root / "decisions" / "dec-abc123.md"


def test_step_id_sanitized(tmp_path: Path) -> None:
    paths = ManagerArtifactPaths(tmp_path, "task-1")

    assert paths.scope_contract("2/1", ext="md").name == "2_1.md"
    assert paths.scope_contract("2/1", ext="json").name == "2_1.json"
    assert paths.context_bundle("2/1").name == "2_1.json"
    assert paths.role_card("weird/role name").name == "weird_role_name.md"
    assert paths.decision("dec/weird id").name == "dec_weird_id.md"


def test_write_json_and_md_create_parents(tmp_path: Path) -> None:
    scope_map = ScopeMap(
        task_id="task-1", workstreams=[Workstream(id="ws-1", name="Backend")]
    )
    json_path = tmp_path / "nested" / "dir" / "scope-map.json"
    assert not json_path.parent.exists()

    write_json(json_path, scope_map)

    assert json_path.exists()
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["task_id"] == "task-1"
    assert ScopeMap.from_dict(loaded) == scope_map

    md_path = tmp_path / "nested2" / "dir2" / "note.md"
    assert not md_path.parent.exists()

    write_text(md_path, "# Hello\n")

    assert md_path.read_text(encoding="utf-8") == "# Hello\n"


def test_decision_log_appends_jsonl(tmp_path: Path) -> None:
    paths = ManagerArtifactPaths(tmp_path, "task-1")
    assert not paths.decision_log.exists()

    d1 = ManagerDecision(
        decision_id="dec-1", task_id="task-1", decision_type="approval", summary="first"
    )
    d2 = ManagerDecision(
        decision_id="dec-2",
        task_id="task-1",
        decision_type="scope_expansion",
        summary="second",
    )

    append_decision_log(paths, d1)
    append_decision_log(paths, d2)

    lines = paths.decision_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["decision_id"] == "dec-1"
    assert json.loads(lines[1])["decision_id"] == "dec-2"
