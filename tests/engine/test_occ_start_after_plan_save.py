"""Regression test for bd-74d7: false ConcurrentModificationError on first start.

Scenario
--------
``baton plan --save`` calls ``SqliteStorage.save_plan()``, which inserts an
``executions`` row for the task at ``version=1`` (status ``queued``).

``baton execute start`` then calls ``ExecutionEngine.start(plan)``.  The fresh
``ExecutionState`` built inside ``start()`` carries ``_loaded_version=0`` (the
Pydantic PrivateAttr default — no prior load ran).

Without the bd-74d7 fix, ``_save_execution()`` issues:
    UPDATE executions ... WHERE task_id=? AND version=0

That UPDATE matches no rows (the row is at version=1), so ``rowcount==0``.
The backend then finds the row IS present and raises
``ConcurrentModificationError("loaded version 0, but the row has advanced")``.
This is a false conflict — no concurrent writer exists.

With the fix (executor.py ~line 1853), ``start()`` reads the persisted version
via ``get_execution_version(task_id)`` and stamps it onto ``state._loaded_version``
before the first save.  The CAS UPDATE then uses the correct baseline (version=1)
and succeeds.

Test structure
--------------
1. ``TestOccStartAfterPlanSave.test_start_succeeds_after_save_plan``
   -- the primary regression: save_plan creates version=1 row; start() must
      return a DISPATCH action without raising.

2. ``TestOccStartAfterPlanSave.test_row_advances_to_version_2_after_start``
   -- post-start the SQLite row must be at version=2 with status "running".

3. ``TestOccStartAfterPlanSave.test_pre_fix_scenario_raises_without_version_stamp``
   -- documents the pre-fix failure by reproducing it directly at the storage
      layer: a fresh ExecutionState (_loaded_version=0) against an existing row
      (version=1) MUST raise ConcurrentModificationError.  This is the exact
      situation start() would have hit before the fix.  The test is labelled
      "pre-fix scenario" to make it clear it is pinning the underlying OCC
      contract, not the fixed code path.

4. ``TestOccStartAfterPlanSaveHighRisk.test_approval_pending_start_succeeds_after_save_plan``
   -- ensures the fix also fires for the approval_pending branch (HIGH risk
      plans) which constructs its ExecutionState the same way.
"""
from __future__ import annotations

import pytest

from agent_baton.core.engine.errors import ConcurrentModificationError
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.events.bus import EventBus
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import (
    ActionType,
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
)

from pathlib import Path


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _step(step_id: str = "1.1") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer",
        task_description="regression work",
    )


def _phase(phase_id: int = 0) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=f"phase-{phase_id}",
        steps=[_step(step_id=f"{phase_id}.1")],
    )


def _plan(task_id: str = "bd74d7-test", risk_level: str = "low") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="bd-74d7 regression plan",
        phases=[_phase()],
        risk_level=risk_level,
    )


def _storage_with_plan(db_path: Path, plan: MachinePlan) -> SqliteStorage:
    """Return a SqliteStorage that already has a ``save_plan()`` row (version=1)."""
    storage = SqliteStorage(db_path)
    storage.save_plan(plan)
    # Confirm the row is there at version=1 — the pre-condition for the bug.
    version = storage.get_execution_version(plan.task_id)
    assert version == 1, (
        f"Precondition failed: expected version=1 after save_plan, got {version!r}"
    )
    return storage


def _engine(tmp_path: Path, storage: SqliteStorage, plan: MachinePlan) -> ExecutionEngine:
    return ExecutionEngine(
        team_context_root=tmp_path,
        bus=EventBus(),
        storage=storage,
        task_id=plan.task_id,
    )


# ---------------------------------------------------------------------------
# Primary regression tests (LOW-risk plan → initial_status == "running")
# ---------------------------------------------------------------------------

class TestOccStartAfterPlanSave:
    """bd-74d7: start() must not raise on the first start after plan --save."""

    def test_start_succeeds_after_save_plan(self, tmp_path: Path) -> None:
        """The primary regression: save_plan() writes version=1; start() must not raise.

        Before the bd-74d7 fix, this raised::

            ConcurrentModificationError: ... loaded version 0, but the row has advanced

        With the fix, ``start()`` reads the persisted version via
        ``get_execution_version`` and stamps it onto the new state before the
        first CAS save.
        """
        plan = _plan(task_id="bd74d7-low-a")
        storage = _storage_with_plan(tmp_path / "baton.db", plan)
        engine = _engine(tmp_path, storage, plan)

        # Must not raise ConcurrentModificationError.
        action = engine.start(plan)

        assert action.action_type in (ActionType.DISPATCH, ActionType.DISPATCH.value), (
            f"Expected first action to be DISPATCH, got {action.action_type!r}"
        )

    def test_row_advances_to_version_2_after_start(self, tmp_path: Path) -> None:
        """After start(), the SQLite row must be at version=2, status='running'."""
        plan = _plan(task_id="bd74d7-low-b")
        storage = _storage_with_plan(tmp_path / "baton.db", plan)
        engine = _engine(tmp_path, storage, plan)

        engine.start(plan)

        # The CAS save in start() should have bumped version=1 → 2.
        version = storage.get_execution_version(plan.task_id)
        assert version == 2, (
            f"Expected version=2 after start(), got {version!r}"
        )

        # Status must be 'running' (not 'queued').
        loaded = storage.load_execution(plan.task_id)
        assert loaded is not None, "Execution state should be loadable after start()"
        assert loaded.status == "running", (
            f"Expected status='running' after start(), got {loaded.status!r}"
        )

    def test_pre_fix_scenario_raises_without_version_stamp(
        self, tmp_path: Path
    ) -> None:
        """Documents the pre-fix failure at the storage layer.

        A fresh ``ExecutionState`` with ``_loaded_version=0`` saving against an
        existing row at ``version=1`` MUST raise ``ConcurrentModificationError``.
        This is the exact storage-layer contract that ``start()`` violated before
        the fix.  Pinning it ensures the OCC detection cannot be silently removed.
        """
        plan = _plan(task_id="bd74d7-prefixlayer")
        storage = _storage_with_plan(tmp_path / "baton.db", plan)

        # Simulate the pre-fix state: fresh ExecutionState, _loaded_version=0.
        fresh_state = ExecutionState(
            task_id=plan.task_id,
            plan=plan,
            status="running",
        )
        assert fresh_state._loaded_version == 0, (
            "Precondition: a freshly constructed ExecutionState must have _loaded_version=0"
        )

        # Without the version stamp, this MUST raise ConcurrentModificationError.
        # This is the exact failure that bd-74d7 surfaced during ``baton execute start``.
        with pytest.raises(ConcurrentModificationError) as exc_info:
            storage.save_execution(fresh_state)

        assert exc_info.value.task_id == plan.task_id
        assert exc_info.value.observed_version == 0, (
            f"Expected observed_version=0, got {exc_info.value.observed_version!r}"
        )


# ---------------------------------------------------------------------------
# HIGH-risk branch (initial_status == "approval_pending")
# ---------------------------------------------------------------------------

class TestOccStartAfterPlanSaveHighRisk:
    """bd-74d7 fix must also cover the approval_pending branch (HIGH/CRITICAL risk)."""

    def test_approval_pending_start_succeeds_after_save_plan(
        self, tmp_path: Path
    ) -> None:
        """HIGH-risk plan sets initial_status='approval_pending'; fix must fire there too."""
        plan = _plan(task_id="bd74d7-high-a", risk_level="high")
        storage = _storage_with_plan(tmp_path / "baton.db", plan)
        engine = _engine(tmp_path, storage, plan)

        # For HIGH-risk, start() returns an APPROVAL action (not DISPATCH).
        # The important thing is that it does not raise ConcurrentModificationError.
        action = engine.start(plan)

        assert action.action_type in (
            ActionType.APPROVAL, ActionType.APPROVAL.value,
            ActionType.DISPATCH, ActionType.DISPATCH.value,
        ), f"Expected APPROVAL or DISPATCH for high-risk plan, got {action.action_type!r}"

        # Row must have advanced beyond version=1.
        version = storage.get_execution_version(plan.task_id)
        assert version is not None and version >= 2, (
            f"Expected version>=2 after high-risk start(), got {version!r}"
        )
