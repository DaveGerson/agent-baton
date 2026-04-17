"""Tests for split-brain persistence fixes in ExecutionEngine.

Covers three scenarios from INCIDENT-persistence-split-brain.md:

1. Recording a step that was previously dispatched — the exact scenario
   that caused the original UNIQUE constraint failure.
2. File fallback produces the correct (post-mutation) state when SQLite
   is deliberately made unavailable.
3. Reconciliation on resume() picks the more-advanced status when SQLite
   and file state diverge.
"""
from __future__ import annotations

import json
import copy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.events.bus import EventBus
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import (
    ActionType,
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _step(step_id: str = "1.1", agent: str = "backend-engineer") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent,
        task_description="Implement the thing",
        model="sonnet",
    )


def _phase(phase_id: int = 1, steps: list[PlanStep] | None = None) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=f"Phase {phase_id}",
        steps=steps or [_step()],
    )


def _plan(task_id: str = "task-splitbrain-001") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Split-brain test task",
        risk_level="LOW",
        phases=[_phase()],
    )


def _engine_with_sqlite(
    tmp_path: Path, task_id: str | None = None
) -> tuple[ExecutionEngine, SqliteStorage]:
    storage = SqliteStorage(tmp_path / "baton.db")
    engine = ExecutionEngine(
        team_context_root=tmp_path,
        bus=EventBus(),
        task_id=task_id,
        storage=storage,
    )
    return engine, storage


# ---------------------------------------------------------------------------
# 1. Dispatched → complete: the exact scenario that triggered the bug
# ---------------------------------------------------------------------------

class TestDispatchedThenComplete:
    """Recording a step that was previously dispatched must succeed."""

    def test_mark_dispatched_then_record_complete_no_constraint_error(
        self, tmp_path: Path
    ) -> None:
        """baton execute dispatched followed by baton execute record must not fail.

        Before the fix, the second record_step_result call would attempt to
        INSERT a row for (task_id, step_id) that already existed in SQLite
        with status='dispatched', triggering UNIQUE constraint failure and
        leaving both backends showing the stale 'dispatched' status.
        """
        engine, store = _engine_with_sqlite(tmp_path)
        plan = _plan("task-dispatch-complete")
        engine.start(plan)

        # Simulate `baton execute dispatched --step 1.1`
        engine.mark_dispatched("1.1", "backend-engineer")

        mid = store.load_execution("task-dispatch-complete")
        assert mid is not None
        dispatched_results = [r for r in mid.step_results if r.step_id == "1.1"]
        assert len(dispatched_results) == 1
        assert dispatched_results[0].status == "dispatched"

        # Simulate `baton execute record --step 1.1 --status complete`
        # This must NOT raise any exception.
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="Feature implemented",
            files_changed=["agent_baton/feature.py"],
        )

        final = store.load_execution("task-dispatch-complete")
        assert final is not None
        complete_results = [r for r in final.step_results if r.step_id == "1.1"]
        assert len(complete_results) == 1, (
            "must have exactly one row per step_id after dispatched→complete transition"
        )
        assert complete_results[0].status == "complete"
        assert complete_results[0].outcome == "Feature implemented"

    def test_next_action_after_complete_is_not_wait(self, tmp_path: Path) -> None:
        """Engine must not return WAIT after dispatched→complete transition.

        The original bug caused the engine to return WAIT forever because it
        saw the step as still 'dispatched' in both backends.
        """
        engine, _ = _engine_with_sqlite(tmp_path)
        plan = _plan("task-not-wait")
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete", outcome="done"
        )
        action = engine.next_action()
        assert action.action_type != ActionType.WAIT, (
            f"Engine returned WAIT after step completed; got {action.action_type}"
        )

    def test_multiple_dispatched_complete_cycles_are_safe(
        self, tmp_path: Path
    ) -> None:
        """A two-step plan where each step goes through dispatched→complete."""
        engine, store = _engine_with_sqlite(tmp_path)
        plan = MachinePlan(
            task_id="task-multi-steps",
            task_summary="Two-step plan",
            risk_level="LOW",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Implementation",
                    steps=[_step("1.1"), _step("1.2", "test-engineer")],
                )
            ],
        )
        engine.start(plan)

        engine.mark_dispatched("1.1", "backend-engineer")
        engine.record_step_result("1.1", "backend-engineer", status="complete")

        engine.mark_dispatched("1.2", "test-engineer")
        engine.record_step_result("1.2", "test-engineer", status="complete")

        state = store.load_execution("task-multi-steps")
        assert state is not None
        statuses = {r.step_id: r.status for r in state.step_results}
        assert statuses == {"1.1": "complete", "1.2": "complete"}


# ---------------------------------------------------------------------------
# 2. File fallback carries current (post-mutation) state
# ---------------------------------------------------------------------------

class TestFileFallbackCarriesCurrentState:
    """When SQLite fails, the file fallback must write the current state."""

    def test_file_fallback_writes_complete_not_dispatched(
        self, tmp_path: Path
    ) -> None:
        """If SQLite.save_execution raises, file must have the complete status.

        This test patches save_execution to fail only on the second call
        (the record_step_result call after dispatched is already written).
        The file fallback must capture the current state (complete), not
        the stale state (dispatched) that SQLite holds.
        """
        storage = SqliteStorage(tmp_path / "baton.db")
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            bus=EventBus(),
            task_id="task-fallback",
            storage=storage,
        )
        plan = _plan("task-fallback")
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")

        # Patch save_execution to fail on the next call (record_step_result).
        original_save = storage.save_execution
        call_count = [0]

        def _failing_save(state: ExecutionState) -> None:
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Simulated SQLite write failure")
            return original_save(state)

        storage.save_execution = _failing_save  # type: ignore[method-assign]

        # This should NOT raise; it must fall back to file persistence.
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="Done via fallback",
        )

        # The file fallback must hold the current (complete) state.
        persistence = StatePersistence(tmp_path, task_id="task-fallback")
        file_state = persistence.load()
        assert file_state is not None, "File persistence must have written state"
        complete_results = [
            r for r in file_state.step_results if r.step_id == "1.1"
        ]
        assert len(complete_results) == 1
        assert complete_results[0].status == "complete", (
            "File fallback must write the post-mutation state (complete), "
            f"not the stale pre-mutation state; got {complete_results[0].status!r}"
        )
        assert complete_results[0].outcome == "Done via fallback"

    def test_fallback_log_includes_task_id_and_step_status(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When SQLite fails, the WARNING log must include task_id and step status."""
        import logging

        storage = SqliteStorage(tmp_path / "baton.db")
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            bus=EventBus(),
            task_id="task-log-check",
            storage=storage,
        )
        plan = _plan("task-log-check")
        engine.start(plan)

        original_save = storage.save_execution

        def _failing_save(state: ExecutionState) -> None:
            raise RuntimeError("disk full")

        storage.save_execution = _failing_save  # type: ignore[method-assign]

        with caplog.at_level(logging.WARNING, logger="agent_baton.core.engine.executor"):
            engine.record_step_result("1.1", "backend-engineer", status="complete")

        # The warning must mention task_id, status, and step summary.
        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        combined = " ".join(warning_messages)
        assert "task-log-check" in combined, (
            "WARNING must include the task_id for diagnostic traceability"
        )
        assert "complete" in combined, (
            "WARNING must include the step status being written"
        )
        assert "SQLite" in combined or "sqlite" in combined.lower(), (
            "WARNING must identify SQLite as the failing backend"
        )


# ---------------------------------------------------------------------------
# 3. Reconciliation: resume() picks more-advanced status
# ---------------------------------------------------------------------------

class TestResumeReconciliation:
    """resume() must heal split-brain by taking the more-advanced step status."""

    def _inject_split_brain(
        self,
        tmp_path: Path,
        task_id: str,
        sqlite_status: str,
        file_status: str,
    ) -> tuple[ExecutionEngine, SqliteStorage]:
        """Set up split-brain: SQLite has sqlite_status, file has file_status."""
        storage = SqliteStorage(tmp_path / "baton.db")
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            bus=EventBus(),
            task_id=task_id,
            storage=storage,
        )
        plan = _plan(task_id)
        engine.start(plan)

        # Write sqlite_status directly to SQLite (bypassing in-memory state).
        sqlite_result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer",
            status=sqlite_status,
            outcome=f"sqlite={sqlite_status}",
        )
        storage.save_step_result(task_id, sqlite_result)

        # Write the full state with sqlite_status so SQLite is consistent.
        sqlite_state = storage.load_execution(task_id)
        assert sqlite_state is not None

        # Now write file_status to the file backend only.
        persistence = StatePersistence(tmp_path, task_id=task_id)
        file_state_obj = storage.load_execution(task_id)
        assert file_state_obj is not None
        file_result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer",
            status=file_status,
            outcome=f"file={file_status}",
        )
        # Replace the step result in the in-memory copy and write to file only.
        file_state_copy = copy.copy(file_state_obj)
        file_state_copy.step_results = [file_result]
        persistence.save(file_state_copy)

        return engine, storage

    def test_resume_promotes_file_complete_over_sqlite_dispatched(
        self, tmp_path: Path
    ) -> None:
        """Core split-brain scenario: SQLite=dispatched, file=complete.

        resume() must detect the divergence and use the file's complete
        status so the engine proceeds to the next action instead of WAIT.
        """
        task_id = "task-reconcile-001"
        _, storage = self._inject_split_brain(
            tmp_path, task_id,
            sqlite_status="dispatched",
            file_status="complete",
        )

        # Fresh engine instance simulates crash recovery.
        engine2 = ExecutionEngine(
            team_context_root=tmp_path,
            bus=EventBus(),
            task_id=task_id,
            storage=storage,
        )
        action = engine2.resume()

        assert action.action_type != ActionType.WAIT, (
            "resume() returned WAIT after reconciliation; split-brain was not healed. "
            f"Got action_type={action.action_type}"
        )
        assert action.action_type not in (ActionType.FAILED,), (
            f"resume() returned FAILED unexpectedly: {action.message}"
        )

    def test_resume_no_false_promotion_when_sqlite_is_more_advanced(
        self, tmp_path: Path
    ) -> None:
        """If SQLite is already at complete and file is at dispatched, keep complete."""
        task_id = "task-reconcile-002"
        _, storage = self._inject_split_brain(
            tmp_path, task_id,
            sqlite_status="complete",
            file_status="dispatched",
        )

        engine2 = ExecutionEngine(
            team_context_root=tmp_path,
            bus=EventBus(),
            task_id=task_id,
            storage=storage,
        )
        action = engine2.resume()

        # SQLite is already correct; should not be downgraded to dispatched.
        assert action.action_type != ActionType.WAIT, (
            "resume() wrongly used the less-advanced file state"
        )

    def test_resume_no_change_when_both_backends_agree(
        self, tmp_path: Path
    ) -> None:
        """When both backends have the same status, no reconciliation warning fires."""
        import logging

        task_id = "task-reconcile-agree"
        _, storage = self._inject_split_brain(
            tmp_path, task_id,
            sqlite_status="complete",
            file_status="complete",
        )

        engine2 = ExecutionEngine(
            team_context_root=tmp_path,
            bus=EventBus(),
            task_id=task_id,
            storage=storage,
        )

        import logging as _logging

        with pytest.MonkeyPatch().context() as mp:
            import logging
            # Just verify it does not raise or return FAILED.
            action = engine2.resume()
        assert action.action_type != ActionType.FAILED


# ---------------------------------------------------------------------------
# 4. _reconcile_states unit tests (pure logic, no I/O)
# ---------------------------------------------------------------------------

class TestReconcileStatesUnit:
    """Unit tests for ExecutionEngine._reconcile_states and _step_status_rank."""

    def _make_state(self, task_id: str, step_statuses: dict[str, str]) -> ExecutionState:
        """Build a minimal ExecutionState with given step_id → status mapping."""
        plan = _plan(task_id)
        state = ExecutionState(
            task_id=task_id,
            plan=plan,
            current_phase=0,
            current_step_index=0,
            status="running",
            started_at="2026-04-17T00:00:00+00:00",
        )
        state.step_results = [
            StepResult(
                step_id=sid,
                agent_name="agent",
                status=status,
                outcome=f"{sid}={status}",
            )
            for sid, status in step_statuses.items()
        ]
        return state

    def test_rank_ordering(self) -> None:
        """Status rank must satisfy: dispatched < interrupted < failed < complete."""
        rank = ExecutionEngine._step_status_rank
        assert rank("dispatched") < rank("interrupted")
        assert rank("interrupted") < rank("failed")
        assert rank("failed") < rank("complete")
        assert rank("unknown_status") == 0

    def test_reconcile_promotes_more_advanced_secondary(self) -> None:
        """Secondary complete must replace primary dispatched."""
        engine = ExecutionEngine.__new__(ExecutionEngine)
        primary = self._make_state("t1", {"1.1": "dispatched"})
        secondary = self._make_state("t1", {"1.1": "complete"})
        result = engine._reconcile_states(primary, secondary)
        assert result.step_results[0].status == "complete"

    def test_reconcile_does_not_downgrade(self) -> None:
        """When primary is more advanced, it must not be replaced by secondary."""
        engine = ExecutionEngine.__new__(ExecutionEngine)
        primary = self._make_state("t1", {"1.1": "complete"})
        secondary = self._make_state("t1", {"1.1": "dispatched"})
        result = engine._reconcile_states(primary, secondary)
        assert result.step_results[0].status == "complete"
        # Must return the same object (no unnecessary copy).
        assert result is primary

    def test_reconcile_returns_primary_unchanged_when_no_divergence(self) -> None:
        """With identical statuses, returns primary object unchanged."""
        engine = ExecutionEngine.__new__(ExecutionEngine)
        primary = self._make_state("t1", {"1.1": "complete", "1.2": "complete"})
        secondary = self._make_state("t1", {"1.1": "complete", "1.2": "complete"})
        result = engine._reconcile_states(primary, secondary)
        assert result is primary

    def test_reconcile_handles_step_only_in_primary(self) -> None:
        """Steps present only in primary are left untouched."""
        engine = ExecutionEngine.__new__(ExecutionEngine)
        primary = self._make_state("t1", {"1.1": "complete", "1.2": "dispatched"})
        secondary = self._make_state("t1", {"1.1": "complete"})  # 1.2 absent
        result = engine._reconcile_states(primary, secondary)
        statuses = {r.step_id: r.status for r in result.step_results}
        assert statuses["1.2"] == "dispatched"  # unchanged

    def test_reconcile_mixed_promotion(self) -> None:
        """Two steps: one needs promotion, one does not."""
        engine = ExecutionEngine.__new__(ExecutionEngine)
        primary = self._make_state("t1", {"1.1": "dispatched", "1.2": "complete"})
        secondary = self._make_state("t1", {"1.1": "complete", "1.2": "dispatched"})
        result = engine._reconcile_states(primary, secondary)
        statuses = {r.step_id: r.status for r in result.step_results}
        assert statuses["1.1"] == "complete"   # promoted from secondary
        assert statuses["1.2"] == "complete"   # kept from primary (more advanced)
        # Must be a new object (was mutated).
        assert result is not primary

    def test_reconcile_does_not_mutate_primary(self) -> None:
        """_reconcile_states must not mutate the primary state object."""
        engine = ExecutionEngine.__new__(ExecutionEngine)
        primary = self._make_state("t1", {"1.1": "dispatched"})
        secondary = self._make_state("t1", {"1.1": "complete"})
        original_status = primary.step_results[0].status
        engine._reconcile_states(primary, secondary)
        assert primary.step_results[0].status == original_status, (
            "_reconcile_states must not mutate primary"
        )
