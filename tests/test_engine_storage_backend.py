"""Integration tests for ExecutionEngine with StorageBackend.

Verifies that the engine routes all persistence through SqliteStorage when
a storage backend is provided, and that it continues to work in legacy
file-based mode when no storage is given.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.events.bus import EventBus
from agent_baton.core.storage import get_project_storage
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.core.storage.file_backend import FileStorage
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step(step_id: str = "1.1", agent: str = "backend-engineer") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent,
        task_description="Do the work",
        model="sonnet",
    )


def _phase(phase_id: int = 1, steps: list[PlanStep] | None = None) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=f"Phase {phase_id}",
        steps=steps or [_step()],
    )


def _plan(task_id: str = "task-test-001") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Test the storage backend wiring",
        risk_level="LOW",
        phases=[_phase()],
    )


def _engine_with_sqlite(tmp_path: Path) -> tuple[ExecutionEngine, SqliteStorage]:
    storage = SqliteStorage(tmp_path / "baton.db")
    engine = ExecutionEngine(
        team_context_root=tmp_path,
        bus=EventBus(),
        storage=storage,
    )
    return engine, storage


def _engine_with_file(tmp_path: Path) -> tuple[ExecutionEngine, FileStorage]:
    storage = FileStorage(tmp_path)
    engine = ExecutionEngine(
        team_context_root=tmp_path,
        bus=EventBus(),
        storage=storage,
    )
    return engine, storage


# ---------------------------------------------------------------------------
# Tests: storage=None (legacy file mode must still work)
# ---------------------------------------------------------------------------

class TestLegacyFileMode:
    """Ensure no regressions when no storage is passed."""

    def test_start_returns_dispatch(self, tmp_path: Path) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        action = engine.start(_plan())
        assert action.action_type == ActionType.DISPATCH

    def test_state_file_is_written(self, tmp_path: Path) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_plan())
        # At minimum one of the state files should exist
        has_state = (
            (tmp_path / "execution-state.json").exists()
            or any((tmp_path / "executions").rglob("execution-state.json"))
        )
        assert has_state

    def test_record_and_complete_round_trip(self, tmp_path: Path) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_plan("legacy-task"))
        engine.record_step_result("1.1", "backend-engineer", status="complete", outcome="done")
        summary = engine.complete()
        assert "legacy-task" in summary or "completed" in summary.lower()


# ---------------------------------------------------------------------------
# Tests: storage=SqliteStorage
# ---------------------------------------------------------------------------

class TestSqliteStorageMode:
    """Engine routes all persistence through SqliteStorage."""

    def test_start_returns_dispatch_action(self, tmp_path: Path) -> None:
        engine, _ = _engine_with_sqlite(tmp_path)
        action = engine.start(_plan())
        assert action.action_type == ActionType.DISPATCH

    def test_state_persisted_to_db(self, tmp_path: Path) -> None:
        engine, store = _engine_with_sqlite(tmp_path)
        plan = _plan("sqlite-001")
        engine.start(plan)
        state = store.load_execution("sqlite-001")
        assert state is not None
        assert state.task_id == "sqlite-001"
        assert state.status == "running"

    def test_active_task_set_on_start(self, tmp_path: Path) -> None:
        engine, store = _engine_with_sqlite(tmp_path)
        engine.start(_plan("active-task-001"))
        assert store.get_active_task() == "active-task-001"

    def test_record_step_persisted(self, tmp_path: Path) -> None:
        engine, store = _engine_with_sqlite(tmp_path)
        plan = _plan("sqlite-002")
        engine.start(plan)
        engine.record_step_result(
            "1.1", "backend-engineer",
            status="complete",
            outcome="feature implemented",
            files_changed=["src/feature.py"],
            estimated_tokens=1000,
        )
        state = store.load_execution("sqlite-002")
        assert state is not None
        results = [r for r in state.step_results if r.step_id == "1.1"]
        assert len(results) == 1
        assert results[0].status == "complete"
        assert results[0].outcome == "feature implemented"

    def test_gate_result_persisted(self, tmp_path: Path) -> None:
        gate_phase = _phase(phase_id=1, steps=[_step()])
        gate_phase.gate = PlanGate(gate_type="test", command="pytest")
        plan = MachinePlan(
            task_id="sqlite-gate-001",
            task_summary="Gate test",
            risk_level="LOW",
            phases=[gate_phase],
        )
        engine, store = _engine_with_sqlite(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete")
        engine.record_gate_result(phase_id=1, passed=True, output="All tests pass")
        state = store.load_execution("sqlite-gate-001")
        assert state is not None
        assert len(state.gate_results) == 1
        assert state.gate_results[0].passed is True

    def test_complete_logs_usage(self, tmp_path: Path) -> None:
        engine, store = _engine_with_sqlite(tmp_path)
        plan = _plan("sqlite-complete-001")
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete")
        engine.complete()
        # Usage should be persisted in SQLite
        records = store.read_usage()
        assert any(r.task_id == "sqlite-complete-001" for r in records)

    def test_resume_reloads_state(self, tmp_path: Path) -> None:
        plan = _plan("sqlite-resume-001")
        engine, store = _engine_with_sqlite(tmp_path)
        engine.start(plan)
        # Create a fresh engine instance (simulating crash recovery)
        engine2 = ExecutionEngine(
            team_context_root=tmp_path,
            bus=EventBus(),
            task_id="sqlite-resume-001",
            storage=store,
        )
        action = engine2.resume()
        # Should return the next pending action (dispatch), not FAILED
        assert action.action_type in (ActionType.DISPATCH, ActionType.COMPLETE)

    def test_status_reflects_db_state(self, tmp_path: Path) -> None:
        engine, store = _engine_with_sqlite(tmp_path)
        engine.start(_plan("sqlite-status-001"))
        st = engine.status()
        assert st["task_id"] == "sqlite-status-001"
        assert st["status"] == "running"

    def test_no_legacy_files_written(self, tmp_path: Path) -> None:
        """In SQLite mode the legacy JSON files must NOT be created."""
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(_plan("sqlite-no-files-001"))
        assert not (tmp_path / "execution-state.json").exists()
        assert not (tmp_path / "usage-log.jsonl").exists()
        assert not (tmp_path / "telemetry.jsonl").exists()

    def test_complete_does_not_raise(self, tmp_path: Path) -> None:
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(_plan("sqlite-complete-noerr-001"))
        engine.record_step_result("1.1", "backend-engineer", status="complete")
        summary = engine.complete()
        assert isinstance(summary, str)
        assert "sqlite-complete-noerr-001" in summary


# ---------------------------------------------------------------------------
# Tests: storage=FileStorage (explicit FileStorage wrapping existing classes)
# ---------------------------------------------------------------------------

class TestFileStorageMode:
    """Engine also works when FileStorage is passed explicitly."""

    def test_start_returns_dispatch(self, tmp_path: Path) -> None:
        engine, _ = _engine_with_file(tmp_path)
        action = engine.start(_plan())
        assert action.action_type == ActionType.DISPATCH

    def test_state_round_trip(self, tmp_path: Path) -> None:
        engine, store = _engine_with_file(tmp_path)
        plan = _plan("file-001")
        engine.start(plan)
        state = store.load_execution("file-001")
        assert state is not None
        assert state.task_id == "file-001"


# ---------------------------------------------------------------------------
# Tests: get_project_storage auto-detection
# ---------------------------------------------------------------------------

class TestAutoDetection:
    """get_project_storage() picks the right backend."""

    def test_new_dir_returns_sqlite(self, tmp_path: Path) -> None:
        storage = get_project_storage(tmp_path)
        assert isinstance(storage, SqliteStorage)

    def test_dir_with_db_returns_sqlite(self, tmp_path: Path) -> None:
        (tmp_path / "baton.db").touch()
        storage = get_project_storage(tmp_path)
        assert isinstance(storage, SqliteStorage)

    def test_dir_with_json_returns_file(self, tmp_path: Path) -> None:
        (tmp_path / "execution-state.json").touch()
        storage = get_project_storage(tmp_path)
        assert isinstance(storage, FileStorage)

    def test_engine_created_via_auto_detect_works(self, tmp_path: Path) -> None:
        storage = get_project_storage(tmp_path)
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            bus=EventBus(),
            storage=storage,
        )
        action = engine.start(_plan("auto-detect-001"))
        assert action.action_type == ActionType.DISPATCH
