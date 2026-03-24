"""End-to-end integration tests for the federated sync system.

Covers the full flow across SqliteStorage → SyncEngine → CentralStore,
including cross-project isolation, PMO migration, auto-sync resolution,
external-source adapters, watermark correctness, and knowledge-field
round-trips.

Each test class covers exactly one integration scenario.  All file paths
use pytest's tmp_path fixture so nothing touches the real ~/.baton directory.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from agent_baton.core.storage.central import CentralStore, _maybe_migrate_pmo
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.core.storage.sync import SyncEngine, SyncResult, auto_sync_current_project
from agent_baton.core.storage.adapters import (
    AdapterRegistry,
    ExternalItem,
    ExternalSourceAdapter,
)
from agent_baton.models.execution import (
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
)
from agent_baton.models.knowledge import (
    KnowledgeAttachment,
    KnowledgeGapSignal,
    ResolvedDecision,
)
from agent_baton.models.retrospective import KnowledgeGap, Retrospective


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _make_project_db(tmp_path: Path, subdir: str) -> tuple[Path, SqliteStorage]:
    """Create a baton.db under tmp_path/<subdir>/ and return (path, store)."""
    db_dir = tmp_path / subdir
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "baton.db"
    store = SqliteStorage(db_path)
    return db_path, store


def _minimal_plan(task_id: str, *, knowledge_packs: list[str] | None = None) -> MachinePlan:
    """Return a minimal MachinePlan suitable for SqliteStorage.save_execution()."""
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer--python",
        task_description="Do the work",
        model="sonnet",
        depends_on=[],
        deliverables=["implementation"],
        allowed_paths=[],
        blocked_paths=[],
        context_files=[],
    )
    phase = PlanPhase(
        phase_id=1,
        name="Implement",
        steps=[step],
        gate=None,
        approval_required=False,
    )
    return MachinePlan(
        task_id=task_id,
        task_summary="Integration test task",
        risk_level="LOW",
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        shared_context="shared ctx",
        phases=[phase],
        explicit_knowledge_packs=knowledge_packs or [],
    )


def _minimal_state(
    task_id: str,
    *,
    status: str = "complete",
    knowledge_packs: list[str] | None = None,
) -> ExecutionState:
    """Return a minimal ExecutionState."""
    plan = _minimal_plan(task_id, knowledge_packs=knowledge_packs)
    return ExecutionState(
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


# ---------------------------------------------------------------------------
# Test 1: End-to-end data round-trip
#   SqliteStorage → SyncEngine.push() → CentralStore.query()
# ---------------------------------------------------------------------------


class TestEndToEndRoundTrip:
    """Create project baton.db via SqliteStorage, push to central.db via
    SyncEngine, then query via CentralStore — verify each major table."""

    def test_execution_row_survives_full_round_trip(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "rt-proj")
        state = _minimal_state("task-rt-1")
        store.save_execution(state)
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        result = engine.push("rt-proj", db_path)

        assert result.success, result.errors
        assert result.rows_synced > 0

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT status, started_at FROM executions "
            "WHERE project_id = ? AND task_id = ?",
            ("rt-proj", "task-rt-1"),
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "complete"
        assert rows[0]["started_at"] == "2026-01-01T00:00:00Z"
        central.close()

    def test_plan_row_with_summary_survives_round_trip(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "rt-plan")
        store.save_execution(_minimal_state("task-rt-plan"))
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        engine.push("rt-plan", db_path)

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT task_summary, risk_level FROM plans "
            "WHERE project_id = ? AND task_id = ?",
            ("rt-plan", "task-rt-plan"),
        )
        assert len(rows) == 1
        assert rows[0]["task_summary"] == "Integration test task"
        assert rows[0]["risk_level"] == "LOW"
        central.close()

    def test_plan_phases_and_steps_round_trip(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "rt-steps")
        store.save_execution(_minimal_state("task-rt-steps"))
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        engine.push("rt-steps", db_path)

        central = CentralStore(central_path)
        phases = central.query(
            "SELECT phase_id, name FROM plan_phases "
            "WHERE project_id = ? AND task_id = ?",
            ("rt-steps", "task-rt-steps"),
        )
        assert len(phases) == 1
        assert phases[0]["name"] == "Implement"

        steps = central.query(
            "SELECT step_id, agent_name FROM plan_steps "
            "WHERE project_id = ? AND task_id = ?",
            ("rt-steps", "task-rt-steps"),
        )
        assert len(steps) == 1
        assert steps[0]["step_id"] == "1.1"
        assert steps[0]["agent_name"] == "backend-engineer--python"
        central.close()

    def test_step_results_round_trip(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "rt-sr")
        store.save_execution(_minimal_state("task-rt-sr"))
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        engine.push("rt-sr", db_path)

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT agent_name, outcome, commit_hash FROM step_results "
            "WHERE project_id = ? AND task_id = ?",
            ("rt-sr", "task-rt-sr"),
        )
        assert len(rows) == 1
        assert rows[0]["agent_name"] == "backend-engineer--python"
        assert rows[0]["outcome"] == "Done"
        assert rows[0]["commit_hash"] == "abc123"
        central.close()

    def test_sync_history_recorded_after_push(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "rt-hist")
        store.save_execution(_minimal_state("task-rt-hist"))
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        engine.push("rt-hist", db_path)

        central = CentralStore(central_path)
        history = central.query(
            "SELECT status, rows_synced FROM sync_history WHERE project_id = ?",
            ("rt-hist",),
        )
        assert len(history) == 1
        assert history[0]["status"] == "success"
        assert history[0]["rows_synced"] > 0
        central.close()


# ---------------------------------------------------------------------------
# Test 2: Cross-project isolation
#   Two projects sync to the same central.db; queries see only their own rows.
# ---------------------------------------------------------------------------


class TestCrossProjectIsolation:
    """Populate two projects, sync both, and verify project_id separation."""

    def _setup(self, tmp_path: Path) -> tuple[Path, str, str]:
        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)

        for proj_id, task_id in [("alpha-project", "task-alpha"), ("beta-project", "task-beta")]:
            db_path, store = _make_project_db(tmp_path, proj_id)
            store.save_execution(_minimal_state(task_id))
            store.close()
            engine.push(proj_id, db_path)

        return central_path, "alpha-project", "beta-project"

    def test_each_project_sees_only_own_executions(self, tmp_path: Path) -> None:
        central_path, proj_a, proj_b = self._setup(tmp_path)
        central = CentralStore(central_path)

        alpha_rows = central.query(
            "SELECT task_id FROM executions WHERE project_id = ?", (proj_a,)
        )
        beta_rows = central.query(
            "SELECT task_id FROM executions WHERE project_id = ?", (proj_b,)
        )

        assert len(alpha_rows) == 1
        assert alpha_rows[0]["task_id"] == "task-alpha"
        assert len(beta_rows) == 1
        assert beta_rows[0]["task_id"] == "task-beta"
        central.close()

    def test_total_execution_count_equals_sum_of_projects(self, tmp_path: Path) -> None:
        central_path, _, _ = self._setup(tmp_path)
        central = CentralStore(central_path)
        rows = central.query("SELECT COUNT(*) AS n FROM executions")
        assert rows[0]["n"] == 2
        central.close()

    def test_project_watermarks_are_independent(self, tmp_path: Path) -> None:
        central_path, proj_a, proj_b = self._setup(tmp_path)
        central = CentralStore(central_path)

        wm_a = central.query(
            "SELECT table_name FROM sync_watermarks WHERE project_id = ?", (proj_a,)
        )
        wm_b = central.query(
            "SELECT table_name FROM sync_watermarks WHERE project_id = ?", (proj_b,)
        )

        # Both should have watermarks; the exact table list is the same but
        # the records are separate per project.
        assert len(wm_a) > 0
        assert len(wm_b) > 0
        table_names_a = {r["table_name"] for r in wm_a}
        table_names_b = {r["table_name"] for r in wm_b}
        # Both projects synced the same tables — watermarks share table names
        assert table_names_a == table_names_b
        central.close()

    def test_plans_from_both_projects_present_in_central(self, tmp_path: Path) -> None:
        central_path, proj_a, proj_b = self._setup(tmp_path)
        central = CentralStore(central_path)

        all_plan_projects = {
            r["project_id"]
            for r in central.query("SELECT DISTINCT project_id FROM plans")
        }
        assert proj_a in all_plan_projects
        assert proj_b in all_plan_projects
        central.close()

    def test_step_results_project_id_matches_parent_execution(self, tmp_path: Path) -> None:
        central_path, proj_a, proj_b = self._setup(tmp_path)
        central = CentralStore(central_path)

        for proj_id in (proj_a, proj_b):
            sr_rows = central.query(
                "SELECT DISTINCT project_id FROM step_results WHERE project_id = ?",
                (proj_id,),
            )
            assert len(sr_rows) == 1
            assert sr_rows[0]["project_id"] == proj_id
        central.close()


# ---------------------------------------------------------------------------
# Test 3: PMO migration
#   Build a legacy pmo.db, run _maybe_migrate_pmo(), verify data in central.db.
# ---------------------------------------------------------------------------


class TestPmoMigration:
    """Integration test for _maybe_migrate_pmo() end-to-end."""

    def _create_pmo_db(self, pmo_path: Path) -> None:
        """Directly create a minimal pmo.db using raw SQLite (no PMO model dep)."""
        conn = sqlite3.connect(str(pmo_path))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                name TEXT, path TEXT, program TEXT,
                color TEXT DEFAULT '', description TEXT DEFAULT '',
                registered_at TEXT DEFAULT '', ado_project TEXT DEFAULT ''
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS programs (name TEXT PRIMARY KEY)"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS signals (
                signal_id TEXT PRIMARY KEY,
                signal_type TEXT, title TEXT,
                description TEXT DEFAULT '', source_project_id TEXT DEFAULT '',
                severity TEXT DEFAULT 'medium', status TEXT DEFAULT 'open',
                created_at TEXT DEFAULT '', resolved_at TEXT DEFAULT '',
                forge_task_id TEXT DEFAULT ''
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS archived_cards (
                card_id TEXT PRIMARY KEY,
                project_id TEXT, program TEXT, title TEXT, column_name TEXT,
                risk_level TEXT DEFAULT 'LOW', priority INTEGER DEFAULT 0,
                agents TEXT DEFAULT '[]', steps_completed INTEGER DEFAULT 0,
                steps_total INTEGER DEFAULT 0, gates_passed INTEGER DEFAULT 0,
                current_phase TEXT DEFAULT '', error TEXT DEFAULT '',
                created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '',
                external_id TEXT DEFAULT ''
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS forge_sessions (
                session_id TEXT PRIMARY KEY,
                project_id TEXT DEFAULT '', title TEXT DEFAULT '',
                status TEXT DEFAULT 'active', created_at TEXT DEFAULT '',
                completed_at TEXT, task_id TEXT,
                notes TEXT DEFAULT ''
            )"""
        )

        conn.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("proj-legacy", "Legacy Project", "/srv/legacy", "prog-A",
             "blue", "Old project", "2025-01-01", ""),
        )
        conn.execute("INSERT INTO programs VALUES (?)", ("prog-A",))
        conn.execute(
            "INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sig-100", "bug", "Auth broken", "Detail here",
             "proj-legacy", "high", "open", "2025-01-01", "", ""),
        )
        conn.execute(
            "INSERT INTO archived_cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("card-200", "proj-legacy", "prog-A", "Build login", "deployed",
             "LOW", 1, "[]", 3, 3, 2, "Review", "", "2025-01-01", "2025-01-02", ""),
        )
        conn.execute(
            "INSERT INTO forge_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("sess-300", "proj-legacy", "My forge", "completed", "2025-01-01",
             "2025-01-02", "task-999", "notes"),
        )
        conn.commit()
        conn.close()

    def test_projects_migrated_to_central(self, tmp_path: Path) -> None:
        pmo_path = tmp_path / "pmo.db"
        central_path = tmp_path / "central.db"
        marker_path = tmp_path / ".pmo-migrated"
        self._create_pmo_db(pmo_path)

        migrated = _maybe_migrate_pmo(central_path, pmo_path, marker_path)

        assert migrated is True
        central = CentralStore(central_path)
        rows = central.query(
            "SELECT name, path, program FROM projects WHERE project_id = ?",
            ("proj-legacy",),
        )
        assert len(rows) == 1
        assert rows[0]["name"] == "Legacy Project"
        assert rows[0]["program"] == "prog-A"
        central.close()

    def test_signals_migrated_to_central(self, tmp_path: Path) -> None:
        pmo_path = tmp_path / "pmo.db"
        central_path = tmp_path / "central.db"
        marker_path = tmp_path / ".pmo-migrated"
        self._create_pmo_db(pmo_path)

        _maybe_migrate_pmo(central_path, pmo_path, marker_path)

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT title, severity, status FROM signals WHERE signal_id = ?",
            ("sig-100",),
        )
        assert len(rows) == 1
        assert rows[0]["title"] == "Auth broken"
        assert rows[0]["severity"] == "high"
        central.close()

    def test_archived_cards_migrated(self, tmp_path: Path) -> None:
        pmo_path = tmp_path / "pmo.db"
        central_path = tmp_path / "central.db"
        marker_path = tmp_path / ".pmo-migrated"
        self._create_pmo_db(pmo_path)

        _maybe_migrate_pmo(central_path, pmo_path, marker_path)

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT title, column_name FROM archived_cards WHERE card_id = ?",
            ("card-200",),
        )
        assert len(rows) == 1
        assert rows[0]["title"] == "Build login"
        assert rows[0]["column_name"] == "deployed"
        central.close()

    def test_forge_sessions_migrated(self, tmp_path: Path) -> None:
        pmo_path = tmp_path / "pmo.db"
        central_path = tmp_path / "central.db"
        marker_path = tmp_path / ".pmo-migrated"
        self._create_pmo_db(pmo_path)

        _maybe_migrate_pmo(central_path, pmo_path, marker_path)

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT title, status FROM forge_sessions WHERE session_id = ?",
            ("sess-300",),
        )
        assert len(rows) == 1
        assert rows[0]["title"] == "My forge"
        assert rows[0]["status"] == "completed"
        central.close()

    def test_migration_marker_created(self, tmp_path: Path) -> None:
        pmo_path = tmp_path / "pmo.db"
        central_path = tmp_path / "central.db"
        marker_path = tmp_path / ".pmo-migrated"
        self._create_pmo_db(pmo_path)

        assert not marker_path.exists()
        _maybe_migrate_pmo(central_path, pmo_path, marker_path)
        assert marker_path.exists()

    def test_second_migration_call_is_no_op(self, tmp_path: Path) -> None:
        pmo_path = tmp_path / "pmo.db"
        central_path = tmp_path / "central.db"
        marker_path = tmp_path / ".pmo-migrated"
        self._create_pmo_db(pmo_path)

        first = _maybe_migrate_pmo(central_path, pmo_path, marker_path)
        second = _maybe_migrate_pmo(central_path, pmo_path, marker_path)

        assert first is True
        assert second is False  # marker present — skipped

    def test_missing_pmo_db_writes_no_source_marker(self, tmp_path: Path) -> None:
        central_path = tmp_path / "central.db"
        marker_path = tmp_path / ".pmo-migrated"
        # pmo_path does not exist
        result = _maybe_migrate_pmo(central_path, tmp_path / "nonexistent.db", marker_path)
        assert result is False
        assert marker_path.exists()
        assert "no-source" in marker_path.read_text()


# ---------------------------------------------------------------------------
# Test 4: Auto-sync (auto_sync_current_project)
# ---------------------------------------------------------------------------


class TestAutoSync:
    """auto_sync_current_project resolves the current project and triggers push."""

    def test_direct_call_syncs_matching_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Call auto_sync_current_project with cwd inside a registered project."""
        from agent_baton.core.storage import sync as sync_module

        central_path = tmp_path / "central.db"
        monkeypatch.setattr(sync_module, "_CENTRAL_DB_DEFAULT", central_path)

        # Set up project with baton.db in expected location
        proj_root = tmp_path / "myproject"
        tc_dir = proj_root / ".claude" / "team-context"
        tc_dir.mkdir(parents=True, exist_ok=True)
        db_path = tc_dir / "baton.db"

        store = SqliteStorage(db_path)
        store.save_execution(_minimal_state("task-auto-sync-1"))
        store.close()

        # Register project in central.db
        engine = SyncEngine(central_path)
        conn = engine._conn_mgr.get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO projects (project_id, name, path, program) "
            "VALUES (?, ?, ?, ?)",
            ("myproject", "My Project", str(proj_root), "test"),
        )
        conn.commit()

        monkeypatch.chdir(proj_root)

        result = auto_sync_current_project()

        assert result is not None
        assert result.project_id == "myproject"
        assert result.success, result.errors
        assert result.rows_synced > 0

        # Verify data made it to central.db
        central = CentralStore(central_path)
        rows = central.query(
            "SELECT task_id FROM executions WHERE project_id = ?", ("myproject",)
        )
        assert len(rows) == 1
        assert rows[0]["task_id"] == "task-auto-sync-1"
        central.close()

    def test_direct_call_returns_none_when_no_central_db(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_baton.core.storage import sync as sync_module

        monkeypatch.setattr(
            sync_module, "_CENTRAL_DB_DEFAULT", tmp_path / "nonexistent.db"
        )
        result = auto_sync_current_project()
        assert result is None

    def test_auto_sync_prefers_most_specific_project_match(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When cwd matches both /proj and /proj/sub-project, the longer path wins."""
        from agent_baton.core.storage import sync as sync_module

        central_path = tmp_path / "central.db"
        monkeypatch.setattr(sync_module, "_CENTRAL_DB_DEFAULT", central_path)

        # Two project registrations: parent and child directory
        parent_root = tmp_path / "workspace"
        child_root = parent_root / "subproject"

        # Create baton.db for the child project (the one we'll chdir into)
        tc_dir = child_root / ".claude" / "team-context"
        tc_dir.mkdir(parents=True, exist_ok=True)
        child_db = tc_dir / "baton.db"
        store = SqliteStorage(child_db)
        store.save_execution(_minimal_state("task-child"))
        store.close()

        # Register both projects
        engine = SyncEngine(central_path)
        conn = engine._conn_mgr.get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO projects (project_id, name, path, program) "
            "VALUES (?, ?, ?, ?)",
            ("parent-proj", "Parent", str(parent_root), "p"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO projects (project_id, name, path, program) "
            "VALUES (?, ?, ?, ?)",
            ("child-proj", "Child", str(child_root), "p"),
        )
        conn.commit()

        monkeypatch.chdir(child_root)

        result = auto_sync_current_project()

        assert result is not None
        # Should resolve to child-proj, not parent-proj
        assert result.project_id == "child-proj"


# ---------------------------------------------------------------------------
# Test 5: External sources adapter integration
#   Register a mock adapter, fetch items, store in central.db external_items.
# ---------------------------------------------------------------------------


class MockAdapter:
    """Minimal adapter that satisfies the ExternalSourceAdapter protocol."""

    source_type: str = "mock"

    def __init__(self) -> None:
        self._items: list[ExternalItem] = []
        self._connected = False

    def connect(self, config: dict) -> None:
        self._connected = True

    def fetch_items(
        self,
        item_types: list[str] | None = None,
        since: str | None = None,
    ) -> list[ExternalItem]:
        return list(self._items)

    def fetch_item(self, external_id: str) -> ExternalItem | None:
        for item in self._items:
            if item.external_id == external_id:
                return item
        return None


class TestExternalSourceAdapters:
    """AdapterRegistry + fetch → store in central.db external_items."""

    def setup_method(self) -> None:
        # Ensure we start with a clean registry for each test by removing the
        # mock adapter if a previous test left it registered.
        AdapterRegistry._adapters.pop("mock", None)

    def test_adapter_registration_and_retrieval(self) -> None:
        AdapterRegistry.register(MockAdapter)
        assert "mock" in AdapterRegistry.available()
        cls = AdapterRegistry.get("mock")
        assert cls is MockAdapter

    def test_unregistered_adapter_returns_none(self) -> None:
        cls = AdapterRegistry.get("no-such-adapter")
        assert cls is None

    def test_fetch_items_stored_in_central_external_items(
        self, tmp_path: Path
    ) -> None:
        AdapterRegistry.register(MockAdapter)

        central_path = tmp_path / "central.db"
        central = CentralStore(central_path)

        # Register an external source
        central.execute(
            "INSERT INTO external_sources "
            "(source_id, source_type, display_name, config) "
            "VALUES (?, ?, ?, ?)",
            ("src-001", "mock", "Mock Source", "{}"),
        )

        # Build a mock adapter and "fetch" items
        adapter = MockAdapter()
        adapter.connect({})
        adapter._items = [
            ExternalItem(
                source_id="src-001",
                external_id="ITEM-1",
                item_type="feature",
                title="Feature Alpha",
                description="Build alpha feature",
                state="active",
                priority=1,
            ),
            ExternalItem(
                source_id="src-001",
                external_id="ITEM-2",
                item_type="bug",
                title="Bug Beta",
                description="Fix beta bug",
                state="new",
                priority=2,
            ),
        ]

        # Persist each item into central.db external_items
        for item in adapter.fetch_items():
            central.execute(
                """
                INSERT OR REPLACE INTO external_items
                    (source_id, external_id, item_type, title, description,
                     state, priority, tags, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.source_id,
                    item.external_id,
                    item.item_type,
                    item.title,
                    item.description,
                    item.state,
                    str(item.priority),
                    json.dumps(item.tags),
                    json.dumps(item.raw_data or {}),
                    "2026-01-01T00:00:00Z",
                ),
            )

        # Verify both items appear in central.db
        rows = central.query(
            "SELECT external_id, title FROM external_items "
            "WHERE source_id = ? ORDER BY external_id",
            ("src-001",),
        )
        assert len(rows) == 2
        assert rows[0]["external_id"] == "ITEM-1"
        assert rows[0]["title"] == "Feature Alpha"
        assert rows[1]["external_id"] == "ITEM-2"
        assert rows[1]["title"] == "Bug Beta"
        central.close()

    def test_external_items_unique_constraint(self, tmp_path: Path) -> None:
        """Inserting the same (source_id, external_id) twice does not duplicate."""
        AdapterRegistry.register(MockAdapter)

        central_path = tmp_path / "central.db"
        central = CentralStore(central_path)

        central.execute(
            "INSERT INTO external_sources "
            "(source_id, source_type, display_name, config) VALUES (?, ?, ?, ?)",
            ("src-dup", "mock", "Dup Source", "{}"),
        )

        for _ in range(2):
            central.execute(
                """
                INSERT OR REPLACE INTO external_items
                    (source_id, external_id, item_type, title)
                VALUES (?, ?, ?, ?)
                """,
                ("src-dup", "DUP-1", "feature", "Dup Item"),
            )

        rows = central.query(
            "SELECT COUNT(*) AS n FROM external_items WHERE source_id = ?",
            ("src-dup",),
        )
        assert rows[0]["n"] == 1
        central.close()

    def test_execute_rejects_write_to_non_external_table(self, tmp_path: Path) -> None:
        central_path = tmp_path / "central.db"
        central = CentralStore(central_path)
        central.query("SELECT 1")  # init schema

        with pytest.raises(ValueError, match="external-source tables"):
            central.execute(
                "INSERT INTO executions (project_id, task_id, started_at) "
                "VALUES (?, ?, ?)",
                ("p", "t", "now"),
            )
        central.close()

    def test_execute_rejects_non_dml_statements(self, tmp_path: Path) -> None:
        central_path = tmp_path / "central.db"
        central = CentralStore(central_path)
        central.query("SELECT 1")  # init schema

        with pytest.raises(ValueError, match="DML"):
            central.execute("SELECT * FROM external_sources")
        central.close()

    def teardown_method(self) -> None:
        AdapterRegistry._adapters.pop("mock", None)


# ---------------------------------------------------------------------------
# Test 6: Watermark correctness
# ---------------------------------------------------------------------------


class TestWatermarkCorrectness:
    """Watermarks advance correctly; second push with no new data copies 0 rows."""

    def test_watermarks_advance_after_first_push(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "wm-proj")
        store.save_execution(_minimal_state("task-wm-1"))
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        engine.push("wm-proj", db_path)

        central = CentralStore(central_path)
        wm_rows = central.query(
            "SELECT table_name, last_rowid FROM sync_watermarks "
            "WHERE project_id = ? ORDER BY table_name",
            ("wm-proj",),
        )
        # Every table that had rows should have a positive watermark
        for row in wm_rows:
            assert row["last_rowid"] > 0, (
                f"Watermark for {row['table_name']} should be positive"
            )
        central.close()

    def test_second_push_copies_zero_rows_when_no_new_data(
        self, tmp_path: Path
    ) -> None:
        db_path, store = _make_project_db(tmp_path, "wm-noop")
        store.save_execution(_minimal_state("task-wm-noop"))
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)

        first = engine.push("wm-noop", db_path)
        assert first.rows_synced > 0

        second = engine.push("wm-noop", db_path)
        assert second.rows_synced == 0
        assert second.success

    def test_watermarks_advance_when_new_rows_added(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "wm-grow")
        store.save_execution(_minimal_state("task-wm-grow-1"))
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        engine.push("wm-grow", db_path)

        # Capture watermark for executions before second push
        central = CentralStore(central_path)
        wm_before = central.query(
            "SELECT last_rowid FROM sync_watermarks "
            "WHERE project_id = ? AND table_name = 'executions'",
            ("wm-grow",),
        )
        assert len(wm_before) == 1
        before_rowid = wm_before[0]["last_rowid"]
        central.close()

        # Add a second execution
        store2 = SqliteStorage(db_path)
        store2.save_execution(_minimal_state("task-wm-grow-2"))
        store2.close()

        engine.push("wm-grow", db_path)

        central2 = CentralStore(central_path)
        wm_after = central2.query(
            "SELECT last_rowid FROM sync_watermarks "
            "WHERE project_id = ? AND table_name = 'executions'",
            ("wm-grow",),
        )
        assert wm_after[0]["last_rowid"] > before_rowid
        central2.close()

    def test_third_push_still_zero_rows_without_more_data(
        self, tmp_path: Path
    ) -> None:
        db_path, store = _make_project_db(tmp_path, "wm-triple")
        store.save_execution(_minimal_state("task-wm-triple"))
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        engine.push("wm-triple", db_path)  # first
        engine.push("wm-triple", db_path)  # second — 0 rows
        third = engine.push("wm-triple", db_path)  # third — also 0 rows
        assert third.rows_synced == 0

    def test_row_count_in_central_does_not_grow_on_repeat_push(
        self, tmp_path: Path
    ) -> None:
        db_path, store = _make_project_db(tmp_path, "wm-count")
        store.save_execution(_minimal_state("task-wm-count"))
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        engine.push("wm-count", db_path)

        central = CentralStore(central_path)
        count_after_first = central.query(
            "SELECT COUNT(*) AS n FROM executions WHERE project_id = ?",
            ("wm-count",),
        )[0]["n"]

        engine.push("wm-count", db_path)
        count_after_second = central.query(
            "SELECT COUNT(*) AS n FROM executions WHERE project_id = ?",
            ("wm-count",),
        )[0]["n"]

        assert count_after_first == count_after_second
        central.close()


# ---------------------------------------------------------------------------
# Test 7: Knowledge delivery + sync
#   Create a plan with knowledge attachments, save via SqliteStorage, sync
#   to central, verify knowledge columns survive the full round-trip.
# ---------------------------------------------------------------------------


class TestKnowledgeDeliverySync:
    """Knowledge-delivery fields persist end-to-end through the sync pipeline."""

    def _make_plan_with_knowledge(self, task_id: str) -> MachinePlan:
        """Return a MachinePlan with knowledge attachments and explicit packs."""
        attachment = KnowledgeAttachment(
            source="explicit",
            pack_name="security-pack",
            document_name="auth-guide.md",
            path="/knowledge/security-pack/auth-guide.md",
            delivery="inline",
            grounding="Context for the auth implementation",
            token_estimate=300,
        )
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--python",
            task_description="Implement OAuth2 login",
            model="sonnet",
            depends_on=[],
            deliverables=["auth.py"],
            allowed_paths=[],
            blocked_paths=[],
            context_files=[],
            knowledge=[attachment],
        )
        phase = PlanPhase(
            phase_id=1,
            name="Implement",
            steps=[step],
            gate=None,
            approval_required=False,
        )
        return MachinePlan(
            task_id=task_id,
            task_summary="Add OAuth2 login",
            risk_level="MEDIUM",
            budget_tier="standard",
            execution_mode="phased",
            git_strategy="commit-per-agent",
            shared_context="auth context",
            phases=[phase],
            explicit_knowledge_packs=["security-pack"],
            explicit_knowledge_docs=["/docs/oauth2-spec.md"],
            intervention_level="medium",
        )

    def test_explicit_knowledge_packs_round_trip(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "kd-packs")
        plan = self._make_plan_with_knowledge("task-kd-packs")
        state = ExecutionState(
            task_id="task-kd-packs",
            plan=plan,
            status="complete",
            current_phase=1,
            current_step_index=0,
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T01:00:00Z",
            step_results=[
                StepResult(
                    step_id="1.1",
                    agent_name="backend-engineer--python",
                    status="complete",
                    outcome="Auth done",
                    files_changed=["auth.py"],
                    commit_hash="def456",
                    estimated_tokens=800,
                    duration_seconds=45.0,
                    retries=0,
                    error="",
                    completed_at="2026-01-01T01:00:00Z",
                )
            ],
            gate_results=[],
            approval_results=[],
            amendments=[],
        )
        store.save_execution(state)
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        result = engine.push("kd-packs", db_path)
        assert result.success, result.errors

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT explicit_knowledge_packs, explicit_knowledge_docs, "
            "intervention_level FROM plans "
            "WHERE project_id = ? AND task_id = ?",
            ("kd-packs", "task-kd-packs"),
        )
        assert len(rows) == 1
        packs = json.loads(rows[0]["explicit_knowledge_packs"])
        docs = json.loads(rows[0]["explicit_knowledge_docs"])
        assert "security-pack" in packs
        assert "/docs/oauth2-spec.md" in docs
        assert rows[0]["intervention_level"] == "medium"
        central.close()

    def test_knowledge_attachments_on_steps_round_trip(self, tmp_path: Path) -> None:
        db_path, store = _make_project_db(tmp_path, "kd-steps")
        plan = self._make_plan_with_knowledge("task-kd-steps")
        state = ExecutionState(
            task_id="task-kd-steps",
            plan=plan,
            status="complete",
            current_phase=1,
            current_step_index=0,
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T01:00:00Z",
            step_results=[
                StepResult(
                    step_id="1.1",
                    agent_name="backend-engineer--python",
                    status="complete",
                    outcome="Done",
                    files_changed=[],
                    commit_hash="",
                    estimated_tokens=0,
                    duration_seconds=0.0,
                    retries=0,
                    error="",
                    completed_at="",
                )
            ],
            gate_results=[],
            approval_results=[],
            amendments=[],
        )
        store.save_execution(state)
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        result = engine.push("kd-steps", db_path)
        assert result.success, result.errors

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT knowledge_attachments FROM plan_steps "
            "WHERE project_id = ? AND task_id = ? AND step_id = ?",
            ("kd-steps", "task-kd-steps", "1.1"),
        )
        assert len(rows) == 1
        attachments = json.loads(rows[0]["knowledge_attachments"])
        assert len(attachments) == 1
        ka = attachments[0]
        assert ka["document_name"] == "auth-guide.md"
        assert ka["pack_name"] == "security-pack"
        assert ka["delivery"] == "inline"
        assert ka["grounding"] == "Context for the auth implementation"
        assert ka["token_estimate"] == 300
        central.close()

    def test_knowledge_round_trip_via_save_plan_and_sync(
        self, tmp_path: Path
    ) -> None:
        """save_plan() (queued path) + sync preserves knowledge fields."""
        db_path, store = _make_project_db(tmp_path, "kd-queued")
        plan = self._make_plan_with_knowledge("task-kd-queued")
        store.save_plan(plan)
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        result = engine.push("kd-queued", db_path)
        assert result.success, result.errors

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT explicit_knowledge_packs, intervention_level FROM plans "
            "WHERE project_id = ? AND task_id = ?",
            ("kd-queued", "task-kd-queued"),
        )
        assert len(rows) == 1
        packs = json.loads(rows[0]["explicit_knowledge_packs"])
        assert "security-pack" in packs
        assert rows[0]["intervention_level"] == "medium"
        central.close()

    def test_pending_gaps_and_resolved_decisions_round_trip(
        self, tmp_path: Path
    ) -> None:
        """ExecutionState.pending_gaps and resolved_decisions survive sync."""
        db_path, store = _make_project_db(tmp_path, "kd-gaps")
        plan = _minimal_plan("task-kd-gaps")

        gap = KnowledgeGapSignal(
            description="Missing auth flow spec",
            confidence="none",
            gap_type="factual",
            step_id="1.1",
            agent_name="backend-engineer--python",
        )
        decision = ResolvedDecision(
            gap_description="Missing auth flow spec",
            resolution="Use OAuth2 with PKCE",
            step_id="1.1",
            timestamp="2026-01-01T00:30:00Z",
        )
        state = ExecutionState(
            task_id="task-kd-gaps",
            plan=plan,
            status="running",
            current_phase=1,
            current_step_index=0,
            started_at="2026-01-01T00:00:00Z",
            completed_at=None,
            step_results=[],
            gate_results=[],
            approval_results=[],
            amendments=[],
            pending_gaps=[gap],
            resolved_decisions=[decision],
        )
        store.save_execution(state)
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        result = engine.push("kd-gaps", db_path)
        assert result.success, result.errors

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT pending_gaps, resolved_decisions FROM executions "
            "WHERE project_id = ? AND task_id = ?",
            ("kd-gaps", "task-kd-gaps"),
        )
        assert len(rows) == 1

        pending_gaps = json.loads(rows[0]["pending_gaps"])
        assert len(pending_gaps) == 1
        assert pending_gaps[0]["description"] == "Missing auth flow spec"
        assert pending_gaps[0]["confidence"] == "none"

        resolved = json.loads(rows[0]["resolved_decisions"])
        assert len(resolved) == 1
        assert resolved[0]["gap_description"] == "Missing auth flow spec"
        assert resolved[0]["resolution"] == "Use OAuth2 with PKCE"
        central.close()

    def test_knowledge_in_plan_created_by_intelligent_planner_syncs(
        self, tmp_path: Path
    ) -> None:
        """IntelligentPlanner produces a plan; explicit_knowledge_packs survive sync."""
        from agent_baton.core.engine.planner import IntelligentPlanner

        planner = IntelligentPlanner()
        plan = planner.create_plan(
            "Add OAuth2 login",
            explicit_knowledge_packs=["security-pack"],
            explicit_knowledge_docs=["/docs/oauth2.md"],
            intervention_level="medium",
        )

        # Persist via SqliteStorage
        db_path, store = _make_project_db(tmp_path, "kd-planner")
        store.save_plan(plan)
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        result = engine.push("kd-planner", db_path)
        assert result.success, result.errors

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT explicit_knowledge_packs, explicit_knowledge_docs, "
            "intervention_level, task_summary FROM plans "
            "WHERE project_id = ? AND task_id = ?",
            ("kd-planner", plan.task_id),
        )
        assert len(rows) == 1

        packs = json.loads(rows[0]["explicit_knowledge_packs"])
        docs = json.loads(rows[0]["explicit_knowledge_docs"])
        assert "security-pack" in packs
        assert "/docs/oauth2.md" in docs
        assert rows[0]["intervention_level"] == "medium"
        assert "OAuth2" in rows[0]["task_summary"]
        central.close()
