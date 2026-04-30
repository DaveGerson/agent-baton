"""Integration tests for the baton execution engine lifecycle.

These tests build synthetic plans and drive them through the execution
engine programmatically, validating state transitions, action types,
and result recording at each step.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_baton.models.execution import (
    ActionType,
    ExecutionState,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepStatus,
    TeamMember,
)
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.persistence import StatePersistence


# ---------------------------------------------------------------------------
# Factories — follow the existing test_executor.py pattern
# ---------------------------------------------------------------------------

def _step(
    step_id: str = "1.1",
    agent_name: str = "backend-engineer--python",
    task: str = "Implement feature",
    model: str = "sonnet",
    step_type: str = "developing",
    parallel_safe: bool = False,
    allowed_paths: list[str] | None = None,
    depends_on: list[str] | None = None,
    team: list[TeamMember] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task,
        model=model,
        step_type=step_type,
        parallel_safe=parallel_safe,
        allowed_paths=allowed_paths or [],
        depends_on=depends_on or [],
        team=team or [],
    )


def _gate(gate_type: str = "build", command: str = "echo ok") -> PlanGate:
    return PlanGate(gate_type=gate_type, command=command, description="Check build")


def _phase(
    phase_id: int = 1,
    name: str = "Build",
    steps: list[PlanStep] | None = None,
    gate: PlanGate | None = None,
) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=name,
        steps=steps or [_step()],
        gate=gate,
    )


def _plan(
    task_id: str = "test-simple",
    task_summary: str = "Simple test plan",
    phases: list[PlanPhase] | None = None,
    risk_level: str = "LOW",
    complexity: str = "light",
    detected_stack: str | None = "python",
) -> MachinePlan:
    if phases is None:
        phases = [_phase()]
    return MachinePlan(
        task_id=task_id,
        task_summary=task_summary,
        risk_level=risk_level,
        budget_tier="lean",
        execution_mode="phased",
        task_type="test",
        phases=phases,
        complexity=complexity,
        classification_source="test-fixture",
        detected_stack=detected_stack,
    )


def _engine(tmp_path: Path, task_id: str | None = None) -> ExecutionEngine:
    """Create an ExecutionEngine backed by a temp dir."""
    return ExecutionEngine(
        team_context_root=tmp_path,
        task_id=task_id,
    )


def _load_state(tmp_path: Path) -> ExecutionState | None:
    """Load state from the legacy flat-file path (file-only mode).

    When the engine is constructed without a storage backend and without
    an explicit task_id, start() writes state to the flat legacy path
    ``<context_root>/execution-state.json``.  This helper matches that
    behavior for test assertions.
    """
    sp = StatePersistence(tmp_path)
    return sp.load()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_plan():
    """A minimal 2-phase plan: one step + gate per phase."""
    return _plan(
        task_id="test-simple",
        phases=[
            _phase(
                phase_id=1,
                name="Build",
                steps=[_step(step_id="1.1")],
                gate=_gate(),
            ),
            _phase(
                phase_id=2,
                name="Review",
                steps=[_step(
                    step_id="2.1",
                    agent_name="code-reviewer",
                    task="Review changes",
                    step_type="reviewing",
                )],
                gate=None,
            ),
        ],
    )


@pytest.fixture
def team_plan():
    """A plan with a team step (lead + implementer + reviewer)."""
    return _plan(
        task_id="test-team",
        complexity="medium",
        phases=[
            _phase(
                phase_id=1,
                name="Implementation",
                steps=[
                    _step(
                        step_id="1.1",
                        agent_name="team",
                        task="Team implementation",
                        team=[
                            TeamMember(
                                member_id="1.1.a",
                                agent_name="architect",
                                role="lead",
                                task_description="Design the solution",
                            ),
                            TeamMember(
                                member_id="1.1.b",
                                agent_name="backend-engineer--python",
                                role="implementer",
                                task_description="Implement the solution",
                            ),
                            TeamMember(
                                member_id="1.1.c",
                                agent_name="code-reviewer",
                                role="reviewer",
                                task_description="Review the implementation",
                            ),
                        ],
                    ),
                ],
                gate=_gate(gate_type="test", command="echo ok"),
            ),
        ],
    )


@pytest.fixture
def parallel_plan():
    """A plan with parallel-safe sibling steps."""
    return _plan(
        task_id="test-parallel",
        detected_stack="python",
        phases=[
            _phase(
                phase_id=1,
                name="Parallel Work",
                steps=[
                    _step(
                        step_id="1.1",
                        agent_name="backend-engineer--python",
                        task="Build backend",
                        parallel_safe=True,
                        allowed_paths=["agent_baton/"],
                    ),
                    _step(
                        step_id="1.2",
                        agent_name="frontend-engineer",
                        task="Build frontend",
                        parallel_safe=True,
                        allowed_paths=["pmo-ui/"],
                    ),
                ],
                gate=_gate(),
            ),
        ],
    )


@pytest.fixture
def gate_fail_plan():
    """A plan with a single gated phase for testing gate failure paths."""
    return _plan(
        task_id="test-gate-fail",
        phases=[
            _phase(
                phase_id=1,
                name="Build",
                steps=[_step(step_id="1.1")],
                gate=_gate(),
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Tests: Plan Construction & Serialization
# ---------------------------------------------------------------------------

class TestPlanConstruction:
    """Validate synthetic plans serialize and deserialize correctly."""

    def test_simple_plan_roundtrip(self, simple_plan):
        data = simple_plan.to_dict()
        restored = MachinePlan.from_dict(data)
        assert restored.task_id == simple_plan.task_id
        assert len(restored.phases) == 2
        assert restored.phases[0].steps[0].step_id == "1.1"

    def test_team_plan_roundtrip(self, team_plan):
        data = team_plan.to_dict()
        restored = MachinePlan.from_dict(data)
        assert len(restored.phases[0].steps[0].team) == 3
        assert restored.phases[0].steps[0].team[0].role == "lead"
        assert restored.phases[0].steps[0].team[1].role == "implementer"
        assert restored.phases[0].steps[0].team[2].role == "reviewer"

    def test_parallel_plan_roundtrip(self, parallel_plan):
        data = parallel_plan.to_dict()
        restored = MachinePlan.from_dict(data)
        assert restored.phases[0].steps[0].parallel_safe is True
        assert restored.phases[0].steps[1].parallel_safe is True

    def test_plan_json_serializable(self, simple_plan):
        data = simple_plan.to_dict()
        json_str = json.dumps(data)
        assert json_str  # no exception

    def test_roundtrip_preserves_gate(self, simple_plan):
        data = simple_plan.to_dict()
        restored = MachinePlan.from_dict(data)
        assert restored.phases[0].gate is not None
        assert restored.phases[0].gate.gate_type == "build"
        assert restored.phases[1].gate is None

    def test_roundtrip_preserves_plan_metadata(self, simple_plan):
        data = simple_plan.to_dict()
        restored = MachinePlan.from_dict(data)
        assert restored.risk_level == "LOW"
        assert restored.budget_tier == "lean"
        assert restored.complexity == "light"
        assert restored.classification_source == "test-fixture"

    def test_team_member_ids_are_hierarchical(self, team_plan):
        members = team_plan.phases[0].steps[0].team
        for m in members:
            parts = m.member_id.split(".")
            assert len(parts) == 3, f"Expected N.N.x format, got {m.member_id}"
            # First two parts are digits
            assert parts[0].isdigit()
            assert parts[1].isdigit()
            # Third part is a letter
            assert parts[2].isalpha()

    def test_plan_total_steps(self, simple_plan):
        assert simple_plan.total_steps == 2

    def test_plan_all_agents(self, simple_plan):
        agents = simple_plan.all_agents
        assert "backend-engineer--python" in agents
        assert "code-reviewer" in agents


# ---------------------------------------------------------------------------
# Tests: Engine Lifecycle
# ---------------------------------------------------------------------------

class TestEngineLifecycle:
    """Validate the execution engine drives plans through the full lifecycle."""

    def test_start_returns_dispatch(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        action = engine.start(simple_plan)
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.1"
        assert action.agent_name == "backend-engineer--python"

    def test_record_then_gate(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)

        # Record step complete
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
            outcome="Done",
        )

        # Next should be GATE for phase 1
        action = engine.next_action()
        assert action.action_type == ActionType.GATE
        assert action.phase_id == 1

    def test_gate_pass_advances_phase(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)

        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="complete", outcome="Done",
        )
        engine.next_action()  # GATE
        engine.record_gate_result(phase_id=1, passed=True, output="OK")

        # Next should dispatch phase 2
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "2.1"

    def test_full_lifecycle_to_complete(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)

        # Phase 1
        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="complete", outcome="Done",
        )
        engine.next_action()  # GATE
        engine.record_gate_result(phase_id=1, passed=True, output="OK")

        # Phase 2
        action = engine.next_action()  # DISPATCH 2.1
        assert action.action_type == ActionType.DISPATCH
        engine.record_step_result(
            step_id="2.1", agent_name="code-reviewer",
            status="complete", outcome="LGTM",
        )

        # Should complete (no gate on phase 2)
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

    def test_empty_plan_raises(self, tmp_path):
        engine = _engine(tmp_path)
        empty = _plan(phases=[])
        with pytest.raises(ValueError, match="no phases"):
            engine.start(empty)

    def test_start_persists_state(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)
        # In file-only mode (no storage, no task_id at construction),
        # state is written to the legacy flat file.
        state = _load_state(tmp_path)
        assert state is not None
        assert state.task_id == simple_plan.task_id
        assert state.status == "running"


# ---------------------------------------------------------------------------
# Tests: Gate Handling
# ---------------------------------------------------------------------------

class TestGateHandling:
    """Validate gate pass and fail results propagate correctly."""

    def test_gate_fail_sets_gate_failed_status(self, tmp_path, gate_fail_plan):
        engine = _engine(tmp_path)
        engine.start(gate_fail_plan)

        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="complete", outcome="Done",
        )
        engine.next_action()  # GATE action
        engine.record_gate_result(phase_id=1, passed=False, output="Build failed")

        # After gate fail, next_action should return GATE (retry) or FAILED
        action = engine.next_action()
        # With max_gate_retries=3, first failure returns GATE (retry)
        assert action.action_type == ActionType.GATE

    def test_gate_exhaustion_returns_failed(self, tmp_path, gate_fail_plan):
        """After max_gate_retries failures, engine returns FAILED."""
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            max_gate_retries=1,
        )
        engine.start(gate_fail_plan)

        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="complete", outcome="Done",
        )
        engine.next_action()  # GATE
        engine.record_gate_result(phase_id=1, passed=False, output="Fail")

        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_gate_result_recorded_in_state(self, tmp_path, gate_fail_plan):
        engine = _engine(tmp_path)
        engine.start(gate_fail_plan)

        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="complete", outcome="Done",
        )
        engine.next_action()  # GATE
        engine.record_gate_result(phase_id=1, passed=True, output="OK")

        # Verify gate result is persisted
        state = _load_state(tmp_path)
        assert state is not None
        assert len(state.gate_results) == 1
        assert state.gate_results[0].passed is True
        assert state.gate_results[0].phase_id == 1


# ---------------------------------------------------------------------------
# Tests: State Transitions
# ---------------------------------------------------------------------------

class TestStateTransitions:
    """Validate step/phase status values transition correctly."""

    def test_step_result_status_complete(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)
        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="complete", outcome="Done",
        )

        state = _load_state(tmp_path)
        assert state is not None
        result = state.get_step_result("1.1")
        assert result is not None
        assert result.status == "complete"

    def test_step_result_status_failed(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)
        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="failed", outcome="", error="Crash",
        )

        state = _load_state(tmp_path)
        assert state is not None
        result = state.get_step_result("1.1")
        assert result is not None
        assert result.status == "failed"
        assert result.error == "Crash"

    def test_execution_status_running_at_start(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)
        state = _load_state(tmp_path)
        assert state is not None
        assert state.status == "running"

    def test_invalid_step_status_raises(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)
        with pytest.raises(ValueError, match="Invalid step status"):
            engine.record_step_result(
                step_id="1.1", agent_name="backend-engineer--python",
                status="bogus_status",
            )

    def test_completed_step_ids_property(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)
        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="complete", outcome="Done",
        )
        state = _load_state(tmp_path)
        assert state is not None
        assert "1.1" in state.completed_step_ids

    def test_failed_step_ids_property(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)
        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="failed", error="nope",
        )
        state = _load_state(tmp_path)
        assert state is not None
        assert "1.1" in state.failed_step_ids


# ---------------------------------------------------------------------------
# Tests: Step Failed -> Execution Failed
# ---------------------------------------------------------------------------

class TestStepFailurePropagation:
    """Validate that step failures propagate to execution-level failure."""

    def test_failed_step_causes_execution_failed(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)
        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="failed", error="Crashed",
        )
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED


# ---------------------------------------------------------------------------
# Tests: Team Steps
# ---------------------------------------------------------------------------

class TestTeamSteps:
    """Validate team member dispatch and recording."""

    def test_team_dispatches_members(self, tmp_path, team_plan):
        engine = _engine(tmp_path)
        action = engine.start(team_plan)

        # Should dispatch a team member (lead first)
        assert action.action_type == ActionType.DISPATCH
        # The step_id should be a team member ID (e.g. "1.1.a")
        assert "." in action.step_id
        assert len(action.step_id.split(".")) >= 3  # N.N.x format

    def test_team_member_id_format(self, team_plan):
        """Team member IDs follow the N.N.x hierarchical pattern."""
        members = team_plan.phases[0].steps[0].team
        expected_ids = {"1.1.a", "1.1.b", "1.1.c"}
        actual_ids = {m.member_id for m in members}
        assert actual_ids == expected_ids

    def test_team_member_roles(self, team_plan):
        members = team_plan.phases[0].steps[0].team
        roles = {m.role for m in members}
        assert "lead" in roles
        assert "implementer" in roles
        assert "reviewer" in roles


# ---------------------------------------------------------------------------
# Tests: Action Type Coverage
# ---------------------------------------------------------------------------

class TestActionTypeCoverage:
    """Validate that all expected ActionTypes are emitted in various scenarios."""

    def test_dispatch_action_type(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        action = engine.start(simple_plan)
        assert action.action_type == ActionType.DISPATCH

    def test_gate_action_type(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)
        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="complete", outcome="Done",
        )
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

    def test_complete_action_type(self, tmp_path):
        """Single phase, no gate -- should complete immediately."""
        plan = _plan(
            task_id="test-complete",
            phases=[_phase(phase_id=1, name="Only", gate=None)],
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="complete", outcome="Done",
        )
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

    def test_failed_action_type(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)
        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="failed", error="boom",
        )
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_dispatch_carries_agent_info(self, tmp_path, simple_plan):
        """DISPATCH actions carry agent_name, step_id, and delegation_prompt."""
        engine = _engine(tmp_path)
        action = engine.start(simple_plan)
        assert action.agent_name == "backend-engineer--python"
        assert action.step_id == "1.1"
        # delegation_prompt should be non-empty
        assert action.delegation_prompt

    def test_gate_carries_phase_info(self, tmp_path, simple_plan):
        """GATE actions carry gate_type and phase_id."""
        engine = _engine(tmp_path)
        engine.start(simple_plan)
        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="complete", outcome="Done",
        )
        action = engine.next_action()
        assert action.gate_type == "build"
        assert action.phase_id == 1


# ---------------------------------------------------------------------------
# Tests: Parallel Step Plans
# ---------------------------------------------------------------------------

class TestParallelSteps:
    """Validate plans with parallel-safe steps."""

    def test_parallel_safe_flag_preserved(self, parallel_plan):
        """parallel_safe flag survives serialization roundtrip."""
        data = parallel_plan.to_dict()
        restored = MachinePlan.from_dict(data)
        step_1 = restored.phases[0].steps[0]
        step_2 = restored.phases[0].steps[1]
        assert step_1.parallel_safe is True
        assert step_2.parallel_safe is True

    def test_allowed_paths_preserved(self, parallel_plan):
        step_1 = parallel_plan.phases[0].steps[0]
        step_2 = parallel_plan.phases[0].steps[1]
        assert step_1.allowed_paths == ["agent_baton/"]
        assert step_2.allowed_paths == ["pmo-ui/"]


# ---------------------------------------------------------------------------
# Tests: State Persistence
# ---------------------------------------------------------------------------

class TestStatePersistence:
    """Validate ExecutionState serialization and persistence."""

    def test_state_roundtrip(self, tmp_path, simple_plan):
        """ExecutionState survives save/load through StatePersistence."""
        engine = _engine(tmp_path)
        engine.start(simple_plan)

        # Engine in file-only mode writes to the legacy flat file
        sp = StatePersistence(tmp_path)
        state = sp.load()
        assert state is not None

        # Re-save and re-load
        sp.save(state)
        state2 = sp.load()
        assert state2 is not None
        assert state2.task_id == state.task_id
        assert state2.status == state.status

    def test_state_json_on_disk(self, tmp_path, simple_plan):
        """State is written as valid JSON on disk."""
        engine = _engine(tmp_path)
        engine.start(simple_plan)

        sp = StatePersistence(tmp_path)
        raw = sp.path.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert data["task_id"] == simple_plan.task_id

    def test_clear_removes_state(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)

        sp = StatePersistence(tmp_path)
        assert sp.exists()
        sp.clear()
        assert not sp.exists()

    def test_load_nonexistent_returns_none(self, tmp_path):
        sp = StatePersistence(tmp_path, task_id="nonexistent")
        assert sp.load() is None

    def test_active_task_management(self, tmp_path, simple_plan):
        engine = _engine(tmp_path)
        engine.start(simple_plan)

        active = StatePersistence.get_active_task_id(tmp_path)
        assert active == simple_plan.task_id

    def test_list_executions_namespaced(self, tmp_path, simple_plan):
        """Namespaced executions appear in list_executions."""
        # Manually save to the namespaced path to test list_executions
        state = ExecutionState(task_id=simple_plan.task_id, plan=simple_plan)
        sp = StatePersistence(tmp_path, task_id=simple_plan.task_id)
        sp.save(state)

        task_ids = StatePersistence.list_executions(tmp_path)
        assert simple_plan.task_id in task_ids

    def test_load_all_includes_legacy(self, tmp_path, simple_plan):
        """load_all picks up the legacy flat file written by the engine."""
        engine = _engine(tmp_path)
        engine.start(simple_plan)

        states = StatePersistence.load_all(tmp_path)
        task_ids = [s.task_id for s in states]
        assert simple_plan.task_id in task_ids


# ---------------------------------------------------------------------------
# Tests: Viz Integration
# ---------------------------------------------------------------------------

class TestVizIntegration:
    """Validate PlanSnapshot can be built from execution state at any point."""

    def test_snapshot_from_plan(self, simple_plan):
        from agent_baton.visualize.snapshot import PlanSnapshot
        snapshot = PlanSnapshot.from_plan(simple_plan)
        assert snapshot.task_id == simple_plan.task_id
        assert snapshot.execution_status == "not_started"
        assert snapshot.total_steps == 2
        assert len(snapshot.phases) == 2

    def test_snapshot_from_running_state(self, tmp_path, simple_plan):
        from agent_baton.visualize.snapshot import PlanSnapshot

        engine = _engine(tmp_path)
        engine.start(simple_plan)

        # Load state from the legacy flat file
        state = _load_state(tmp_path)
        assert state is not None
        snapshot = PlanSnapshot.from_state(state)
        assert snapshot.execution_status == "running"
        assert snapshot.current_phase_index == 0

    def test_snapshot_from_complete_state(self, tmp_path):
        """Snapshot from a completed execution has correct status."""
        from agent_baton.visualize.snapshot import PlanSnapshot

        plan = _plan(
            task_id="test-snap-complete",
            phases=[_phase(phase_id=1, name="Only", gate=None)],
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="complete", outcome="Done",
        )
        # Drive to COMPLETE
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

        state = _load_state(tmp_path)
        assert state is not None
        snapshot = PlanSnapshot.from_state(state)
        assert snapshot.steps_complete == 1
        assert snapshot.progress_pct == pytest.approx(100.0)

    def test_snapshot_step_count(self, simple_plan):
        from agent_baton.visualize.snapshot import PlanSnapshot
        snapshot = PlanSnapshot.from_plan(simple_plan)
        # 2 phases, each with 1 step
        assert snapshot.total_steps == 2

    def test_snapshot_phases_match_plan(self, simple_plan):
        from agent_baton.visualize.snapshot import PlanSnapshot
        snapshot = PlanSnapshot.from_plan(simple_plan)
        assert snapshot.phases[0].name == "Build"
        assert snapshot.phases[1].name == "Review"

    def test_snapshot_gate_status(self, simple_plan):
        from agent_baton.visualize.snapshot import PlanSnapshot
        snapshot = PlanSnapshot.from_plan(simple_plan)
        assert snapshot.phases[0].gate is not None
        assert snapshot.phases[0].gate.status == "pending"
        assert snapshot.phases[1].gate is None

    def test_compact_renderer_no_crash(self, simple_plan):
        from agent_baton.visualize.snapshot import PlanSnapshot
        from agent_baton.visualize.compact import render_compact
        from io import StringIO

        snapshot = PlanSnapshot.from_plan(simple_plan)
        # Should not raise
        try:
            from rich.console import Console
            console = Console(file=StringIO(), stderr=True)
            render_compact(snapshot, console=console)
        except ImportError:
            pytest.skip("rich not installed")

    def test_html_render_no_crash(self, simple_plan):
        from agent_baton.visualize.snapshot import PlanSnapshot
        from agent_baton.visualize.web_renderer import render_html

        snapshot = PlanSnapshot.from_plan(simple_plan)
        html = render_html(snapshot)
        assert "__BATON_PLAN__" in html
        assert simple_plan.task_id in html

    def test_team_plan_snapshot(self, team_plan):
        """Snapshot correctly represents team members."""
        from agent_baton.visualize.snapshot import PlanSnapshot
        snapshot = PlanSnapshot.from_plan(team_plan)
        step_snap = snapshot.phases[0].steps[0]
        assert len(step_snap.team) == 3
        roles = {m.role for m in step_snap.team}
        assert roles == {"lead", "implementer", "reviewer"}


# ---------------------------------------------------------------------------
# Tests: ExecutionState Model
# ---------------------------------------------------------------------------

class TestExecutionStateModel:
    """Validate ExecutionState dataclass behavior."""

    def test_state_from_dict_roundtrip(self, simple_plan):
        state = ExecutionState(
            task_id=simple_plan.task_id,
            plan=simple_plan,
            status="running",
        )
        data = state.to_dict()
        restored = ExecutionState.from_dict(data)
        assert restored.task_id == state.task_id
        assert restored.status == state.status
        assert len(restored.plan.phases) == len(state.plan.phases)

    def test_state_started_at_auto_set(self, simple_plan):
        state = ExecutionState(
            task_id="t1",
            plan=simple_plan,
        )
        assert state.started_at  # auto-set by __post_init__
        # Should parse as valid ISO 8601
        dt = datetime.fromisoformat(state.started_at)
        assert dt.year >= 2024

    def test_current_phase_obj(self, simple_plan):
        state = ExecutionState(
            task_id="t1",
            plan=simple_plan,
            current_phase=0,
        )
        phase = state.current_phase_obj
        assert phase is not None
        assert phase.phase_id == 1

    def test_current_phase_obj_out_of_bounds(self, simple_plan):
        state = ExecutionState(
            task_id="t1",
            plan=simple_plan,
            current_phase=999,
        )
        assert state.current_phase_obj is None


# ---------------------------------------------------------------------------
# Tests: Multi-phase Flow
# ---------------------------------------------------------------------------

class TestMultiPhaseFlow:
    """End-to-end flow validation across multiple phases."""

    def test_three_phase_flow(self, tmp_path):
        """Drive a 3-phase plan from start to COMPLETE."""
        plan = _plan(
            task_id="test-3phase",
            phases=[
                _phase(
                    phase_id=1, name="Design",
                    steps=[_step(step_id="1.1", agent_name="architect", task="Design")],
                    gate=_gate(gate_type="review"),
                ),
                _phase(
                    phase_id=2, name="Implement",
                    steps=[_step(step_id="2.1", task="Build")],
                    gate=_gate(gate_type="test"),
                ),
                _phase(
                    phase_id=3, name="Review",
                    steps=[_step(step_id="3.1", agent_name="code-reviewer", task="Review")],
                    gate=None,
                ),
            ],
        )
        engine = _engine(tmp_path)

        # Phase 1
        action = engine.start(plan)
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.1"

        engine.record_step_result(step_id="1.1", agent_name="architect", status="complete", outcome="Design done")
        action = engine.next_action()
        assert action.action_type == ActionType.GATE
        engine.record_gate_result(phase_id=1, passed=True, output="OK")

        # Phase 2
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "2.1"

        engine.record_step_result(step_id="2.1", agent_name="backend-engineer--python", status="complete", outcome="Built")
        action = engine.next_action()
        assert action.action_type == ActionType.GATE
        engine.record_gate_result(phase_id=2, passed=True, output="Tests pass")

        # Phase 3
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "3.1"

        engine.record_step_result(step_id="3.1", agent_name="code-reviewer", status="complete", outcome="LGTM")

        # Should complete
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

    def test_gate_fail_then_retry_then_pass(self, tmp_path):
        """Gate failure followed by a successful retry."""
        plan = _plan(
            task_id="test-retry",
            phases=[
                _phase(
                    phase_id=1, name="Build",
                    steps=[_step(step_id="1.1")],
                    gate=_gate(),
                ),
                _phase(
                    phase_id=2, name="Final",
                    steps=[_step(step_id="2.1", agent_name="code-reviewer", task="Final review")],
                    gate=None,
                ),
            ],
        )
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer--python",
            status="complete", outcome="Done",
        )
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

        # First gate fail
        engine.record_gate_result(phase_id=1, passed=False, output="Build broken")
        action = engine.next_action()
        assert action.action_type == ActionType.GATE  # retry

        # Second gate pass
        engine.record_gate_result(phase_id=1, passed=True, output="Fixed")
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "2.1"
