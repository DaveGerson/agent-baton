"""Tests for Wave 2.2 ContextHarvester (bd-f638 / bd-9fac).

Covers:

- v27 migration creates the agent_context table on a fresh DB
- harvest() inserts a row for a complete step
- harvest() upserts (replaces) when called twice for same (agent, domain)
- harvest() handles steps with no files_changed gracefully
- harvest() is a no-op when BATON_HARVEST_CONTEXT=0
- dispatcher.build_delegation_prompt prepends the Prior Context block
  when an agent_context row exists
- dispatcher.build_delegation_prompt is unchanged when no row exists
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.intel.context_harvester import (
    ContextHarvester,
    derive_domain,
    is_enabled,
)
from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION
from agent_baton.models.execution import PlanStep


# ---------------------------------------------------------------------------
# Lightweight fakes
# ---------------------------------------------------------------------------

@dataclass
class FakeStepResult:
    """Minimal StepResult-shaped fake for harvester tests."""

    step_id: str = "1.1"
    agent_name: str = "backend-engineer"
    status: str = "complete"
    outcome: str = "Did some work."
    files_changed: list[str] = field(default_factory=list)


def _open_project_db(tmp_path: Path) -> sqlite3.Connection:
    """Initialize a fresh project baton.db at SCHEMA_VERSION 27 and return conn."""
    db_path = tmp_path / "baton.db"
    mgr = ConnectionManager(db_path)
    mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    return mgr.get_connection()


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

def test_migration_creates_table(tmp_path: Path) -> None:
    """A fresh project DB at v27 must have agent_context with the right shape."""
    conn = _open_project_db(tmp_path)
    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='agent_context'"
    ).fetchone()
    assert row is not None, "agent_context table missing from PROJECT_SCHEMA_DDL"

    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(agent_context)").fetchall()
    }
    assert {
        "agent_name",
        "domain",
        "expertise_summary",
        "strategies_worked",
        "strategies_failed",
        "last_task_id",
        "updated_at",
    }.issubset(cols)

    # Schema version stamp
    ver = conn.execute("SELECT version FROM _schema_version").fetchone()
    assert ver is not None and ver[0] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Harvest insert / upsert / edge cases
# ---------------------------------------------------------------------------

def test_harvest_inserts_row(tmp_path: Path) -> None:
    conn = _open_project_db(tmp_path)
    plan_step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer",
        task_description="Add a new endpoint.",
        allowed_paths=["agent_baton/api/foo.py"],
    )
    sr = FakeStepResult(
        files_changed=["agent_baton/api/foo.py", "agent_baton/api/bar.py"],
        outcome="Added foo and bar handlers.",
    )

    ContextHarvester().harvest(
        sr, conn,
        plan_step=plan_step,
        task_id="t-100",
        gate_outcomes={"build": "pass", "lint": "pass"},
    )

    rows = conn.execute(
        "SELECT * FROM agent_context WHERE agent_name=?", ("backend-engineer",)
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["domain"] == "agent_baton"
    assert "Touched 2 file(s)" in row["expertise_summary"]
    assert row["last_task_id"] == "t-100"
    assert "build" in row["strategies_worked"]
    assert row["updated_at"]  # non-empty timestamp


def test_harvest_upserts_same_agent_domain(tmp_path: Path) -> None:
    """Calling harvest twice for the same (agent, domain) replaces the row."""
    conn = _open_project_db(tmp_path)
    plan_step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer",
        task_description="x",
        allowed_paths=["agent_baton/api/foo.py"],
    )

    # First call
    sr1 = FakeStepResult(files_changed=["agent_baton/api/foo.py"], outcome="first")
    ContextHarvester().harvest(sr1, conn, plan_step=plan_step, task_id="t-1")

    # Second call — different content, same (agent, domain)
    sr2 = FakeStepResult(
        files_changed=[
            "agent_baton/api/foo.py",
            "agent_baton/api/baz.py",
            "agent_baton/api/qux.py",
        ],
        outcome="second pass",
    )
    ContextHarvester().harvest(sr2, conn, plan_step=plan_step, task_id="t-2")

    rows = conn.execute(
        "SELECT * FROM agent_context WHERE agent_name=? AND domain=?",
        ("backend-engineer", "agent_baton"),
    ).fetchall()
    assert len(rows) == 1, "Expected upsert (single row), got duplicates"
    row = rows[0]
    assert row["last_task_id"] == "t-2"
    assert "Touched 3 file(s)" in row["expertise_summary"]


def test_harvest_handles_no_files(tmp_path: Path) -> None:
    """Harvest must not fail when files_changed is empty."""
    conn = _open_project_db(tmp_path)
    plan_step = PlanStep(
        step_id="1.1",
        agent_name="auditor",
        task_description="Review only",
        allowed_paths=[],
    )
    sr = FakeStepResult(
        agent_name="auditor",
        files_changed=[],
        outcome="No files modified — review-only step.",
    )

    # Should NOT raise
    ContextHarvester().harvest(sr, conn, plan_step=plan_step, task_id="t-3")

    rows = conn.execute(
        "SELECT * FROM agent_context WHERE agent_name=?", ("auditor",)
    ).fetchall()
    # Domain falls back to "general" when no path hints exist
    assert len(rows) == 1
    assert rows[0]["domain"] == "general"
    assert "Touched 0 file(s)" in rows[0]["expertise_summary"]


def test_harvest_disabled_when_env_zero(tmp_path: Path, monkeypatch) -> None:
    """BATON_HARVEST_CONTEXT=0 disables harvesting; no rows are written."""
    monkeypatch.setenv("BATON_HARVEST_CONTEXT", "0")
    assert not is_enabled()

    conn = _open_project_db(tmp_path)
    plan_step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer",
        task_description="x",
        allowed_paths=["agent_baton/api/foo.py"],
    )
    sr = FakeStepResult(files_changed=["agent_baton/api/foo.py"])

    ContextHarvester().harvest(sr, conn, plan_step=plan_step, task_id="t-disabled")

    rows = conn.execute("SELECT COUNT(*) FROM agent_context").fetchone()
    assert rows[0] == 0


# ---------------------------------------------------------------------------
# Dispatcher integration
# ---------------------------------------------------------------------------

def test_dispatcher_prepends_prior_context() -> None:
    """When prior_context_block is non-empty, build_delegation_prompt
    prepends a Prior Context section before Shared Context."""
    step = PlanStep(
        step_id="2.1",
        agent_name="backend-engineer",
        task_description="Refactor the foo endpoint.",
        allowed_paths=["agent_baton/api/foo.py"],
    )
    row = {
        "agent_name": "backend-engineer",
        "domain": "agent_baton",
        "expertise_summary": (
            "Touched 2 file(s). Gates passed: build. Failures: none.\n"
            "Last step 1.1 (complete): Added foo and bar handlers."
        ),
        "strategies_worked": "build; lint",
        "strategies_failed": "",
        "last_task_id": "t-100",
        "updated_at": "2026-04-27T12:00:00Z",
    }
    block = ContextHarvester.render_prior_context_block(row)
    assert block.startswith("## Prior Context")
    assert "agent_baton" in block

    prompt = PromptDispatcher().build_delegation_prompt(
        step,
        shared_context="Some shared context here.",
        task_summary="Refactor",
        prior_context_block=block,
    )

    assert "## Prior Context" in prompt
    assert "Touched 2 file(s)" in prompt
    # Block must come BEFORE Shared Context
    assert prompt.index("## Prior Context") < prompt.index("## Shared Context")


def test_dispatcher_no_block_when_no_row() -> None:
    """When prior_context_block is empty (no harvested row), the prompt
    is unchanged from pre-harvester behavior — no Prior Context section."""
    step = PlanStep(
        step_id="2.1",
        agent_name="backend-engineer",
        task_description="Add a new endpoint.",
        allowed_paths=["agent_baton/api/foo.py"],
    )

    prompt = PromptDispatcher().build_delegation_prompt(
        step,
        shared_context="Some shared context here.",
        task_summary="Add",
        prior_context_block="",
    )

    assert "## Prior Context" not in prompt
    assert "## Shared Context" in prompt


# ---------------------------------------------------------------------------
# Domain derivation
# ---------------------------------------------------------------------------

def test_derive_domain_from_allowed_paths() -> None:
    step = PlanStep(
        step_id="1.1",
        agent_name="x",
        task_description="x",
        allowed_paths=["agent_baton/core/foo.py", "tests/foo_test.py"],
    )
    sr = FakeStepResult(files_changed=[])
    assert derive_domain(sr, plan_step=step) == "agent_baton"


def test_derive_domain_falls_back_to_files_changed() -> None:
    step = PlanStep(
        step_id="1.1",
        agent_name="x",
        task_description="x",
        allowed_paths=[],
    )
    sr = FakeStepResult(files_changed=["pmo-ui/src/App.tsx"])
    assert derive_domain(sr, plan_step=step) == "pmo-ui"


def test_derive_domain_default_general() -> None:
    sr = FakeStepResult(files_changed=[])
    assert derive_domain(sr, plan_step=None) == "general"
