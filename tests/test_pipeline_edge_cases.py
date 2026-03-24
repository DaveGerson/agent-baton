"""Edge-case tests for the execution pipeline — covering gaps not in existing suites.

Tests in this file:
    TestDualWriteFallbackOnSqliteFailure  — second save_execution call fails; file fallback still current
    TestLoadFallbackOnSqliteFailure       — load_execution raises; falls back to file persistence
    TestGateCountingInRetrospective       — 2-gate plan: retro records gates_passed == 2
    TestActiveTaskSetOnStartSqliteMode    — no task_id at construction; start() still sets active task
    TestPmoScannerWithSqliteBackend       — scanner discovers executions from a baton.db
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.events.bus import EventBus
from agent_baton.core.pmo.scanner import PmoScanner
from agent_baton.core.pmo.store import PmoStore
from agent_baton.core.storage import get_project_storage
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import (
    ActionType,
    ExecutionState,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)
from agent_baton.models.pmo import PmoProject


# ---------------------------------------------------------------------------
# Shared plan builders
# ---------------------------------------------------------------------------

def _step(step_id: str, agent: str = "backend-engineer") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent,
        task_description=f"Work for step {step_id}",
        model="sonnet",
    )


def _phase_with_gate(phase_id: int, step_id: str, agent: str = "backend-engineer") -> PlanPhase:
    phase = PlanPhase(
        phase_id=phase_id,
        name=f"Phase {phase_id}",
        steps=[_step(step_id, agent)],
    )
    phase.gate = PlanGate(gate_type="test", command="pytest")
    return phase


def _two_gate_plan(task_id: str) -> MachinePlan:
    """A plan with 2 phases, each ending with a gate."""
    return MachinePlan(
        task_id=task_id,
        task_summary="Two-gate edge-case plan",
        risk_level="LOW",
        phases=[
            _phase_with_gate(1, "1.1", agent="backend-engineer"),
            _phase_with_gate(2, "2.1", agent="test-engineer"),
        ],
    )


def _single_step_plan(task_id: str) -> MachinePlan:
    """Minimal one-step, no-gate plan."""
    return MachinePlan(
        task_id=task_id,
        task_summary="Minimal single-step plan",
        risk_level="LOW",
        phases=[
            PlanPhase(phase_id=1, name="Implement", steps=[_step("1.1")]),
        ],
    )


# ---------------------------------------------------------------------------
# Helper: drive a plan to completion, recording all dispatches and gates
# ---------------------------------------------------------------------------

def _run_to_complete(engine: ExecutionEngine, plan: MachinePlan) -> tuple[int, int]:
    """Drive the engine loop to COMPLETE.  Returns (steps_dispatched, gates_run)."""
    action = engine.start(plan)
    steps = 0
    gates = 0
    for _ in range(200):
        if action.action_type in (ActionType.COMPLETE, ActionType.FAILED):
            break
        if action.action_type == ActionType.DISPATCH:
            engine.record_step_result(
                step_id=action.step_id,
                agent_name=action.agent_name,
                status="complete",
                outcome=f"{action.step_id} done",
            )
            steps += 1
        elif action.action_type == ActionType.GATE:
            engine.record_gate_result(
                phase_id=action.phase_id,
                passed=True,
                output="OK",
            )
            gates += 1
        action = engine.next_action()
    else:
        raise RuntimeError("Execution loop exceeded limit")
    return steps, gates


# ---------------------------------------------------------------------------
# Test 1: Dual-write fallback — SQLite succeeds on first call, fails on second
# ---------------------------------------------------------------------------

class TestDualWriteFallbackOnSqliteFailure:
    """When the *second* save_execution call raises (after initial start),
    the engine falls back to file persistence so the file layer stays current.
    """

    def test_file_has_state_after_second_sqlite_save_fails(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        storage = SqliteStorage(tmp_path / "baton.db")
        plan = _single_step_plan("dual-write-second-fail-001")

        # Wire file persistence manually so the fallback path can write.
        file_persistence = StatePersistence(tmp_path, task_id=plan.task_id)

        engine = ExecutionEngine(
            team_context_root=tmp_path,
            bus=EventBus(),
            storage=storage,
            task_id=plan.task_id,
        )
        engine._persistence = file_persistence

        # start() triggers the FIRST save — let it succeed normally.
        action = engine.start(plan)
        assert action.action_type == ActionType.DISPATCH

        # Confirm the first save wrote to SQLite successfully.
        assert storage.load_execution(plan.task_id) is not None

        # Now intercept future save_execution calls — fail on the SECOND call.
        call_count = [0]
        original_save = storage.save_execution

        def save_that_fails_on_second(state: ExecutionState) -> None:
            call_count[0] += 1
            if call_count[0] == 1:
                # Second real call (record_step_result triggers this)
                raise RuntimeError("Simulated SQLite second-call failure")
            original_save(state)

        storage.save_execution = save_that_fails_on_second  # type: ignore[method-assign]

        # record_step_result triggers the failing save — should not raise.
        with caplog.at_level(logging.WARNING):
            engine.record_step_result(
                step_id="1.1",
                agent_name="backend-engineer",
                status="complete",
                outcome="feature done",
            )

        # At least one attempt was made to call the patched save.
        assert call_count[0] >= 1

        # A warning must have been logged mentioning the failure.
        warning_texts = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "SQLite" in warning_texts or "fallback" in warning_texts.lower(), (
            f"Expected SQLite fallback warning, got: {warning_texts!r}"
        )

        # File persistence must have the step result captured.
        task_scoped = tmp_path / "executions" / plan.task_id / "execution-state.json"
        legacy = tmp_path / "execution-state.json"
        file_exists = task_scoped.exists() or legacy.exists()
        assert file_exists, (
            "File persistence must have execution-state.json after SQLite fallback. "
            f"Checked {task_scoped} and {legacy}"
        )

        # Verify the file actually records the step result.
        state_path = task_scoped if task_scoped.exists() else legacy
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
        assert len(state_data["step_results"]) >= 1, (
            "File-backed state must contain the step result that triggered the fallback"
        )


# ---------------------------------------------------------------------------
# Test 2: Load fallback on SQLite failure
# ---------------------------------------------------------------------------

class TestLoadFallbackOnSqliteFailure:
    """When load_execution raises, _load_execution() falls back to file
    persistence and returns a valid ExecutionState.
    """

    def test_next_action_falls_back_to_file_when_sqlite_load_fails(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        plan = _single_step_plan("load-fallback-001")

        # Start with SQLite storage so initial save goes to both backends.
        storage = SqliteStorage(tmp_path / "baton.db")
        file_persistence = StatePersistence(tmp_path, task_id=plan.task_id)

        engine = ExecutionEngine(
            team_context_root=tmp_path,
            bus=EventBus(),
            storage=storage,
            task_id=plan.task_id,
        )
        engine._persistence = file_persistence

        # start() — writes state to SQLite AND (via dual-write) to file.
        action = engine.start(plan)
        assert action.action_type == ActionType.DISPATCH

        # Confirm the file backend already has state (dual-write should have run).
        file_state = file_persistence.load()
        assert file_state is not None, (
            "Dual-write must have populated file persistence before the load test"
        )

        # Patch load_execution to always raise.
        def always_raising_load(task_id: str) -> None:
            raise RuntimeError("Simulated SQLite load failure")

        storage.load_execution = always_raising_load  # type: ignore[method-assign]

        # next_action() calls _load_execution() — must fall back to file.
        with caplog.at_level(logging.WARNING):
            result_action = engine.next_action()

        # The fallback must return a valid (non-None-triggered) action.
        assert result_action.action_type not in (ActionType.FAILED,), (
            f"Expected a valid action from file fallback, got FAILED: "
            f"{result_action.message!r}"
        )

        # A warning must have been logged about the SQLite load failure.
        warning_texts = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "SQLite" in warning_texts or "load" in warning_texts.lower(), (
            f"Expected a SQLite load-failure warning, got: {warning_texts!r}"
        )


# ---------------------------------------------------------------------------
# Test 3: Gate counting in retrospective — 2 gates, both passing
# ---------------------------------------------------------------------------

class TestGateCountingInRetrospective:
    """After completing a 2-gate plan, retrospective records gates_passed == 2
    and agent_count matches the number of unique agents in the plan.
    """

    def test_retrospective_records_two_gates_passed(self, tmp_path: Path) -> None:
        storage = get_project_storage(tmp_path, backend="sqlite")
        plan = _two_gate_plan("retro-two-gates-001")

        engine = ExecutionEngine(
            team_context_root=tmp_path,
            bus=EventBus(),
            storage=storage,
            task_id=plan.task_id,
        )

        steps_dispatched, gates_run = _run_to_complete(engine, plan)

        # Sanity-check the loop ran correctly.
        assert gates_run == 2, (
            f"Expected 2 gates to be run by the loop, got {gates_run}"
        )

        engine.complete()

        retro = storage.load_retrospective(plan.task_id)
        assert retro is not None, "Retrospective must be persisted after complete()"
        assert retro.task_id == plan.task_id

        # Core assertion: both gates must be counted.
        assert retro.gates_passed == 2, (
            f"Retrospective must report gates_passed=2, got {retro.gates_passed}"
        )

        # Agent count must equal the number of unique agents that ran steps.
        unique_agents = len({step.agent_name for phase in plan.phases for step in phase.steps})
        assert retro.agent_count == unique_agents, (
            f"Expected agent_count={unique_agents}, got {retro.agent_count}"
        )


# ---------------------------------------------------------------------------
# Test 4: Active task set on start() — SQLite mode, no task_id at construction
# ---------------------------------------------------------------------------

class TestActiveTaskSetOnStartSqliteMode:
    """When the engine is constructed without task_id, start() must still
    register the plan's task_id as the active task in SQLite storage.
    """

    def test_get_active_task_returns_plan_task_id(self, tmp_path: Path) -> None:
        storage = SqliteStorage(tmp_path / "baton.db")
        plan = _single_step_plan("active-task-no-ctor-id-001")

        # Deliberately omit task_id at construction.
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            bus=EventBus(),
            storage=storage,
            # task_id intentionally not passed
        )

        # Before start, no active task should exist.
        assert storage.get_active_task() is None

        action = engine.start(plan)
        assert action.action_type == ActionType.DISPATCH

        # After start(), storage must know the active task.
        active = storage.get_active_task()
        assert active == plan.task_id, (
            f"storage.get_active_task() must return {plan.task_id!r}, got {active!r}"
        )


# ---------------------------------------------------------------------------
# Test 5: PMO scanner with SQLite backend
# ---------------------------------------------------------------------------

class TestPmoScannerWithSqliteBackend:
    """PmoScanner.scan_project() discovers executions stored in baton.db."""

    def test_scanner_discovers_sqlite_execution(self, tmp_path: Path) -> None:
        # Build the project directory structure the scanner expects:
        #   <project_root>/.claude/team-context/baton.db
        project_root = tmp_path / "myproject"
        context_root = project_root / ".claude" / "team-context"
        context_root.mkdir(parents=True)

        # Create and populate a SQLite storage with one completed execution.
        storage = SqliteStorage(context_root / "baton.db")
        plan = _single_step_plan("scanner-sqlite-task-001")

        engine = ExecutionEngine(
            team_context_root=context_root,
            bus=EventBus(),
            storage=storage,
            task_id=plan.task_id,
        )
        action = engine.start(plan)
        assert action.action_type == ActionType.DISPATCH

        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome="done",
        )
        engine.complete()

        # Confirm the execution is in SQLite.
        assert storage.load_execution(plan.task_id) is not None

        # Register the project with a temporary PmoStore.
        baton_dir = tmp_path / ".baton"
        baton_dir.mkdir()
        pmo_store = PmoStore(
            config_path=baton_dir / "pmo-config.json",
            archive_path=baton_dir / "pmo-archive.jsonl",
        )
        project = PmoProject(
            project_id="test-proj",
            name="Test Project",
            path=str(project_root),
            program="TEST",
        )
        pmo_store.register_project(project)

        # Run the scanner.
        scanner = PmoScanner(store=pmo_store)
        cards = scanner.scan_project(project)

        # The scanner must have produced at least one card for the task.
        assert len(cards) >= 1, (
            f"Expected at least 1 PMO card from SQLite backend, got {len(cards)}"
        )

        card_ids = [c.card_id for c in cards]
        assert plan.task_id in card_ids, (
            f"Expected task_id {plan.task_id!r} in scanner results: {card_ids}"
        )

        # The card column must reflect a completed execution.
        matching = [c for c in cards if c.card_id == plan.task_id]
        assert matching[0].column == "deployed", (
            f"Completed task must be in 'deployed' column, got {matching[0].column!r}"
        )
