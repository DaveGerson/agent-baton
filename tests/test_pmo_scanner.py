"""Tests for agent_baton.core.pmo.scanner.PmoScanner."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.pmo.scanner import PmoScanner, _risk_level_to_priority
from agent_baton.core.pmo.store import PmoStore
from agent_baton.models.execution import (
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)
from agent_baton.models.pmo import PMO_COLUMNS, PmoProject, ProgramHealth


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

def _store(tmp_path: Path) -> PmoStore:
    return PmoStore(
        config_path=tmp_path / "pmo-config.json",
        archive_path=tmp_path / "pmo-archive.jsonl",
    )


def _step(step_id: str = "1.1", agent: str = "backend-engineer") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent,
        task_description="Do some work",
    )


def _gate() -> PlanGate:
    return PlanGate(gate_type="test", command="pytest")


def _phase(phase_id: int = 0, steps: list[PlanStep] | None = None, gate: PlanGate | None = None) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name="Implementation",
        steps=steps or [_step()],
        gate=gate,
    )


def _plan(
    task_id: str = "task-001",
    task_summary: str = "Build a thing",
    phases: list[PlanPhase] | None = None,
    risk_level: str = "LOW",
) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary=task_summary,
        risk_level=risk_level,
        phases=phases if phases is not None else [_phase()],
    )


def _execution_state(
    plan: MachinePlan | None = None,
    status: str = "running",
    step_results: list[StepResult] | None = None,
    gate_results: list[GateResult] | None = None,
    current_phase: int = 0,
) -> ExecutionState:
    # Slice 13's I1/I2/I9 model_validator forbids constructing torn
    # states; populate the coupled siblings to satisfy the invariants.
    from agent_baton.models.execution import PendingApprovalRequest
    p = plan or _plan()
    kwargs: dict = {
        "task_id": p.task_id,
        "plan": p,
        "status": status,
        "current_phase": current_phase,
        "step_results": step_results or [],
        "gate_results": gate_results or [],
    }
    if status == "approval_pending":
        kwargs["pending_approval_request"] = PendingApprovalRequest(
            phase_id=current_phase, requester="test",
        )
    if status in {"complete", "failed", "cancelled"}:
        kwargs["completed_at"] = "2026-05-07T00:00:00+00:00"
    return ExecutionState(**kwargs)


def _project_dir(tmp_path: Path, project_id: str = "nds") -> tuple[Path, PmoProject]:
    """Create a project directory and return (root_path, PmoProject)."""
    project_root = tmp_path / project_id
    project_root.mkdir()
    project = PmoProject(
        project_id=project_id,
        name=project_id.upper(),
        path=str(project_root),
        program=project_id.upper(),
    )
    return project_root, project


def _write_execution_state(project_root: Path, state: ExecutionState) -> None:
    context_root = project_root / ".claude" / "team-context"
    StatePersistence(context_root).save(state)


def _write_plan_json(project_root: Path, plan: MachinePlan) -> None:
    context_root = project_root / ".claude" / "team-context"
    context_root.mkdir(parents=True, exist_ok=True)
    plan_path = context_root / "plan.json"
    plan_path.write_text(
        json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _scanner(store: PmoStore) -> PmoScanner:
    return PmoScanner(store)


# ---------------------------------------------------------------------------
# scan_project — no team-context dir
# ---------------------------------------------------------------------------

class TestScanProjectNoContext:
    def test_returns_empty_list_when_no_team_context_dir(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        # No .claude/team-context dir at all
        scanner = _scanner(store)
        cards = scanner.scan_project(project)
        assert cards == []

    def test_returns_empty_list_when_team_context_dir_empty(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        (project_root / ".claude" / "team-context").mkdir(parents=True)
        scanner = _scanner(store)
        cards = scanner.scan_project(project)
        assert cards == []


# ---------------------------------------------------------------------------
# scan_project — execution state present
# ---------------------------------------------------------------------------

class TestScanProjectWithExecutionState:
    @pytest.mark.parametrize("status,expected_column", [
        ("running",           "executing"),
        ("gate_pending",      "validating"),
        ("approval_pending",  "awaiting_human"),
        ("complete",          "deployed"),
        ("failed",            "executing"),
    ])
    def test_status_maps_to_correct_column(
        self, tmp_path: Path, status: str, expected_column: str
    ):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        state = _execution_state(status=status)
        _write_execution_state(project_root, state)

        scanner = _scanner(store)
        cards = scanner.scan_project(project)
        assert len(cards) == 1
        assert cards[0].column == expected_column

    def test_card_id_matches_plan_task_id(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(task_id="my-unique-task")
        state = _execution_state(plan=plan)
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        assert cards[0].card_id == "my-unique-task"

    def test_card_title_matches_plan_task_summary(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(task_summary="Refactor auth module")
        state = _execution_state(plan=plan)
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        assert cards[0].title == "Refactor auth module"

    def test_card_risk_level_matches_plan(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(risk_level="HIGH")
        state = _execution_state(plan=plan)
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        assert cards[0].risk_level == "HIGH"

    def test_card_project_id_and_program_from_project(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path, project_id="atl")
        state = _execution_state()
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        assert cards[0].project_id == "atl"
        assert cards[0].program == "ATL"

    def test_steps_completed_counts_complete_results(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(phases=[_phase(steps=[_step("1.1"), _step("1.2"), _step("1.3")])])
        results = [
            StepResult(step_id="1.1", agent_name="backend-engineer", status="complete"),
            StepResult(step_id="1.2", agent_name="backend-engineer", status="complete"),
            StepResult(step_id="1.3", agent_name="backend-engineer", status="failed"),
        ]
        state = _execution_state(plan=plan, step_results=results)
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        assert cards[0].steps_completed == 2

    def test_steps_total_from_plan(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(phases=[_phase(steps=[_step("1.1"), _step("1.2"), _step("1.3")])])
        state = _execution_state(plan=plan)
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        assert cards[0].steps_total == 3

    def test_gates_passed_counts_passed_gate_results(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(phases=[_phase(gate=_gate())])
        gate_results = [
            GateResult(phase_id=0, gate_type="test", passed=True),
            GateResult(phase_id=1, gate_type="test", passed=False),
        ]
        state = _execution_state(plan=plan, gate_results=gate_results)
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        assert cards[0].gates_passed == 1

    def test_agents_list_from_plan_steps(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(phases=[_phase(steps=[
            _step("1.1", "backend-engineer"),
            _step("1.2", "test-engineer"),
        ])])
        state = _execution_state(plan=plan)
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        assert "backend-engineer" in cards[0].agents
        assert "test-engineer" in cards[0].agents

    def test_current_phase_name_set_when_in_bounds(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        phase = PlanPhase(phase_id=0, name="Database Migration", steps=[_step()])
        plan = _plan(phases=[phase])
        state = _execution_state(plan=plan, current_phase=0)
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        assert cards[0].current_phase == "Database Migration"

    def test_current_phase_empty_when_out_of_bounds(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(phases=[_phase()])
        # current_phase = 1 but only 1 phase (index 0) exists
        state = _execution_state(plan=plan, current_phase=1)
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        assert cards[0].current_phase == ""

    def test_card_column_is_valid_pmo_column(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        state = _execution_state(status="running")
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        assert cards[0].column in PMO_COLUMNS


# ---------------------------------------------------------------------------
# scan_project — failed execution has error field set
# ---------------------------------------------------------------------------

class TestScanProjectFailedExecution:
    def test_error_field_set_from_most_recent_failed_step(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(phases=[_phase(steps=[_step("1.1"), _step("1.2")])])
        results = [
            StepResult(step_id="1.1", agent_name="backend-engineer",
                       status="failed", error="First failure"),
            StepResult(step_id="1.2", agent_name="backend-engineer",
                       status="failed", error="Second failure"),
        ]
        state = _execution_state(plan=plan, status="failed", step_results=results)
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        assert cards[0].error == "Second failure"

    def test_error_field_empty_when_no_failures(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        results = [
            StepResult(step_id="1.1", agent_name="backend-engineer", status="complete"),
        ]
        state = _execution_state(step_results=results)
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        assert cards[0].error == ""


# ---------------------------------------------------------------------------
# scan_project — plan.json present but no execution state → queued
# ---------------------------------------------------------------------------

class TestScanProjectQueuedFromPlanJson:
    def test_plan_json_only_gives_queued_card(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(task_id="queued-task", task_summary="Waiting to start")
        _write_plan_json(project_root, plan)

        cards = _scanner(store).scan_project(project)
        assert len(cards) == 1
        assert cards[0].column == "queued"

    def test_queued_card_title_from_plan(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(task_summary="Queue me up")
        _write_plan_json(project_root, plan)

        cards = _scanner(store).scan_project(project)
        assert cards[0].title == "Queue me up"

    def test_queued_card_steps_total_from_plan(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(phases=[_phase(steps=[_step("1.1"), _step("1.2")])])
        _write_plan_json(project_root, plan)

        cards = _scanner(store).scan_project(project)
        assert cards[0].steps_total == 2

    def test_execution_state_takes_priority_over_plan_json(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(task_id="shared-id")
        _write_plan_json(project_root, plan)
        state = _execution_state(plan=plan, status="running")
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        # Should use the execution state, not queued
        assert cards[0].column == "executing"

    def test_corrupt_plan_json_returns_empty_list(self, tmp_path: Path):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        context_root = project_root / ".claude" / "team-context"
        context_root.mkdir(parents=True, exist_ok=True)
        (context_root / "plan.json").write_text("not json {{{", encoding="utf-8")

        cards = _scanner(store).scan_project(project)
        assert cards == []


# ---------------------------------------------------------------------------
# scan_all
# ---------------------------------------------------------------------------

class TestScanAll:
    def test_scan_all_aggregates_across_projects(self, tmp_path: Path):
        store = _store(tmp_path)

        root_a, proj_a = _project_dir(tmp_path, project_id="nds")
        root_b, proj_b = _project_dir(tmp_path, project_id="atl")
        _write_execution_state(root_a, _execution_state(plan=_plan(task_id="t-nds")))
        _write_execution_state(root_b, _execution_state(plan=_plan(task_id="t-atl")))

        store.register_project(proj_a)
        store.register_project(proj_b)

        scanner = _scanner(store)
        cards = scanner.scan_all()
        ids = {c.card_id for c in cards}
        assert "t-nds" in ids
        assert "t-atl" in ids

    def test_scan_all_includes_archived_cards(self, tmp_path: Path):
        from agent_baton.models.pmo import PmoCard
        store = _store(tmp_path)
        archived = PmoCard(
            card_id="archived-task",
            project_id="nds",
            program="NDS",
            title="Old work",
            column="deployed",
        )
        store.archive_card(archived)

        scanner = _scanner(store)
        cards = scanner.scan_all()
        ids = {c.card_id for c in cards}
        assert "archived-task" in ids

    def test_scan_all_deduplicates_archived_and_active_cards(self, tmp_path: Path):
        from agent_baton.models.pmo import PmoCard
        store = _store(tmp_path)
        root_a, proj_a = _project_dir(tmp_path, project_id="nds")
        store.register_project(proj_a)

        plan = _plan(task_id="shared-id")
        _write_execution_state(root_a, _execution_state(plan=plan, status="running"))

        # Archive a card with the same ID
        archived = PmoCard(
            card_id="shared-id",
            project_id="nds",
            program="NDS",
            title="Archived copy",
            column="deployed",
        )
        store.archive_card(archived)

        scanner = _scanner(store)
        cards = scanner.scan_all()
        matching = [c for c in cards if c.card_id == "shared-id"]
        assert len(matching) == 1

    def test_scan_all_empty_when_no_projects(self, tmp_path: Path):
        store = _store(tmp_path)
        scanner = _scanner(store)
        cards = scanner.scan_all()
        assert cards == []

    def test_scan_all_skips_projects_with_no_state(self, tmp_path: Path):
        store = _store(tmp_path)
        root_a, proj_a = _project_dir(tmp_path, project_id="nds")
        root_b, proj_b = _project_dir(tmp_path, project_id="atl")
        # Only nds has execution state
        _write_execution_state(root_a, _execution_state(plan=_plan(task_id="t-nds")))
        store.register_project(proj_a)
        store.register_project(proj_b)

        cards = _scanner(store).scan_all()
        ids = {c.card_id for c in cards}
        assert "t-nds" in ids
        assert len([c for c in cards if c.project_id == "atl"]) == 0


# ---------------------------------------------------------------------------
# program_health
# ---------------------------------------------------------------------------

class TestProgramHealth:
    def _setup_projects(self, tmp_path: Path, store: PmoStore) -> dict[str, Path]:
        """Register two programs with projects and return {project_id: root_path}."""
        root_nds, proj_nds = _project_dir(tmp_path, project_id="nds")
        root_atl, proj_atl = _project_dir(tmp_path, project_id="atl")
        store.register_project(proj_nds)
        store.register_project(proj_atl)
        return {"nds": root_nds, "atl": root_atl}

    def test_returns_dict_keyed_by_program(self, tmp_path: Path):
        store = _store(tmp_path)
        roots = self._setup_projects(tmp_path, store)
        _write_execution_state(roots["nds"], _execution_state(plan=_plan(task_id="t1")))

        health = _scanner(store).program_health()
        assert "NDS" in health

    def test_health_values_are_program_health_instances(self, tmp_path: Path):
        store = _store(tmp_path)
        roots = self._setup_projects(tmp_path, store)
        _write_execution_state(roots["nds"], _execution_state(plan=_plan(task_id="t1")))

        health = _scanner(store).program_health()
        for v in health.values():
            assert isinstance(v, ProgramHealth)

    def test_deployed_card_increments_completed(self, tmp_path: Path):
        store = _store(tmp_path)
        roots = self._setup_projects(tmp_path, store)
        plan = _plan(task_id="t-complete")
        _write_execution_state(roots["nds"],
                               _execution_state(plan=plan, status="complete"))

        health = _scanner(store).program_health()
        assert health["NDS"].completed == 1

    def test_awaiting_human_card_increments_blocked(self, tmp_path: Path):
        store = _store(tmp_path)
        roots = self._setup_projects(tmp_path, store)
        plan = _plan(task_id="t-blocked")
        _write_execution_state(roots["nds"],
                               _execution_state(plan=plan, status="approval_pending"))

        health = _scanner(store).program_health()
        assert health["NDS"].blocked == 1

    def test_failed_card_increments_failed(self, tmp_path: Path):
        store = _store(tmp_path)
        roots = self._setup_projects(tmp_path, store)
        plan = _plan(task_id="t-failed")
        results = [
            StepResult(step_id="1.1", agent_name="be", status="failed", error="Boom"),
        ]
        _write_execution_state(roots["nds"],
                               _execution_state(plan=plan, status="failed", step_results=results))

        health = _scanner(store).program_health()
        assert health["NDS"].failed == 1

    def test_running_card_increments_active(self, tmp_path: Path):
        store = _store(tmp_path)
        roots = self._setup_projects(tmp_path, store)
        _write_execution_state(roots["nds"],
                               _execution_state(plan=_plan(task_id="t-running"), status="running"))

        health = _scanner(store).program_health()
        assert health["NDS"].active == 1

    def test_completion_pct_computed_correctly(self, tmp_path: Path):
        store = _store(tmp_path)
        roots = self._setup_projects(tmp_path, store)
        # Two plans for NDS: one complete, one running
        _write_execution_state(roots["nds"],
                               _execution_state(plan=_plan(task_id="done"), status="complete"))
        # Add a second project for NDS with a running plan
        root_nds2, proj_nds2 = _project_dir(tmp_path, project_id="nds2")
        proj_nds2 = PmoProject(
            project_id="nds2", name="NDS2", path=str(root_nds2), program="NDS"
        )
        store.register_project(proj_nds2)
        _write_execution_state(root_nds2,
                               _execution_state(plan=_plan(task_id="running"), status="running"))

        health = _scanner(store).program_health()
        nds_health = health["NDS"]
        assert nds_health.total_plans == 2
        assert nds_health.completed == 1
        assert nds_health.completion_pct == 50.0

    def test_completion_pct_zero_when_no_plans(self, tmp_path: Path):
        store = _store(tmp_path)
        store.save_config(
            __import__("agent_baton.models.pmo", fromlist=["PmoConfig"]).PmoConfig(
                programs=["EMPTY_PROGRAM"]
            )
        )
        health = _scanner(store).program_health()
        assert health["EMPTY_PROGRAM"].completion_pct == 0.0

    def test_program_health_uses_config_programs_list_when_set(self, tmp_path: Path):
        from agent_baton.models.pmo import PmoConfig
        store = _store(tmp_path)
        # Register a program explicitly without any associated project cards
        store.save_config(PmoConfig(programs=["STANDALONE"]))

        health = _scanner(store).program_health()
        assert "STANDALONE" in health

    def test_total_plans_counts_all_cards(self, tmp_path: Path):
        store = _store(tmp_path)
        roots = self._setup_projects(tmp_path, store)
        _write_execution_state(roots["nds"],
                               _execution_state(plan=_plan(task_id="t1"), status="running"))
        root_nds2, _ = _project_dir(tmp_path, project_id="nds2")
        proj_nds2 = PmoProject(
            project_id="nds2", name="NDS2", path=str(root_nds2), program="NDS"
        )
        store.register_project(proj_nds2)
        _write_execution_state(root_nds2,
                               _execution_state(plan=_plan(task_id="t2"), status="complete"))

        health = _scanner(store).program_health()
        assert health["NDS"].total_plans == 2


# ---------------------------------------------------------------------------
# _risk_level_to_priority — unit tests for the helper function
# ---------------------------------------------------------------------------

class TestRiskLevelToPriority:
    @pytest.mark.parametrize("risk_level,expected", [
        ("CRITICAL", 2),
        ("HIGH",     1),
        ("MEDIUM",   0),
        ("LOW",      0),
    ])
    def test_known_risk_levels(self, risk_level: str, expected: int):
        assert _risk_level_to_priority(risk_level) == expected

    def test_unknown_risk_level_returns_zero(self):
        assert _risk_level_to_priority("UNKNOWN") == 0

    @pytest.mark.parametrize("risk_level,expected", [
        ("critical", 2),
        ("Critical", 2),
        ("high",     1),
        ("High",     1),
        ("medium",   0),
        ("low",      0),
    ])
    def test_case_insensitive(self, risk_level: str, expected: int):
        assert _risk_level_to_priority(risk_level) == expected


# ---------------------------------------------------------------------------
# Priority field — integration with _state_to_card and queued plan path
# ---------------------------------------------------------------------------

class TestCardPriorityFromRiskLevel:
    @pytest.mark.parametrize("risk_level,expected_priority", [
        ("CRITICAL", 2),
        ("HIGH",     1),
        ("MEDIUM",   0),
        ("LOW",      0),
    ])
    def test_execution_state_card_priority(
        self, tmp_path: Path, risk_level: str, expected_priority: int
    ):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(risk_level=risk_level)
        state = _execution_state(plan=plan)
        _write_execution_state(project_root, state)

        cards = _scanner(store).scan_project(project)
        assert cards[0].priority == expected_priority

    @pytest.mark.parametrize("risk_level,expected_priority", [
        ("CRITICAL", 2),
        ("HIGH",     1),
        ("MEDIUM",   0),
        ("LOW",      0),
    ])
    def test_queued_plan_json_card_priority(
        self, tmp_path: Path, risk_level: str, expected_priority: int
    ):
        store = _store(tmp_path)
        project_root, project = _project_dir(tmp_path)
        plan = _plan(risk_level=risk_level)
        _write_plan_json(project_root, plan)

        cards = _scanner(store).scan_project(project)
        assert cards[0].priority == expected_priority
