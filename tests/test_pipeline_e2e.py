"""End-to-end integration tests for the full execution pipeline.

Exercises the complete lifecycle — plan → start → dispatch → record →
gate → complete → retrospective → query — as a single flow, with both
file and SQLite backends.

Tests in this file:
    test_full_lifecycle_file_backend      — file persistence, no storage= arg
    test_full_lifecycle_sqlite_backend    — SQLite backend, full lifecycle
    test_retrospective_captures_gate_data — Bug 6 fix: retro contains gate data
    test_error_handling_sqlite_failure    — Bugs 1-2 fix: fallback on save failure
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.events.bus import EventBus
from agent_baton.core.storage import get_project_storage
from agent_baton.core.storage.queries import QueryEngine
from agent_baton.models.execution import ActionType, ExecutionState, MachinePlan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_loop(
    engine: ExecutionEngine,
    plan: MachinePlan,
) -> tuple[int, int]:
    """Drive the execution engine loop to COMPLETE (or FAILED).

    Returns (steps_dispatched, gates_run).  Raises RuntimeError if the loop
    iterates more than 100 times to prevent infinite loops in tests.
    """
    action = engine.start(plan)
    steps_dispatched = 0
    gates_run = 0

    for _ in range(100):
        if action.action_type in (ActionType.COMPLETE, ActionType.FAILED):
            break

        if action.action_type == ActionType.DISPATCH:
            engine.record_step_result(
                step_id=action.step_id,
                agent_name=action.agent_name,
                status="complete",
                outcome=f"Step {action.step_id} done by {action.agent_name}",
                estimated_tokens=5000,
                duration_seconds=30.0,
            )
            steps_dispatched += 1

        elif action.action_type == ActionType.GATE:
            engine.record_gate_result(
                phase_id=action.phase_id,
                passed=True,
                output="All checks passed",
            )
            gates_run += 1

        action = engine.next_action()
    else:
        raise RuntimeError("Execution loop exceeded 100 iterations — stuck?")

    return steps_dispatched, gates_run


# ---------------------------------------------------------------------------
# Test 1: Full lifecycle — file backend (no storage= parameter)
# ---------------------------------------------------------------------------

class TestFullLifecycleFileBackend:
    """Complete plan → start → dispatch → gate → complete lifecycle over files."""

    def test_full_lifecycle_file_backend(self, tmp_path: Path) -> None:
        tc = tmp_path

        # Create a plan via IntelligentPlanner
        planner = IntelligentPlanner(team_context_root=tc)
        plan = planner.create_plan("Test task", task_type="feature")

        assert isinstance(plan, MachinePlan)
        assert plan.task_id
        assert plan.phases, "Plan must have at least one phase"

        # Count expected gates from plan structure
        expected_gates = sum(1 for p in plan.phases if p.gate is not None)
        expected_steps = plan.total_steps

        # Create engine in legacy file mode (no storage=)
        engine = ExecutionEngine(team_context_root=tc, bus=EventBus())

        steps_dispatched, gates_run = _run_loop(engine, plan)

        # Complete the execution
        summary = engine.complete()

        # Verify summary contains task_id
        assert plan.task_id in summary

        # Verify step and gate counts are correct
        assert steps_dispatched == expected_steps, (
            f"Expected {expected_steps} steps dispatched, got {steps_dispatched}"
        )
        assert gates_run == expected_gates, (
            f"Expected {expected_gates} gates run, got {gates_run}"
        )

        # Verify execution state file is present and shows 'complete'
        state_path = tc / "execution-state.json"
        assert state_path.exists(), "execution-state.json must exist in file mode"

        import json
        state_data = json.loads(state_path.read_text())
        assert state_data["status"] == "complete"

        # Verify step_results count matches dispatched steps
        assert len(state_data["step_results"]) == steps_dispatched

        # Verify gate_results count matches gates run
        assert len(state_data["gate_results"]) == gates_run


# ---------------------------------------------------------------------------
# Test 2: Full lifecycle — SQLite backend
# ---------------------------------------------------------------------------

class TestFullLifecycleSqliteBackend:
    """Complete plan → start → dispatch → gate → complete lifecycle over SQLite."""

    def test_full_lifecycle_sqlite_backend(self, tmp_path: Path) -> None:
        tc = tmp_path

        # Create storage explicitly with SQLite backend
        storage = get_project_storage(tc, backend="sqlite")

        # Create a plan via IntelligentPlanner
        planner = IntelligentPlanner(team_context_root=tc)
        plan = planner.create_plan("Test task", task_type="feature")

        assert isinstance(plan, MachinePlan)
        assert plan.task_id
        assert plan.phases, "Plan must have at least one phase"

        expected_gates = sum(1 for p in plan.phases if p.gate is not None)
        expected_steps = plan.total_steps

        # Create engine with SQLite storage
        engine = ExecutionEngine(
            team_context_root=tc,
            bus=EventBus(),
            storage=storage,
            task_id=plan.task_id,
        )

        steps_dispatched, gates_run = _run_loop(engine, plan)

        summary = engine.complete()

        # Verify summary contains task_id
        assert plan.task_id in summary

        # Verify counts
        assert steps_dispatched == expected_steps
        assert gates_run == expected_gates

        # Verify via SQLite: execution state is 'complete'
        loaded_state = storage.load_execution(plan.task_id)
        assert loaded_state is not None, "SQLite must have the execution state"
        assert loaded_state.task_id == plan.task_id
        assert loaded_state.status == "complete"

        # Verify step_results count matches dispatched steps
        assert len(loaded_state.step_results) == steps_dispatched, (
            f"Expected {steps_dispatched} step results in SQLite, "
            f"got {len(loaded_state.step_results)}"
        )

        # Verify gate_results count matches gates run
        assert len(loaded_state.gate_results) == gates_run, (
            f"Expected {gates_run} gate results in SQLite, "
            f"got {len(loaded_state.gate_results)}"
        )

        # Verify retrospective was saved to SQLite
        retro = storage.load_retrospective(plan.task_id)
        assert retro is not None, "Retrospective must be saved to SQLite after complete()"
        assert retro.task_id == plan.task_id
        assert retro.agent_count > 0, "Retrospective must record at least one agent"

        # Verify via QueryEngine: task_list() returns the completed task
        db_path = tc / "baton.db"
        qe = QueryEngine(db_path)
        try:
            tasks = qe.task_list()
            task_ids = [t.task_id for t in tasks]
            assert plan.task_id in task_ids, (
                f"task_id {plan.task_id!r} not in QueryEngine.task_list() results: "
                f"{task_ids}"
            )

            # Verify agent_reliability() shows agents that were used
            reliability = qe.agent_reliability()
            assert len(reliability) > 0, (
                "agent_reliability() must return at least one AgentStats entry "
                "after a completed task"
            )
        finally:
            qe.close()


# ---------------------------------------------------------------------------
# Test 3: Retrospective captures gate data (Bug 6 fix)
# ---------------------------------------------------------------------------

class TestRetrospectiveCapturesGateData:
    """After completion, the retrospective records gate and agent data."""

    def test_retrospective_captures_gate_data(self, tmp_path: Path) -> None:
        tc = tmp_path
        storage = get_project_storage(tc, backend="sqlite")

        planner = IntelligentPlanner(team_context_root=tc)
        plan = planner.create_plan("Test task with gates", task_type="feature")

        # Ensure the plan has at least one gate to make the test meaningful
        gates_in_plan = [p for p in plan.phases if p.gate is not None]
        # If the planner returned no gates, skip rather than test a no-op
        if not gates_in_plan:
            pytest.skip("Planner produced a plan with no gates for this task type")

        engine = ExecutionEngine(
            team_context_root=tc,
            bus=EventBus(),
            storage=storage,
            task_id=plan.task_id,
        )

        steps_dispatched, gates_run = _run_loop(engine, plan)
        assert gates_run > 0, "At least one gate must have been processed"

        engine.complete()

        # Check that the retrospective recorded gate data
        retro = storage.load_retrospective(plan.task_id)
        assert retro is not None, "Retrospective must be persisted after complete()"
        assert retro.gates_passed > 0, (
            f"retrospective.gates_passed must be > 0 after {gates_run} gates passed, "
            f"got {retro.gates_passed}"
        )

        # Check that agent_count reflects the agents that actually ran
        assert retro.agent_count > 0, (
            "retrospective.agent_count must be > 0 after at least one dispatch"
        )


# ---------------------------------------------------------------------------
# Test 4: Error handling — SQLite failure falls back to file (Bugs 1-2 fix)
# ---------------------------------------------------------------------------

class TestErrorHandlingSqliteFailure:
    """When SQLite save_execution raises, the engine falls back to file persistence."""

    def test_error_handling_sqlite_failure(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        tc = tmp_path
        storage = get_project_storage(tc, backend="sqlite")

        # Build a minimal plan manually to avoid planner complexity
        from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="Implement the feature",
            model="sonnet",
        )
        phase = PlanPhase(
            phase_id=1,
            name="Implement",
            steps=[step],
        )
        plan = MachinePlan(
            task_id="e2e-fallback-task-001",
            task_summary="Fallback test task",
            risk_level="LOW",
            phases=[phase],
        )

        # Create a file persistence alongside the storage so fallback works
        file_persistence = StatePersistence(tc, task_id=plan.task_id)

        # Create engine with SQLite storage
        engine = ExecutionEngine(
            team_context_root=tc,
            bus=EventBus(),
            storage=storage,
            task_id=plan.task_id,
        )
        # Wire in the file persistence so the fallback path can write
        engine._persistence = file_persistence

        # Start the engine (this writes initial state via SQLite)
        action = engine.start(plan)
        assert action.action_type == ActionType.DISPATCH

        # Now make SQLite save_execution raise on subsequent calls
        fail_count = 0

        original_save = storage.save_execution

        def failing_save(state: ExecutionState) -> None:
            nonlocal fail_count
            fail_count += 1
            raise RuntimeError("Simulated SQLite failure")

        storage.save_execution = failing_save  # type: ignore[method-assign]

        # Record step result — this should trigger the failing save, then fall
        # back to file persistence without raising.
        with caplog.at_level(logging.WARNING):
            engine.record_step_result(
                step_id="1.1",
                agent_name="backend-engineer",
                status="complete",
                outcome="Done",
            )

        # At least one SQLite save attempt was made
        assert fail_count >= 1, "save_execution must have been called at least once"

        # A warning must have been logged for the failure
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("SQLite" in msg or "fallback" in msg.lower() or "save" in msg.lower()
                   for msg in warning_messages), (
            f"Expected a SQLite fallback warning in logs, got: {warning_messages}"
        )

        # Verify the file fallback wrote state to disk
        # The task-scoped path is used when task_id is set
        task_dir = tc / "executions" / plan.task_id
        task_scoped_state = task_dir / "execution-state.json"
        legacy_state = tc / "execution-state.json"

        file_state_exists = task_scoped_state.exists() or legacy_state.exists()
        assert file_state_exists, (
            "File persistence fallback must write execution-state.json after SQLite failure. "
            f"Checked: {task_scoped_state} and {legacy_state}"
        )
