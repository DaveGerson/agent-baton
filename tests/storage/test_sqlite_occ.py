"""SQLite OCC (slice 14) — concurrent-modification detection.

Two SqliteStorage instances saving the same task_id produces a
``ConcurrentModificationError`` on the second one (after the first has
bumped the version column).  Tests rely on a shared on-disk DB to
simulate two processes; per-thread connection isolation in
``ConnectionManager`` keeps the version reads consistent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.errors import ConcurrentModificationError
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
)


def _plan() -> MachinePlan:
    return MachinePlan(
        task_id="t-occ",
        task_summary="occ test",
        phases=[
            PlanPhase(
                phase_id=0,
                name="p0",
                steps=[PlanStep(step_id="0.1", agent_name="x", task_description="t")],
            ),
        ],
    )


def _state(plan: MachinePlan, status: str = "running") -> ExecutionState:
    return ExecutionState(
        task_id=plan.task_id,
        plan=plan,
        status=status,
    )


class TestOccVersionRoundtrip:
    def test_save_then_load_carries_version(self, tmp_path: Path) -> None:
        store = SqliteStorage(tmp_path / "db.sqlite")
        plan = _plan()
        state = _state(plan)

        store.save_execution(state)
        loaded = store.load_execution(plan.task_id)
        assert loaded is not None
        # First save produced version=1; loaded state carries it.
        assert loaded._loaded_version == 1

    def test_repeated_saves_bump_version(self, tmp_path: Path) -> None:
        store = SqliteStorage(tmp_path / "db.sqlite")
        plan = _plan()
        state = _state(plan)
        store.save_execution(state)
        # In-memory state's _loaded_version reflects the bump after save.
        assert state._loaded_version == 1
        store.save_execution(state)
        assert state._loaded_version == 2
        reloaded = store.load_execution(plan.task_id)
        assert reloaded is not None
        assert reloaded._loaded_version == 2


class TestOccConflictDetection:
    def test_save_with_stale_version_raises(self, tmp_path: Path) -> None:
        """Two writers race; the second to save sees the conflict."""
        store_a = SqliteStorage(tmp_path / "db.sqlite")
        store_b = SqliteStorage(tmp_path / "db.sqlite")
        plan = _plan()
        state_a = _state(plan)

        # Initial save by writer A.
        store_a.save_execution(state_a)

        # Writer B loads the row at version 1.
        state_b = store_b.load_execution(plan.task_id)
        assert state_b is not None
        assert state_b._loaded_version == 1

        # Writer A saves again, bumping version to 2.
        store_a.save_execution(state_a)
        assert state_a._loaded_version == 2

        # Writer B's CAS UPDATE now misses (version=1 condition fails).
        with pytest.raises(ConcurrentModificationError) as exc:
            store_b.save_execution(state_b)
        assert exc.value.task_id == plan.task_id
        assert exc.value.observed_version == 1

    def test_first_save_creates_row_at_version_1(self, tmp_path: Path) -> None:
        """Fresh state with _loaded_version=0 inserts cleanly, no CAS conflict."""
        store = SqliteStorage(tmp_path / "db.sqlite")
        plan = _plan()
        state = _state(plan)
        # _loaded_version starts at 0 — no row exists yet.
        assert state._loaded_version == 0
        store.save_execution(state)  # must not raise
        assert state._loaded_version == 1


class TestOccVersionInToDict:
    def test_loaded_version_is_private_not_serialised(self, tmp_path: Path) -> None:
        """``_loaded_version`` is a PrivateAttr; to_dict / model_dump skip it."""
        store = SqliteStorage(tmp_path / "db.sqlite")
        plan = _plan()
        state = _state(plan)
        store.save_execution(state)
        loaded = store.load_execution(plan.task_id)
        assert loaded is not None
        d = loaded.to_dict()
        assert "_loaded_version" not in d
        assert "version" not in d


class TestOccFreshStateOnClaimedTaskRaises:
    """Regression: a fresh ExecutionState built for an already-claimed
    task_id must raise ``ConcurrentModificationError`` rather than
    silently overwriting the persisted row.

    The original slice-14 follow-up added a "split-brain recovery"
    branch that fired whenever ``_loaded_version == 0`` AND a row
    already existed.  That branch swallowed the conflict — a second
    ``engine.start()`` call (or any other fresh-state-with-row
    scenario) silently overwrote the first writer's progress.

    The OCC contract per sqlite-parity-proposal §3.2 is "raise typed
    ``ConcurrentModificationError``".  This test pins that contract
    against fresh-state collisions so the recovery branch cannot
    grow back unnoticed.
    """

    def test_fresh_state_with_existing_row_raises(self, tmp_path: Path) -> None:
        store = SqliteStorage(tmp_path / "db.sqlite")
        plan = _plan()

        # Writer A saves first; row now exists at version 1.
        state_a = _state(plan)
        state_a.run_cumulative_spend_usd = 100.0
        store.save_execution(state_a)
        assert state_a._loaded_version == 1

        # Writer B constructs a fresh ExecutionState for the same
        # task_id (e.g. a second ``engine.start()`` call after a
        # crash).  ``_loaded_version`` is 0 because no load ran.
        state_b = _state(plan)
        state_b.run_cumulative_spend_usd = 99999.0
        assert state_b._loaded_version == 0

        # Save MUST raise — silently overwriting A's progress would
        # be a data-loss bug.
        with pytest.raises(ConcurrentModificationError) as exc:
            store.save_execution(state_b)
        assert exc.value.task_id == plan.task_id
        assert exc.value.observed_version == 0

        # And the row must still carry A's value.
        reloaded = store.load_execution(plan.task_id)
        assert reloaded is not None
        assert reloaded.run_cumulative_spend_usd == 100.0


class TestGetExecutionVersion:
    """``get_execution_version`` exposes the row's OCC version for the
    file-fallback enrichment path in ``ExecutionEngine._load_execution``.
    """

    def test_returns_none_for_missing_row(self, tmp_path: Path) -> None:
        store = SqliteStorage(tmp_path / "db.sqlite")
        assert store.get_execution_version("does-not-exist") is None

    def test_returns_persisted_version(self, tmp_path: Path) -> None:
        store = SqliteStorage(tmp_path / "db.sqlite")
        plan = _plan()
        state = _state(plan)
        store.save_execution(state)
        store.save_execution(state)  # bump to version 2
        assert store.get_execution_version(plan.task_id) == 2
