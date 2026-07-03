"""Tests for :mod:`agent_baton.core.manager.charter` (M2 — project charter).

See docs/internal/manager-mode-pmo-plan.md Wave 1 / Task 5 and PRD §4.1 /
§10.1 / §16 Milestone 2. All builders here are deterministic (no clock, no
randomness, no LLM calls) -- see the ``ambiguity`` heuristic documented on
``agent_baton.core.manager.charter._ambiguity``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.config.manager import ManagerConfig
from agent_baton.core.manager.artifacts import ManagerArtifacts, write_all
from agent_baton.core.manager.charter import (
    ProjectCharterBuilder,
    _likely_repo_areas,
    charter_to_markdown,
)
from agent_baton.core.manager.enrich import maybe_enrich_charter
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.manager.scope import ScopeMapBuilder
from agent_baton.models.execution import MachinePlan, PlanGate, PlanPhase, PlanStep
from agent_baton.models.manager import ProjectCharter


def _make_plan(
    *,
    task_id: str = "task-charter-1",
    task_summary: str = "Add a reporting endpoint with tests and docs",
    complexity: str = "medium",
    risk_level: str = "MEDIUM",
    task_type: str | None = "feature",
    detected_stack: str | None = "python",
    phases: list[PlanPhase] | None = None,
) -> MachinePlan:
    if phases is None:
        phases = [
            PlanPhase(
                phase_id=1,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Build the reporting endpoint.",
                        deliverables=["reporting endpoint"],
                        allowed_paths=["app/reporting/service.py"],
                        context_files=["app/reporting/service.py"],
                    ),
                    PlanStep(
                        step_id="1.2",
                        agent_name="backend-engineer",
                        task_description="Wire the endpoint into the router.",
                        deliverables=["router wiring"],
                        allowed_paths=["app/reporting/routes.py"],
                        depends_on=["1.1"],
                    ),
                ],
                gate=PlanGate(gate_type="test", command="pytest tests/reporting -q"),
            ),
            PlanPhase(
                phase_id=2,
                name="Testing and docs",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="test-engineer",
                        task_description="Write tests for the reporting endpoint.",
                        deliverables=["test suite"],
                        allowed_paths=["tests/reporting"],
                        depends_on=["1.1"],
                    ),
                    PlanStep(
                        step_id="2.2",
                        agent_name="test-engineer",
                        task_description="Document the reporting endpoint.",
                        deliverables=["README section"],
                        allowed_paths=["docs/reporting.md"],
                        depends_on=["2.1"],
                    ),
                ],
                gate=PlanGate(gate_type="test", command="pytest -q"),
            ),
        ]
    return MachinePlan(
        task_id=task_id,
        task_summary=task_summary,
        complexity=complexity,
        risk_level=risk_level,
        task_type=task_type,
        detected_stack=detected_stack,
        phases=phases,
    )


def _make_ambiguous_plan(task_id: str = "task-ambiguous-1") -> MachinePlan:
    """A plan whose steps carry no path signals, paired with a short,
    vague task summary -- exercises both ambiguity triggers (short
    summary AND no inferable repo areas) without any fixture directories
    on disk matching the summary's words.
    """
    phases = [
        PlanPhase(
            phase_id=1,
            name="Improvements",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Improve things.",
                    deliverables=["improvements"],
                ),
            ],
        ),
    ]
    return _make_plan(
        task_id=task_id,
        task_summary="improve things",
        complexity="medium",
        risk_level="MEDIUM",
        phases=phases,
    )


def test_medium_project_charter_nonempty(tmp_path: Path) -> None:
    plan = _make_plan()
    charter = ProjectCharterBuilder(ManagerConfig()).build(plan, plan.task_summary, tmp_path)

    assert charter.objective
    assert charter.in_scope
    assert charter.out_of_scope
    assert charter.assumptions
    assert charter.success_criteria


def test_multipart_task_creates_two_plus_workstreams(tmp_path: Path) -> None:
    plan = _make_plan()
    config = ManagerConfig()
    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, tmp_path)
    scope_map = ScopeMapBuilder(config).build(charter, plan)

    assert len(scope_map.workstreams) >= 2


def test_workstream_has_owner_deliverables_paths_risks(tmp_path: Path) -> None:
    plan = _make_plan()
    config = ManagerConfig()
    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, tmp_path)
    scope_map = ScopeMapBuilder(config).build(charter, plan)

    assert scope_map.workstreams
    for workstream in scope_map.workstreams:
        assert workstream.owner_role
        assert workstream.deliverables
        assert workstream.likely_paths
        assert workstream.risks


def test_ambiguous_task_records_assumptions(tmp_path: Path) -> None:
    plan = _make_ambiguous_plan()
    charter = ProjectCharterBuilder(ManagerConfig()).build(plan, plan.task_summary, tmp_path)

    assert len(charter.assumptions) >= 1
    # No repo areas could be inferred -- must not invent paths.
    assert charter.likely_repo_areas == []


def test_context_files_do_not_pollute_likely_repo_areas(tmp_path: Path) -> None:
    """I1 regression: ``context_files`` (conventionally ``CLAUDE.md`` --
    a read-this hint, not a repo area) must never contribute a segment to
    ``likely_repo_areas``. Every step here has ``context_files`` but no
    ``allowed_paths``, so pre-fix this returned ``["CLAUDE.md"]``."""
    phases = [
        PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Build the reporting endpoint.",
                    deliverables=["reporting endpoint"],
                    context_files=["CLAUDE.md"],
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)

    areas, was_assumed = _likely_repo_areas(plan, plan.task_summary, tmp_path)

    assert "CLAUDE.md" not in areas
    assert areas == []
    assert was_assumed is True


def test_likely_repo_areas_filtered_to_existing_directories(tmp_path: Path) -> None:
    """I1: candidate segments are filtered to those that exist as real
    directories under ``project_root`` -- a step pointing at a directory
    that doesn't exist on disk must not surface as a likely repo area,
    but a segment that IS a real directory survives."""
    (tmp_path / "app").mkdir()
    phases = [
        PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Build the reporting endpoint.",
                    deliverables=["reporting endpoint"],
                    allowed_paths=["app/reporting/service.py", "ghost/nowhere.py"],
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)

    areas, was_assumed = _likely_repo_areas(plan, plan.task_summary, tmp_path)

    assert areas == ["app"]
    assert was_assumed is False


def test_likely_repo_areas_falls_back_to_assumption_when_no_segment_survives(
    tmp_path: Path,
) -> None:
    """I1: when every candidate segment is filtered out (none exist as
    real directories under project_root) and no directory name matches a
    word in the task summary either, the builder records an assumption
    instead of guessing -- it must NOT silently keep the filtered-out
    (nonexistent) segments."""
    phases = [
        PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Build the reporting endpoint.",
                    deliverables=["reporting endpoint"],
                    allowed_paths=["ghost/nowhere.py"],
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)

    areas, was_assumed = _likely_repo_areas(plan, plan.task_summary, tmp_path)

    assert areas == []
    assert was_assumed is True


def test_high_impact_ambiguity_creates_decision_point(tmp_path: Path) -> None:
    plan = _make_ambiguous_plan()
    config = ManagerConfig()
    assert config.manager_mode.ambiguity_policy == "ask_when_high_impact"
    assert plan.complexity in ("medium", "heavy")

    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, tmp_path)

    assert charter.manager_decision_points


def test_ambiguity_policy_record_and_continue_skips_decision_point(tmp_path: Path) -> None:
    plan = _make_ambiguous_plan()
    config = ManagerConfig.from_dict(
        {"manager_mode": {"ambiguity_policy": "record_and_continue"}}
    )

    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, tmp_path)

    assert charter.manager_decision_points == []
    # Still records the ambiguity as an assumption even when not escalated.
    assert charter.assumptions


def test_charter_markdown_renders_all_sections(tmp_path: Path) -> None:
    plan = _make_plan()
    charter = ProjectCharterBuilder(ManagerConfig()).build(plan, plan.task_summary, tmp_path)

    md = charter_to_markdown(charter)

    for heading in (
        "## Objective",
        "## Background",
        "## In Scope",
        "## Out of Scope",
        "## Assumptions",
        "## Constraints",
        "## Risks",
        "## Manager Decision Points",
        "## Success Criteria",
        "## Likely Repo Areas",
    ):
        assert heading in md


def test_scope_map_json_round_trip(tmp_path: Path) -> None:
    plan = _make_plan()
    config = ManagerConfig()
    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, tmp_path)
    scope_map = ScopeMapBuilder(config).build(charter, plan)

    serialized = json.dumps(scope_map.to_dict())
    reloaded = type(scope_map).from_dict(json.loads(serialized))

    assert reloaded == scope_map


def test_enrich_stub_is_noop_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    charter = ProjectCharter(task_id="task-1", objective="Do the thing")

    monkeypatch.delenv("BATON_MANAGER_ENRICH", raising=False)
    assert maybe_enrich_charter(charter, "Do the thing") == charter

    monkeypatch.setenv("BATON_MANAGER_ENRICH", "off")
    assert maybe_enrich_charter(charter, "Do the thing") == charter


def test_enrich_unknown_mode_falls_back_to_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charter = ProjectCharter(task_id="task-1", objective="Do the thing")
    monkeypatch.setenv("BATON_MANAGER_ENRICH", "not-a-real-mode")

    assert maybe_enrich_charter(charter, "Do the thing") == charter


# ---------------------------------------------------------------------------
# Wave-0 test gap: write_all() with a populated charter (hard constraint in
# docs/internal/manager-mode-pmo-plan.md Task 5 dispatch note).
# ---------------------------------------------------------------------------

def test_write_all_writes_charter_markdown(tmp_path: Path) -> None:
    plan = _make_plan()
    config = ManagerConfig()
    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, tmp_path)

    team_context_dir = tmp_path / ".claude" / "team-context"
    paths = ManagerArtifactPaths(team_context_dir, plan.task_id)
    artifacts = ManagerArtifacts(charter=charter)

    written = write_all(paths, artifacts)

    assert paths.charter in written
    assert paths.charter.exists()
    assert paths.charter.read_text(encoding="utf-8") == charter_to_markdown(charter)
