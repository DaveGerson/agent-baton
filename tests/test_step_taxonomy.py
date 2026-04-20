"""Tests for the Step Execution Taxonomy feature (Layer 1).

Coverage:
1. Schema & Persistence
   - Migration v9 applies to existing v8 database
   - Fresh database has step_type/command columns in plan_steps
   - Fresh database has step_type column in step_results
   - Central schema mirrors project schema (step_type + command in plan_steps,
     step_type in step_results)
   - SQLite round-trip: plan_step with step_type/command persists and reloads
   - SQLite round-trip: step_result with step_type persists and reloads

2. Backward Compatibility
   - PlanStep.from_dict without step_type defaults to "developing"
   - PlanStep.from_dict without command defaults to ""
   - StepResult.from_dict without step_type defaults to "developing"
   - PlanStep.to_dict always serialises step_type
   - PlanStep.to_dict only serialises command when non-empty
   - Old plan JSON loaded by MachinePlan.from_dict produces steps with
     step_type "developing"
   - Execution state without step_type on step_results loads cleanly

3. Execution Path Routing
   - _dispatch_action returns command (no delegation_prompt) for automation steps
   - _dispatch_action returns consultation prompt for consulting steps
   - _dispatch_action returns task prompt for task steps
   - _dispatch_action returns delegation prompt for developing steps
   - _dispatch_action returns delegation prompt for unknown step types
   - Automation action carries step_type="automation" and command

4. Automation Execution (engine guards only — worker subprocess tests below)
   - record_step_result with agent_name="automation" does not parse bead signals
   - record_step_result with agent_name="automation" does not parse knowledge gaps
   - Automation step result carries step_type="automation" in state

5. Prompt Builders
   - build_consultation_prompt produces shorter output than build_delegation_prompt
   - build_consultation_prompt excludes shared context section
   - build_consultation_prompt includes task description verbatim
   - build_task_prompt produces shorter output than build_delegation_prompt
   - build_task_prompt passes task_description verbatim
   - build_task_prompt excludes knowledge chain sections

6. CLI Output
   - _print_action includes Type: line for standard DISPATCH
   - _print_action shows Command block for automation DISPATCH
   - _print_action omits Agent/Model lines for automation DISPATCH
   - _print_action for developing step has existing fields unchanged

7. End-to-End Integration
   - Mixed step-type plan (automation, planning, developing, task) runs to COMPLETE
   - Automation steps are dispatched without delegation_prompt
   - Step results record correct step_type after full loop
   - 0 estimated_tokens for automation steps (no LLM)

8. Worker (daemon mode)
   - TaskWorker routes automation actions to _run_automation (not scheduler)
   - TaskWorker records automation result with agent_name="automation"
   - Automation command success: status="complete", stdout as outcome
   - Automation command failure: status="failed", stderr in error field
   - Automation command timeout records timeout error message
   - Empty command runs but can fail gracefully

9. Planner Step Type Assignment
   - architect agent gets step_type="planning"
   - code-reviewer agent gets step_type="reviewing"
   - test-engineer agent gets step_type="testing"
   - test-engineer with "create" keyword gets step_type="developing"
   - task-runner agent gets step_type="task"
   - unknown agent gets step_type="developing"
   - backend-engineer gets step_type="developing"
"""
from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_baton.cli.commands.execution.execute import _print_action
from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.planner import _step_type_for_agent
from agent_baton.core.events.bus import EventBus
from agent_baton.core.runtime.launcher import DryRunLauncher, LaunchResult
from agent_baton.core.runtime.worker import TaskWorker
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import (
    ActionType,
    ExecutionAction,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Shared test factories
# ---------------------------------------------------------------------------


def _step(
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task: str = "Implement feature X",
    step_type: str = "developing",
    command: str = "",
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task,
        step_type=step_type,
        command=command,
    )


def _phase(
    phase_id: int = 1,
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
    task_id: str = "task-tax-001",
    task_summary: str = "Taxonomy test task",
    phases: list[PlanPhase] | None = None,
    shared_context: str = "## Shared Context\n\nProject background here.",
) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary=task_summary,
        phases=phases or [_phase()],
        shared_context=shared_context,
        risk_level="LOW",
    )


def _engine(tmp_path: Path, task_id: str | None = None) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path, task_id=task_id)


def _engine_with_sqlite(
    tmp_path: Path, task_id: str = "task-tax-001"
) -> tuple[ExecutionEngine, SqliteStorage]:
    storage = SqliteStorage(tmp_path / "baton.db")
    engine = ExecutionEngine(
        team_context_root=tmp_path,
        bus=EventBus(),
        storage=storage,
        task_id=task_id,
    )
    return engine, storage


def _capture_print_action(action_dict: dict) -> str:
    """Capture stdout from _print_action and return it."""
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        _print_action(action_dict)
    finally:
        sys.stdout = old_stdout
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. Schema & Persistence
# ---------------------------------------------------------------------------


class TestSchemaAndPersistence:
    """Migration v9, DDL columns, and SQLite round-trips."""

    def test_schema_version_is_9(self) -> None:
        from agent_baton.core.storage.schema import SCHEMA_VERSION
        assert SCHEMA_VERSION >= 9

    def test_fresh_project_db_has_step_type_on_plan_steps(self, tmp_path: Path) -> None:
        storage = SqliteStorage(tmp_path / "baton.db")
        # Trigger schema initialization by opening the connection.
        conn = storage._conn_mgr.get_connection()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(plan_steps)")}
        assert "step_type" in cols
        assert "command" in cols

    def test_fresh_project_db_has_step_type_on_step_results(self, tmp_path: Path) -> None:
        storage = SqliteStorage(tmp_path / "baton.db")
        conn = storage._conn_mgr.get_connection()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(step_results)")}
        assert "step_type" in cols

    def test_migration_v9_adds_columns_to_existing_db(self, tmp_path: Path) -> None:
        """Simulate an existing v8 database and apply migration to v9."""
        from agent_baton.core.storage.schema import MIGRATIONS, PROJECT_SCHEMA_DDL
        db_path = tmp_path / "baton.db"

        # Build the schema up to v8 (omit step_type columns).
        # We create a v8-equivalent schema manually by stripping the new cols,
        # then running the v9 migration.
        conn = sqlite3.connect(str(db_path))
        try:
            # Create minimal schema matching v8 state (no step_type/command).
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER NOT NULL);
                INSERT INTO _schema_version VALUES (8);
                CREATE TABLE IF NOT EXISTS executions (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'running',
                    current_phase INTEGER NOT NULL DEFAULT 0,
                    current_step_index INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    pending_gaps TEXT NOT NULL DEFAULT '[]',
                    resolved_decisions TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS plans (
                    task_id TEXT PRIMARY KEY,
                    task_summary TEXT NOT NULL,
                    risk_level TEXT NOT NULL DEFAULT 'LOW',
                    budget_tier TEXT NOT NULL DEFAULT 'standard',
                    execution_mode TEXT NOT NULL DEFAULT 'phased',
                    git_strategy TEXT NOT NULL DEFAULT 'commit-per-agent',
                    shared_context TEXT NOT NULL DEFAULT '',
                    pattern_source TEXT,
                    plan_markdown TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    explicit_knowledge_packs TEXT NOT NULL DEFAULT '[]',
                    explicit_knowledge_docs TEXT NOT NULL DEFAULT '[]',
                    intervention_level TEXT NOT NULL DEFAULT 'low',
                    task_type TEXT
                );
                CREATE TABLE IF NOT EXISTS plan_phases (
                    task_id TEXT NOT NULL,
                    phase_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    approval_required INTEGER NOT NULL DEFAULT 0,
                    approval_description TEXT NOT NULL DEFAULT '',
                    gate_type TEXT,
                    gate_command TEXT,
                    gate_description TEXT,
                    gate_fail_on TEXT,
                    PRIMARY KEY (task_id, phase_id)
                );
                CREATE TABLE IF NOT EXISTS plan_steps (
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    phase_id INTEGER NOT NULL,
                    agent_name TEXT NOT NULL,
                    task_description TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT 'sonnet',
                    depends_on TEXT NOT NULL DEFAULT '[]',
                    deliverables TEXT NOT NULL DEFAULT '[]',
                    allowed_paths TEXT NOT NULL DEFAULT '[]',
                    blocked_paths TEXT NOT NULL DEFAULT '[]',
                    context_files TEXT NOT NULL DEFAULT '[]',
                    knowledge_attachments TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (task_id, step_id)
                );
                CREATE TABLE IF NOT EXISTS step_results (
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'complete',
                    outcome TEXT NOT NULL DEFAULT '',
                    files_changed TEXT NOT NULL DEFAULT '[]',
                    commit_hash TEXT NOT NULL DEFAULT '',
                    estimated_tokens INTEGER NOT NULL DEFAULT 0,
                    duration_seconds REAL NOT NULL DEFAULT 0.0,
                    retries INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    deviations TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (task_id, step_id)
                );
            """)
            conn.commit()
            # Confirm columns are absent pre-migration.
            cols_before = {row[1] for row in conn.execute("PRAGMA table_info(plan_steps)")}
            assert "step_type" not in cols_before
            assert "command" not in cols_before
            # Apply v9 migration DDL.
            conn.executescript(MIGRATIONS[9])
            cols_after = {row[1] for row in conn.execute("PRAGMA table_info(plan_steps)")}
            res_cols_after = {row[1] for row in conn.execute("PRAGMA table_info(step_results)")}
        finally:
            conn.close()
        assert "step_type" in cols_after
        assert "command" in cols_after
        assert "step_type" in res_cols_after

    def test_migration_v9_default_for_existing_rows(self, tmp_path: Path) -> None:
        """Existing plan_steps rows survive v9 migration with DEFAULT 'developing'."""
        from agent_baton.core.storage.schema import MIGRATIONS
        db_path = tmp_path / "baton.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript("""
                CREATE TABLE plan_steps (
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    phase_id INTEGER NOT NULL DEFAULT 1,
                    agent_name TEXT NOT NULL DEFAULT 'x',
                    PRIMARY KEY (task_id, step_id)
                );
                CREATE TABLE step_results (
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'complete',
                    PRIMARY KEY (task_id, step_id)
                );
                INSERT INTO plan_steps (task_id, step_id) VALUES ('t1', '1.1');
                INSERT INTO step_results (task_id, step_id) VALUES ('t1', '1.1');
            """)
            conn.executescript(MIGRATIONS[9])
            step_row = conn.execute(
                "SELECT step_type, command FROM plan_steps WHERE task_id='t1'"
            ).fetchone()
            result_row = conn.execute(
                "SELECT step_type FROM step_results WHERE task_id='t1'"
            ).fetchone()
        finally:
            conn.close()
        assert step_row[0] == "developing"
        assert step_row[1] == ""
        assert result_row[0] == "developing"

    def test_plan_step_sqlite_round_trip(self, tmp_path: Path) -> None:
        """plan_steps with step_type and command survive INSERT → load cycle."""
        engine, storage = _engine_with_sqlite(tmp_path, "task-rt-001")
        p = _plan(
            task_id="task-rt-001",
            phases=[
                _phase(
                    steps=[
                        _step("1.1", step_type="automation", command="echo hello"),
                        _step("1.2", step_type="planning"),
                    ]
                )
            ],
        )
        engine.start(p)
        # Reload state from SQLite and verify step_type/command preserved.
        loaded_state = storage.load_execution("task-rt-001")
        assert loaded_state is not None
        plan_steps = loaded_state.plan.all_steps
        step_11 = next(s for s in plan_steps if s.step_id == "1.1")
        step_12 = next(s for s in plan_steps if s.step_id == "1.2")
        assert step_11.step_type == "automation"
        assert step_11.command == "echo hello"
        assert step_12.step_type == "planning"
        assert step_12.command == ""

    def test_step_result_step_type_sqlite_round_trip(self, tmp_path: Path) -> None:
        """StepResult.step_type survives INSERT → load cycle through SQLite."""
        engine, storage = _engine_with_sqlite(tmp_path, "task-rt-002")
        p = _plan(task_id="task-rt-002", phases=[_phase(steps=[_step("1.1", step_type="testing")])])
        engine.start(p)
        engine.record_step_result(
            step_id="1.1",
            agent_name="test-engineer",
            status="complete",
            outcome="All tests pass.",
        )
        loaded = storage.load_execution("task-rt-002")
        assert loaded is not None
        result = loaded.get_step_result("1.1")
        assert result is not None
        assert result.step_type == "testing"

    def test_central_schema_plan_steps_has_step_type_and_command(self) -> None:
        """Central DDL mirrors project DDL: plan_steps has step_type and command."""
        from agent_baton.core.storage.schema import CENTRAL_SCHEMA_DDL
        # Both columns must appear in the central plan_steps CREATE TABLE block.
        assert "step_type" in CENTRAL_SCHEMA_DDL
        assert "command" in CENTRAL_SCHEMA_DDL

    def test_central_schema_step_results_has_step_type(self) -> None:
        """Central DDL mirrors project DDL: step_results has step_type."""
        from agent_baton.core.storage.schema import CENTRAL_SCHEMA_DDL
        # Check within context of the step_results section.
        # The word "step_type" appears twice (once per table); just assert presence.
        assert CENTRAL_SCHEMA_DDL.count("step_type") >= 2  # plan_steps + step_results


# ---------------------------------------------------------------------------
# 2. Backward Compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Old plans and states without step_type load with defaults."""

    def test_plan_step_from_dict_without_step_type_defaults_to_developing(self) -> None:
        data = {"step_id": "1.1", "agent_name": "backend-engineer", "task_description": "work"}
        step = PlanStep.from_dict(data)
        assert step.step_type == "developing"

    def test_plan_step_from_dict_without_command_defaults_empty(self) -> None:
        data = {"step_id": "1.1", "agent_name": "backend-engineer", "task_description": "work"}
        step = PlanStep.from_dict(data)
        assert step.command == ""

    def test_step_result_from_dict_without_step_type_defaults_to_developing(self) -> None:
        data = {
            "step_id": "1.1",
            "agent_name": "backend-engineer",
            "status": "complete",
            "outcome": "done",
        }
        result = StepResult.from_dict(data)
        assert result.step_type == "developing"

    def test_plan_step_to_dict_always_includes_step_type(self) -> None:
        step = PlanStep(step_id="1.1", agent_name="x", task_description="t")
        d = step.to_dict()
        assert "step_type" in d
        assert d["step_type"] == "developing"

    def test_plan_step_to_dict_omits_command_when_empty(self) -> None:
        step = PlanStep(step_id="1.1", agent_name="x", task_description="t", command="")
        d = step.to_dict()
        assert "command" not in d

    def test_plan_step_to_dict_includes_command_when_set(self) -> None:
        step = PlanStep(
            step_id="1.1", agent_name="x", task_description="t",
            step_type="automation", command="pytest"
        )
        d = step.to_dict()
        assert "command" in d
        assert d["command"] == "pytest"

    def test_old_plan_json_loads_steps_as_developing(self) -> None:
        """A plan dict without step_type on steps round-trips to 'developing'."""
        old_plan = {
            "task_id": "old-task-001",
            "task_summary": "Old plan",
            "phases": [
                {
                    "phase_id": 1,
                    "name": "Implement",
                    "steps": [
                        {
                            "step_id": "1.1",
                            "agent_name": "backend-engineer",
                            "task_description": "Build it",
                        }
                    ],
                }
            ],
        }
        plan = MachinePlan.from_dict(old_plan)
        assert plan.phases[0].steps[0].step_type == "developing"

    def test_old_execution_state_loads_results_as_developing(self) -> None:
        """StepResults without step_type in serialized state load as 'developing'."""
        step = PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="work")
        phase = PlanPhase(phase_id=1, name="Phase", steps=[step])
        plan = MachinePlan(task_id="legacy-001", task_summary="Legacy", phases=[phase])
        state_dict = {
            "task_id": "legacy-001",
            "plan": plan.to_dict(),
            "current_phase": 0,
            "current_step_index": 0,
            "status": "running",
            "step_results": [
                {
                    "step_id": "1.1",
                    "agent_name": "backend-engineer",
                    "status": "complete",
                    "outcome": "done",
                    # Intentionally omit step_type to simulate pre-v9 state
                }
            ],
            "gate_results": [],
            "approval_results": [],
            "feedback_results": [],
            "amendments": [],
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "",
            "pending_gaps": [],
            "resolved_decisions": [],
        }
        from agent_baton.models.execution import ExecutionState
        state = ExecutionState.from_dict(state_dict)
        assert state.get_step_result("1.1").step_type == "developing"

    def test_plan_with_mixed_old_new_steps(self) -> None:
        """Mix of steps with and without step_type all produce valid objects."""
        old_plan = {
            "task_id": "mixed-001",
            "task_summary": "Mixed",
            "phases": [
                {
                    "phase_id": 1,
                    "name": "Phase 1",
                    "steps": [
                        {"step_id": "1.1", "agent_name": "a", "task_description": "x"},
                        {"step_id": "1.2", "agent_name": "b", "task_description": "y", "step_type": "testing"},
                    ],
                }
            ],
        }
        plan = MachinePlan.from_dict(old_plan)
        steps = plan.phases[0].steps
        assert steps[0].step_type == "developing"
        assert steps[1].step_type == "testing"


# ---------------------------------------------------------------------------
# 3. Execution Path Routing
# ---------------------------------------------------------------------------


class TestExecutionPathRouting:
    """_dispatch_action returns the right action shape per step_type."""

    def test_automation_returns_command_not_prompt(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        step = _step("1.1", step_type="automation", command="pytest tests/")
        p = _plan(phases=[_phase(steps=[step])])
        engine.start(p)
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_type == "automation"
        assert action.command == "pytest tests/"
        # No LLM prompt for automation steps.
        assert action.delegation_prompt == ""

    def test_automation_action_has_no_agent(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        step = _step("1.1", step_type="automation", command="echo hello")
        p = _plan(phases=[_phase(steps=[step])])
        engine.start(p)
        action = engine.next_action()
        assert action.agent_name == ""

    def test_consulting_returns_consultation_prompt(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        step = _step("1.1", agent_name="architect", step_type="consulting",
                     task="Review the auth design decision.")
        p = _plan(phases=[_phase(steps=[step])])
        engine.start(p)
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.delegation_prompt != ""
        # Consultation prompt must include the task description.
        assert "Review the auth design decision." in action.delegation_prompt

    def test_task_returns_task_prompt(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        step = _step("1.1", agent_name="task-runner", step_type="task",
                     task="Format the output report as CSV.")
        p = _plan(phases=[_phase(steps=[step])])
        engine.start(p)
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.delegation_prompt != ""
        assert "Format the output report as CSV." in action.delegation_prompt

    def test_developing_returns_delegation_prompt(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        step = _step("1.1", step_type="developing")
        p = _plan(phases=[_phase(steps=[step])])
        engine.start(p)
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.delegation_prompt != ""
        # Full delegation prompt includes shared context section.
        assert "Shared Context" in action.delegation_prompt

    def test_unknown_step_type_falls_through_to_delegation(self, tmp_path: Path) -> None:
        """Unknown step_type must not crash — falls through to full delegation."""
        engine = _engine(tmp_path)
        step = _step("1.1", step_type="custom-unknown-type")
        p = _plan(phases=[_phase(steps=[step])])
        engine.start(p)
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.delegation_prompt != ""

    def test_planning_returns_delegation_prompt(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        step = _step("1.1", agent_name="architect", step_type="planning")
        p = _plan(phases=[_phase(steps=[step])])
        engine.start(p)
        action = engine.next_action()
        assert action.delegation_prompt != ""
        assert "Shared Context" in action.delegation_prompt

    def test_reviewing_returns_delegation_prompt(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        step = _step("1.1", agent_name="code-reviewer", step_type="reviewing")
        p = _plan(phases=[_phase(steps=[step])])
        engine.start(p)
        action = engine.next_action()
        assert action.delegation_prompt != ""

    def test_testing_returns_delegation_prompt(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        step = _step("1.1", agent_name="test-engineer", step_type="testing")
        p = _plan(phases=[_phase(steps=[step])])
        engine.start(p)
        action = engine.next_action()
        assert action.delegation_prompt != ""

    def test_action_carries_step_type_for_all_types(self, tmp_path: Path) -> None:
        """ExecutionAction.step_type is echoed from PlanStep for all types."""
        for st in ("developing", "planning", "testing", "reviewing", "consulting", "task"):
            engine = _engine(tmp_path / st)
            step = _step("1.1", step_type=st)
            p = _plan(phases=[_phase(steps=[step])])
            engine.start(p)
            action = engine.next_action()
            assert action.step_type == st, f"Expected step_type={st!r}, got {action.step_type!r}"


# ---------------------------------------------------------------------------
# 4. Automation Execution Guards (engine-level signal skipping)
# ---------------------------------------------------------------------------


class TestAutomationExecutionGuards:
    """The engine must NOT parse bead signals or knowledge gaps for automation steps."""

    def test_automation_step_does_not_write_bead_signals(self, tmp_path: Path) -> None:
        engine, storage = _engine_with_sqlite(tmp_path, "task-auto-bead-001")
        p = _plan(
            task_id="task-auto-bead-001",
            phases=[_phase(steps=[_step("1.1", step_type="automation", command="echo hi")])],
        )
        engine.start(p)
        engine.record_step_result(
            step_id="1.1",
            agent_name="automation",
            status="complete",
            outcome="BEAD_DISCOVERY: this should NOT be stored.",
        )
        # Check bead table directly — no beads should exist.
        conn = sqlite3.connect(str(tmp_path / "baton.db"))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM beads WHERE task_id='task-auto-bead-001'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 0, "Automation steps must not produce bead signals"

    def test_automation_step_does_not_process_knowledge_gap(self, tmp_path: Path) -> None:
        """Knowledge gap signals in automation output must be ignored."""
        engine, _ = _engine_with_sqlite(tmp_path, "task-auto-kg-001")
        p = _plan(
            task_id="task-auto-kg-001",
            phases=[_phase(steps=[_step("1.1", step_type="automation", command="echo hi")])],
        )
        engine.start(p)
        # record_step_result must complete without raising and without queuing a gap.
        engine.record_step_result(
            step_id="1.1",
            agent_name="automation",
            status="complete",
            outcome="KNOWLEDGE_GAP: missing deployment docs\nCONFIDENCE: low",
        )
        state = engine._load_state()
        assert len(state.pending_gaps) == 0, "Automation steps must not queue knowledge gaps"

    def test_automation_step_result_carries_correct_step_type(self, tmp_path: Path) -> None:
        engine, _ = _engine_with_sqlite(tmp_path, "task-auto-type-001")
        p = _plan(
            task_id="task-auto-type-001",
            phases=[_phase(steps=[_step("1.1", step_type="automation", command="true")])],
        )
        engine.start(p)
        engine.record_step_result(
            step_id="1.1",
            agent_name="automation",
            status="complete",
            outcome="",
        )
        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result is not None
        assert result.step_type == "automation"

    def test_automation_step_result_agent_name_is_automation(self, tmp_path: Path) -> None:
        """The engine accepts 'automation' as the agent_name for tracking."""
        engine, _ = _engine_with_sqlite(tmp_path, "task-auto-agent-001")
        p = _plan(
            task_id="task-auto-agent-001",
            phases=[_phase(steps=[_step("1.1", step_type="automation", command="true")])],
        )
        engine.start(p)
        engine.record_step_result(
            step_id="1.1",
            agent_name="automation",
            status="complete",
            outcome="exit 0",
        )
        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result is not None
        assert result.agent_name == "automation"


# ---------------------------------------------------------------------------
# 5. Prompt Builders
# ---------------------------------------------------------------------------


class TestPromptBuilders:
    """build_consultation_prompt and build_task_prompt properties."""

    def _dispatcher(self) -> PromptDispatcher:
        return PromptDispatcher()

    def test_consultation_prompt_smaller_than_delegation(self) -> None:
        dispatcher = self._dispatcher()
        step = _step("1.1", agent_name="architect", step_type="consulting",
                     task="Review auth approach.")
        consultation = dispatcher.build_consultation_prompt(
            step, task_summary="Build auth service"
        )
        delegation = dispatcher.build_delegation_prompt(
            step,
            shared_context="## Shared Context\n\nProject overview here.\n\n" * 20,
            task_summary="Build auth service",
        )
        assert len(consultation) < len(delegation), (
            f"Consultation prompt ({len(consultation)} chars) should be shorter than "
            f"delegation prompt ({len(delegation)} chars)"
        )

    def test_consultation_prompt_excludes_shared_context_section(self) -> None:
        dispatcher = self._dispatcher()
        step = _step("1.1", step_type="consulting", task="What should we use?")
        prompt = dispatcher.build_consultation_prompt(
            step,
            task_summary="Build it",
        )
        assert "## Shared Context" not in prompt

    def test_consultation_prompt_includes_task_description(self) -> None:
        dispatcher = self._dispatcher()
        step = _step("1.1", step_type="consulting", task="Decide between Postgres and MySQL.")
        prompt = dispatcher.build_consultation_prompt(step, task_summary="Store the data")
        assert "Decide between Postgres and MySQL." in prompt

    def test_consultation_prompt_includes_flag_context_when_provided(self) -> None:
        dispatcher = self._dispatcher()
        step = _step("1.1", step_type="consulting", task="Choose a caching strategy.")
        flag_ctx = "OPTIONS: Redis | Memcached\nRECOMMENDATION: Redis"
        prompt = dispatcher.build_consultation_prompt(step, flag_context=flag_ctx)
        assert "Redis" in prompt

    def test_consultation_prompt_includes_original_outcome_excerpt(self) -> None:
        dispatcher = self._dispatcher()
        step = _step("1.1", step_type="consulting", task="Review approach.")
        long_outcome = "X" * 3000 + "THE_UNIQUE_EXCERPT_MARKER"
        prompt = dispatcher.build_consultation_prompt(step, original_outcome=long_outcome)
        assert "THE_UNIQUE_EXCERPT_MARKER" in prompt

    def test_consultation_prompt_includes_resolution_instructions(self) -> None:
        dispatcher = self._dispatcher()
        step = _step("1.1", step_type="consulting", task="Advise.")
        prompt = dispatcher.build_consultation_prompt(step)
        assert "FLAG_RESOLVED" in prompt

    def test_task_prompt_smaller_than_delegation(self) -> None:
        dispatcher = self._dispatcher()
        step = _step("1.1", agent_name="task-runner", step_type="task",
                     task="Send a Slack notification with the result.")
        task_p = dispatcher.build_task_prompt(step, task_summary="Notify team")
        delegation_p = dispatcher.build_delegation_prompt(
            step,
            shared_context="## Shared Context\n\n" + "Background info.\n" * 20,
            task_summary="Notify team",
        )
        assert len(task_p) < len(delegation_p)

    def test_task_prompt_passes_task_description_verbatim(self) -> None:
        dispatcher = self._dispatcher()
        bespoke = "Step 1: call /api/notify\nStep 2: check response\nStep 3: log result"
        step = _step("1.1", step_type="task", task=bespoke)
        prompt = dispatcher.build_task_prompt(step, task_summary="Notify")
        assert bespoke in prompt

    def test_task_prompt_excludes_shared_context_section(self) -> None:
        dispatcher = self._dispatcher()
        step = _step("1.1", step_type="task", task="Do the thing.")
        prompt = dispatcher.build_task_prompt(step, task_summary="Context")
        assert "## Shared Context" not in prompt

    def test_task_prompt_excludes_knowledge_section(self) -> None:
        dispatcher = self._dispatcher()
        step = _step("1.1", step_type="task", task="Do it.")
        prompt = dispatcher.build_task_prompt(step, task_summary="Context")
        assert "## Knowledge Context" not in prompt
        assert "## Knowledge References" not in prompt

    def test_task_prompt_includes_task_runner_preamble(self) -> None:
        dispatcher = self._dispatcher()
        step = _step("1.1", step_type="task", task="Follow these steps.")
        prompt = dispatcher.build_task_prompt(step)
        assert "task runner" in prompt.lower()


# ---------------------------------------------------------------------------
# 6. CLI Output
# ---------------------------------------------------------------------------


class TestCLIOutput:
    """_print_action output format for step taxonomy fields."""

    def test_standard_dispatch_includes_type_line(self) -> None:
        action = {
            "action_type": "dispatch",
            "step_id": "1.1",
            "step_type": "developing",
            "agent_name": "backend-engineer",
            "agent_model": "sonnet",
            "message": "Dispatch agent.",
            "delegation_prompt": "Do the work.",
            "path_enforcement": "",
        }
        output = _capture_print_action(action)
        assert "Type:  developing" in output

    def test_automation_dispatch_shows_command_block(self) -> None:
        action = {
            "action_type": "dispatch",
            "step_id": "1.1",
            "step_type": "automation",
            "command": "pytest tests/",
            "message": "Execute automation step 1.1.",
        }
        output = _capture_print_action(action)
        assert "--- Command ---" in output
        assert "pytest tests/" in output
        assert "--- End Command ---" in output

    def test_automation_dispatch_shows_type_automation(self) -> None:
        action = {
            "action_type": "dispatch",
            "step_id": "2.3",
            "step_type": "automation",
            "command": "make build",
            "message": "Execute automation step 2.3.",
        }
        output = _capture_print_action(action)
        assert "Type:    automation" in output

    def test_automation_dispatch_omits_agent_and_model_lines(self) -> None:
        action = {
            "action_type": "dispatch",
            "step_id": "1.1",
            "step_type": "automation",
            "command": "echo hi",
            "message": "Run.",
        }
        output = _capture_print_action(action)
        # Agent: and Model: lines should not appear for automation steps.
        lines = output.splitlines()
        assert not any(line.strip().startswith("Agent:") for line in lines)
        assert not any(line.strip().startswith("Model:") for line in lines)

    def test_standard_dispatch_preserves_existing_fields(self) -> None:
        """Existing Agent/Model/Step fields must still appear for non-automation dispatch."""
        action = {
            "action_type": "dispatch",
            "step_id": "3.2",
            "step_type": "testing",
            "agent_name": "test-engineer",
            "agent_model": "sonnet",
            "message": "Dispatch test-engineer.",
            "delegation_prompt": "Test it.",
            "path_enforcement": "",
        }
        output = _capture_print_action(action)
        assert "Agent: test-engineer" in output
        assert "Model: sonnet" in output
        assert "Step:  3.2" in output

    def test_consulting_dispatch_includes_type_line(self) -> None:
        action = {
            "action_type": "dispatch",
            "step_id": "1.2",
            "step_type": "consulting",
            "agent_name": "architect",
            "agent_model": "sonnet",
            "message": "Consult architect.",
            "delegation_prompt": "Advise on the approach.",
            "path_enforcement": "",
        }
        output = _capture_print_action(action)
        assert "Type:  consulting" in output

    def test_dispatch_with_no_step_type_omits_type_line(self) -> None:
        """When step_type is empty string, Type: line should not appear."""
        action = {
            "action_type": "dispatch",
            "step_id": "1.1",
            "step_type": "",
            "agent_name": "backend-engineer",
            "agent_model": "sonnet",
            "message": "Dispatch.",
            "delegation_prompt": "Do it.",
            "path_enforcement": "",
        }
        output = _capture_print_action(action)
        lines = output.splitlines()
        assert not any(line.strip().startswith("Type:") for line in lines)


# ---------------------------------------------------------------------------
# 7. End-to-End Integration
# ---------------------------------------------------------------------------


def _make_mixed_plan(task_id: str = "task-mixed-001") -> MachinePlan:
    """A plan with automation, planning, developing, and task step types."""
    return MachinePlan(
        task_id=task_id,
        task_summary="Mixed step taxonomy integration test",
        risk_level="LOW",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Setup",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="automation",
                        task_description="Prepare environment",
                        step_type="automation",
                        command="echo setup",
                    ),
                    PlanStep(
                        step_id="1.2",
                        agent_name="architect",
                        task_description="Design the approach",
                        step_type="planning",
                    ),
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Build",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="backend-engineer",
                        task_description="Implement the feature",
                        step_type="developing",
                        depends_on=["1.2"],
                    ),
                    PlanStep(
                        step_id="2.2",
                        agent_name="task-runner",
                        task_description="Format output as JSON",
                        step_type="task",
                        depends_on=["2.1"],
                    ),
                ],
                gate=PlanGate(gate_type="build", command="echo gate"),
            ),
        ],
        shared_context="## Shared Context\n\nProject background.",
    )


def _drive_mixed_plan(
    engine: ExecutionEngine,
    plan: MachinePlan,
) -> dict[str, str]:
    """Drive the engine loop, recording results for each step type.

    Returns a dict mapping step_id → step_type from recorded results.
    """
    action = engine.start(plan)
    results: dict[str, str] = {}
    max_iters = 50
    i = 0
    while action.action_type not in (ActionType.COMPLETE, ActionType.FAILED):
        if i > max_iters:
            raise RuntimeError(f"Loop stuck after {max_iters} iterations")
        i += 1

        if action.action_type == ActionType.DISPATCH:
            step_id = action.step_id
            step_type = action.step_type
            results[step_id] = step_type

            if step_type == "automation":
                # Record directly — no LLM involved.
                engine.record_step_result(
                    step_id=step_id,
                    agent_name="automation",
                    status="complete",
                    outcome="stdout output",
                    estimated_tokens=0,
                )
            else:
                engine.record_step_result(
                    step_id=step_id,
                    agent_name=action.agent_name,
                    status="complete",
                    outcome="Done",
                    estimated_tokens=1000,
                )
        elif action.action_type == ActionType.GATE:
            engine.record_gate_result(
                phase_id=action.phase_id,
                passed=True,
                output="pass",
            )
        action = engine.next_action()

    return results


class TestEndToEndIntegration:
    """Mixed step-type plan driven through the engine."""

    def test_mixed_plan_reaches_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        plan = _make_mixed_plan()
        results = _drive_mixed_plan(engine, plan)
        final = engine.next_action()
        assert final.action_type == ActionType.COMPLETE

    def test_automation_steps_dispatched_without_prompt(self, tmp_path: Path) -> None:
        """Automation steps must not carry a delegation_prompt."""
        dispatched_actions: list[ExecutionAction] = []
        engine = _engine(tmp_path)
        plan = _make_mixed_plan()
        action = engine.start(plan)
        max_iters = 50
        i = 0
        while action.action_type not in (ActionType.COMPLETE, ActionType.FAILED):
            if i > max_iters:
                break
            i += 1
            if action.action_type == ActionType.DISPATCH:
                dispatched_actions.append(action)
                if action.step_type == "automation":
                    engine.record_step_result(
                        step_id=action.step_id,
                        agent_name="automation",
                        status="complete",
                        outcome="ok",
                        estimated_tokens=0,
                    )
                else:
                    engine.record_step_result(
                        step_id=action.step_id,
                        agent_name=action.agent_name,
                        status="complete",
                        outcome="Done",
                    )
            elif action.action_type == ActionType.GATE:
                engine.record_gate_result(
                    phase_id=action.phase_id, passed=True, output="pass"
                )
            action = engine.next_action()

        automation_dispatches = [a for a in dispatched_actions if a.step_type == "automation"]
        assert len(automation_dispatches) > 0, "Expected at least one automation dispatch"
        for a in automation_dispatches:
            assert a.delegation_prompt == "", (
                f"Automation step {a.step_id} should have no delegation_prompt"
            )

    def test_step_results_record_correct_step_types(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        plan = _make_mixed_plan()
        results_by_step = _drive_mixed_plan(engine, plan)
        # Verify the step types we dispatched match the plan.
        assert results_by_step.get("1.1") == "automation"
        assert results_by_step.get("1.2") == "planning"
        assert results_by_step.get("2.1") == "developing"
        assert results_by_step.get("2.2") == "task"

    def test_automation_worker_passes_zero_tokens_to_engine(self, tmp_path: Path) -> None:
        """Worker records automation results with estimated_tokens=0 (no LLM call).

        The engine may apply a task_description-length fallback estimate when
        estimated_tokens=0 is passed; what we verify here is that the worker
        itself never passes a non-zero token count from an LLM — the caller
        supplies 0. We capture the call the worker makes to record_step_result.
        """
        recorded_calls: list[dict] = []

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            p = _plan(
                phases=[_phase(steps=[_step("1.1", step_type="automation", command="echo ok")])],
            )
            engine.start(p)

            # Capture calls to record_step_result after engine is started.
            original_record = engine.record_step_result

            def _capturing_record(*args, **kwargs):
                recorded_calls.append(kwargs)
                return original_record(*args, **kwargs)

            engine.record_step_result = _capturing_record  # type: ignore[method-assign]
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
            await worker.run()

        asyncio.run(_run())

        # Find the call for the automation step (1.1).
        auto_calls = [c for c in recorded_calls if c.get("step_id") == "1.1"]
        assert auto_calls, "Expected at least one record_step_result call for step 1.1"
        # The worker must not pass any token count for automation — no LLM was called.
        assert auto_calls[0].get("estimated_tokens", 0) == 0

    def test_state_step_results_carry_step_type_after_loop(self, tmp_path: Path) -> None:
        engine, _ = _engine_with_sqlite(tmp_path, "task-state-type-001")
        plan = _make_mixed_plan("task-state-type-001")
        _drive_mixed_plan(engine, plan)
        state = engine._load_state()
        expected = {"1.1": "automation", "1.2": "planning", "2.1": "developing", "2.2": "task"}
        for step_id, expected_type in expected.items():
            result = state.get_step_result(step_id)
            assert result is not None, f"Missing result for step {step_id}"
            assert result.step_type == expected_type, (
                f"Step {step_id}: expected step_type={expected_type!r}, got {result.step_type!r}"
            )


# ---------------------------------------------------------------------------
# 8. Worker (daemon mode)
# ---------------------------------------------------------------------------


class TestWorkerAutomationRouting:
    """TaskWorker routes automation actions without touching AgentLauncher.

    All worker tests create engine + call start() inside the async function,
    matching the pattern used in test_runtime.py.  This avoids SQLite
    threading issues when engine state is initialized outside asyncio.run().
    State is read back via engine._load_state() (file-based fallback) inside
    the same async context, or via captured variables passed out.
    """

    def test_automation_not_routed_to_agent_launcher(self, tmp_path: Path) -> None:
        """When a batch contains only automation actions, the launcher is never called."""
        launcher = DryRunLauncher()

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            p = _plan(
                phases=[_phase(steps=[_step("1.1", step_type="automation", command="echo hello")])],
            )
            engine.start(p)
            worker = TaskWorker(engine=engine, launcher=launcher)
            await worker.run()

        asyncio.run(_run())

        # Launcher.launches should be empty — no LLM dispatch for automation.
        assert len(launcher.launches) == 0

    def test_automation_success_recorded_as_complete(self, tmp_path: Path) -> None:
        """Successful automation command → step lands in completed_step_ids, stdout captured."""
        # completed_step_ids uses a set-comprehension over all results with status="complete",
        # which correctly handles the mark_dispatched → record_complete append pattern.
        captured: dict = {}

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            p = _plan(
                phases=[_phase(steps=[_step("1.1", step_type="automation", command="echo success_marker")])],
            )
            engine.start(p)
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
            await worker.run()
            state = engine._load_state()
            captured["completed"] = state.completed_step_ids
            # Find the complete result (last matching by status, not first).
            complete_results = [r for r in state.step_results if r.step_id == "1.1" and r.status == "complete"]
            captured["result"] = complete_results[-1] if complete_results else None

        asyncio.run(_run())

        assert "1.1" in captured["completed"], "Step 1.1 should be in completed_step_ids"
        result = captured["result"]
        assert result is not None
        assert result.agent_name == "automation"
        assert "success_marker" in result.outcome

    def test_automation_failure_recorded_as_failed(self, tmp_path: Path) -> None:
        """Nonzero exit code → step lands in failed_step_ids."""
        captured: dict = {}

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            p = _plan(
                phases=[_phase(steps=[_step("1.1", step_type="automation", command="sh -c 'exit 1'")])],
            )
            engine.start(p)
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
            await worker.run()
            state = engine._load_state()
            captured["failed"] = state.failed_step_ids

        asyncio.run(_run())

        assert "1.1" in captured["failed"], "Step 1.1 should be in failed_step_ids"

    def test_worker_mixed_batch_automation_and_agent(self, tmp_path: Path) -> None:
        """Mixed batch: automation runs directly, agent steps go to launcher."""
        launcher = DryRunLauncher()
        captured: dict = {}

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            p = _plan(
                phases=[
                    _phase(
                        steps=[
                            _step("1.1", step_type="automation", command="echo auto"),
                            _step("1.2", agent_name="backend-engineer", step_type="developing"),
                        ]
                    )
                ],
            )
            engine.start(p)
            worker = TaskWorker(engine=engine, launcher=launcher)
            await worker.run()
            state = engine._load_state()
            captured["completed"] = state.completed_step_ids

        asyncio.run(_run())

        assert "1.1" in captured["completed"], "Automation step 1.1 should have completed"
        assert "1.2" in captured["completed"], "Agent step 1.2 should have completed"
        # Launcher was called exactly once for the agent step.
        assert len(launcher.launches) == 1
        assert launcher.launches[0]["agent_name"] == "backend-engineer"

    def test_run_automation_success(self, tmp_path: Path) -> None:
        """_run_automation returns CompletedProcess with returncode=0 on success."""
        engine = _engine(tmp_path)
        worker = TaskWorker(engine=engine, launcher=DryRunLauncher())

        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            step_id="1.1",
            step_type="automation",
            command="echo hello_from_test",
        )

        async def _run():
            return await worker._run_automation(action)

        proc = asyncio.run(_run())
        assert proc.returncode == 0
        assert "hello_from_test" in proc.stdout

    def test_run_automation_failed_command(self, tmp_path: Path) -> None:
        """_run_automation returns nonzero returncode on command failure."""
        engine = _engine(tmp_path)
        worker = TaskWorker(engine=engine, launcher=DryRunLauncher())

        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            step_id="1.1",
            step_type="automation",
            command="sh -c 'exit 42'",
        )

        async def _run():
            return await worker._run_automation(action)

        proc = asyncio.run(_run())
        assert proc.returncode != 0

    def test_run_automation_timeout_raises(self, tmp_path: Path) -> None:
        """_run_automation raises subprocess.TimeoutExpired on timeout."""
        engine = _engine(tmp_path)
        worker = TaskWorker(engine=engine, launcher=DryRunLauncher())

        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            step_id="1.1",
            step_type="automation",
            command="sleep 600",
        )

        async def _run():
            return await worker._run_automation(action)

        with patch(
            "agent_baton.core.runtime.worker.subprocess.run",
            side_effect=subprocess.TimeoutExpired("sleep 600", 300),
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                asyncio.run(_run())

    def test_automation_timeout_recorded_as_failed(self, tmp_path: Path) -> None:
        """When _run_automation times out, the engine records a failed result."""
        captured: dict = {}

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            p = _plan(
                phases=[_phase(steps=[_step("1.1", step_type="automation", command="sleep 600")])],
            )
            engine.start(p)
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
            with patch(
                "agent_baton.core.runtime.worker.subprocess.run",
                side_effect=subprocess.TimeoutExpired("sleep 600", 300),
            ):
                await worker.run()
            state = engine._load_state()
            captured["failed"] = state.failed_step_ids
            # Find the failed result to check the error message.
            failed_results = [r for r in state.step_results if r.step_id == "1.1" and r.status == "failed"]
            captured["error"] = failed_results[-1].error if failed_results else ""

        asyncio.run(_run())

        assert "1.1" in captured["failed"], "Step 1.1 should be in failed_step_ids after timeout"
        assert "timed out" in captured["error"].lower()

    def test_automation_empty_command_runs_without_crash(self, tmp_path: Path) -> None:
        """Empty command does not crash the worker — must complete or fail, not throw."""
        captured: dict = {}
        fake_proc = subprocess.CompletedProcess(args="", returncode=0, stdout="", stderr="")

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            p = _plan(
                phases=[_phase(steps=[_step("1.1", step_type="automation", command="")])],
            )
            engine.start(p)
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
            with patch(
                "agent_baton.core.runtime.worker.subprocess.run",
                return_value=fake_proc,
            ):
                await worker.run()
            state = engine._load_state()
            captured["completed"] = state.completed_step_ids
            captured["failed"] = state.failed_step_ids

        asyncio.run(_run())

        assert "1.1" in captured["completed"] or "1.1" in captured["failed"], (
            "Step 1.1 must be either complete or failed — not stuck in dispatched"
        )


# ---------------------------------------------------------------------------
# 9. Planner Step Type Assignment
# ---------------------------------------------------------------------------


class TestPlannerStepTypeAssignment:
    """_step_type_for_agent maps agent roles to step types correctly."""

    def test_architect_gets_planning(self) -> None:
        assert _step_type_for_agent("architect") == "planning"

    def test_ai_systems_architect_gets_planning(self) -> None:
        assert _step_type_for_agent("ai-systems-architect") == "planning"

    def test_code_reviewer_gets_reviewing(self) -> None:
        assert _step_type_for_agent("code-reviewer") == "reviewing"

    def test_security_reviewer_gets_reviewing(self) -> None:
        assert _step_type_for_agent("security-reviewer") == "reviewing"

    def test_auditor_gets_reviewing(self) -> None:
        assert _step_type_for_agent("auditor") == "reviewing"

    def test_test_engineer_gets_testing(self) -> None:
        assert _step_type_for_agent("test-engineer", "Run tests on the module") == "testing"

    def test_test_engineer_create_keyword_gets_developing(self) -> None:
        assert _step_type_for_agent("test-engineer", "Create the test suite") == "developing"

    def test_test_engineer_build_keyword_gets_developing(self) -> None:
        assert _step_type_for_agent("test-engineer", "Build test infrastructure") == "developing"

    def test_test_engineer_scaffold_keyword_gets_developing(self) -> None:
        assert _step_type_for_agent("test-engineer", "Scaffold the test fixtures") == "developing"

    def test_task_runner_gets_task(self) -> None:
        assert _step_type_for_agent("task-runner") == "task"

    def test_backend_engineer_gets_developing(self) -> None:
        assert _step_type_for_agent("backend-engineer") == "developing"

    def test_frontend_engineer_gets_developing(self) -> None:
        assert _step_type_for_agent("frontend-engineer") == "developing"

    def test_unknown_agent_gets_developing(self) -> None:
        assert _step_type_for_agent("some-unknown-agent-xyz") == "developing"

    def test_variant_suffix_stripped_for_lookup(self) -> None:
        """backend-engineer--python strips to backend-engineer → developing."""
        assert _step_type_for_agent("backend-engineer--python") == "developing"

    def test_architect_variant_stripped_correctly(self) -> None:
        assert _step_type_for_agent("architect--cloud") == "planning"

    def test_test_engineer_keyword_case_insensitive(self) -> None:
        """Keyword check must be case-insensitive."""
        assert _step_type_for_agent("test-engineer", "CREATE the test framework") == "developing"

    def test_planner_assigns_step_type_from_agent(self, tmp_path: Path) -> None:
        """IntelligentPlanner assigns step_type based on agent role in generated plan."""
        from agent_baton.core.engine.planner import IntelligentPlanner
        planner = IntelligentPlanner(team_context_root=tmp_path)
        plan = planner.create_plan("Add REST API endpoint for user profiles")
        # All steps in the plan must have a non-empty step_type.
        for step in plan.all_steps:
            assert step.step_type, f"Step {step.step_id} ({step.agent_name}) has no step_type"
