"""Tests for agent_baton.core.storage.sqlite_backend.SqliteStorage."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.budget import BudgetRecommendation
from agent_baton.models.enums import FailureClass
from agent_baton.models.events import Event
from agent_baton.models.execution import (
    ApprovalResult,
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanAmendment,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
    TeamMember,
    TeamStepResult,
)
from agent_baton.models.pattern import LearnedPattern
from agent_baton.models.plan import MissionLogEntry
from agent_baton.models.retrospective import (
    AgentOutcome,
    KnowledgeGap,
    Retrospective,
    RosterRecommendation,
    SequencingNote,
)
from agent_baton.models.trace import TaskTrace, TraceEvent
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> SqliteStorage:
    return SqliteStorage(tmp_path / "baton.db")


@pytest.fixture
def simple_plan() -> MachinePlan:
    """A minimal two-phase plan for reuse across tests."""
    step1 = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer--python",
        task_description="Implement the feature",
        model="sonnet",
        depends_on=[],
        deliverables=["agent_baton/feature.py"],
        allowed_paths=["agent_baton/"],
        blocked_paths=[],
        context_files=["CLAUDE.md"],
    )
    gate = PlanGate(
        gate_type="test",
        command="pytest tests/",
        description="All tests pass",
        fail_on=["test failures"],
    )
    phase1 = PlanPhase(
        phase_id=1,
        name="Implementation",
        steps=[step1],
        gate=gate,
        approval_required=False,
    )
    step2 = PlanStep(
        step_id="2.1",
        agent_name="code-reviewer",
        task_description="Review the implementation",
        model="opus",
        depends_on=["1.1"],
    )
    phase2 = PlanPhase(
        phase_id=2,
        name="Review",
        steps=[step2],
        approval_required=True,
        approval_description="Human review required before merge",
    )
    return MachinePlan(
        task_id="task-abc123",
        task_summary="Add new storage backend",
        risk_level="MEDIUM",
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[phase1, phase2],
        shared_context="shared ctx",
        pattern_source="pattern-001",
        created_at="2026-01-01T00:00:00+00:00",
    )


@pytest.fixture
def simple_state(simple_plan: MachinePlan) -> ExecutionState:
    """A minimal ExecutionState wrapping simple_plan."""
    return ExecutionState(
        task_id=simple_plan.task_id,
        plan=simple_plan,
        current_phase=0,
        current_step_index=0,
        status="running",
        started_at="2026-01-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _round_trip_plan(store: SqliteStorage, plan: MachinePlan) -> MachinePlan:
    store.save_plan(plan)
    loaded = store.load_plan(plan.task_id)
    assert loaded is not None
    return loaded


# ---------------------------------------------------------------------------
# db_path property
# ---------------------------------------------------------------------------


def test_db_path(store: SqliteStorage, tmp_path: Path) -> None:
    assert store.db_path == tmp_path / "baton.db"
    # The file is created lazily on first connection; trigger it.
    store.get_active_task()
    assert store.db_path.exists()


# ---------------------------------------------------------------------------
# Active task
# ---------------------------------------------------------------------------


class TestActiveTask:
    def test_none_when_unset(self, store: SqliteStorage) -> None:
        assert store.get_active_task() is None

    def test_set_and_get(self, store: SqliteStorage) -> None:
        store.set_active_task("task-001")
        assert store.get_active_task() == "task-001"

    def test_replace(self, store: SqliteStorage) -> None:
        store.set_active_task("task-001")
        store.set_active_task("task-002")
        assert store.get_active_task() == "task-002"


# ---------------------------------------------------------------------------
# Plans (standalone)
# ---------------------------------------------------------------------------


class TestPlanStorage:
    def test_save_and_load(
        self, store: SqliteStorage, simple_plan: MachinePlan
    ) -> None:
        loaded = _round_trip_plan(store, simple_plan)
        assert loaded.task_id == simple_plan.task_id
        assert loaded.task_summary == simple_plan.task_summary
        assert loaded.risk_level == simple_plan.risk_level
        assert loaded.budget_tier == simple_plan.budget_tier
        assert loaded.execution_mode == simple_plan.execution_mode
        assert loaded.git_strategy == simple_plan.git_strategy
        assert loaded.shared_context == simple_plan.shared_context
        assert loaded.pattern_source == simple_plan.pattern_source

    def test_phases_round_trip(
        self, store: SqliteStorage, simple_plan: MachinePlan
    ) -> None:
        loaded = _round_trip_plan(store, simple_plan)
        assert len(loaded.phases) == 2
        p1 = loaded.phases[0]
        assert p1.phase_id == 1
        assert p1.name == "Implementation"
        assert p1.approval_required is False
        assert p1.gate is not None
        assert p1.gate.gate_type == "test"
        assert p1.gate.command == "pytest tests/"
        assert p1.gate.fail_on == ["test failures"]

    def test_approval_phase_round_trip(
        self, store: SqliteStorage, simple_plan: MachinePlan
    ) -> None:
        loaded = _round_trip_plan(store, simple_plan)
        p2 = loaded.phases[1]
        assert p2.approval_required is True
        assert p2.approval_description == "Human review required before merge"

    def test_steps_round_trip(
        self, store: SqliteStorage, simple_plan: MachinePlan
    ) -> None:
        loaded = _round_trip_plan(store, simple_plan)
        step = loaded.phases[0].steps[0]
        assert step.step_id == "1.1"
        assert step.agent_name == "backend-engineer--python"
        assert step.deliverables == ["agent_baton/feature.py"]
        assert step.allowed_paths == ["agent_baton/"]
        assert step.context_files == ["CLAUDE.md"]
        assert step.depends_on == []

    def test_none_for_missing_plan(self, store: SqliteStorage) -> None:
        assert store.load_plan("nonexistent") is None

    def test_overwrite_plan(
        self, store: SqliteStorage, simple_plan: MachinePlan
    ) -> None:
        store.save_plan(simple_plan)
        simple_plan.task_summary = "Updated summary"
        store.save_plan(simple_plan)
        loaded = store.load_plan(simple_plan.task_id)
        assert loaded is not None
        assert loaded.task_summary == "Updated summary"

    def test_team_step_round_trip(self, store: SqliteStorage) -> None:
        member_a = TeamMember(
            member_id="1.1.a",
            agent_name="backend-engineer--python",
            role="implementer",
            task_description="Write the code",
            model="sonnet",
            depends_on=[],
            deliverables=["feature.py"],
        )
        member_b = TeamMember(
            member_id="1.1.b",
            agent_name="test-engineer",
            role="reviewer",
            task_description="Write tests",
            model="sonnet",
            depends_on=["1.1.a"],
            deliverables=["test_feature.py"],
        )
        team_step = PlanStep(
            step_id="1.1",
            agent_name="team",
            task_description="Team implementation",
            team=[member_a, member_b],
        )
        phase = PlanPhase(phase_id=1, name="Build", steps=[team_step])
        plan = MachinePlan(
            task_id="team-task",
            task_summary="Team task",
            phases=[phase],
        )
        loaded = _round_trip_plan(store, plan)
        step = loaded.phases[0].steps[0]
        assert len(step.team) == 2
        assert step.team[0].member_id == "1.1.a"
        assert step.team[1].member_id == "1.1.b"
        assert step.team[1].depends_on == ["1.1.a"]


# ---------------------------------------------------------------------------
# Execution State
# ---------------------------------------------------------------------------


class TestExecutionState:
    def test_save_and_load_minimal(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        store.save_execution(simple_state)
        loaded = store.load_execution(simple_state.task_id)
        assert loaded is not None
        assert loaded.task_id == simple_state.task_id
        assert loaded.status == "running"
        assert loaded.current_phase == 0
        assert loaded.current_step_index == 0
        assert loaded.started_at == simple_state.started_at
        assert loaded.completed_at == ""

    def test_list_executions(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        assert store.list_executions() == []
        store.save_execution(simple_state)
        assert store.list_executions() == [simple_state.task_id]

    def test_delete_execution(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        store.save_execution(simple_state)
        store.delete_execution(simple_state.task_id)
        assert store.load_execution(simple_state.task_id) is None
        assert store.list_executions() == []

    def test_delete_execution_cascades_to_child_tables(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        """Deleting an execution must remove all FK-linked child rows."""
        simple_state.step_results = [
            StepResult(
                step_id="1.1",
                agent_name="backend-engineer--python",
                status="complete",
                outcome="Done",
            )
        ]
        simple_state.gate_results = [
            GateResult(phase_id=1, gate_type="test", passed=True, output="OK")
        ]
        simple_state.approval_results = [
            ApprovalResult(phase_id=2, result="approve")
        ]
        simple_state.amendments = [
            PlanAmendment(
                amendment_id="amd-cascade",
                trigger="manual",
                trigger_phase_id=1,
                description="Cascade test",
            )
        ]
        store.save_execution(simple_state)

        conn = store._conn()
        task_id = simple_state.task_id

        # Confirm child rows exist before deletion
        assert conn.execute(
            "SELECT COUNT(*) FROM step_results WHERE task_id = ?", (task_id,)
        ).fetchone()[0] > 0
        assert conn.execute(
            "SELECT COUNT(*) FROM gate_results WHERE task_id = ?", (task_id,)
        ).fetchone()[0] > 0
        assert conn.execute(
            "SELECT COUNT(*) FROM approval_results WHERE task_id = ?", (task_id,)
        ).fetchone()[0] > 0
        assert conn.execute(
            "SELECT COUNT(*) FROM amendments WHERE task_id = ?", (task_id,)
        ).fetchone()[0] > 0

        store.delete_execution(task_id)

        # All child rows must have been removed via CASCADE
        assert conn.execute(
            "SELECT COUNT(*) FROM step_results WHERE task_id = ?", (task_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM gate_results WHERE task_id = ?", (task_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM approval_results WHERE task_id = ?", (task_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM amendments WHERE task_id = ?", (task_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM plans WHERE task_id = ?", (task_id,)
        ).fetchone()[0] == 0

    def test_multiple_executions_coexist(self, store: SqliteStorage) -> None:
        """Saving multiple executions keeps them independently accessible."""
        for i in range(3):
            plan = MachinePlan(
                task_id=f"task-{i:03d}",
                task_summary=f"Task {i}",
                phases=[],
            )
            state = ExecutionState(
                task_id=f"task-{i:03d}",
                plan=plan,
                status="running",
                started_at=f"2026-01-0{i + 1}T00:00:00Z",
            )
            store.save_execution(state)

        ids = store.list_executions()
        assert set(ids) == {"task-000", "task-001", "task-002"}

        # Each loads independently and correctly
        for i in range(3):
            loaded = store.load_execution(f"task-{i:03d}")
            assert loaded is not None
            assert loaded.plan.task_summary == f"Task {i}"

        # Deleting one doesn't affect the others
        store.delete_execution("task-001")
        assert store.load_execution("task-000") is not None
        assert store.load_execution("task-001") is None
        assert store.load_execution("task-002") is not None

    def test_load_nonexistent_returns_none(self, store: SqliteStorage) -> None:
        assert store.load_execution("no-such-task") is None

    def test_update_execution_status(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        store.save_execution(simple_state)
        simple_state.status = "complete"
        simple_state.current_phase = 1
        simple_state.completed_at = "2026-01-02T00:00:00+00:00"
        store.save_execution(simple_state)
        loaded = store.load_execution(simple_state.task_id)
        assert loaded is not None
        assert loaded.status == "complete"
        assert loaded.current_phase == 1
        assert loaded.completed_at == "2026-01-02T00:00:00+00:00"

    def test_step_results_round_trip(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
            outcome="Implemented feature.py",
            files_changed=["agent_baton/feature.py", "tests/test_feature.py"],
            commit_hash="abc1234",
            estimated_tokens=5000,
            duration_seconds=120.5,
            retries=1,
            error="",
            completed_at="2026-01-01T01:00:00+00:00",
        )
        simple_state.step_results = [result]
        store.save_execution(simple_state)
        loaded = store.load_execution(simple_state.task_id)
        assert loaded is not None
        assert len(loaded.step_results) == 1
        sr = loaded.step_results[0]
        assert sr.step_id == "1.1"
        assert sr.outcome == "Implemented feature.py"
        assert sr.files_changed == ["agent_baton/feature.py", "tests/test_feature.py"]
        assert sr.commit_hash == "abc1234"
        assert sr.estimated_tokens == 5000
        assert sr.duration_seconds == 120.5
        assert sr.retries == 1

    def test_team_step_results_round_trip(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        mr = TeamStepResult(
            member_id="1.1.a",
            agent_name="backend-engineer--python",
            status="complete",
            outcome="Done",
            files_changed=["feature.py"],
        )
        result = StepResult(
            step_id="1.1",
            agent_name="team",
            status="complete",
            member_results=[mr],
        )
        simple_state.step_results = [result]
        store.save_execution(simple_state)
        loaded = store.load_execution(simple_state.task_id)
        assert loaded is not None
        sr = loaded.step_results[0]
        assert len(sr.member_results) == 1
        assert sr.member_results[0].member_id == "1.1.a"
        assert sr.member_results[0].files_changed == ["feature.py"]

    def test_gate_results_round_trip(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        gr = GateResult(
            phase_id=1,
            gate_type="test",
            passed=True,
            output="All 50 tests passed",
            checked_at="2026-01-01T01:30:00+00:00",
        )
        simple_state.gate_results = [gr]
        store.save_execution(simple_state)
        loaded = store.load_execution(simple_state.task_id)
        assert loaded is not None
        assert len(loaded.gate_results) == 1
        assert loaded.gate_results[0].passed is True
        assert loaded.gate_results[0].gate_type == "test"
        assert loaded.gate_results[0].output == "All 50 tests passed"

    def test_approval_results_round_trip(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        ar = ApprovalResult(
            phase_id=2,
            result="approve",
            feedback="Looks good",
            decided_at="2026-01-01T02:00:00+00:00",
        )
        simple_state.approval_results = [ar]
        store.save_execution(simple_state)
        loaded = store.load_execution(simple_state.task_id)
        assert loaded is not None
        assert len(loaded.approval_results) == 1
        assert loaded.approval_results[0].result == "approve"
        assert loaded.approval_results[0].feedback == "Looks good"

    def test_amendments_round_trip(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        am = PlanAmendment(
            amendment_id="amd-001",
            trigger="gate_feedback",
            trigger_phase_id=1,
            description="Add a linting phase",
            phases_added=[3],
            steps_added=["3.1"],
            feedback="CI lint step was missing",
            created_at="2026-01-01T01:45:00+00:00",
        )
        simple_state.amendments = [am]
        store.save_execution(simple_state)
        loaded = store.load_execution(simple_state.task_id)
        assert loaded is not None
        assert len(loaded.amendments) == 1
        a = loaded.amendments[0]
        assert a.amendment_id == "amd-001"
        assert a.phases_added == [3]
        assert a.steps_added == ["3.1"]
        assert a.feedback == "CI lint step was missing"


# ---------------------------------------------------------------------------
# Incremental result writers
# ---------------------------------------------------------------------------


class TestIncrementalResultWriters:
    def test_save_step_result(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        store.save_execution(simple_state)
        result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
            outcome="Done",
        )
        store.save_step_result(simple_state.task_id, result)
        loaded = store.load_execution(simple_state.task_id)
        assert loaded is not None
        assert len(loaded.step_results) == 1
        assert loaded.step_results[0].step_id == "1.1"

    def test_save_step_result_idempotent(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        store.save_execution(simple_state)
        result = StepResult(step_id="1.1", agent_name="agent", outcome="v1")
        store.save_step_result(simple_state.task_id, result)
        result.outcome = "v2"
        store.save_step_result(simple_state.task_id, result)
        loaded = store.load_execution(simple_state.task_id)
        assert loaded is not None
        assert loaded.step_results[0].outcome == "v2"

    def test_save_gate_result(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        store.save_execution(simple_state)
        gr = GateResult(phase_id=1, gate_type="lint", passed=False, output="errors")
        store.save_gate_result(simple_state.task_id, gr)
        loaded = store.load_execution(simple_state.task_id)
        assert loaded is not None
        assert loaded.gate_results[0].passed is False

    def test_save_approval_result(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        store.save_execution(simple_state)
        ar = ApprovalResult(phase_id=2, result="reject", feedback="Not ready")
        store.save_approval_result(simple_state.task_id, ar)
        loaded = store.load_execution(simple_state.task_id)
        assert loaded is not None
        assert loaded.approval_results[0].result == "reject"

    def test_save_amendment(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        store.save_execution(simple_state)
        am = PlanAmendment(
            amendment_id="amd-x",
            trigger="manual",
            trigger_phase_id=1,
            description="Extra step",
        )
        store.save_amendment(simple_state.task_id, am)
        loaded = store.load_execution(simple_state.task_id)
        assert loaded is not None
        assert loaded.amendments[0].amendment_id == "amd-x"


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class TestEvents:
    def test_append_and_read(self, store: SqliteStorage) -> None:
        ev = Event.create(topic="step.completed", task_id="t1", payload={"step": "1.1"})
        store.append_event(ev)
        events = store.read_events("t1")
        assert len(events) == 1
        assert events[0].topic == "step.completed"
        assert events[0].payload == {"step": "1.1"}

    def test_read_from_seq(self, store: SqliteStorage) -> None:
        for i in range(5):
            ev = Event.create(topic="x", task_id="t1", sequence=i)
            store.append_event(ev)
        events = store.read_events("t1", from_seq=3)
        assert len(events) == 2
        assert all(e.sequence >= 3 for e in events)

    def test_read_filters_by_task(self, store: SqliteStorage) -> None:
        ev1 = Event.create(topic="a", task_id="t1")
        ev2 = Event.create(topic="b", task_id="t2")
        store.append_event(ev1)
        store.append_event(ev2)
        assert len(store.read_events("t1")) == 1
        assert len(store.read_events("t2")) == 1

    def test_delete_events(self, store: SqliteStorage) -> None:
        store.append_event(Event.create(topic="x", task_id="t1"))
        store.delete_events("t1")
        assert store.read_events("t1") == []

    def test_read_empty_returns_empty_list(self, store: SqliteStorage) -> None:
        assert store.read_events("no-task") == []


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


class TestUsage:
    def _make_record(self, task_id: str = "task-001") -> TaskUsageRecord:
        agents = [
            AgentUsageRecord(
                name="backend-engineer--python",
                model="sonnet",
                steps=2,
                retries=0,
                gate_results=["pass", "pass"],
                estimated_tokens=4000,
                duration_seconds=90.0,
            ),
            AgentUsageRecord(
                name="code-reviewer",
                model="opus",
                steps=1,
                retries=0,
                gate_results=["pass"],
                estimated_tokens=2000,
                duration_seconds=45.0,
            ),
        ]
        return TaskUsageRecord(
            task_id=task_id,
            timestamp="2026-01-01T00:00:00Z",
            agents_used=agents,
            total_agents=2,
            risk_level="MEDIUM",
            sequencing_mode="phased_delivery",
            gates_passed=2,
            gates_failed=0,
            outcome="SHIP",
            notes="All good",
        )

    def test_log_and_read(self, store: SqliteStorage) -> None:
        rec = self._make_record()
        store.log_usage(rec)
        records = store.read_usage()
        assert len(records) == 1
        loaded = records[0]
        assert loaded.task_id == "task-001"
        assert loaded.outcome == "SHIP"
        assert loaded.total_agents == 2
        assert len(loaded.agents_used) == 2

    def test_agent_usage_fields(self, store: SqliteStorage) -> None:
        rec = self._make_record()
        store.log_usage(rec)
        loaded = store.read_usage()[0]
        agent = next(a for a in loaded.agents_used if a.name == "backend-engineer--python")
        assert agent.steps == 2
        assert agent.gate_results == ["pass", "pass"]
        assert agent.estimated_tokens == 4000
        assert agent.duration_seconds == 90.0

    def test_limit_parameter(self, store: SqliteStorage) -> None:
        for i in range(5):
            store.log_usage(self._make_record(task_id=f"task-{i:03d}"))
        assert len(store.read_usage(limit=3)) == 3

    def test_idempotent_log(self, store: SqliteStorage) -> None:
        rec = self._make_record()
        store.log_usage(rec)
        rec.outcome = "REVISE"
        store.log_usage(rec)
        records = store.read_usage()
        assert len(records) == 1
        assert records[0].outcome == "REVISE"


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


class TestTelemetry:
    def _event(self, **kwargs) -> dict:
        base = {
            "timestamp": "2026-01-01T00:00:00Z",
            "agent_name": "backend-engineer--python",
            "event_type": "tool_call",
            "tool_name": "Read",
            "file_path": "",
            "duration_ms": 10,
            "details": "",
            "task_id": "task-001",
        }
        base.update(kwargs)
        return base

    def test_log_and_read(self, store: SqliteStorage) -> None:
        store.log_telemetry(self._event())
        events = store.read_telemetry()
        assert len(events) == 1
        assert events[0]["agent_name"] == "backend-engineer--python"

    def test_limit(self, store: SqliteStorage) -> None:
        for _ in range(5):
            store.log_telemetry(self._event())
        assert len(store.read_telemetry(limit=2)) == 2

    def test_summary_totals(self, store: SqliteStorage) -> None:
        store.log_telemetry(self._event(event_type="file_read", file_path="/a.py"))
        store.log_telemetry(self._event(event_type="file_write", file_path="/b.py"))
        store.log_telemetry(
            self._event(agent_name="other-agent", event_type="tool_call")
        )
        s = store.telemetry_summary()
        assert s["total_events"] == 3
        assert s["events_by_type"]["file_read"] == 1
        assert s["events_by_type"]["file_write"] == 1
        assert "/a.py" in s["files_read"]
        assert "/b.py" in s["files_written"]
        assert s["events_by_agent"]["other-agent"] == 1

    def test_empty_summary(self, store: SqliteStorage) -> None:
        s = store.telemetry_summary()
        assert s["total_events"] == 0


# ---------------------------------------------------------------------------
# Retrospectives
# ---------------------------------------------------------------------------


class TestRetrospectives:
    def _make_retro(self, task_id: str = "retro-task") -> Retrospective:
        return Retrospective(
            task_id=task_id,
            task_name="Add storage backend",
            timestamp="2026-01-01T00:00:00Z",
            agent_count=3,
            retry_count=1,
            gates_passed=2,
            gates_failed=0,
            risk_level="MEDIUM",
            duration_estimate="2h",
            estimated_tokens=15000,
            what_worked=[
                AgentOutcome(
                    name="backend-engineer--python",
                    worked_well="Implemented cleanly",
                    issues="",
                    root_cause="",
                )
            ],
            what_didnt=[
                AgentOutcome(
                    name="code-reviewer",
                    worked_well="",
                    issues="Missed a bug",
                    root_cause="Insufficient context",
                )
            ],
            knowledge_gaps=[
                KnowledgeGap(
                    description="SQLite WAL mode",
                    affected_agent="backend-engineer--python",
                    suggested_fix="Add knowledge pack",
                )
            ],
            roster_recommendations=[
                RosterRecommendation(
                    action="improve",
                    target="code-reviewer",
                    reason="Add SQLite expertise",
                )
            ],
            sequencing_notes=[
                SequencingNote(
                    phase="Implementation",
                    observation="Gate was useful",
                    keep=True,
                )
            ],
        )

    def test_save_and_load(self, store: SqliteStorage) -> None:
        retro = self._make_retro()
        store.save_retrospective(retro)
        loaded = store.load_retrospective(retro.task_id)
        assert loaded is not None
        assert loaded.task_name == "Add storage backend"
        assert loaded.agent_count == 3
        assert loaded.estimated_tokens == 15000

    def test_what_worked_round_trip(self, store: SqliteStorage) -> None:
        retro = self._make_retro()
        store.save_retrospective(retro)
        loaded = store.load_retrospective(retro.task_id)
        assert loaded is not None
        assert len(loaded.what_worked) == 1
        assert loaded.what_worked[0].name == "backend-engineer--python"
        assert loaded.what_worked[0].worked_well == "Implemented cleanly"

    def test_what_didnt_round_trip(self, store: SqliteStorage) -> None:
        retro = self._make_retro()
        store.save_retrospective(retro)
        loaded = store.load_retrospective(retro.task_id)
        assert loaded is not None
        assert len(loaded.what_didnt) == 1
        assert loaded.what_didnt[0].issues == "Missed a bug"

    def test_knowledge_gaps_round_trip(self, store: SqliteStorage) -> None:
        retro = self._make_retro()
        store.save_retrospective(retro)
        loaded = store.load_retrospective(retro.task_id)
        assert loaded is not None
        assert loaded.knowledge_gaps[0].description == "SQLite WAL mode"
        assert loaded.knowledge_gaps[0].suggested_fix == "Add knowledge pack"

    def test_roster_recommendations_round_trip(self, store: SqliteStorage) -> None:
        retro = self._make_retro()
        store.save_retrospective(retro)
        loaded = store.load_retrospective(retro.task_id)
        assert loaded is not None
        assert loaded.roster_recommendations[0].action == "improve"
        assert loaded.roster_recommendations[0].target == "code-reviewer"

    def test_sequencing_notes_round_trip(self, store: SqliteStorage) -> None:
        retro = self._make_retro()
        store.save_retrospective(retro)
        loaded = store.load_retrospective(retro.task_id)
        assert loaded is not None
        note = loaded.sequencing_notes[0]
        assert note.phase == "Implementation"
        assert note.keep is True

    def test_list_retrospective_ids(self, store: SqliteStorage) -> None:
        store.save_retrospective(self._make_retro("r1"))
        store.save_retrospective(self._make_retro("r2"))
        ids = store.list_retrospective_ids()
        assert set(ids) == {"r1", "r2"}

    def test_load_nonexistent(self, store: SqliteStorage) -> None:
        assert store.load_retrospective("no-such") is None

    def test_overwrite_idempotent(self, store: SqliteStorage) -> None:
        retro = self._make_retro()
        store.save_retrospective(retro)
        retro.agent_count = 99
        store.save_retrospective(retro)
        loaded = store.load_retrospective(retro.task_id)
        assert loaded is not None
        assert loaded.agent_count == 99


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------


class TestTraces:
    def _make_trace(self, task_id: str = "trace-task") -> TaskTrace:
        events = [
            TraceEvent(
                timestamp="2026-01-01T00:00:00Z",
                event_type="agent_start",
                agent_name="backend-engineer--python",
                phase=1,
                step=1,
                details={"model": "sonnet"},
                duration_seconds=None,
            ),
            TraceEvent(
                timestamp="2026-01-01T00:01:00Z",
                event_type="agent_complete",
                agent_name="backend-engineer--python",
                phase=1,
                step=1,
                details={"outcome": "done"},
                duration_seconds=60.0,
            ),
        ]
        return TaskTrace(
            task_id=task_id,
            plan_snapshot={"task_id": task_id, "phases": []},
            events=events,
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T00:01:00Z",
            outcome="SHIP",
        )

    def test_save_and_load(self, store: SqliteStorage) -> None:
        trace = self._make_trace()
        store.save_trace(trace)
        loaded = store.load_trace(trace.task_id)
        assert loaded is not None
        assert loaded.task_id == trace.task_id
        assert loaded.outcome == "SHIP"
        assert loaded.plan_snapshot == {"task_id": trace.task_id, "phases": []}

    def test_events_round_trip(self, store: SqliteStorage) -> None:
        trace = self._make_trace()
        store.save_trace(trace)
        loaded = store.load_trace(trace.task_id)
        assert loaded is not None
        assert len(loaded.events) == 2
        ev0 = loaded.events[0]
        assert ev0.event_type == "agent_start"
        assert ev0.details == {"model": "sonnet"}
        assert ev0.duration_seconds is None

    def test_load_nonexistent(self, store: SqliteStorage) -> None:
        assert store.load_trace("no-such") is None

    def test_overwrite_clears_old_events(self, store: SqliteStorage) -> None:
        trace = self._make_trace()
        store.save_trace(trace)
        trace.events = []
        store.save_trace(trace)
        loaded = store.load_trace(trace.task_id)
        assert loaded is not None
        assert loaded.events == []


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------


class TestPatterns:
    def _make_pattern(self, pid: str = "p-001") -> LearnedPattern:
        return LearnedPattern(
            pattern_id=pid,
            task_type="new-api-endpoint",
            stack="python/fastapi",
            recommended_template="phased-api",
            recommended_agents=["backend-engineer--python", "test-engineer"],
            confidence=0.85,
            sample_size=10,
            success_rate=0.9,
            avg_token_cost=12000,
            evidence=["task-1", "task-2"],
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )

    def test_save_and_load(self, store: SqliteStorage) -> None:
        patterns = [self._make_pattern("p-001"), self._make_pattern("p-002")]
        store.save_patterns(patterns)
        loaded = store.load_patterns()
        assert len(loaded) == 2
        ids = {p.pattern_id for p in loaded}
        assert ids == {"p-001", "p-002"}

    def test_round_trip_fields(self, store: SqliteStorage) -> None:
        p = self._make_pattern()
        store.save_patterns([p])
        loaded = store.load_patterns()
        lp = loaded[0]
        assert lp.task_type == "new-api-endpoint"
        assert lp.recommended_agents == ["backend-engineer--python", "test-engineer"]
        assert lp.confidence == pytest.approx(0.85)
        assert lp.evidence == ["task-1", "task-2"]
        assert lp.stack == "python/fastapi"

    def test_full_replacement(self, store: SqliteStorage) -> None:
        store.save_patterns([self._make_pattern("p-001"), self._make_pattern("p-002")])
        store.save_patterns([self._make_pattern("p-003")])
        loaded = store.load_patterns()
        assert len(loaded) == 1
        assert loaded[0].pattern_id == "p-003"

    def test_save_empty_clears_all(self, store: SqliteStorage) -> None:
        store.save_patterns([self._make_pattern()])
        store.save_patterns([])
        assert store.load_patterns() == []

    def test_none_stack(self, store: SqliteStorage) -> None:
        p = self._make_pattern()
        p.stack = None
        store.save_patterns([p])
        loaded = store.load_patterns()
        assert loaded[0].stack is None


# ---------------------------------------------------------------------------
# Budget Recommendations
# ---------------------------------------------------------------------------


class TestBudgetRecommendations:
    def _make_rec(self, task_type: str = "bug-fix") -> BudgetRecommendation:
        return BudgetRecommendation(
            task_type=task_type,
            current_tier="standard",
            recommended_tier="lean",
            reason="Small fixes rarely need 5 agents",
            avg_tokens_used=3000,
            median_tokens_used=2800,
            p95_tokens_used=5000,
            sample_size=20,
            confidence=0.9,
            potential_savings=5000,
        )

    def test_save_and_load(self, store: SqliteStorage) -> None:
        recs = [self._make_rec("bug-fix"), self._make_rec("refactor")]
        store.save_budget_recommendations(recs)
        loaded = store.load_budget_recommendations()
        assert len(loaded) == 2

    def test_round_trip_fields(self, store: SqliteStorage) -> None:
        store.save_budget_recommendations([self._make_rec()])
        loaded = store.load_budget_recommendations()
        r = loaded[0]
        assert r.task_type == "bug-fix"
        assert r.recommended_tier == "lean"
        assert r.potential_savings == 5000
        assert r.confidence == pytest.approx(0.9)

    def test_full_replacement(self, store: SqliteStorage) -> None:
        store.save_budget_recommendations([self._make_rec("a"), self._make_rec("b")])
        store.save_budget_recommendations([self._make_rec("c")])
        loaded = store.load_budget_recommendations()
        assert len(loaded) == 1
        assert loaded[0].task_type == "c"


# ---------------------------------------------------------------------------
# Mission Log
# ---------------------------------------------------------------------------


class TestMissionLog:
    def _make_entry(
        self,
        agent: str = "backend-engineer--python",
        status: str = "COMPLETE",
    ) -> MissionLogEntry:
        return MissionLogEntry(
            agent_name=agent,
            status=status,
            assignment="Implement storage backend",
            result="Done. Created sqlite_backend.py",
            files=["agent_baton/core/storage/sqlite_backend.py"],
            decisions=["Used INSERT OR REPLACE for upserts"],
            issues=[],
            handoff="Ready for review",
            commit_hash="def5678",
            failure_class=None,
            timestamp=datetime(2026, 1, 1, 0, 0, 0),
        )

    def test_append_and_read(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        store.save_execution(simple_state)
        entry = self._make_entry()
        store.append_mission_log(simple_state.task_id, entry)
        entries = store.read_mission_log(simple_state.task_id)
        assert len(entries) == 1
        e = entries[0]
        assert e.agent_name == "backend-engineer--python"
        assert e.status == "COMPLETE"
        assert e.files == ["agent_baton/core/storage/sqlite_backend.py"]
        assert e.decisions == ["Used INSERT OR REPLACE for upserts"]
        assert e.commit_hash == "def5678"

    def test_multiple_entries_in_order(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        store.save_execution(simple_state)
        store.append_mission_log(simple_state.task_id, self._make_entry("agent-a"))
        store.append_mission_log(simple_state.task_id, self._make_entry("agent-b"))
        entries = store.read_mission_log(simple_state.task_id)
        assert len(entries) == 2
        assert entries[0].agent_name == "agent-a"
        assert entries[1].agent_name == "agent-b"

    def test_failure_class_round_trip(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        store.save_execution(simple_state)
        entry = self._make_entry(status="FAILED")
        entry.failure_class = FailureClass.QUALITY
        store.append_mission_log(simple_state.task_id, entry)
        entries = store.read_mission_log(simple_state.task_id)
        assert entries[0].failure_class == FailureClass.QUALITY

    def test_read_empty(self, store: SqliteStorage) -> None:
        assert store.read_mission_log("no-task") == []


# ---------------------------------------------------------------------------
# Shared Context & Codebase Profile
# ---------------------------------------------------------------------------


class TestContextAndProfile:
    def test_save_and_read_context(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        store.save_execution(simple_state)
        store.save_context(
            simple_state.task_id,
            content="# Context\nThis is the shared context.",
            task_title="Add storage backend",
            stack="python",
        )
        content = store.read_context(simple_state.task_id)
        assert content == "# Context\nThis is the shared context."

    def test_read_context_none_when_missing(self, store: SqliteStorage) -> None:
        assert store.read_context("no-task") is None

    def test_save_context_overwrites(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        store.save_execution(simple_state)
        store.save_context(simple_state.task_id, content="v1")
        store.save_context(simple_state.task_id, content="v2")
        assert store.read_context(simple_state.task_id) == "v2"

    def test_save_and_read_profile(self, store: SqliteStorage) -> None:
        store.save_profile("# Codebase Profile\nPython monorepo.")
        profile = store.read_profile()
        assert profile == "# Codebase Profile\nPython monorepo."

    def test_read_profile_none_when_missing(self, store: SqliteStorage) -> None:
        assert store.read_profile() is None

    def test_save_profile_overwrites(self, store: SqliteStorage) -> None:
        store.save_profile("v1")
        store.save_profile("v2")
        assert store.read_profile() == "v2"

    def test_context_extra_sections(
        self, store: SqliteStorage, simple_state: ExecutionState
    ) -> None:
        store.save_execution(simple_state)
        store.save_context(
            simple_state.task_id,
            content="full text",
            task_title="My Task",
            stack="python/fastapi",
            architecture="layered",
            conventions="PEP 8",
            guardrails="no external calls",
            agent_assignments="backend does X",
            domain_context="healthcare",
        )
        # Verify the row columns (reading raw from DB for thoroughness)
        conn = store._conn()
        row = conn.execute(
            "SELECT * FROM shared_context WHERE task_id = ?",
            (simple_state.task_id,),
        ).fetchone()
        assert row["task_title"] == "My Task"
        assert row["stack"] == "python/fastapi"
        assert row["domain_context"] == "healthcare"
