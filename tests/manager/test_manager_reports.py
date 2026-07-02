"""Tests for :mod:`agent_baton.core.manager.reports` (M7 -- manager brief
and manager report).

See docs/internal/manager-mode-pmo-plan.md Wave 2 / Task 10 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §15.1/§15.2/§16
Milestone 7.

Test inputs are hand-constructed (``MachinePlan``, ``ScopeMap``,
``TeamBlueprint``, ``KnowledgePlan``) -- ``ManagerModePlanner`` composition
is not invoked (that's Wave 3).

Every fixture deliberately makes ``Workstream.owner_role`` (the scope map's
pre-diversification baseline) *disagree* with
``TeamBlueprint.workstream_assignments`` (the post-diversification,
authoritative owner) for ``ws-1``, and includes a role ("architect") that
owns zero workstreams. This exercises the two binding Wave-1-review rules:

1. Workstream ownership authority is ``workstream_assignments``, never
   ``Workstream.owner_role`` and never a step's ``agent_name``.
2. A role that owns zero workstreams (a "displaced generalist") is listed
   under Team only -- never rendered as a workstream's owner.
"""
from __future__ import annotations

from pathlib import Path

from agent_baton.core.config.manager import ManagerConfig
from agent_baton.core.manager.artifacts import ManagerArtifacts
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.manager.reports import ManagerReportBuilder
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.models.manager import (
    KnowledgePackReference,
    KnowledgePlan,
    MissingKnowledgePack,
    ProjectCharter,
    RoleCard,
    ScopeMap,
    TeamBlueprint,
    Workstream,
)

_RAW_PROMPT_MARKER = "RAW_PROMPT_MARKER_DO_NOT_LEAK"
_RAW_OUTCOME_MARKER = "RAW_OUTCOME_MARKER_DO_NOT_LEAK"


def _sample_plan() -> MachinePlan:
    return MachinePlan(
        task_id="task-report",
        task_summary="Add a reporting endpoint with tests",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Design",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="architect",
                        task_description=f"Design the reporting service. {_RAW_PROMPT_MARKER}",
                        deliverables=["design doc"],
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
                        task_description=f"Add tests for the reporting endpoint. {_RAW_PROMPT_MARKER}",
                        depends_on=["1.1"],
                        deliverables=["endpoint tests"],
                    ),
                ],
            ),
        ],
    )


def _sample_scope_map() -> ScopeMap:
    return ScopeMap(
        task_id="task-report",
        workstreams=[
            # Baseline owner_role ("architect") deliberately disagrees with
            # the blueprint's authoritative workstream_assignments
            # ("backend-engineer") below -- proves ownership sourcing.
            Workstream(
                id="ws-1", name="Design", owner_role="architect",
                allowed_paths=["app/reporting/**"], deliverables=["design doc"],
            ),
            Workstream(
                id="ws-2", name="Test", owner_role="test-engineer",
                allowed_paths=["tests/reporting/**"], deliverables=["endpoint tests"],
                dependencies=["ws-1"],
            ),
        ],
        cross_cutting_concerns=[],
        out_of_scope=["Repo areas outside the scope map"],
    )


def _sample_blueprint() -> TeamBlueprint:
    return TeamBlueprint(
        task_id="task-report",
        team_name="Delivery Team",
        mission="Add a reporting endpoint with tests",
        roles=[
            # "architect" owns zero workstreams -- a displaced generalist.
            RoleCard(role="architect", agent_name="architect", mission="Own design guidance",
                      owns=["design guidance"]),
            RoleCard(role="backend-engineer", agent_name="backend-engineer",
                      mission="Own the reporting endpoint", owns=["endpoint handler"]),
            RoleCard(role="test-engineer", agent_name="test-engineer",
                      mission="Own endpoint tests", owns=["endpoint tests"]),
        ],
        # Authoritative: ws-1 owned by backend-engineer, NOT architect
        # (Workstream.owner_role above) and NOT step 1.1's agent_name
        # ("architect" happens to be both, which makes this fixture even
        # more pointed -- ownership really is a distinct concept).
        workstream_assignments={"ws-1": "backend-engineer", "ws-2": "test-engineer"},
        collaboration_rules=["Hand off via phase handoff artifacts."],
        escalation_triggers=["scope expansion beyond an assigned workstream"],
        phase_policies={"adversarial_review": "always"},
    )


def _sample_knowledge_plan() -> KnowledgePlan:
    return KnowledgePlan(
        task_id="task-report",
        selected_packs=[
            KnowledgePackReference(name="coding-conventions", confidence="high", status="active"),
        ],
        missing_packs=[MissingKnowledgePack(name="repo-architecture", reason="config: default_packs")],
        stale_packs=["testing-strategy"],
    )


def _sample_charter() -> ProjectCharter:
    return ProjectCharter(
        task_id="task-report",
        objective="Add a reporting endpoint with tests.",
        assumptions=["Scope inferred from the plan."],
        manager_decision_points=["Confirm auth scope before implementation."],
        risks=["Risk level classified as MEDIUM."],
    )


def _sample_artifacts() -> ManagerArtifacts:
    return ManagerArtifacts(
        charter=_sample_charter(),
        scope_map=_sample_scope_map(),
        blueprint=_sample_blueprint(),
        knowledge_plan=_sample_knowledge_plan(),
    )


def _builder(tmp_path: Path, task_id: str = "task-report") -> ManagerReportBuilder:
    paths = ManagerArtifactPaths(tmp_path, task_id)
    return ManagerReportBuilder(ManagerConfig(), paths)


# ---------------------------------------------------------------------------
# Brief
# ---------------------------------------------------------------------------


def test_brief_includes_required_sections(tmp_path: Path) -> None:
    plan = _sample_plan()
    artifacts = _sample_artifacts()
    builder = _builder(tmp_path)

    text = builder.build_brief(artifacts, plan)

    for header in (
        "## Objective", "## Assumptions", "## Workstreams", "## Team",
        "## Knowledge Packs", "## Configured Policies",
        "## Manager Decision Points", "## Risks",
    ):
        assert header in text, f"missing section: {header}"

    assert "Add a reporting endpoint with tests." in text
    assert "coding-conventions" in text
    assert "repo-architecture" in text  # missing pack callout
    assert "testing-strategy" in text  # stale pack callout


def test_brief_workstream_owner_sourced_from_blueprint_assignments(tmp_path: Path) -> None:
    """Rule #1: owner is workstream_assignments, never Workstream.owner_role."""
    plan = _sample_plan()
    artifacts = _sample_artifacts()
    builder = _builder(tmp_path)

    text = builder.build_brief(artifacts, plan)

    ws1_line = next(line for line in text.splitlines() if line.startswith("| Design "))
    assert "backend-engineer" in ws1_line
    assert "architect" not in ws1_line


def test_brief_displaced_generalist_not_a_workstream_owner(tmp_path: Path) -> None:
    """Rule #2: a role owning zero workstreams appears under Team only."""
    plan = _sample_plan()
    artifacts = _sample_artifacts()
    builder = _builder(tmp_path)

    text = builder.build_brief(artifacts, plan)

    # "architect" is listed as a team member...
    team_section = text.split("## Team")[1].split("## Knowledge Packs")[0]
    assert "architect" in team_section
    assert "no workstream (support role)" in team_section

    # ...but never as the owner of either workstream row.
    workstream_section = text.split("## Workstreams")[1].split("## Team")[0]
    for line in workstream_section.splitlines():
        if line.startswith("|") and "Owner" not in line and "---" not in line:
            assert "architect" not in line


def test_brief_degrades_gracefully_without_charter(tmp_path: Path) -> None:
    plan = _sample_plan()
    artifacts = ManagerArtifacts(
        scope_map=_sample_scope_map(),
        blueprint=_sample_blueprint(),
        knowledge_plan=_sample_knowledge_plan(),
    )
    builder = _builder(tmp_path)

    text = builder.build_brief(artifacts, plan)

    assert plan.task_summary in text  # Objective falls back to task_summary
    assert "## Assumptions" in text
    assert "_None recorded._" in text  # no charter -> no assumptions/risks/decision points


def test_save_brief_writes_manager_brief_md(tmp_path: Path) -> None:
    plan = _sample_plan()
    artifacts = _sample_artifacts()
    paths = ManagerArtifactPaths(tmp_path, plan.task_id)
    builder = ManagerReportBuilder(ManagerConfig(), paths)

    written = builder.save_brief(artifacts, plan)

    assert written == paths.manager_brief
    assert written.is_file()
    assert written.read_text(encoding="utf-8") == builder.build_brief(artifacts, plan)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _execution_state(*, status: str = "running") -> dict:
    return {
        "status": status,
        "current_phase": 1,
        "step_results": [
            {
                "step_id": "1.1",
                "agent_name": "architect",
                "status": "complete",
                "outcome": f"Completed the design doc. {_RAW_OUTCOME_MARKER}",
            },
        ],
        "gate_results": [],
        "pending_gaps": [],
    }


def test_report_includes_team_and_workstream_status(tmp_path: Path) -> None:
    plan = _sample_plan()
    artifacts = _sample_artifacts()
    builder = _builder(tmp_path)

    text = builder.build_report(plan, artifacts, _execution_state())

    assert "## Phase / Workstream Progress" in text
    assert "## Team Activity" in text

    # ws-1 (Design) is complete (its one step, 1.1, is complete); owner is
    # the authoritative backend-engineer, never architect.
    progress_section = text.split("## Phase / Workstream Progress")[1].split("## Team Activity")[0]
    ws1_line = next(line for line in progress_section.splitlines() if line.startswith("| Design "))
    assert "backend-engineer" in ws1_line
    assert "complete" in ws1_line
    assert "1/1" in ws1_line

    ws2_line = next(line for line in progress_section.splitlines() if line.startswith("| Test "))
    assert "test-engineer" in ws2_line
    assert "0/1" in ws2_line

    team_section = text.split("## Team Activity")[1].split("## Handoffs Completed")[0]
    assert "architect" in team_section
    assert "backend-engineer" in team_section
    assert "test-engineer" in team_section


def test_report_data_has_minimum_json_keys(tmp_path: Path) -> None:
    plan = _sample_plan()
    artifacts = _sample_artifacts()
    builder = _builder(tmp_path)

    data = builder.build_report_data(plan, artifacts, _execution_state())

    assert "status" in data
    assert "workstreams" in data
    assert "open_decisions" in data
    assert data["status"] == "running"


def test_report_reflects_knowledge_gaps_and_missing_packs(tmp_path: Path) -> None:
    plan = _sample_plan()
    artifacts = _sample_artifacts()
    builder = _builder(tmp_path)

    text = builder.build_report(plan, artifacts, None)

    gaps_section = text.split("## Knowledge Gaps")[1].split("## Scope Changes")[0]
    assert "repo-architecture" in gaps_section
    assert "testing-strategy" in gaps_section


def test_report_not_started_when_no_execution_state(tmp_path: Path) -> None:
    plan = _sample_plan()
    artifacts = _sample_artifacts()
    builder = _builder(tmp_path)

    text = builder.build_report(plan, artifacts, None)

    assert "## Status" in text
    assert "planned" in text
    progress_section = text.split("## Phase / Workstream Progress")[1].split("## Team Activity")[0]
    assert "not_started" in progress_section


def test_final_recommendation_present_only_when_complete(tmp_path: Path) -> None:
    plan = _sample_plan()
    artifacts = _sample_artifacts()
    builder = _builder(tmp_path)

    running_text = builder.build_report(plan, artifacts, _execution_state(status="running"))
    assert "no final recommendation yet" in running_text

    complete_state = _execution_state(status="complete")
    complete_text = builder.build_report(plan, artifacts, complete_state)
    assert "Project complete." in complete_text


def test_save_report_writes_manager_report_md(tmp_path: Path) -> None:
    plan = _sample_plan()
    artifacts = _sample_artifacts()
    paths = ManagerArtifactPaths(tmp_path, plan.task_id)
    builder = ManagerReportBuilder(ManagerConfig(), paths)

    written = builder.save_report(plan, artifacts, _execution_state())

    assert written == paths.manager_report
    assert written.is_file()


def test_no_raw_logs_by_default(tmp_path: Path) -> None:
    """PRD: 'raw logs are not included by default' -- a step's
    task_description (the dispatch prompt) and a StepResult's free-text
    outcome must never surface in the manager report."""
    plan = _sample_plan()
    artifacts = _sample_artifacts()
    builder = _builder(tmp_path)

    report_text = builder.build_report(plan, artifacts, _execution_state())
    brief_text = builder.build_brief(artifacts, plan)

    for marker in (_RAW_PROMPT_MARKER, _RAW_OUTCOME_MARKER):
        assert marker not in report_text
        assert marker not in brief_text


# ---------------------------------------------------------------------------
# Decision log reading (shared with ``baton team``)
# ---------------------------------------------------------------------------


def test_read_decision_log_returns_empty_list_when_absent(tmp_path: Path) -> None:
    builder = _builder(tmp_path)
    assert builder.read_decision_log() == []


def test_read_decision_log_dedupes_by_decision_id_keeping_last(tmp_path: Path) -> None:
    paths = ManagerArtifactPaths(tmp_path, "task-report")
    paths.decision_log.parent.mkdir(parents=True, exist_ok=True)
    paths.decision_log.write_text(
        '{"decision_id": "dec-1", "decision_type": "scope_expansion", "summary": "first", "resolved_at": null}\n'
        '{"decision_id": "dec-1", "decision_type": "scope_expansion", "summary": "first", "resolved_at": "2026-07-02T00:00:00Z"}\n'
        '{"decision_id": "dec-2", "decision_type": "ambiguity", "summary": "second", "resolved_at": null}\n',
        encoding="utf-8",
    )
    builder = _builder(tmp_path)

    entries = builder.read_decision_log()

    assert len(entries) == 2
    by_id = {e["decision_id"]: e for e in entries}
    assert by_id["dec-1"]["resolved_at"] == "2026-07-02T00:00:00Z"
    assert by_id["dec-2"]["resolved_at"] is None


def test_open_decisions_reflected_in_report(tmp_path: Path) -> None:
    paths = ManagerArtifactPaths(tmp_path, "task-report")
    paths.decision_log.parent.mkdir(parents=True, exist_ok=True)
    paths.decision_log.write_text(
        '{"decision_id": "dec-1", "decision_type": "scope_expansion", '
        '"summary": "Backend engineer needs app/auth/session.py", "resolved_at": null}\n',
        encoding="utf-8",
    )
    plan = _sample_plan()
    artifacts = _sample_artifacts()
    builder = ManagerReportBuilder(ManagerConfig(), paths)

    text = builder.build_report(plan, artifacts, None)

    assert "dec-1" in text
    assert "Backend engineer needs app/auth/session.py" in text
    open_section = text.split("## Open Decisions")[1].split("## Final Recommendation")[0]
    assert "dec-1" in open_section
