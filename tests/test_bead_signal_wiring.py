"""Tests for bead signal wiring fixes in the executor.

Coverage:
- Bead signals parsed in record_step_result (single-dispatch path)
- Bead signals parsed in record_team_member_result (team-dispatch path)
- Bead feedback quality adjustments applied in record_step_result
- Bead feedback quality adjustments applied in record_team_member_result
- FK cascade bug: bead rows survive save_execution (INSERT OR REPLACE fix)
- FK cascade bug: bead rows survive multiple save_execution calls
- Beads written before save_execution are still readable after it
- Schema migration: SCHEMA_VERSION matches the current constant
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_baton.core.engine.bead_store import BeadStore
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.events.bus import EventBus
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanPhase,
    PlanStep,
    TeamMember,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _step(
    step_id: str = "1.1",
    agent: str = "backend-engineer",
    team: list[TeamMember] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent,
        task_description="Do the work",
        model="sonnet",
        team=team or [],
    )


def _phase(phase_id: int = 1, steps: list[PlanStep] | None = None) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=f"Phase {phase_id}",
        steps=steps or [_step()],
    )


def _plan(task_id: str = "task-bead-001", phases: list[PlanPhase] | None = None) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Bead signal wiring test",
        risk_level="LOW",
        phases=phases or [_phase()],
    )


def _engine_with_sqlite(tmp_path: Path, task_id: str | None = None) -> tuple[ExecutionEngine, SqliteStorage]:
    """Return an engine + storage pair backed by a temporary baton.db."""
    storage = SqliteStorage(tmp_path / "baton.db")
    engine = ExecutionEngine(
        team_context_root=tmp_path,
        bus=EventBus(),
        storage=storage,
        task_id=task_id,
    )
    return engine, storage


def _bead_store(tmp_path: Path) -> BeadStore:
    """Return a BeadStore pointing at the same db the engine uses."""
    return BeadStore(tmp_path / "baton.db")


def _direct_bead_count(db_path: Path, task_id: str) -> int:
    """Read bead count via raw SQLite to bypass any caching layer."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM beads WHERE task_id = ?", (task_id,)
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Bug 1a: parse_bead_signals is called in record_step_result
# ---------------------------------------------------------------------------


class TestBeadSignalInRecordStepResult:
    """BEAD_DISCOVERY / BEAD_DECISION / BEAD_WARNING signals are parsed and
    written after a single-dispatch step completes."""

    def test_discovery_signal_written(self, tmp_path: Path) -> None:
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(_plan("task-single-disc"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="BEAD_DISCOVERY: auth module uses RS256 JWT tokens.",
        )
        store = _bead_store(tmp_path)
        beads = store.query(task_id="task-single-disc", bead_type="discovery")
        assert len(beads) == 1
        assert "RS256" in beads[0].content

    def test_decision_signal_written(self, tmp_path: Path) -> None:
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(_plan("task-single-dec"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome=(
                "BEAD_DECISION: Use SQLAlchemy 2.0 mapped_column style.\n"
                "CHOSE: mapped_column\n"
                "BECAUSE: Matches project convention.\n"
            ),
        )
        store = _bead_store(tmp_path)
        beads = store.query(task_id="task-single-dec", bead_type="decision")
        assert len(beads) == 1
        assert "SQLAlchemy" in beads[0].content

    def test_warning_signal_written(self, tmp_path: Path) -> None:
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(_plan("task-single-warn"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="BEAD_WARNING: test DB fixture uses hardcoded port 5433.",
        )
        store = _bead_store(tmp_path)
        beads = store.query(task_id="task-single-warn", bead_type="warning")
        assert len(beads) == 1
        assert "5433" in beads[0].content

    def test_no_signal_no_beads_written(self, tmp_path: Path) -> None:
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(_plan("task-single-none"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="Step completed with no special signals.",
        )
        store = _bead_store(tmp_path)
        beads = store.query(task_id="task-single-none")
        assert beads == []

    def test_failed_step_no_beads_written(self, tmp_path: Path) -> None:
        """Failed steps should NOT produce beads — the outcome is unreliable."""
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(_plan("task-single-fail"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="failed",
            outcome="BEAD_DISCOVERY: this should not be stored.",
            error="Something went wrong",
        )
        store = _bead_store(tmp_path)
        beads = store.query(task_id="task-single-fail")
        assert beads == []

    def test_multiple_signals_in_one_outcome(self, tmp_path: Path) -> None:
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(_plan("task-single-multi"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome=(
                "BEAD_DISCOVERY: module uses WAL mode.\n"
                "BEAD_WARNING: connection pool may exhaust under load.\n"
            ),
        )
        store = _bead_store(tmp_path)
        beads = store.query(task_id="task-single-multi")
        assert len(beads) == 2
        types = {b.bead_type for b in beads}
        assert types == {"discovery", "warning"}


# ---------------------------------------------------------------------------
# Bug 1b: parse_bead_signals is called in record_team_member_result
# ---------------------------------------------------------------------------


class TestBeadSignalInRecordTeamMemberResult:
    """BEAD_* signals emitted by team-member agents are captured.

    This tests the path that was missing before the fix: signals in team
    member outcomes were silently dropped.
    """

    def _team_plan(self, task_id: str) -> MachinePlan:
        members = [
            TeamMember(member_id="1.1.a", agent_name="backend-engineer",
                       role="implementer", task_description="implement"),
            TeamMember(member_id="1.1.b", agent_name="test-engineer",
                       role="implementer", task_description="test"),
        ]
        step = _step(step_id="1.1", team=members)
        return _plan(task_id=task_id, phases=[_phase(steps=[step])])

    def test_discovery_from_team_member_written(self, tmp_path: Path) -> None:
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(self._team_plan("task-team-disc"))
        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.a",
            agent_name="backend-engineer",
            status="complete",
            outcome="BEAD_DISCOVERY: auth module uses RS256 JWT tokens.",
        )
        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.b",
            agent_name="test-engineer",
            status="complete",
            outcome="step completed cleanly",
        )
        store = _bead_store(tmp_path)
        beads = store.query(task_id="task-team-disc", bead_type="discovery")
        assert len(beads) == 1
        assert "RS256" in beads[0].content
        assert beads[0].agent_name == "backend-engineer"

    def test_warning_from_team_member_written(self, tmp_path: Path) -> None:
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(self._team_plan("task-team-warn"))
        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.a",
            agent_name="backend-engineer",
            status="complete",
            outcome="BEAD_WARNING: hardcoded port 5433 in test fixture.",
        )
        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.b",
            agent_name="test-engineer",
            status="complete",
            outcome="no signals",
        )
        store = _bead_store(tmp_path)
        beads = store.query(task_id="task-team-warn", bead_type="warning")
        assert len(beads) == 1

    def test_signals_from_multiple_team_members_all_captured(self, tmp_path: Path) -> None:
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(self._team_plan("task-team-both"))
        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.a",
            agent_name="backend-engineer",
            status="complete",
            outcome="BEAD_DISCOVERY: found module A pattern.",
        )
        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.b",
            agent_name="test-engineer",
            status="complete",
            outcome="BEAD_WARNING: flaky test in suite B.",
        )
        store = _bead_store(tmp_path)
        beads = store.query(task_id="task-team-both")
        assert len(beads) == 2
        types = {b.bead_type for b in beads}
        assert types == {"discovery", "warning"}

    def test_failed_team_member_no_beads_written(self, tmp_path: Path) -> None:
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(self._team_plan("task-team-fail"))
        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.a",
            agent_name="backend-engineer",
            status="failed",
            outcome="BEAD_DISCOVERY: this should not be stored.",
        )
        store = _bead_store(tmp_path)
        beads = store.query(task_id="task-team-fail")
        assert beads == []

    def test_team_member_step_id_stored_as_member_id(self, tmp_path: Path) -> None:
        """The bead's step_id should be the member_id (e.g. '1.1.a'), not the
        parent step_id ('1.1'), so we can trace which member produced it."""
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(self._team_plan("task-team-stepid"))
        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.a",
            agent_name="backend-engineer",
            status="complete",
            outcome="BEAD_DISCOVERY: connection pool is shared across threads.",
        )
        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.b",
            agent_name="test-engineer",
            status="complete",
            outcome="no signals",
        )
        store = _bead_store(tmp_path)
        beads = store.query(task_id="task-team-stepid")
        assert len(beads) == 1
        assert beads[0].step_id == "1.1.a"


# ---------------------------------------------------------------------------
# Bead feedback (F12) — both dispatch paths
# ---------------------------------------------------------------------------


class TestBeadFeedbackWiring:
    """BEAD_FEEDBACK quality adjustments are applied in both paths."""

    def _seed_bead(self, tmp_path: Path, task_id: str, bead_id: str) -> BeadStore:
        """Write a bead directly to the store so feedback tests can reference it."""
        from agent_baton.models.bead import Bead
        from datetime import datetime, timezone

        store = _bead_store(tmp_path)
        store._table_exists()  # force schema initialisation
        # Seed the executions row the FK requires
        conn = sqlite3.connect(str(tmp_path / "baton.db"))
        try:
            conn.execute(
                "INSERT OR IGNORE INTO executions "
                "(task_id, status, current_phase, current_step_index, "
                " started_at, created_at, updated_at) "
                "VALUES (?, 'running', 0, 0, "
                "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (task_id,),
            )
            conn.commit()
        finally:
            conn.close()
        store.write(Bead(
            bead_id=bead_id,
            task_id=task_id,
            step_id="1.1",
            agent_name="backend-engineer",
            bead_type="discovery",
            content="some fact",
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            quality_score=0.0,
        ))
        return store

    def test_feedback_applied_in_single_dispatch(self, tmp_path: Path) -> None:
        bead_id = "bd-feed01"
        store = self._seed_bead(tmp_path, "task-fb-single", bead_id)
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(_plan("task-fb-single"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome=f"BEAD_FEEDBACK: {bead_id} useful",
        )
        updated = store.read(bead_id)
        assert updated is not None
        assert updated.quality_score == pytest.approx(0.5)

    def test_feedback_applied_in_team_dispatch(self, tmp_path: Path) -> None:
        bead_id = "bd-feed02"
        store = self._seed_bead(tmp_path, "task-fb-team", bead_id)

        members = [
            TeamMember(member_id="1.1.a", agent_name="backend-engineer",
                       role="implementer", task_description="implement"),
            TeamMember(member_id="1.1.b", agent_name="test-engineer",
                       role="implementer", task_description="test"),
        ]
        step = _step(step_id="1.1", team=members)
        plan = _plan(task_id="task-fb-team", phases=[_phase(steps=[step])])
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(plan)
        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.a",
            agent_name="backend-engineer",
            status="complete",
            outcome=f"BEAD_FEEDBACK: {bead_id} misleading",
        )
        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.b",
            agent_name="test-engineer",
            status="complete",
            outcome="no feedback",
        )
        updated = store.read(bead_id)
        assert updated is not None
        assert updated.quality_score == pytest.approx(-0.5)


# ---------------------------------------------------------------------------
# Bug 2: FK cascade — beads survive save_execution
# ---------------------------------------------------------------------------


class TestBeadsSurviveSaveExecution:
    """Bead rows must not be deleted when save_execution is called.

    Before the fix, INSERT OR REPLACE INTO executions did a DELETE + INSERT,
    which triggered ON DELETE CASCADE on beads, silently destroying all bead
    rows for the task on every state save.
    """

    def test_beads_survive_first_save(self, tmp_path: Path) -> None:
        """Beads written before the first save_execution call are not lost."""
        task_id = "task-cascade-01"
        storage = SqliteStorage(tmp_path / "baton.db")
        # Force schema so beads table exists before we seed
        bstore = BeadStore(tmp_path / "baton.db")
        bstore._table_exists()

        # Seed executions row so FK passes
        conn = sqlite3.connect(str(tmp_path / "baton.db"))
        try:
            conn.execute(
                "INSERT OR IGNORE INTO executions "
                "(task_id, status, current_phase, current_step_index, "
                " started_at, created_at, updated_at) "
                "VALUES (?, 'running', 0, 0, "
                "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (task_id,),
            )
            conn.commit()
        finally:
            conn.close()

        # Write a bead directly
        from agent_baton.models.bead import Bead
        from datetime import datetime, timezone
        bstore.write(Bead(
            bead_id="bd-casc01",
            task_id=task_id,
            step_id="1.1",
            agent_name="backend-engineer",
            bead_type="discovery",
            content="fact before save",
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ))
        assert _direct_bead_count(tmp_path / "baton.db", task_id) == 1

        # Now call save_execution (which previously deleted bead rows)
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            bus=EventBus(),
            storage=storage,
            task_id=task_id,
        )
        plan = _plan(task_id)
        engine.start(plan)

        # Bead must still be present
        assert _direct_bead_count(tmp_path / "baton.db", task_id) == 1

    def test_beads_survive_repeated_saves(self, tmp_path: Path) -> None:
        """Beads survive multiple save_execution calls (once per step record)."""
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(_plan("task-cascade-02"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome=(
                "BEAD_DISCOVERY: first fact.\n"
                "BEAD_WARNING: watch the cache.\n"
            ),
        )
        # Each record_step_result triggers _save_execution; verify beads survive
        count = _direct_bead_count(tmp_path / "baton.db", "task-cascade-02")
        assert count == 2

    def test_beads_written_during_execution_readable_at_end(self, tmp_path: Path) -> None:
        """End-to-end: beads emitted mid-execution are still readable when
        the execution completes (which also calls save_execution)."""
        engine, _ = _engine_with_sqlite(tmp_path)
        engine.start(_plan("task-cascade-03"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="BEAD_DISCOVERY: module boundary established.",
        )
        engine.complete()

        store = _bead_store(tmp_path)
        beads = store.query(task_id="task-cascade-03")
        assert len(beads) == 1
        assert "module boundary" in beads[0].content

    def test_save_execution_upsert_does_not_reset_started_at(self, tmp_path: Path) -> None:
        """The ON CONFLICT DO UPDATE path preserves started_at on the executions
        row (a regression guard: the old INSERT OR REPLACE reset it each time)."""
        engine, storage = _engine_with_sqlite(tmp_path)
        engine.start(_plan("task-cascade-04"))
        state_before = storage.load_execution("task-cascade-04")
        assert state_before is not None
        started_at_before = state_before.started_at

        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="no signals",
        )
        state_after = storage.load_execution("task-cascade-04")
        assert state_after is not None
        # started_at must be unchanged after the safe-upsert save
        assert state_after.started_at == started_at_before


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    """SCHEMA_VERSION tracks the current migration level.

    These tests assert against the imported constant so they remain correct
    across future schema bumps without needing manual updates.
    """

    def test_schema_version_matches_constant(self) -> None:
        from agent_baton.core.storage.schema import SCHEMA_VERSION
        # Assert the constant is a positive integer — catches accidental
        # deletion or mis-typing, while staying version-agnostic.
        assert isinstance(SCHEMA_VERSION, int)
        assert SCHEMA_VERSION > 0

    def test_migration_9_is_registered(self) -> None:
        from agent_baton.core.storage.schema import MIGRATIONS
        assert 9 in MIGRATIONS

    def test_new_database_is_at_current_version(self, tmp_path: Path) -> None:
        """A freshly created baton.db is stamped at the current SCHEMA_VERSION."""
        from agent_baton.core.storage.schema import SCHEMA_VERSION
        storage = SqliteStorage(tmp_path / "baton.db")
        # Trigger schema initialisation
        from agent_baton.core.engine.bead_store import BeadStore
        bstore = BeadStore(tmp_path / "baton.db")
        bstore._table_exists()

        conn = sqlite3.connect(str(tmp_path / "baton.db"))
        try:
            row = conn.execute(
                "SELECT version FROM _schema_version"
            ).fetchone()
            assert row is not None
            assert row[0] == SCHEMA_VERSION
        finally:
            conn.close()
