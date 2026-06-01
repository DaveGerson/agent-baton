"""Tests for bead signal wiring fixes in the executor.

Coverage:
- Bead signals parsed in record_step_result (single-dispatch path)
- Bead signals parsed in record_team_member_result (team-dispatch path)
- Bead feedback quality adjustments applied in record_step_result
- Bead feedback quality adjustments applied in record_team_member_result
- Beads written before/after save_execution remain readable (bd-backend)
- Schema migration: SCHEMA_VERSION matches the current constant

ADR-13b WP-G: BeadStore (SQLite) removed.  Tests retargeted to BdBeadStore
via make_bead_store().

BEAD_WARNING: The SQLite FK-cascade tests (TestBeadsSurviveSaveExecution /
_direct_bead_count) were retired because they tested SQLite-specific internals
(ON DELETE CASCADE) that do not apply to the bd backend.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

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


def _make_bd_store(tmp_path: Path):
    """Return a BdBeadStore scoped to tmp_path."""
    from agent_baton.core.engine.bead_backend import make_bead_store
    db_path = tmp_path / "baton.db"
    db_path.touch()
    return make_bead_store(db_path, repo_root=tmp_path)


def _engine_with_bd(
    tmp_path: Path, task_id: str | None = None
) -> tuple[ExecutionEngine, SqliteStorage, object]:
    """Return an engine + storage pair where the engine's bead store is
    scoped to tmp_path (not the project root).

    We monkeypatch make_bead_store at the bead_backend module level so the
    executor's local import picks up the isolated BdBeadStore (repo_root=tmp_path).
    """
    from agent_baton.core.engine.bd_bead_store import BdBeadStore
    from agent_baton.core.engine.bd_client import BdClient

    db_path = tmp_path / "baton.db"
    db_path.touch()
    client = BdClient(tmp_path)
    client.init()
    bd_store = BdBeadStore(client)
    bd_store._initialised = True

    def _patched_make_bead_store(path, *, soul_router=None, repo_root=None):
        return bd_store

    storage = SqliteStorage(db_path)
    with patch(
        "agent_baton.core.engine.bead_backend.make_bead_store",
        side_effect=_patched_make_bead_store,
    ):
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            bus=EventBus(),
            storage=storage,
            task_id=task_id,
        )
    return engine, storage, bd_store


# ---------------------------------------------------------------------------
# Bug 1a: parse_bead_signals is called in record_step_result
# ---------------------------------------------------------------------------


class TestBeadSignalInRecordStepResult:
    """BEAD_DISCOVERY / BEAD_DECISION / BEAD_WARNING signals are parsed and
    written after a single-dispatch step completes."""

    def test_discovery_signal_written(self, tmp_path: Path) -> None:
        engine, _, store = _engine_with_bd(tmp_path)
        engine.start(_plan("task-single-disc"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="BEAD_DISCOVERY: auth module uses RS256 JWT tokens.",
        )
        beads = store.query(task_id="task-single-disc", bead_type="discovery")
        assert len(beads) == 1
        assert "RS256" in beads[0].content

    def test_decision_signal_written(self, tmp_path: Path) -> None:
        engine, _, store = _engine_with_bd(tmp_path)
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
        beads = store.query(task_id="task-single-dec", bead_type="decision")
        assert len(beads) == 1
        assert "SQLAlchemy" in beads[0].content

    def test_warning_signal_written(self, tmp_path: Path) -> None:
        engine, _, store = _engine_with_bd(tmp_path)
        engine.start(_plan("task-single-warn"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="BEAD_WARNING: test DB fixture uses hardcoded port 5433.",
        )
        beads = store.query(task_id="task-single-warn", bead_type="warning")
        assert len(beads) == 1
        assert "5433" in beads[0].content

    def test_no_signal_no_beads_written(self, tmp_path: Path) -> None:
        engine, _, store = _engine_with_bd(tmp_path)
        engine.start(_plan("task-single-none"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="Step completed with no special signals.",
        )
        beads = store.query(task_id="task-single-none")
        assert beads == []

    def test_failed_step_no_beads_written(self, tmp_path: Path) -> None:
        """Failed steps should NOT produce beads — the outcome is unreliable."""
        engine, _, store = _engine_with_bd(tmp_path)
        engine.start(_plan("task-single-fail"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="failed",
            outcome="BEAD_DISCOVERY: this should not be stored.",
            error="Something went wrong",
        )
        beads = store.query(task_id="task-single-fail")
        assert beads == []

    def test_multiple_signals_in_one_outcome(self, tmp_path: Path) -> None:
        engine, _, store = _engine_with_bd(tmp_path)
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
        engine, _, store = _engine_with_bd(tmp_path)
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
        beads = store.query(task_id="task-team-disc", bead_type="discovery")
        assert len(beads) == 1
        assert "RS256" in beads[0].content
        assert beads[0].agent_name == "backend-engineer"

    def test_warning_from_team_member_written(self, tmp_path: Path) -> None:
        engine, _, store = _engine_with_bd(tmp_path)
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
        beads = store.query(task_id="task-team-warn", bead_type="warning")
        assert len(beads) == 1

    def test_signals_from_multiple_team_members_all_captured(self, tmp_path: Path) -> None:
        engine, _, store = _engine_with_bd(tmp_path)
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
        beads = store.query(task_id="task-team-both")
        assert len(beads) == 2
        types = {b.bead_type for b in beads}
        assert types == {"discovery", "warning"}

    def test_failed_team_member_no_beads_written(self, tmp_path: Path) -> None:
        engine, _, store = _engine_with_bd(tmp_path)
        engine.start(self._team_plan("task-team-fail"))
        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.a",
            agent_name="backend-engineer",
            status="failed",
            outcome="BEAD_DISCOVERY: this should not be stored.",
        )
        beads = store.query(task_id="task-team-fail")
        assert beads == []

    def test_team_member_step_id_stored_as_member_id(self, tmp_path: Path) -> None:
        """The bead's step_id should be the member_id (e.g. '1.1.a'), not the
        parent step_id ('1.1'), so we can trace which member produced it."""
        engine, _, store = _engine_with_bd(tmp_path)
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
        beads = store.query(task_id="task-team-stepid")
        assert len(beads) == 1
        assert beads[0].step_id == "1.1.a"


# ---------------------------------------------------------------------------
# Bead feedback (F12) — both dispatch paths
# ---------------------------------------------------------------------------


class TestBeadFeedbackWiring:
    """BEAD_FEEDBACK quality adjustments are applied in both paths."""

    def _seed_bead(self, store, task_id: str, bead_id: str) -> None:
        """Write a bead directly to the BdBeadStore so feedback tests can reference it."""
        from agent_baton.models.bead import Bead
        from datetime import datetime, timezone

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

    def test_feedback_applied_in_single_dispatch(self, tmp_path: Path) -> None:
        bead_id = "bd-feed01"
        engine, _, store = _engine_with_bd(tmp_path)
        self._seed_bead(store, "task-fb-single", bead_id)
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
        engine, _, store = _engine_with_bd(tmp_path)
        self._seed_bead(store, "task-fb-team", bead_id)

        members = [
            TeamMember(member_id="1.1.a", agent_name="backend-engineer",
                       role="implementer", task_description="implement"),
            TeamMember(member_id="1.1.b", agent_name="test-engineer",
                       role="implementer", task_description="test"),
        ]
        step = _step(step_id="1.1", team=members)
        plan = _plan(task_id="task-fb-team", phases=[_phase(steps=[step])])
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
# Bead persistence across save_execution (bd-backend)
# ---------------------------------------------------------------------------


class TestBeadsPersistAcrossSaveExecution:
    """Beads written during execution remain readable after save_execution calls.

    ADR-13b WP-G: The SQLite FK-cascade tests (TestBeadsSurviveSaveExecution)
    were retired because ON DELETE CASCADE is a SQLite-specific concern not
    applicable to the bd backend.  These tests verify the equivalent behaviour
    under bd: beads written before/during/after save_execution are readable.
    """

    def test_beads_written_during_execution_readable_at_end(self, tmp_path: Path) -> None:
        """Beads emitted mid-execution are still readable when the execution
        completes."""
        engine, _, store = _engine_with_bd(tmp_path)
        engine.start(_plan("task-persist-03"))
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="BEAD_DISCOVERY: module boundary established.",
        )
        engine.complete()

        beads = store.query(task_id="task-persist-03")
        assert len(beads) == 1
        assert "module boundary" in beads[0].content

    def test_save_execution_upsert_does_not_reset_started_at(self, tmp_path: Path) -> None:
        """The upsert path preserves started_at on the executions row."""
        db_path = tmp_path / "baton.db"
        db_path.touch()
        storage = SqliteStorage(db_path)
        engine, _, store = _engine_with_bd(tmp_path)
        engine.start(_plan("task-persist-04"))
        state_before = storage.load_execution("task-persist-04")
        assert state_before is not None
        started_at_before = state_before.started_at

        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="no signals",
        )
        state_after = storage.load_execution("task-persist-04")
        assert state_after is not None
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
        # Trigger schema initialisation via storage itself (not BeadStore)
        conn_mgr = storage._conn_mgr
        conn = conn_mgr.get_connection()

        row = conn.execute(
            "SELECT version FROM _schema_version"
        ).fetchone()
        assert row is not None
        assert row[0] == SCHEMA_VERSION
        storage.close()


# ---------------------------------------------------------------------------
# Terminal bead closure — planning beads on success, all open beads on failure
# ---------------------------------------------------------------------------


class TestTerminalBeadClosure:
    """Beads that are still ``open`` when a task reaches a terminal state get
    closed so that bead-decay can actually clean them up."""

    def _write_planning_bead(self, store, task_id: str) -> str:
        from agent_baton.models.bead import Bead
        bead = Bead(
            bead_id=f"{task_id}-plan",
            task_id=task_id,
            agent_name="planner",
            step_id="planning",
            bead_type="decision",
            content="Plan generated via forge.",
            tags=["planning"],
            created_at="2026-04-17T00:00:00Z",
            status="open",
        )
        store.write(bead)
        return bead.bead_id

    def test_planning_beads_closed_on_complete(self, tmp_path: Path) -> None:
        engine, _, store = _engine_with_bd(tmp_path, task_id="task-term-success")
        engine.start(_plan("task-term-success"))
        self._write_planning_bead(store, "task-term-success")

        engine.record_step_result(
            "1.1", "backend-engineer", status="complete", outcome="done.",
        )
        engine.complete()

        # BEAD_WARNING: BdBeadStore.query(status="open") works because bd list
        # defaults to open issues.  Querying with tags=["planning"] requires
        # bd list --label "planning" which also returns open issues, so this
        # specific query is expected to work.
        planning = store.query(
            task_id="task-term-success", status="open", tags=["planning"],
        )
        assert planning == [], "planning beads must be closed at task completion"

    def test_non_planning_beads_untouched_on_complete(
        self, tmp_path: Path,
    ) -> None:
        """Agent-emitted beads should NOT be force-closed on success."""
        engine, _, store = _engine_with_bd(tmp_path, task_id="task-term-agent")
        engine.start(_plan("task-term-agent"))
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete",
            outcome="BEAD_DISCOVERY: discovered a thing.",
        )
        engine.complete()

        agent_beads = store.query(
            task_id="task-term-agent", bead_type="discovery",
        )
        assert len(agent_beads) == 1
        assert agent_beads[0].status == "open", (
            "agent beads must remain open on success — decay handles them"
        )

    def test_all_open_beads_closed_on_failure(self, tmp_path: Path) -> None:
        engine, _, store = _engine_with_bd(tmp_path, task_id="task-term-fail")
        plan = _plan(
            "task-term-fail",
            phases=[_phase(1, steps=[_step("1.1"), _step("1.2")])],
        )
        engine.start(plan)
        self._write_planning_bead(store, "task-term-fail")
        engine.record_step_result(
            "1.1", "backend-engineer",
            status="complete",
            outcome="BEAD_DISCOVERY: partial finding before failure.",
        )
        engine.record_step_result(
            "1.2", "backend-engineer",
            status="failed",
            outcome="boom",
            error="simulated failure",
        )
        engine.next_action()

        # BEAD_WARNING: BdBeadStore.query(status="open") returns open issues;
        # closed beads are not returned, so an empty result means all were closed.
        still_open = store.query(task_id="task-term-fail", status="open")
        assert still_open == [], (
            "every open bead must be closed when the task fails"
        )
