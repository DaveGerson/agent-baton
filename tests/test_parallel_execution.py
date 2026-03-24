"""Integration-style tests for multi-execution scenarios using StatePersistence.

Tests that exercise the ``ExecutionEngine(task_id=...)`` constructor are
written to use ``StatePersistence.save()`` directly when the engine does not
yet support the ``task_id`` parameter.  That way the StatePersistence layer
(which is already merged) can be tested in isolation, and the engine-level
tests will naturally start passing once the engine change lands.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Factories (minimal valid objects)
# ---------------------------------------------------------------------------

def _step(step_id: str = "1.1", agent: str = "backend") -> PlanStep:
    return PlanStep(step_id=step_id, agent_name=agent, task_description="do work")


def _phase(phase_id: int = 0, steps: list[PlanStep] | None = None) -> PlanPhase:
    return PlanPhase(phase_id=phase_id, name="Impl", steps=steps or [_step()])


def _plan(task_id: str = "task-001", summary: str = "Build a thing") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary=summary,
        phases=[_phase()],
    )


def _save_state(context_root: Path, task_id: str, summary: str = "A task") -> None:
    """Save a minimal ExecutionState via StatePersistence (no engine needed)."""
    plan = _plan(task_id=task_id, summary=summary)
    state = ExecutionState(
        task_id=task_id,
        plan=plan,
        current_phase=0,
        current_step_index=0,
        status="running",
    )
    sp = StatePersistence(context_root, task_id=task_id)
    sp.save(state)


def _save_legacy_state(context_root: Path, task_id: str) -> None:
    """Save a minimal ExecutionState at the legacy flat path (no task_id)."""
    plan = _plan(task_id=task_id)
    state = ExecutionState(
        task_id=task_id,
        plan=plan,
        current_phase=0,
        current_step_index=0,
        status="running",
    )
    sp = StatePersistence(context_root)  # no task_id → flat path
    sp.save(state)


# ---------------------------------------------------------------------------
# Two executions write to different state files
# ---------------------------------------------------------------------------

class TestIsolatedStatePaths:
    def test_two_task_ids_produce_separate_state_files(self, tmp_path: Path) -> None:
        sp_a = StatePersistence(tmp_path, task_id="task-alpha")
        sp_b = StatePersistence(tmp_path, task_id="task-beta")
        assert sp_a.path != sp_b.path

    def test_namespaced_path_is_under_executions_dir(self, tmp_path: Path) -> None:
        sp = StatePersistence(tmp_path, task_id="my-task")
        assert sp.path.parent == tmp_path / "executions" / "my-task"

    def test_legacy_path_is_flat(self, tmp_path: Path) -> None:
        sp = StatePersistence(tmp_path)
        assert sp.path == tmp_path / "execution-state.json"

    def test_two_task_ids_write_independent_state_files(
        self, tmp_path: Path
    ) -> None:
        _save_state(tmp_path, "alpha", summary="Task Alpha")
        _save_state(tmp_path, "beta", summary="Task Beta")

        state_a_path = tmp_path / "executions" / "alpha" / "execution-state.json"
        state_b_path = tmp_path / "executions" / "beta" / "execution-state.json"

        assert state_a_path.exists()
        assert state_b_path.exists()

        data_a = json.loads(state_a_path.read_text())
        data_b = json.loads(state_b_path.read_text())

        assert data_a["task_id"] == "alpha"
        assert data_b["task_id"] == "beta"

    def test_legacy_path_state_file_is_flat(self, tmp_path: Path) -> None:
        _save_legacy_state(tmp_path, "legacy-task")
        assert (tmp_path / "execution-state.json").exists()


# ---------------------------------------------------------------------------
# list_executions
# ---------------------------------------------------------------------------

class TestListExecutions:
    def test_empty_dir_returns_empty_list(self, tmp_path: Path) -> None:
        assert StatePersistence.list_executions(tmp_path) == []

    def test_missing_executions_dir_returns_empty_list(self, tmp_path: Path) -> None:
        non_existent = tmp_path / "no-such-dir"
        assert StatePersistence.list_executions(non_existent) == []

    def test_returns_task_id_after_saving_state(self, tmp_path: Path) -> None:
        _save_state(tmp_path, "task-001")
        task_ids = StatePersistence.list_executions(tmp_path)
        assert "task-001" in task_ids

    def test_returns_both_task_ids_for_two_executions(self, tmp_path: Path) -> None:
        for tid in ("alpha", "beta"):
            _save_state(tmp_path, tid)
        task_ids = StatePersistence.list_executions(tmp_path)
        assert set(task_ids) == {"alpha", "beta"}

    def test_returns_sorted_list(self, tmp_path: Path) -> None:
        for tid in ("charlie", "alpha", "bravo"):
            _save_state(tmp_path, tid)
        task_ids = StatePersistence.list_executions(tmp_path)
        assert task_ids == sorted(task_ids)

    def test_directory_without_state_file_is_excluded(self, tmp_path: Path) -> None:
        """An executions/ sub-dir that has no execution-state.json is ignored."""
        empty_exec_dir = tmp_path / "executions" / "orphan"
        empty_exec_dir.mkdir(parents=True)
        assert StatePersistence.list_executions(tmp_path) == []


# ---------------------------------------------------------------------------
# load_all
# ---------------------------------------------------------------------------

class TestLoadAll:
    def test_empty_context_root_returns_empty_list(self, tmp_path: Path) -> None:
        states = StatePersistence.load_all(tmp_path)
        assert states == []

    def test_loads_both_namespaced_executions(self, tmp_path: Path) -> None:
        for tid in ("task-a", "task-b"):
            _save_state(tmp_path, tid)
        states = StatePersistence.load_all(tmp_path)
        task_ids = {s.task_id for s in states}
        assert "task-a" in task_ids
        assert "task-b" in task_ids

    def test_includes_legacy_flat_file_when_task_id_not_namespaced(
        self, tmp_path: Path
    ) -> None:
        _save_legacy_state(tmp_path, "legacy")
        states = StatePersistence.load_all(tmp_path)
        assert any(s.task_id == "legacy" for s in states)

    def test_does_not_duplicate_legacy_when_same_task_namespaced(
        self, tmp_path: Path
    ) -> None:
        """If a namespaced copy of the same task_id exists, the legacy entry
        should not be included again."""
        _save_legacy_state(tmp_path, "dup-task")
        _save_state(tmp_path, "dup-task")

        states = StatePersistence.load_all(tmp_path)
        dup_states = [s for s in states if s.task_id == "dup-task"]
        assert len(dup_states) == 1

    def test_returns_correct_number_of_states(self, tmp_path: Path) -> None:
        for i in range(3):
            tid = f"task-{i}"
            _save_state(tmp_path, tid)
        states = StatePersistence.load_all(tmp_path)
        assert len(states) == 3


# ---------------------------------------------------------------------------
# set_active / get_active_task_id
# ---------------------------------------------------------------------------

class TestActiveTaskId:
    def test_no_active_file_returns_none(self, tmp_path: Path) -> None:
        assert StatePersistence.get_active_task_id(tmp_path) is None

    def test_set_active_round_trips_task_id(self, tmp_path: Path) -> None:
        sp = StatePersistence(tmp_path, task_id="my-task")
        sp.set_active()
        assert StatePersistence.get_active_task_id(tmp_path) == "my-task"

    def test_set_active_overwrites_previous_active(self, tmp_path: Path) -> None:
        StatePersistence(tmp_path, task_id="first").set_active()
        StatePersistence(tmp_path, task_id="second").set_active()
        assert StatePersistence.get_active_task_id(tmp_path) == "second"

    def test_set_active_on_legacy_instance_is_noop(self, tmp_path: Path) -> None:
        """A StatePersistence with no task_id should not write the active file."""
        sp = StatePersistence(tmp_path)
        sp.set_active()
        assert not (tmp_path / "active-task-id.txt").exists()

    def test_empty_active_file_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "active-task-id.txt").write_text("   ", encoding="utf-8")
        assert StatePersistence.get_active_task_id(tmp_path) is None

    def test_active_file_strips_whitespace(self, tmp_path: Path) -> None:
        (tmp_path / "active-task-id.txt").write_text("  my-task\n", encoding="utf-8")
        assert StatePersistence.get_active_task_id(tmp_path) == "my-task"

    def test_active_task_switch_tracks_most_recent(self, tmp_path: Path) -> None:
        for tid in ("alpha", "beta", "gamma"):
            sp = StatePersistence(tmp_path, task_id=tid)
            sp.set_active()
        assert StatePersistence.get_active_task_id(tmp_path) == "gamma"


# ---------------------------------------------------------------------------
# StatePersistence.task_id property
# ---------------------------------------------------------------------------

class TestTaskIdProperty:
    def test_task_id_returns_value_set_at_construction(self, tmp_path: Path) -> None:
        sp = StatePersistence(tmp_path, task_id="proj-123")
        assert sp.task_id == "proj-123"

    def test_task_id_is_none_for_legacy_instance(self, tmp_path: Path) -> None:
        sp = StatePersistence(tmp_path)
        assert sp.task_id is None


# ---------------------------------------------------------------------------
# Save / load round-trip across independent StatePersistence instances
# ---------------------------------------------------------------------------

class TestSaveLoadIsolation:
    def test_reading_from_different_instance_with_same_task_id(
        self, tmp_path: Path
    ) -> None:
        _save_state(tmp_path, "shared-task")

        # Read via a fresh StatePersistence — simulates crash recovery.
        sp = StatePersistence(tmp_path, task_id="shared-task")
        state = sp.load()
        assert state is not None
        assert state.task_id == "shared-task"

    def test_loading_wrong_task_id_returns_none(self, tmp_path: Path) -> None:
        _save_state(tmp_path, "task-real")

        sp_other = StatePersistence(tmp_path, task_id="task-fake")
        assert sp_other.load() is None
