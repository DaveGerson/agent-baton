"""Tests for the five new subcommands added to ``baton query``.

Subcommands covered:
  plans           — list plans with phase/step counts
  phase-status    — per-phase breakdown for a task
  forge-sessions  — PMO forge sessions
  stalled         — running executions with no recent update
  portfolio       — cross-project status counts

All tests use tmp_path SQLite databases and the QueryEngine API directly
(plus CLI handler invocations for integration coverage).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_baton.core.storage.queries import QueryEngine, open_query_engine
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import (
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_plan(task_id: str, summary: str = "Test plan", risk: str = "LOW") -> MachinePlan:
    step = PlanStep(
        step_id=f"{task_id}-s1",
        agent_name="backend-engineer--python",
        task_description="Implement feature",
        model="sonnet",
        depends_on=[],
        deliverables=[],
        allowed_paths=["agent_baton/"],
        blocked_paths=[],
        context_files=[],
    )
    gate = PlanGate(gate_type="pytest", command="pytest", description="Run tests", fail_on=[])
    phase = PlanPhase(
        phase_id=1,
        name="Implementation",
        steps=[step],
        approval_required=False,
        gate=gate,
    )
    return MachinePlan(
        task_id=task_id,
        task_summary=summary,
        risk_level=risk,
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[phase],
        shared_context="",
        created_at=_now(),
    )


def _make_state(
    task_id: str,
    status: str = "complete",
    plan: MachinePlan | None = None,
    step_results: list[StepResult] | None = None,
    gate_results: list[GateResult] | None = None,
) -> ExecutionState:
    if plan is None:
        plan = _make_plan(task_id)
    now = _now()
    return ExecutionState(
        task_id=task_id,
        plan=plan,
        current_phase=1,
        current_step_index=0,
        status=status,
        step_results=step_results or [],
        gate_results=gate_results or [],
        started_at=now,
        completed_at=now if status == "complete" else "",
    )


def _make_step_result(
    task_id: str,
    step_id: str,
    agent: str = "backend-engineer--python",
    status: str = "complete",
    tokens: int = 1000,
) -> StepResult:
    return StepResult(
        step_id=step_id,
        agent_name=agent,
        status=status,
        outcome="Done",
        files_changed=[],
        commit_hash="",
        estimated_tokens=tokens,
        duration_seconds=5.0,
        retries=0,
        error="" if status == "complete" else "Something failed",
        completed_at=_now(),
    )


def _persist(db_path: Path, state: ExecutionState) -> None:
    """Persist an ExecutionState via SqliteStorage."""
    store = SqliteStorage(db_path)
    store.save_execution(state)
    store.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "baton.db"


@pytest.fixture
def engine(db_path: Path) -> QueryEngine:
    qe = QueryEngine(db_path)
    yield qe
    qe.close()


@pytest.fixture
def populated_db(db_path: Path) -> Path:
    """Return a db_path with two plans persisted."""
    _persist(db_path, _make_state("task-001", status="complete"))
    _persist(db_path, _make_state("task-002", status="running"))
    return db_path


# ---------------------------------------------------------------------------
# plans_list
# ---------------------------------------------------------------------------


class TestPlansList:
    def test_empty_db_returns_empty_list(self, engine: QueryEngine) -> None:
        assert engine.plans_list() == []

    def test_returns_one_row_per_plan(self, db_path: Path, engine: QueryEngine) -> None:
        _persist(db_path, _make_state("plan-a"))
        _persist(db_path, _make_state("plan-b"))
        rows = engine.plans_list()
        assert len(rows) == 2

    def test_row_has_expected_keys(self, db_path: Path, engine: QueryEngine) -> None:
        _persist(db_path, _make_state("plan-a"))
        rows = engine.plans_list()
        assert len(rows) == 1
        row = rows[0]
        assert "task_id" in row
        assert "summary" in row
        assert "risk_level" in row
        assert "phase_count" in row
        assert "step_count" in row
        assert "created_at" in row

    def test_phase_and_step_counts_correct(self, db_path: Path, engine: QueryEngine) -> None:
        plan = _make_plan("plan-x")
        # Our helper makes 1 phase with 1 step
        _persist(db_path, _make_state("plan-x", plan=plan))
        rows = engine.plans_list()
        assert len(rows) == 1
        row = rows[0]
        assert row["phase_count"] == 1
        assert row["step_count"] == 1

    def test_limit_respected(self, db_path: Path, engine: QueryEngine) -> None:
        for i in range(5):
            _persist(db_path, _make_state(f"plan-{i}"))
        rows = engine.plans_list(limit=3)
        assert len(rows) == 3

    def test_sorted_by_created_at_desc(self, db_path: Path, engine: QueryEngine) -> None:
        for i in range(3):
            _persist(db_path, _make_state(f"plan-{i}"))
        rows = engine.plans_list()
        dates = [r["created_at"] for r in rows]
        assert dates == sorted(dates, reverse=True)

    def test_days_filter_excludes_old(self, db_path: Path, engine: QueryEngine) -> None:
        # Inject a plan with a very old created_at directly via SQL
        qe = QueryEngine(db_path)
        qe.close()
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # First persist a normal plan so the schema is initialised
        _persist(db_path, _make_state("new-plan"))
        # Manually insert an old plan
        conn.execute(
            "INSERT INTO executions (task_id, status, started_at, created_at, updated_at) "
            "VALUES (?, 'complete', '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z')",
            ("old-plan",),
        )
        conn.execute(
            "INSERT INTO plans (task_id, task_summary, risk_level, budget_tier, "
            "execution_mode, git_strategy, plan_markdown, created_at) "
            "VALUES (?, 'Old plan', 'LOW', 'standard', 'phased', 'commit-per-agent', '', '2020-01-01T00:00:00Z')",
            ("old-plan",),
        )
        conn.commit()
        conn.close()

        rows = engine.plans_list(days=7)
        task_ids = [r["task_id"] for r in rows]
        assert "new-plan" in task_ids
        assert "old-plan" not in task_ids


# ---------------------------------------------------------------------------
# phase_status
# ---------------------------------------------------------------------------


class TestPhaseStatus:
    def test_unknown_task_returns_empty(self, engine: QueryEngine) -> None:
        assert engine.phase_status("no-such-task") == []

    def test_returns_one_row_per_phase(self, db_path: Path, engine: QueryEngine) -> None:
        _persist(db_path, _make_state("t1"))
        rows = engine.phase_status("t1")
        assert len(rows) == 1

    def test_row_has_expected_keys(self, db_path: Path, engine: QueryEngine) -> None:
        _persist(db_path, _make_state("t1"))
        row = engine.phase_status("t1")[0]
        for key in ("phase_id", "phase_name", "steps_completed", "steps_total",
                    "gate_type", "gate_passed", "is_current"):
            assert key in row, f"missing key: {key}"

    def test_gate_pending_when_no_gate_result(self, db_path: Path, engine: QueryEngine) -> None:
        _persist(db_path, _make_state("t1"))
        row = engine.phase_status("t1")[0]
        assert row["gate_passed"] == "pending"

    def test_gate_passed_after_pass_result(self, db_path: Path, engine: QueryEngine) -> None:
        gate = GateResult(phase_id=1, gate_type="pytest", passed=True, output="ok")
        state = _make_state("t1", gate_results=[gate])
        _persist(db_path, state)
        row = engine.phase_status("t1")[0]
        assert row["gate_passed"] == "passed"

    def test_gate_failed_after_fail_result(self, db_path: Path, engine: QueryEngine) -> None:
        gate = GateResult(phase_id=1, gate_type="pytest", passed=False, output="fail")
        state = _make_state("t1", gate_results=[gate])
        _persist(db_path, state)
        row = engine.phase_status("t1")[0]
        assert row["gate_passed"] == "failed"

    def test_steps_completed_count(self, db_path: Path, engine: QueryEngine) -> None:
        sr = _make_step_result("t1", "t1-s1")
        state = _make_state("t1", step_results=[sr])
        _persist(db_path, state)
        row = engine.phase_status("t1")[0]
        assert row["steps_completed"] == 1
        assert row["steps_total"] == 1

    def test_is_current_marks_current_phase(self, db_path: Path, engine: QueryEngine) -> None:
        _persist(db_path, _make_state("t1", status="running"))
        rows = engine.phase_status("t1")
        # current_phase=1 in our helper state
        current_rows = [r for r in rows if r["is_current"] == ">"]
        assert len(current_rows) == 1
        assert current_rows[0]["phase_id"] == 1


# ---------------------------------------------------------------------------
# forge_sessions
# ---------------------------------------------------------------------------


class TestForgeSessions:
    def test_empty_when_no_table(self, engine: QueryEngine) -> None:
        """forge_sessions returns [] gracefully when the table doesn't exist in local DB."""
        # Local baton.db does NOT have forge_sessions table by default
        result = engine.forge_sessions()
        assert result == []

    def test_returns_rows_from_central_db(self, tmp_path: Path) -> None:
        """forge_sessions returns rows when querying central.db."""
        from agent_baton.core.storage.central import CentralStore

        db_path = tmp_path / "central.db"
        store = CentralStore(db_path)
        store._conn().execute(
            "INSERT INTO forge_sessions (session_id, project_id, title, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("sess-1", "proj-a", "My Forge", "active", "2026-01-01T00:00:00Z"),
        )
        store._conn().commit()
        store.close()

        engine = QueryEngine(db_path)
        rows = engine.forge_sessions()
        engine.close()
        assert len(rows) == 1
        assert rows[0]["session_id"] == "sess-1"
        assert rows[0]["project_id"] == "proj-a"
        assert rows[0]["status"] == "active"

    def test_limit_respected(self, tmp_path: Path) -> None:
        from agent_baton.core.storage.central import CentralStore

        db_path = tmp_path / "central.db"
        store = CentralStore(db_path)
        for i in range(5):
            store._conn().execute(
                "INSERT INTO forge_sessions (session_id, project_id, title, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"sess-{i}", "proj-a", f"Session {i}", "active", f"2026-01-0{i + 1}T00:00:00Z"),
            )
        store._conn().commit()
        store.close()

        engine = QueryEngine(db_path)
        rows = engine.forge_sessions(limit=3)
        engine.close()
        assert len(rows) == 3

    def test_sorted_by_created_at_desc(self, tmp_path: Path) -> None:
        from agent_baton.core.storage.central import CentralStore

        db_path = tmp_path / "central.db"
        store = CentralStore(db_path)
        for i in range(3):
            store._conn().execute(
                "INSERT INTO forge_sessions (session_id, project_id, title, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"sess-{i}", "proj-a", f"Session {i}", "active", f"2026-01-0{i + 1}T00:00:00Z"),
            )
        store._conn().commit()
        store.close()

        engine = QueryEngine(db_path)
        rows = engine.forge_sessions()
        engine.close()
        dates = [r["created_at"] for r in rows]
        assert dates == sorted(dates, reverse=True)


# ---------------------------------------------------------------------------
# stalled_executions
# ---------------------------------------------------------------------------


class TestStalledExecutions:
    def test_empty_db_returns_empty(self, engine: QueryEngine) -> None:
        assert engine.stalled_executions() == []

    def test_complete_task_not_returned(self, db_path: Path, engine: QueryEngine) -> None:
        _persist(db_path, _make_state("t1", status="complete"))
        assert engine.stalled_executions() == []

    def test_recently_updated_running_not_returned(
        self, db_path: Path, engine: QueryEngine
    ) -> None:
        """A running execution updated just now is not stalled."""
        _persist(db_path, _make_state("t1", status="running"))
        # updated_at defaults to now — should not appear
        rows = engine.stalled_executions(hours=24)
        assert rows == []

    def test_old_running_execution_is_returned(self, db_path: Path, engine: QueryEngine) -> None:
        """A running execution whose updated_at is very old is stalled."""
        _persist(db_path, _make_state("t1", status="running"))
        # Backdate updated_at to simulate staleness
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE executions SET updated_at = '2020-01-01T00:00:00Z' WHERE task_id = 't1'"
        )
        conn.commit()
        conn.close()

        rows = engine.stalled_executions(hours=1)
        assert len(rows) == 1
        assert rows[0]["task_id"] == "t1"

    def test_row_has_expected_keys(self, db_path: Path, engine: QueryEngine) -> None:
        _persist(db_path, _make_state("t1", status="running"))
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE executions SET updated_at = '2020-01-01T00:00:00Z' WHERE task_id = 't1'"
        )
        conn.commit()
        conn.close()

        rows = engine.stalled_executions(hours=1)
        assert len(rows) == 1
        row = rows[0]
        for key in ("task_id", "status", "current_phase", "started_at", "updated_at",
                    "hours_stalled"):
            assert key in row, f"missing key: {key}"

    def test_hours_threshold_respected(self, db_path: Path, engine: QueryEngine) -> None:
        """Threshold=1h does not match an execution stalled for <1h."""
        _persist(db_path, _make_state("t1", status="running"))
        # updated_at is 'now' — not stalled
        rows = engine.stalled_executions(hours=1)
        assert rows == []

    def test_sorted_by_hours_stalled_desc(self, db_path: Path, engine: QueryEngine) -> None:
        for task_id, old_date in [
            ("t-oldest", "2019-01-01T00:00:00Z"),
            ("t-old", "2020-01-01T00:00:00Z"),
            ("t-less-old", "2022-01-01T00:00:00Z"),
        ]:
            _persist(db_path, _make_state(task_id, status="running"))
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE executions SET updated_at = '2019-01-01T00:00:00Z' WHERE task_id = 't-oldest'")
        conn.execute("UPDATE executions SET updated_at = '2020-01-01T00:00:00Z' WHERE task_id = 't-old'")
        conn.execute("UPDATE executions SET updated_at = '2022-01-01T00:00:00Z' WHERE task_id = 't-less-old'")
        conn.commit()
        conn.close()

        rows = engine.stalled_executions(hours=1)
        assert rows[0]["task_id"] == "t-oldest"
        stalled_hours = [r["hours_stalled"] for r in rows]
        assert stalled_hours == sorted(stalled_hours, reverse=True)


# ---------------------------------------------------------------------------
# portfolio
# ---------------------------------------------------------------------------


class TestPortfolio:
    def test_local_db_returns_empty_gracefully(self, engine: QueryEngine) -> None:
        """Local baton.db lacks project_id column — portfolio returns []."""
        result = engine.portfolio()
        assert result == []

    def test_central_db_returns_rows(self, tmp_path: Path) -> None:
        from agent_baton.core.storage.central import CentralStore

        db_path = tmp_path / "central.db"
        store = CentralStore(db_path)
        conn = store._conn()
        conn.execute(
            "INSERT INTO executions (project_id, task_id, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("proj-a", "t1", "complete", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO executions (project_id, task_id, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("proj-a", "t2", "running", "2026-01-02T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO executions (project_id, task_id, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("proj-b", "t3", "complete", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        store.close()

        engine = QueryEngine(db_path)
        rows = engine.portfolio()
        engine.close()

        assert len(rows) >= 3  # at least one row per (project_id, status) group
        project_ids = {r["project_id"] for r in rows}
        assert "proj-a" in project_ids
        assert "proj-b" in project_ids

    def test_counts_per_project_status(self, tmp_path: Path) -> None:
        from agent_baton.core.storage.central import CentralStore

        db_path = tmp_path / "central.db"
        store = CentralStore(db_path)
        conn = store._conn()
        for i in range(3):
            conn.execute(
                "INSERT INTO executions (project_id, task_id, status, started_at) "
                "VALUES (?, ?, ?, ?)",
                ("proj-x", f"t{i}", "complete", "2026-01-01T00:00:00Z"),
            )
        conn.commit()
        store.close()

        engine = QueryEngine(db_path)
        rows = engine.portfolio()
        engine.close()

        proj_x_rows = [r for r in rows if r["project_id"] == "proj-x" and r["status"] == "complete"]
        assert len(proj_x_rows) == 1
        assert proj_x_rows[0]["count"] == 3


# ---------------------------------------------------------------------------
# CLI handler integration tests (observe/query.py subcommands)
# ---------------------------------------------------------------------------


def _invoke_query(
    args_list: list[str],
    db_path: Path,
    capsys: pytest.CaptureFixture,
    *,
    central: bool = False,
) -> tuple[str, str]:
    """Invoke the ``baton query`` handler directly and return (stdout, stderr)."""
    from agent_baton.cli.commands.observe.query import handler, register

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register(sub)

    extra = ["--central"] if central else ["--db", str(db_path)]
    args = parser.parse_args(["query"] + args_list + extra)
    handler(args)
    captured = capsys.readouterr()
    return captured.out, captured.err


class TestQueryCLIPlans:
    def test_plans_empty(self, db_path: Path, capsys: pytest.CaptureFixture) -> None:
        out, err = _invoke_query(["plans"], db_path, capsys)
        assert "Traceback" not in err
        assert "No plans found" in out

    def test_plans_with_data(self, db_path: Path, capsys: pytest.CaptureFixture) -> None:
        _persist(db_path, _make_state("t1"))
        out, err = _invoke_query(["plans"], db_path, capsys)
        assert "Traceback" not in err
        assert "t1" in out

    def test_plans_json_format(self, db_path: Path, capsys: pytest.CaptureFixture) -> None:
        _persist(db_path, _make_state("t1"))
        out, err = _invoke_query(["plans", "--format", "json"], db_path, capsys)
        assert err == ""
        parsed = json.loads(out.strip())
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["task_id"] == "t1"

    def test_plans_csv_format(self, db_path: Path, capsys: pytest.CaptureFixture) -> None:
        _persist(db_path, _make_state("t1"))
        out, err = _invoke_query(["plans", "--format", "csv"], db_path, capsys)
        assert err == ""
        lines = [l for l in out.splitlines() if l]
        # CSV header + at least one data row
        assert len(lines) >= 2

    def test_plans_limit_flag(self, db_path: Path, capsys: pytest.CaptureFixture) -> None:
        for i in range(5):
            _persist(db_path, _make_state(f"t{i}"))
        out, err = _invoke_query(["plans", "--limit", "2"], db_path, capsys)
        assert "Traceback" not in err
        # Table output; count task_id occurrences (one per row)
        rows_with_t = sum(1 for line in out.splitlines() if line.startswith("t"))
        assert rows_with_t == 2


class TestQueryCLIPhaseStatus:
    def test_requires_task_id(self, db_path: Path, capsys: pytest.CaptureFixture) -> None:
        out, err = _invoke_query(["phase-status"], db_path, capsys)
        assert "phase-status requires" in err or "phase-status requires" in out

    def test_unknown_task_id(self, db_path: Path, capsys: pytest.CaptureFixture) -> None:
        out, err = _invoke_query(["phase-status", "no-such-id"], db_path, capsys)
        assert "Traceback" not in err
        assert "not found" in out or "No phase data" in out

    def test_known_task_id(self, db_path: Path, capsys: pytest.CaptureFixture) -> None:
        _persist(db_path, _make_state("t1"))
        out, err = _invoke_query(["phase-status", "t1"], db_path, capsys)
        assert "Traceback" not in err
        assert "Phase Status" in out or "phase_id" in out.lower() or "PHASE_ID" in out

    def test_phase_status_json(self, db_path: Path, capsys: pytest.CaptureFixture) -> None:
        _persist(db_path, _make_state("t1"))
        out, err = _invoke_query(["phase-status", "t1", "--format", "json"], db_path, capsys)
        assert err == ""
        parsed = json.loads(out.strip())
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["phase_id"] == 1


class TestQueryCLIForgeSessions:
    def test_no_sessions_prints_hint(self, db_path: Path, capsys: pytest.CaptureFixture) -> None:
        out, err = _invoke_query(["forge-sessions"], db_path, capsys)
        assert "Traceback" not in err
        # Should suggest using --central
        assert "central" in out.lower() or "No forge sessions" in out

    def test_forge_sessions_central(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        from agent_baton.core.storage.central import CentralStore

        db_path = tmp_path / "central.db"
        store = CentralStore(db_path)
        store._conn().execute(
            "INSERT INTO forge_sessions (session_id, project_id, title, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("s1", "proj-a", "My Session", "active", "2026-01-01T00:00:00Z"),
        )
        store._conn().commit()
        store.close()

        from agent_baton.cli.commands.observe.query import handler, register

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        register(sub)
        args = parser.parse_args(["query", "forge-sessions", "--db", str(db_path)])
        handler(args)
        out, err = capsys.readouterr()
        assert "Traceback" not in err
        assert "s1" in out


class TestQueryCLIStalled:
    def test_no_stalled_prints_message(self, db_path: Path, capsys: pytest.CaptureFixture) -> None:
        out, err = _invoke_query(["stalled"], db_path, capsys)
        assert "Traceback" not in err
        assert "No stalled" in out

    def test_stalled_task_appears(self, db_path: Path, capsys: pytest.CaptureFixture) -> None:
        _persist(db_path, _make_state("t1", status="running"))
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE executions SET updated_at = '2020-01-01T00:00:00Z' WHERE task_id = 't1'"
        )
        conn.commit()
        conn.close()
        out, err = _invoke_query(["stalled", "--hours", "1"], db_path, capsys)
        assert "Traceback" not in err
        assert "t1" in out

    def test_stalled_json_format(self, db_path: Path, capsys: pytest.CaptureFixture) -> None:
        _persist(db_path, _make_state("t1", status="running"))
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE executions SET updated_at = '2020-01-01T00:00:00Z' WHERE task_id = 't1'"
        )
        conn.commit()
        conn.close()
        out, err = _invoke_query(["stalled", "--hours", "1", "--format", "json"], db_path, capsys)
        assert err == ""
        parsed = json.loads(out.strip())
        assert isinstance(parsed, list)
        assert parsed[0]["task_id"] == "t1"


class TestQueryCLIPortfolio:
    def test_no_data_prints_hint(self, db_path: Path, capsys: pytest.CaptureFixture) -> None:
        out, err = _invoke_query(["portfolio"], db_path, capsys)
        assert "Traceback" not in err
        # Local db has no project_id column — should print a helpful message
        assert "No portfolio data" in out or "central" in out.lower()

    def test_portfolio_central_with_data(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        from agent_baton.core.storage.central import CentralStore

        db_path = tmp_path / "central.db"
        store = CentralStore(db_path)
        conn = store._conn()
        conn.execute(
            "INSERT INTO executions (project_id, task_id, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("proj-a", "t1", "complete", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        store.close()

        from agent_baton.cli.commands.observe.query import handler, register

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        register(sub)
        args = parser.parse_args(["query", "portfolio", "--db", str(db_path)])
        handler(args)
        out, err = capsys.readouterr()
        assert "Traceback" not in err
        assert "proj-a" in out

    def test_portfolio_json_format(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        from agent_baton.core.storage.central import CentralStore

        db_path = tmp_path / "central.db"
        store = CentralStore(db_path)
        conn = store._conn()
        conn.execute(
            "INSERT INTO executions (project_id, task_id, status, started_at) "
            "VALUES (?, ?, ?, ?)",
            ("proj-a", "t1", "complete", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        store.close()

        from agent_baton.cli.commands.observe.query import handler, register

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        register(sub)
        args = parser.parse_args(["query", "portfolio", "--db", str(db_path), "--format", "json"])
        handler(args)
        out, err = capsys.readouterr()
        assert err == ""
        parsed = json.loads(out.strip())
        assert isinstance(parsed, list)
        proj_ids = {r["project_id"] for r in parsed}
        assert "proj-a" in proj_ids


# ---------------------------------------------------------------------------
# Registration sanity checks
# ---------------------------------------------------------------------------


class TestNewSubcommandsRegistered:
    def test_choices_include_new_subcommands(self) -> None:
        from agent_baton.cli.commands.observe.query import register

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        register(sub)

        for name in ("plans", "phase-status", "forge-sessions", "stalled", "portfolio"):
            args = parser.parse_args(["query", name])
            assert args.subcommand == name, f"'{name}' not registered as a valid subcommand"

    def test_hours_flag_registered(self) -> None:
        from agent_baton.cli.commands.observe.query import register

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        register(sub)
        args = parser.parse_args(["query", "stalled", "--hours", "48"])
        assert args.hours == 48
