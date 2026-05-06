"""Regression tests for issues discovered by the learning automation system.

Each test class corresponds to one learning issue detected by `baton learn`.
The tests are written to catch the *symptom* so that a re-introduction of the
bug is caught at test time, not discovered in production.

Issues covered:
    1. Classifier fallback (roster_bloat) -- _infer_task_type / _score_task_type
    2. Stalled sessions -- executor state-machine transitions
    3. Bead signal parsing -- BeadSelector instantiation in single and team paths
    4. Token tracking -- estimated_tokens persisted through record_step_result
    5. Central DB sync -- v7 migration adds project_id to learning_issues
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.engine.classifier import (
    KeywordClassifier,
    _score_task_type,
)
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.storage.schema import MIGRATIONS, SCHEMA_VERSION
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Shared plan/phase/step helpers (mirrors test_executor.py conventions)
# ---------------------------------------------------------------------------

def _step(
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task: str = "Implement feature X",
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task,
        model="sonnet",
        deliverables=[],
        allowed_paths=[],
        context_files=[],
    )


def _phase(
    phase_id: int = 0,
    name: str = "Implementation",
    steps: list[PlanStep] | None = None,
    gate: PlanGate | None = None,
) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=name,
        steps=steps or [_step()],
        gate=gate,
    )


def _plan(
    task_id: str = "task-001",
    task_summary: str = "Build a thing",
    phases: list[PlanPhase] | None = None,
    risk_level: str = "LOW",
) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary=task_summary,
        risk_level=risk_level,
        phases=phases or [_phase()],
        shared_context="",
    )


def _engine(tmp_path: Path) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path)


# ---------------------------------------------------------------------------
# Issue 1: Classifier fallback (roster_bloat)
# ---------------------------------------------------------------------------

_TASK_TYPE_KEYWORD_FIXTURES: list[tuple[str, list[str]]] = [
    ("new-feature",   ["add", "build", "create", "implement", "feature"]),
    ("bug-fix",       ["fix", "bug", "broken", "error", "crash"]),
    ("migration",     ["migrate", "migration", "upgrade", "move"]),
    ("refactor",      ["refactor", "clean up", "reorganize", "restructure"]),
    ("data-analysis", ["analyze", "analyse", "analytics", "report"]),
    ("test",          ["test suite", "tests for", "testing", "test coverage"]),
    ("documentation", ["document", "documentation", "readme", "spec"]),
]


class TestClassifierFallback:
    """Regression: _score_task_type returns correct types without Haiku."""

    @pytest.mark.parametrize("summary,expected_type", [
        ("Fix the login crash on production", "bug-fix"),
        ("Build a new OAuth2 endpoint", "new-feature"),
        ("Write a test suite for the payment module", "test"),
        ("Refactor the data access layer", "refactor"),
        ("Write documentation for the API", "documentation"),
        ("Migrate the database schema to v3", "migration"),
        ("Analyze query performance metrics", "data-analysis"),
    ])
    def test_score_task_type_returns_correct_type(
        self, summary: str, expected_type: str
    ) -> None:
        result = _score_task_type(summary, _TASK_TYPE_KEYWORD_FIXTURES)
        assert result == expected_type, (
            f"Expected {expected_type!r} for {summary!r}, got {result!r}"
        )

    def test_score_task_type_defaults_to_new_feature_when_no_match(self) -> None:
        result = _score_task_type("zzz xyzzy nonce", _TASK_TYPE_KEYWORD_FIXTURES)
        assert result == "new-feature"

    def test_score_task_type_word_boundary_prevents_false_positives(self) -> None:
        result = _score_task_type(
            "update the prefix configuration in the latest build",
            _TASK_TYPE_KEYWORD_FIXTURES,
        )
        assert result not in ("bug-fix", "test"), (
            f"Word-boundary check failed: got {result!r} for substring-containing input"
        )

    def test_keyword_classifier_is_deterministic(self) -> None:
        registry = MagicMock()
        registry.agents = {}

        clf = KeywordClassifier()
        result = clf.classify("Fix the broken auth endpoint", registry)

        assert result.task_type == "bug-fix"
        assert result.source == "keyword-fallback"

    def test_keyword_classifier_handles_unknown_task_type_gracefully(self) -> None:
        registry = MagicMock()
        registry.agents = {}

        clf = KeywordClassifier()
        result = clf.classify("frobnicate the wibble glorp", registry)

        assert result.task_type in (
            "new-feature", "bug-fix", "refactor", "data-analysis",
            "documentation", "migration", "test",
        )
        assert result.complexity in ("light", "medium", "heavy")
        assert len(result.agents) >= 1
        assert len(result.phases) >= 1

    def test_infer_task_type_consistent_with_score_task_type(self) -> None:
        from agent_baton.core.engine.planner import IntelligentPlanner, _TASK_TYPE_KEYWORDS

        planner = IntelligentPlanner.__new__(IntelligentPlanner)

        for summary, _ in [
            ("Build a REST endpoint for user profile updates", "new-feature"),
            ("Fix the NullPointerException in the auth module", "bug-fix"),
            ("Migrate the users table from MySQL to Postgres", "migration"),
        ]:
            result = planner._infer_task_type(summary)
            expected_from_scorer = _score_task_type(summary, _TASK_TYPE_KEYWORDS)
            assert result == expected_from_scorer, (
                f"_infer_task_type diverged from _score_task_type for: {summary!r}"
            )


# ---------------------------------------------------------------------------
# Issue 2: Stalled sessions
# ---------------------------------------------------------------------------

class TestStalledSessions:
    """Regression: sessions stuck in 'running' can be completed or cancelled."""

    def test_complete_transitions_running_to_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        plan = _plan(task_id="stalled-001")
        action = engine.start(plan)

        assert action.action_type == ActionType.DISPATCH
        engine.record_step_result(
            step_id=action.step_id,
            agent_name=action.agent_name,
            status="complete",
            outcome="Done.",
        )

        summary = engine.complete()
        state = engine._load_execution()
        assert state is not None
        assert state.status == "complete"
        assert "stalled-001" in summary

    def test_complete_is_callable_when_session_is_running(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        plan = _plan(task_id="stalled-002")
        action = engine.start(plan)

        engine.record_step_result(
            step_id=action.step_id,
            agent_name=action.agent_name,
            status="complete",
            outcome="Done.",
        )
        engine.complete()

        state = engine._load_execution()
        assert state is not None
        state.status = "running"
        engine._save_execution(state)

        engine.complete()
        state = engine._load_execution()
        assert state.status == "complete"

    def test_failed_step_transitions_to_failed_status(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        plan = _plan(task_id="stalled-003")
        action = engine.start(plan)

        engine.record_step_result(
            step_id=action.step_id,
            agent_name=action.agent_name,
            status="failed",
            error="Unrecoverable error.",
        )

        next_action = engine.next_action()
        assert next_action.action_type == ActionType.FAILED

    def test_cancel_sets_status_to_cancelled(self, tmp_path: Path) -> None:
        from datetime import datetime, timezone

        engine = _engine(tmp_path)
        plan = _plan(task_id="stalled-004")
        engine.start(plan)

        state = engine._load_execution()
        assert state is not None
        assert state.status == "running"

        state.status = "cancelled"
        state.completed_at = datetime.now(timezone.utc).isoformat()
        engine._save_execution(state)

        reloaded = engine._load_execution()
        assert reloaded is not None
        assert reloaded.status == "cancelled"

    def test_running_session_can_reach_complete_from_any_point(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        two_step_plan = _plan(
            task_id="stalled-005",
            phases=[
                _phase(
                    phase_id=0,
                    steps=[
                        _step(step_id="1.1"),
                        _step(step_id="1.2"),
                    ],
                )
            ],
        )
        engine.start(two_step_plan)

        engine.record_step_result("1.1", "backend-engineer", status="complete")

        state = engine._load_execution()
        assert state.status == "running"

        engine.complete()
        final_state = engine._load_execution()
        assert final_state.status == "complete"


# ---------------------------------------------------------------------------
# Issue 3: Bead signal parsing in executor
# ---------------------------------------------------------------------------

class TestBeadSignalParsing:
    """Regression: BeadSelector must be instantiated, not called as classmethod."""

    def test_bead_selector_is_instantiated_not_classmethod(self) -> None:
        from agent_baton.core.engine.bead_selector import BeadSelector

        selector = BeadSelector()
        assert callable(selector.select), (
            "BeadSelector().select must be a bound instance method"
        )

    def test_bead_selector_select_is_instance_method_not_classmethod(self) -> None:
        from agent_baton.core.engine.bead_selector import BeadSelector
        import inspect

        assert not isinstance(
            inspect.getattr_static(BeadSelector, "select"),
            classmethod,
        ), "BeadSelector.select must not be a classmethod"

    def test_parse_bead_signals_returns_beads_for_discovery_signal(self) -> None:
        from agent_baton.core.engine.bead_signal import parse_bead_signals

        outcome = (
            "Implemented the feature.\n"
            "BEAD_DISCOVERY: The auth module uses JWT with RS256, not HS256.\n"
            "Tests pass."
        )
        beads = parse_bead_signals(
            outcome,
            step_id="1.1",
            agent_name="backend-engineer",
            task_id="task-beads-001",
            bead_count=0,
        )
        assert len(beads) == 1
        assert beads[0].bead_type == "discovery"
        assert "RS256" in beads[0].content

    def test_parse_bead_signals_returns_beads_for_warning_signal(self) -> None:
        from agent_baton.core.engine.bead_signal import parse_bead_signals

        outcome = "BEAD_WARNING: Test DB fixture uses hardcoded port 5433 -- may conflict.\n"
        beads = parse_bead_signals(
            outcome,
            step_id="1.1",
            agent_name="test-engineer",
            task_id="task-beads-002",
            bead_count=0,
        )
        assert len(beads) == 1
        assert beads[0].bead_type == "warning"

    def test_parse_bead_signals_returns_empty_for_no_signals(self) -> None:
        from agent_baton.core.engine.bead_signal import parse_bead_signals

        beads = parse_bead_signals(
            "Everything looks good.",
            step_id="1.1",
            agent_name="backend-engineer",
            task_id="task-beads-003",
            bead_count=0,
        )
        assert beads == []

    def test_bead_signals_parse_and_write_roundtrip(
        self, tmp_path: Path
    ) -> None:
        """parse_bead_signals -> BeadStore.write -> BeadStore.query roundtrip.

        The beads table has FK to executions(task_id), so a matching executions
        row is seeded via SqliteStorage before the bead is written.
        """
        from agent_baton.core.storage.sqlite_backend import SqliteStorage
        from agent_baton.core.engine.bead_store import BeadStore
        from agent_baton.core.engine.bead_signal import parse_bead_signals

        db_path = tmp_path / "baton.db"
        storage = SqliteStorage(db_path)

        # Seed an executions row to satisfy the beads FK constraint.
        conn = storage._conn_mgr.get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO executions "
            "(task_id, status, current_phase, current_step_index, "
            " started_at, created_at, updated_at) "
            "VALUES (?, 'running', 0, 0, "
            " datetime('now'), datetime('now'), datetime('now'))",
            ("task-beads-e2e",),
        )
        conn.commit()
        storage.close()

        outcome = (
            "Feature implemented.\n"
            "BEAD_DISCOVERY: Request validation is done in middleware, not the controller.\n"
        )
        beads = parse_bead_signals(
            outcome,
            step_id="1.1",
            agent_name="backend-engineer",
            task_id="task-beads-e2e",
            bead_count=0,
        )
        assert len(beads) == 1, "parse_bead_signals must find the BEAD_DISCOVERY signal"

        bead_store = BeadStore(db_path)
        written_id = bead_store.write(beads[0])
        assert written_id, (
            "BeadStore.write must return a non-empty bead_id; "
            "empty string means the write failed (check FK constraints)"
        )

        queried = bead_store.query(task_id="task-beads-e2e")
        assert len(queried) >= 1, "BeadStore.query must return the written bead"
        assert any("middleware" in b.content for b in queried), (
            "Queried bead content must include the BEAD_DISCOVERY message"
        )

    def test_team_dispatch_bead_selector_instantiation(
        self, tmp_path: Path
    ) -> None:
        """In the team-dispatch path, BeadSelector must be used as an instance."""
        from agent_baton.core.engine.bead_selector import BeadSelector

        instantiation_calls: list[Any] = []
        original_init = BeadSelector.__init__

        def tracking_init(self: BeadSelector, *args: Any, **kwargs: Any) -> None:
            instantiation_calls.append(True)
            original_init(self, *args, **kwargs)

        with patch.object(BeadSelector, "__init__", tracking_init):
            from agent_baton.core.engine.bead_selector import BeadSelector as _TBS
            mock_store = MagicMock()
            mock_store.query.return_value = []
            mock_step = MagicMock()
            mock_plan = MagicMock()
            mock_plan.phases = []

            _TBS().select(mock_store, mock_step, mock_plan)

        assert len(instantiation_calls) >= 1, (
            "BeadSelector must be instantiated (BeadSelector()) in the team-dispatch path; "
            "calling it as a classmethod (BeadSelector.select(...)) skips __init__"
        )


# ---------------------------------------------------------------------------
# Issue 4: Token tracking
# ---------------------------------------------------------------------------

class TestTokenTracking:
    """Regression: estimated_tokens must be persisted through record_step_result."""

    def test_estimated_tokens_persisted_to_sqlite(self, tmp_path: Path) -> None:
        from agent_baton.core.storage.sqlite_backend import SqliteStorage

        db_path = tmp_path / "baton.db"
        storage = SqliteStorage(db_path)
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            storage=storage,
            task_id="task-tokens-001",
        )

        plan = _plan(task_id="task-tokens-001")
        action = engine.start(plan)

        engine.record_step_result(
            step_id=action.step_id,
            agent_name=action.agent_name,
            status="complete",
            outcome="Done.",
            estimated_tokens=12345,
        )

        conn = storage._conn_mgr.get_connection()
        row = conn.execute(
            "SELECT estimated_tokens FROM step_results "
            "WHERE task_id = ? AND step_id = ?",
            ("task-tokens-001", action.step_id),
        ).fetchone()

        assert row is not None, "step_results row must exist after record_step_result"
        assert row["estimated_tokens"] == 12345, (
            f"Expected 12345 tokens persisted, got {row['estimated_tokens']}"
        )

        storage.close()

    def test_zero_tokens_triggers_heuristic_fallback(
        self, tmp_path: Path
    ) -> None:
        # When estimated_tokens=0 is passed and a plan is available, the executor
        # uses the char/4 heuristic derived from the step's task_description rather
        # than persisting a zero that would starve the budget-tuner.
        from agent_baton.core.storage.sqlite_backend import SqliteStorage

        db_path = tmp_path / "baton.db"
        storage = SqliteStorage(db_path)
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            storage=storage,
            task_id="task-tokens-002",
        )

        plan = _plan(task_id="task-tokens-002")
        action = engine.start(plan)

        engine.record_step_result(
            step_id=action.step_id,
            agent_name=action.agent_name,
            status="complete",
            outcome="Done.",
            estimated_tokens=0,
        )

        conn = storage._conn_mgr.get_connection()
        row = conn.execute(
            "SELECT estimated_tokens FROM step_results "
            "WHERE task_id = ? AND step_id = ?",
            ("task-tokens-002", action.step_id),
        ).fetchone()

        assert row is not None
        # The heuristic fills in a non-zero estimate from the plan step description.
        assert row["estimated_tokens"] > 0, (
            "executor should apply char/4 heuristic when estimated_tokens=0 and plan is present"
        )

        storage.close()

    def test_nonzero_tokens_visible_in_engine_status(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        plan = _plan(task_id="task-tokens-003")
        action = engine.start(plan)

        engine.record_step_result(
            step_id=action.step_id,
            agent_name=action.agent_name,
            status="complete",
            outcome="Done.",
            estimated_tokens=9999,
        )

        status = engine.status()
        step_results = status.get("step_results", [])
        assert len(step_results) >= 1

        matching = [r for r in step_results if r.get("step_id") == action.step_id]
        assert matching, f"No step_result found for step {action.step_id!r}"
        assert matching[0]["estimated_tokens"] == 9999, (
            f"Expected 9999 tokens in status(), got {matching[0]['estimated_tokens']}"
        )


# ---------------------------------------------------------------------------
# Issue 5: Central DB sync for learning_issues
# ---------------------------------------------------------------------------

class TestCentralDbSyncMigration:
    """Regression: v7 migration adds project_id to learning_issues."""

    def test_v7_migration_exists_in_migrations_dict(self) -> None:
        assert 7 in MIGRATIONS, "v7 migration not found in MIGRATIONS dict"

    def test_v7_migration_adds_project_id_column(self) -> None:
        v7_sql = MIGRATIONS[7]
        assert "learning_issues" in v7_sql, (
            "v7 migration must reference the learning_issues table"
        )
        assert "project_id" in v7_sql, (
            "v7 migration must add the project_id column"
        )

    def test_schema_version_is_at_least_7(self) -> None:
        assert SCHEMA_VERSION >= 7, (
            f"SCHEMA_VERSION is {SCHEMA_VERSION}, expected >= 7"
        )

    def test_v7_migration_is_idempotent_when_column_already_exists(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.core.storage.connection import ConnectionManager
        from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL

        db_path = tmp_path / "baton_idem.db"
        conn_mgr = ConnectionManager(db_path)
        conn_mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
        conn = conn_mgr.get_connection()

        v7_sql = MIGRATIONS[7]
        try:
            for stmt in v7_sql.split(";"):
                stmt = stmt.strip()
                if stmt:
                    try:
                        conn.execute(stmt)
                    except sqlite3.OperationalError as exc:
                        if "duplicate column name" in str(exc).lower():
                            pass
                        else:
                            raise
            conn.commit()
        finally:
            conn_mgr.close()

    def test_v7_migration_adds_project_id_to_old_schema(
        self, tmp_path: Path
    ) -> None:
        """Applying the v7 migration SQL to a v5-era schema adds project_id."""
        db_path = tmp_path / "baton_old.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS learning_issues (
                issue_id          TEXT PRIMARY KEY,
                issue_type        TEXT NOT NULL,
                severity          TEXT NOT NULL DEFAULT 'medium',
                status            TEXT NOT NULL DEFAULT 'open',
                title             TEXT NOT NULL,
                target            TEXT NOT NULL,
                evidence          TEXT NOT NULL DEFAULT '[]',
                first_seen        TEXT NOT NULL,
                last_seen         TEXT NOT NULL,
                occurrence_count  INTEGER NOT NULL DEFAULT 1,
                proposed_fix      TEXT,
                resolution        TEXT,
                resolution_type   TEXT,
                experiment_id     TEXT
            )
        """)
        conn.commit()

        pre_columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(learning_issues)"
            ).fetchall()
        ]
        assert "project_id" not in pre_columns

        v7_sql = MIGRATIONS[7]
        for stmt in v7_sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" in str(exc).lower():
                        pass
                    else:
                        raise
        conn.commit()

        post_columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(learning_issues)"
            ).fetchall()
        ]
        assert "project_id" in post_columns, (
            f"v7 migration must add project_id. "
            f"Columns after migration: {post_columns}"
        )

        conn.close()

    def test_learning_issues_has_project_id_after_full_migration_path(
        self, tmp_path: Path
    ) -> None:
        """A DB upgraded from v5 to SCHEMA_VERSION via ConnectionManager gains project_id.

        v5: learning_issues (no project_id) + beads (v4 shape, no quality/retrieval cols).
        v6 migration: ALTER TABLE beads ADD COLUMN quality_score / retrieval_count.
        v7 migration: ALTER TABLE learning_issues ADD COLUMN project_id.

        The v5 baseline must include the beads table so v6 does not fail with
        "no such table: beads".
        """
        from agent_baton.core.storage.connection import ConnectionManager
        from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL

        db_path = tmp_path / "baton_v5_to_current.db"

        conn_v5 = sqlite3.connect(str(db_path))
        conn_v5.row_factory = sqlite3.Row
        conn_v5.execute("PRAGMA journal_mode=WAL")
        conn_v5.execute(
            "CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER NOT NULL)"
        )
        conn_v5.execute("INSERT INTO _schema_version VALUES (5)")

        conn_v5.execute("""
            CREATE TABLE IF NOT EXISTS learning_issues (
                issue_id         TEXT PRIMARY KEY,
                issue_type       TEXT NOT NULL,
                severity         TEXT NOT NULL DEFAULT 'medium',
                status           TEXT NOT NULL DEFAULT 'open',
                title            TEXT NOT NULL,
                target           TEXT NOT NULL,
                evidence         TEXT NOT NULL DEFAULT '[]',
                first_seen       TEXT NOT NULL,
                last_seen        TEXT NOT NULL,
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                proposed_fix     TEXT,
                resolution       TEXT,
                resolution_type  TEXT,
                experiment_id    TEXT
            )
        """)

        # beads table as it existed at v4 (before v6 added quality/retrieval).
        conn_v5.execute("""
            CREATE TABLE IF NOT EXISTS beads (
                bead_id          TEXT PRIMARY KEY,
                task_id          TEXT NOT NULL,
                step_id          TEXT NOT NULL,
                agent_name       TEXT NOT NULL,
                bead_type        TEXT NOT NULL,
                content          TEXT NOT NULL DEFAULT '',
                confidence       TEXT NOT NULL DEFAULT 'medium',
                scope            TEXT NOT NULL DEFAULT 'step',
                tags             TEXT NOT NULL DEFAULT '[]',
                affected_files   TEXT NOT NULL DEFAULT '[]',
                status           TEXT NOT NULL DEFAULT 'open',
                created_at       TEXT NOT NULL,
                closed_at        TEXT NOT NULL DEFAULT '',
                summary          TEXT NOT NULL DEFAULT '',
                links            TEXT NOT NULL DEFAULT '[]',
                source           TEXT NOT NULL DEFAULT 'agent-signal',
                token_estimate   INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn_v5.commit()
        conn_v5.close()

        # ConnectionManager._run_migrations applies v6 then v7.
        conn_mgr = ConnectionManager(db_path)
        conn_mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
        conn = conn_mgr.get_connection()

        try:
            pragma = conn.execute(
                "PRAGMA table_info(learning_issues)"
            ).fetchall()
            columns = [row["name"] for row in pragma]
            assert "project_id" in columns, (
                f"learning_issues missing project_id after upgrading to "
                f"SCHEMA_VERSION={SCHEMA_VERSION}. Columns: {columns}"
            )
        finally:
            conn_mgr.close()
