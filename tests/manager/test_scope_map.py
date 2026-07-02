"""Tests for :mod:`agent_baton.core.manager.scope` (M2 -- scope map).

See docs/internal/manager-mode-pmo-plan.md Wave 1 / Task 5 and PRD §4.1 /
§10.2 / §16 Milestone 2.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.core.config.manager import ManagerConfig
from agent_baton.core.manager.charter import ProjectCharterBuilder
from agent_baton.core.manager.scope import ScopeMapBuilder
from agent_baton.models.execution import MachinePlan, PlanGate, PlanPhase, PlanStep


def _make_plan(
    *,
    task_id: str = "task-scope-1",
    task_summary: str = "Add a reporting endpoint with tests and docs",
    complexity: str = "medium",
    risk_level: str = "MEDIUM",
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
        task_type="feature",
        detected_stack="python",
        phases=phases,
    )


def _build_scope_map(plan: MachinePlan, config: ManagerConfig | None = None):
    config = config or ManagerConfig()
    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, Path("/nonexistent"))
    return ScopeMapBuilder(config).build(charter, plan), charter


def test_scope_map_json_round_trip() -> None:
    plan = _make_plan()
    scope_map, _charter = _build_scope_map(plan)

    reloaded = type(scope_map).from_dict(json.loads(json.dumps(scope_map.to_dict())))

    assert reloaded == scope_map


def test_one_workstream_per_phase_ids_are_stable() -> None:
    plan = _make_plan()
    scope_map, _charter = _build_scope_map(plan)

    assert [ws.id for ws in scope_map.workstreams] == ["ws-1", "ws-2"]
    assert [ws.name for ws in scope_map.workstreams] == ["Implementation", "Testing and docs"]


def test_single_phase_light_plan_yields_one_workstream() -> None:
    phases = [
        PlanPhase(
            phase_id=1,
            name="Quick fix",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Fix the bug.",
                    deliverables=["bug fix"],
                    allowed_paths=["app/bugfix.py"],
                ),
            ],
        ),
    ]
    plan = _make_plan(complexity="light", phases=phases)
    scope_map, _charter = _build_scope_map(plan)

    assert len(scope_map.workstreams) == 1


def test_owner_role_is_modal_agent_name() -> None:
    phases = [
        PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Step 1.",
                    deliverables=["a"],
                    allowed_paths=["app/a.py"],
                ),
                PlanStep(
                    step_id="1.2",
                    agent_name="backend-engineer",
                    task_description="Step 2.",
                    deliverables=["b"],
                    allowed_paths=["app/b.py"],
                ),
                PlanStep(
                    step_id="1.3",
                    agent_name="test-engineer",
                    task_description="Step 3.",
                    deliverables=["c"],
                    allowed_paths=["tests/c.py"],
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)
    scope_map, _charter = _build_scope_map(plan)

    assert scope_map.workstreams[0].owner_role == "backend-engineer"


def test_cross_phase_dependency_produces_workstream_edge() -> None:
    plan = _make_plan()
    scope_map, _charter = _build_scope_map(plan)

    # Phase 2 step "2.1" depends_on "1.1" (phase 1) -> ws-2 depends on ws-1.
    ws_by_id = {ws.id: ws for ws in scope_map.workstreams}
    assert "ws-1" in ws_by_id["ws-2"].dependencies


def test_no_explicit_cross_phase_dep_falls_back_to_previous_phase() -> None:
    phases = [
        PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Step 1.",
                    deliverables=["a"],
                    allowed_paths=["app/a.py"],
                ),
            ],
        ),
        PlanPhase(
            phase_id=2,
            name="Testing",
            steps=[
                PlanStep(
                    step_id="2.1",
                    agent_name="test-engineer",
                    task_description="Step 2 -- no depends_on.",
                    deliverables=["b"],
                    allowed_paths=["tests/b.py"],
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)
    scope_map, _charter = _build_scope_map(plan)

    ws_by_id = {ws.id: ws for ws in scope_map.workstreams}
    assert ws_by_id["ws-2"].dependencies == ["ws-1"]
    assert ws_by_id["ws-1"].dependencies == []


def test_allowed_paths_fall_back_to_charter_likely_repo_areas() -> None:
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
                    # no allowed_paths / context_files
                ),
            ],
        ),
    ]
    plan = _make_plan(task_summary="improve things", phases=phases)
    scope_map, charter = _build_scope_map(plan)

    assert charter.likely_repo_areas == []
    assert scope_map.workstreams[0].allowed_paths == []
    assert scope_map.workstreams[0].likely_paths == []


def test_scope_expansion_policy_from_config() -> None:
    plan = _make_plan()
    config = ManagerConfig.from_dict({"scoping": {"scope_expansion_policy": "block"}})
    scope_map, _charter = _build_scope_map(plan, config)

    assert scope_map.scope_expansion_policy == "block"


def test_scope_map_out_of_scope_matches_charter() -> None:
    plan = _make_plan()
    scope_map, charter = _build_scope_map(plan)

    assert scope_map.out_of_scope == charter.out_of_scope
