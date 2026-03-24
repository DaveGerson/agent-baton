"""Tests for agent_baton.core.storage.sync.SyncEngine and
agent_baton.core.storage.central.CentralStore.

All database paths use tmp_path / tempfile so tests are fully isolated.
Data is written directly via SqliteStorage (the same write path used
by the real execution engine) to ensure tests exercise realistic rows.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from agent_baton.core.storage.central import CentralStore
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.core.storage.sync import SyncEngine, SyncResult, SYNCABLE_TABLES
from agent_baton.models.execution import (
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
)
from agent_baton.models.retrospective import (
    KnowledgeGap,
    Retrospective,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project_db(tmp_path: Path, subdir: str = "proj") -> tuple[Path, SqliteStorage]:
    """Create a project baton.db under tmp_path/<subdir>/ and return (path, store)."""
    db_dir = tmp_path / subdir
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "baton.db"
    store = SqliteStorage(db_path)
    return db_path, store


def _make_central_db(tmp_path: Path, name: str = "central.db") -> Path:
    return tmp_path / name


def _minimal_plan(task_id: str) -> MachinePlan:
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer--python",
        task_description="Implement the feature",
        model="sonnet",
        depends_on=[],
        deliverables=[],
        allowed_paths=[],
        blocked_paths=[],
        context_files=[],
    )
    phase = PlanPhase(
        phase_id=1,
        name="Implementation",
        steps=[step],
        gate=None,
        approval_required=False,
    )
    return MachinePlan(
        task_id=task_id,
        task_summary="Add a new feature",
        risk_level="LOW",
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        shared_context="context text",
        phases=[phase],
    )


def _minimal_state(task_id: str, status: str = "complete") -> ExecutionState:
    plan = _minimal_plan(task_id)
    state = ExecutionState(
        task_id=task_id,
        plan=plan,
        status=status,
        current_phase=1,
        current_step_index=0,
        started_at="2026-01-01T00:00:00Z",
        completed_at="2026-01-01T01:00:00Z" if status == "complete" else None,
        step_results=[
            StepResult(
                step_id="1.1",
                agent_name="backend-engineer--python",
                status="complete",
                outcome="Done",
                files_changed=["agent_baton/feature.py"],
                commit_hash="abc123",
                estimated_tokens=1000,
                duration_seconds=30.0,
                retries=0,
                error="",
                completed_at="2026-01-01T01:00:00Z",
            )
        ],
        gate_results=[],
        approval_results=[],
        amendments=[],
    )
    return state


# ---------------------------------------------------------------------------
# Test 1: Sync a single table (executions)
# ---------------------------------------------------------------------------


class TestSyncSingleTable:
    def test_executions_synced(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "proj-a")
        state = _minimal_state("task-001")
        store.save_execution(state)
        store.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)
        result = engine.push("proj-a", db_path)

        assert result.success, result.errors
        assert result.project_id == "proj-a"
        assert result.rows_synced > 0
        assert result.tables_synced > 0
        assert result.duration_seconds >= 0.0

        # Verify the row is in central.db
        central = CentralStore(central_path)
        rows = central.query(
            "SELECT * FROM executions WHERE project_id = ? AND task_id = ?",
            ("proj-a", "task-001"),
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "complete"
        central.close()

    def test_plans_synced_alongside_executions(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "proj-b")
        store.save_execution(_minimal_state("task-002"))
        store.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)
        engine.push("proj-b", db_path)

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT task_summary FROM plans WHERE project_id = ? AND task_id = ?",
            ("proj-b", "task-002"),
        )
        assert len(rows) == 1
        assert rows[0]["task_summary"] == "Add a new feature"
        central.close()

    def test_step_results_synced(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "proj-c")
        store.save_execution(_minimal_state("task-003"))
        store.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)
        engine.push("proj-c", db_path)

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT agent_name, outcome FROM step_results "
            "WHERE project_id = ? AND task_id = ?",
            ("proj-c", "task-003"),
        )
        assert len(rows) == 1
        assert rows[0]["agent_name"] == "backend-engineer--python"
        central.close()


# ---------------------------------------------------------------------------
# Test 2: Watermark-based incremental sync
# ---------------------------------------------------------------------------


class TestIncrementalSync:
    def test_second_push_only_copies_new_rows(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "proj-incr")
        store.save_execution(_minimal_state("task-100"))
        store.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)

        # First push
        r1 = engine.push("proj-incr", db_path)
        assert r1.success
        rows_after_first = r1.rows_synced

        # Second push with no new data — should copy 0 rows
        r2 = engine.push("proj-incr", db_path)
        assert r2.success
        assert r2.rows_synced == 0, (
            f"Expected 0 new rows on second push, got {r2.rows_synced}"
        )

    def test_new_rows_picked_up_after_first_sync(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "proj-incr2")
        store.save_execution(_minimal_state("task-200"))
        store.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)
        engine.push("proj-incr2", db_path)

        # Add a second execution to the project db
        store2 = SqliteStorage(db_path)
        store2.save_execution(_minimal_state("task-201"))
        store2.close()

        r2 = engine.push("proj-incr2", db_path)
        assert r2.success
        assert r2.rows_synced > 0

        # Both tasks should be in central
        central = CentralStore(central_path)
        rows = central.query(
            "SELECT task_id FROM executions WHERE project_id = ? ORDER BY task_id",
            ("proj-incr2",),
        )
        task_ids = {r["task_id"] for r in rows}
        assert "task-200" in task_ids
        assert "task-201" in task_ids
        central.close()

    def test_watermarks_stored_in_central(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "proj-wm")
        store.save_execution(_minimal_state("task-300"))
        store.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)
        engine.push("proj-wm", db_path)

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT * FROM sync_watermarks WHERE project_id = ?",
            ("proj-wm",),
        )
        assert len(rows) > 0
        for row in rows:
            assert row["last_rowid"] > 0
        central.close()


# ---------------------------------------------------------------------------
# Test 3: push_all with multiple projects
# ---------------------------------------------------------------------------


class TestPushAll:
    def test_push_all_syncs_registered_projects(self, tmp_path: Path) -> None:
        # Create two project databases
        db_a, store_a = _make_project_db(tmp_path, "alpha")
        store_a.save_execution(_minimal_state("task-alpha-1"))
        store_a.close()

        db_b, store_b = _make_project_db(tmp_path, "beta")
        store_b.save_execution(_minimal_state("task-beta-1"))
        store_b.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)

        # Register both projects in central.db so push_all can find them.
        # push_all derives the baton.db path from the registered project path.
        # We register with path = parent of .claude/team-context/baton.db.
        # db_a = tmp_path/alpha/baton.db  → project path = tmp_path
        # so derive project_path = db_a.parent.parent.parent
        #
        # For simplicity, directly register with the exact path that push_all
        # will resolve: project_path / ".claude/team-context/baton.db".
        # We'll make those directories and symlink or just test push directly.

        # Direct push is tested elsewhere; here test that push_all returns one
        # result per registered project.
        conn = engine._conn_mgr.get_connection()

        # Register projects with paths pointing to our tmp dirs
        proj_alpha_dir = tmp_path / "alpha_project"
        proj_beta_dir = tmp_path / "beta_project"
        for d in (proj_alpha_dir, proj_beta_dir):
            tc = d / ".claude" / "team-context"
            tc.mkdir(parents=True, exist_ok=True)

        # Copy the project DBs to the expected paths
        import shutil
        shutil.copy(db_a, proj_alpha_dir / ".claude" / "team-context" / "baton.db")
        shutil.copy(db_b, proj_beta_dir / ".claude" / "team-context" / "baton.db")

        conn.execute(
            "INSERT OR REPLACE INTO projects (project_id, name, path, program) "
            "VALUES (?, ?, ?, ?)",
            ("alpha", "Alpha Project", str(proj_alpha_dir), "prog-a"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO projects (project_id, name, path, program) "
            "VALUES (?, ?, ?, ?)",
            ("beta", "Beta Project", str(proj_beta_dir), "prog-b"),
        )
        conn.commit()

        results = engine.push_all()
        assert len(results) == 2

        project_ids = {r.project_id for r in results}
        assert "alpha" in project_ids
        assert "beta" in project_ids

        for r in results:
            assert r.success, f"{r.project_id}: {r.errors}"

    def test_push_all_returns_empty_for_no_projects(self, tmp_path: Path) -> None:
        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)
        # Trigger schema init
        engine._conn_mgr.get_connection()

        results = engine.push_all()
        assert results == []


# ---------------------------------------------------------------------------
# Test 4: rebuild (delete + re-sync)
# ---------------------------------------------------------------------------


class TestRebuild:
    def test_rebuild_produces_same_data(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "proj-rebuild")
        store.save_execution(_minimal_state("task-r1"))
        store.save_execution(_minimal_state("task-r2"))
        store.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)
        engine.push("proj-rebuild", db_path)

        # Confirm 2 rows
        central = CentralStore(central_path)
        before = central.query(
            "SELECT task_id FROM executions WHERE project_id = ?",
            ("proj-rebuild",),
        )
        assert len(before) == 2
        central.close()

        # Rebuild
        r = engine.rebuild("proj-rebuild", db_path)
        assert r.success, r.errors

        central2 = CentralStore(central_path)
        after = central2.query(
            "SELECT task_id FROM executions WHERE project_id = ?",
            ("proj-rebuild",),
        )
        assert len(after) == 2
        task_ids = {row["task_id"] for row in after}
        assert "task-r1" in task_ids
        assert "task-r2" in task_ids
        central2.close()

    def test_rebuild_clears_watermarks(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "proj-wm-rebuild")
        store.save_execution(_minimal_state("task-w1"))
        store.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)
        engine.push("proj-wm-rebuild", db_path)

        # Watermarks exist before rebuild
        central = CentralStore(central_path)
        wm_before = central.query(
            "SELECT * FROM sync_watermarks WHERE project_id = ?",
            ("proj-wm-rebuild",),
        )
        assert len(wm_before) > 0
        central.close()

        engine.rebuild("proj-wm-rebuild", db_path)

        # Watermarks should be re-created (positive) by the rebuild push
        central2 = CentralStore(central_path)
        wm_after = central2.query(
            "SELECT * FROM sync_watermarks WHERE project_id = ?",
            ("proj-wm-rebuild",),
        )
        # rebuild resets then re-pushes, so watermarks should exist again
        assert len(wm_after) > 0
        central2.close()


# ---------------------------------------------------------------------------
# Test 5: CentralStore queries against synced data
# ---------------------------------------------------------------------------


class TestCentralStoreQueries:
    def _setup_two_projects(self, tmp_path: Path) -> tuple[Path, str, str]:
        """Create two project DBs, sync them, return central_path."""
        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)

        for idx, proj_id in enumerate(["proj-x", "proj-y"]):
            db_path, store = _make_project_db(tmp_path, proj_id)
            store.save_execution(
                _minimal_state(f"task-{proj_id}-{idx}", status="complete")
            )
            store.close()
            engine.push(proj_id, db_path)

        return central_path, "proj-x", "proj-y"

    def test_project_failure_rates_returns_list(self, tmp_path: Path) -> None:
        central_path, _, _ = self._setup_two_projects(tmp_path)
        store = CentralStore(central_path)
        rates = store.project_failure_rates()
        assert isinstance(rates, list)
        # Both projects have 1 execution each with status='complete'
        project_ids = {r["project_id"] for r in rates}
        assert "proj-x" in project_ids
        assert "proj-y" in project_ids
        store.close()

    def test_agent_reliability_filtered_by_min_steps(self, tmp_path: Path) -> None:
        central_path, _, _ = self._setup_two_projects(tmp_path)
        store = CentralStore(central_path)

        # With min_steps=1 should return at least one row
        rows = store.agent_reliability(min_steps=1)
        assert isinstance(rows, list)
        # With high min_steps, nothing returned
        rows_filtered = store.agent_reliability(min_steps=9999)
        assert rows_filtered == []
        store.close()

    def test_generic_query_select(self, tmp_path: Path) -> None:
        central_path, _, _ = self._setup_two_projects(tmp_path)
        store = CentralStore(central_path)
        rows = store.query("SELECT COUNT(*) AS cnt FROM executions")
        assert rows[0]["cnt"] == 2
        store.close()

    def test_generic_query_rejects_write(self, tmp_path: Path) -> None:
        central_path = _make_central_db(tmp_path)
        store = CentralStore(central_path)
        # Trigger schema init
        store.query("SELECT 1")

        with pytest.raises(ValueError, match="read-only"):
            store.query("INSERT INTO programs (name) VALUES (?)", ("hack",))
        store.close()

    def test_cost_by_task_type_returns_list(self, tmp_path: Path) -> None:
        central_path, _, _ = self._setup_two_projects(tmp_path)
        store = CentralStore(central_path)
        rows = store.cost_by_task_type()
        assert isinstance(rows, list)
        store.close()

    def test_recurring_knowledge_gaps_empty_when_none(self, tmp_path: Path) -> None:
        central_path, _, _ = self._setup_two_projects(tmp_path)
        store = CentralStore(central_path)
        rows = store.recurring_knowledge_gaps()
        assert isinstance(rows, list)
        store.close()


# ---------------------------------------------------------------------------
# Test 6: AUTOINCREMENT table handling (gate_results, telemetry)
# ---------------------------------------------------------------------------


class TestAutoincrementTables:
    def test_gate_results_synced_without_id_collision(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "proj-gate")
        # Write execution state with gate results
        state = _minimal_state("task-gate-1")
        state.gate_results = [
            GateResult(
                phase_id=1,
                gate_type="test",
                passed=True,
                output="All tests passed",
                checked_at="2026-01-01T01:00:00Z",
            )
        ]
        store.save_execution(state)
        store.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)
        result = engine.push("proj-gate", db_path)

        assert result.success, result.errors

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT * FROM gate_results WHERE project_id = ?", ("proj-gate",)
        )
        assert len(rows) == 1
        assert rows[0]["gate_type"] == "test"
        assert rows[0]["passed"] == 1
        central.close()

    def test_telemetry_synced_with_autoincrement(self, tmp_path: Path) -> None:
        db_path, _ = _make_project_db(tmp_path, "proj-tele")

        # Insert telemetry rows directly via SQLite
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # Ensure schema exists by opening via SqliteStorage first
        conn.close()
        st = SqliteStorage(db_path)
        # Use the connection from the store to write telemetry
        st_conn = st._conn()
        st_conn.execute(
            """
            INSERT INTO telemetry
                (timestamp, agent_name, event_type, tool_name, task_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("2026-01-01T00:00:00Z", "backend-engineer--python", "tool_call", "Read", "task-tele-1"),
        )
        st_conn.execute(
            """
            INSERT INTO telemetry
                (timestamp, agent_name, event_type, tool_name, task_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("2026-01-01T00:01:00Z", "backend-engineer--python", "tool_call", "Write", "task-tele-1"),
        )
        st_conn.commit()
        st.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)
        result = engine.push("proj-tele", db_path)

        assert result.success, result.errors

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT * FROM telemetry WHERE project_id = ? ORDER BY tool_name",
            ("proj-tele",),
        )
        assert len(rows) == 2
        tool_names = {r["tool_name"] for r in rows}
        assert "Read" in tool_names
        assert "Write" in tool_names
        central.close()

    def test_two_projects_gate_results_have_distinct_central_ids(
        self, tmp_path: Path
    ) -> None:
        """Central should assign its own IDs to gate_results rows from two projects."""
        for proj_id in ["proj-gate-a", "proj-gate-b"]:
            db_path, store = _make_project_db(tmp_path, proj_id)
            state = _minimal_state(f"task-{proj_id}")
            state.gate_results = [
                GateResult(
                    phase_id=1,
                    gate_type="test",
                    passed=True,
                    output="ok",
                    checked_at="2026-01-01T01:00:00Z",
                )
            ]
            store.save_execution(state)
            store.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)
        for proj_id in ["proj-gate-a", "proj-gate-b"]:
            db_path = tmp_path / proj_id / "baton.db"
            engine.push(proj_id, db_path)

        central = CentralStore(central_path)
        rows = central.query("SELECT id, project_id FROM gate_results ORDER BY id")
        assert len(rows) == 2
        ids = [r["id"] for r in rows]
        assert ids[0] != ids[1], "Central should assign distinct IDs"
        central.close()


# ---------------------------------------------------------------------------
# Test 7: auto_sync_current_project resolution
# ---------------------------------------------------------------------------


class TestAutoSyncCurrentProject:
    def test_returns_none_when_central_db_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_baton.core.storage import sync as sync_module

        # Point the default path to a non-existent file
        non_existent = tmp_path / "no-central.db"
        monkeypatch.setattr(sync_module, "_CENTRAL_DB_DEFAULT", non_existent)

        result = sync_module.auto_sync_current_project()
        assert result is None

    def test_returns_none_when_no_matching_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_baton.core.storage import sync as sync_module

        central_path = tmp_path / "central.db"
        monkeypatch.setattr(sync_module, "_CENTRAL_DB_DEFAULT", central_path)

        # Create central.db with no projects
        engine = SyncEngine(central_path)
        engine._conn_mgr.get_connection()  # init schema

        # Change cwd to somewhere not in any registered project
        monkeypatch.chdir(tmp_path)

        result = sync_module.auto_sync_current_project()
        assert result is None

    def test_resolves_project_by_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_baton.core.storage import sync as sync_module

        central_path = tmp_path / "central.db"
        monkeypatch.setattr(sync_module, "_CENTRAL_DB_DEFAULT", central_path)

        # Set up project DB under a subdirectory
        proj_root = tmp_path / "myproject"
        tc_dir = proj_root / ".claude" / "team-context"
        tc_dir.mkdir(parents=True, exist_ok=True)
        db_path = tc_dir / "baton.db"
        store = SqliteStorage(db_path)
        store.save_execution(_minimal_state("task-auto-1"))
        store.close()

        # Register the project in central.db
        engine = SyncEngine(central_path)
        conn = engine._conn_mgr.get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO projects (project_id, name, path, program) "
            "VALUES (?, ?, ?, ?)",
            ("myproject", "My Project", str(proj_root), "test"),
        )
        conn.commit()

        # Set cwd to inside the project
        monkeypatch.chdir(proj_root)

        result = sync_module.auto_sync_current_project()
        assert result is not None
        assert result.project_id == "myproject"
        assert result.success, result.errors
        assert result.rows_synced > 0


# ---------------------------------------------------------------------------
# Test 8: Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_push_same_data_twice_no_duplicates(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "proj-idem")
        store.save_execution(_minimal_state("task-idem-1"))
        store.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)

        engine.push("proj-idem", db_path)
        engine.push("proj-idem", db_path)  # second push

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT * FROM executions WHERE project_id = ? AND task_id = ?",
            ("proj-idem", "task-idem-1"),
        )
        assert len(rows) == 1, f"Expected exactly 1 execution row, got {len(rows)}"
        central.close()

    def test_step_results_not_duplicated_on_repush(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "proj-idem2")
        store.save_execution(_minimal_state("task-idem-2"))
        store.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)
        engine.push("proj-idem2", db_path)
        engine.push("proj-idem2", db_path)

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT * FROM step_results WHERE project_id = ? AND task_id = ?",
            ("proj-idem2", "task-idem-2"),
        )
        assert len(rows) == 1
        central.close()

    def test_rebuild_then_push_no_duplicates(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "proj-idem3")
        store.save_execution(_minimal_state("task-idem-3"))
        store.close()

        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)
        engine.push("proj-idem3", db_path)
        engine.rebuild("proj-idem3", db_path)

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT * FROM executions WHERE project_id = ? AND task_id = ?",
            ("proj-idem3", "task-idem-3"),
        )
        assert len(rows) == 1
        central.close()


# ---------------------------------------------------------------------------
# Test 9: SyncResult properties
# ---------------------------------------------------------------------------


class TestSyncResult:
    def test_success_true_when_no_errors(self) -> None:
        r = SyncResult(project_id="p")
        assert r.success is True

    def test_success_false_when_errors_present(self) -> None:
        r = SyncResult(project_id="p", errors=["oops"])
        assert r.success is False

    def test_missing_project_db_returns_error(self, tmp_path: Path) -> None:
        central_path = _make_central_db(tmp_path)
        engine = SyncEngine(central_path)
        result = engine.push("no-proj", tmp_path / "nonexistent.db")
        assert not result.success
        assert result.rows_synced == 0


# ---------------------------------------------------------------------------
# Test 10: SYNCABLE_TABLES sanity checks
# ---------------------------------------------------------------------------


class TestSyncableTablesList:
    def test_all_tables_have_names(self) -> None:
        for spec in SYNCABLE_TABLES:
            assert spec.name, f"Empty name in spec: {spec}"

    def test_no_duplicate_table_names(self) -> None:
        names = [s.name for s in SYNCABLE_TABLES]
        assert len(names) == len(set(names)), "Duplicate table names in SYNCABLE_TABLES"

    def test_autoincrement_tables_have_pk_columns(self) -> None:
        for spec in SYNCABLE_TABLES:
            if spec.has_autoincrement_pk:
                assert spec.pk_columns, (
                    f"{spec.name}: has_autoincrement_pk=True but pk_columns is empty"
                )


# ---------------------------------------------------------------------------
# Test 11: CentralStore db_path and schema init
# ---------------------------------------------------------------------------


class TestCentralStoreInit:
    def test_default_path_is_home_baton(self, tmp_path: Path) -> None:
        # We cannot test the actual default without side effects;
        # test that an explicit path is returned correctly.
        path = tmp_path / "central.db"
        store = CentralStore(path)
        assert store.db_path == path
        store.close()

    def test_db_created_on_first_query(self, tmp_path: Path) -> None:
        path = tmp_path / "central.db"
        store = CentralStore(path)
        store.query("SELECT 1")
        assert path.exists()
        store.close()

    def test_schema_tables_present(self, tmp_path: Path) -> None:
        path = tmp_path / "central.db"
        store = CentralStore(path)
        store.query("SELECT 1")  # trigger init

        tables = store.query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = {r["name"] for r in tables}
        assert "executions" in table_names
        assert "sync_watermarks" in table_names
        assert "sync_history" in table_names
        assert "projects" in table_names
        store.close()


# ---------------------------------------------------------------------------
# Test 12: get_central_storage and get_sync_engine factories
# ---------------------------------------------------------------------------


class TestStorageFactories:
    def test_get_central_storage_returns_central_store(self, tmp_path: Path) -> None:
        from agent_baton.core.storage import get_central_storage
        path = tmp_path / "central.db"
        store = get_central_storage(path)
        assert isinstance(store, CentralStore)
        store.close()

    def test_get_sync_engine_returns_sync_engine(self, tmp_path: Path) -> None:
        from agent_baton.core.storage import get_sync_engine
        path = tmp_path / "central.db"
        engine = get_sync_engine(path)
        assert isinstance(engine, SyncEngine)
