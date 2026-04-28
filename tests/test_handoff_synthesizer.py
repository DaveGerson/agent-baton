"""Tests for Wave 3.2 HandoffSynthesizer (resolves bd-65d4 / bd-61a5)."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.intel.handoff_synthesizer import (
    HANDOFF_MAX_CHARS,
    HandoffSynthesizer,
)
from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION
from agent_baton.models.execution import PlanStep


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------

@dataclass
class FakeStepResult:
    """Minimal StepResult-shaped fake."""

    step_id: str = "1.1"
    agent_name: str = "backend-engineer"
    status: str = "complete"
    outcome: str = ""
    files_changed: list[str] = field(default_factory=list)
    task_id: str = ""


def _open_project_db(tmp_path: Path) -> sqlite3.Connection:
    """Initialize a fresh project baton.db at SCHEMA_VERSION and return a conn."""
    db_path = tmp_path / "baton.db"
    mgr = ConnectionManager(db_path)
    mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    return mgr.get_connection()


def _open_project_db_with_path(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    db_path = tmp_path / "baton.db"
    mgr = ConnectionManager(db_path)
    mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    return mgr.get_connection(), db_path


def _ensure_execution_row(conn: sqlite3.Connection, task_id: str) -> None:
    """Insert a minimal executions row so beads-with-FK can be inserted.

    Uses INSERT OR IGNORE so callers can call this multiple times safely.
    Only the columns NOT NULL without DEFAULT are populated; everything
    else falls back to its DDL default.
    """
    try:
        conn.execute(
            "INSERT OR IGNORE INTO executions (task_id, status, started_at) "
            "VALUES (?, ?, ?)",
            (task_id, "running", "2026-04-27T12:00:00Z"),
        )
        conn.commit()
    except Exception:
        # If the schema changed shape, fall back to silent skip — the
        # FK is OFF by default in SQLite anyway.
        pass


def _insert_bead(
    conn: sqlite3.Connection,
    *,
    bead_id: str,
    task_id: str,
    step_id: str,
    bead_type: str = "discovery",
    content: str = "",
    status: str = "open",
    affected_files: list[str] | None = None,
    tags: list[str] | None = None,
    agent_name: str = "test-agent",
) -> None:
    affected_files = affected_files or []
    tags = tags or []
    conn.execute(
        "INSERT INTO beads (bead_id, task_id, step_id, agent_name, bead_type, "
        "content, affected_files, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            bead_id,
            task_id,
            step_id,
            agent_name,
            bead_type,
            content,
            json.dumps(affected_files),
            status,
            "2026-04-27T12:00:00Z",
        ),
    )
    for tag in tags:
        conn.execute(
            "INSERT OR IGNORE INTO bead_tags (bead_id, tag) VALUES (?, ?)",
            (bead_id, tag),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

def test_migration_creates_table(tmp_path: Path) -> None:
    """A fresh project DB at SCHEMA_VERSION must have handoff_beads."""
    conn = _open_project_db(tmp_path)
    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='handoff_beads'"
    ).fetchone()
    assert row is not None, "handoff_beads table missing from PROJECT_SCHEMA_DDL"

    cols = {r[1] for r in conn.execute("PRAGMA table_info(handoff_beads)").fetchall()}
    assert {
        "handoff_id",
        "task_id",
        "from_step_id",
        "to_step_id",
        "content",
        "created_at",
    }.issubset(cols)

    # Schema version stamp must be at v29 or higher.
    ver = conn.execute("SELECT version FROM _schema_version").fetchone()
    assert ver is not None and ver[0] >= 29


# ---------------------------------------------------------------------------
# Synthesizer behavior
# ---------------------------------------------------------------------------

def test_no_handoff_when_no_prior_step(tmp_path: Path) -> None:
    """Returns None when prior_step_result is None (first step of a phase)."""
    conn = _open_project_db(tmp_path)
    next_step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer",
        task_description="x",
        allowed_paths=["agent_baton/api/foo.py"],
    )
    out = HandoffSynthesizer().synthesize_for_dispatch(None, next_step, conn)
    assert out is None


def test_handoff_includes_files_changed(tmp_path: Path) -> None:
    conn = _open_project_db(tmp_path)
    prior = FakeStepResult(
        step_id="1.1",
        files_changed=[
            "agent_baton/api/foo.py",
            "agent_baton/api/bar.py",
        ],
        outcome="ok",
        task_id="t-files",
    )
    nxt = PlanStep(
        step_id="1.2",
        agent_name="test-engineer",
        task_description="x",
        allowed_paths=["tests/test_foo.py"],
    )
    out = HandoffSynthesizer().synthesize_for_dispatch(
        prior, nxt, conn, task_id="t-files"
    )
    assert out is not None
    assert "Files (2)" in out
    assert "agent_baton/api/foo.py" in out
    assert "agent_baton/api/bar.py" in out


def test_handoff_includes_discoveries_from_beads(tmp_path: Path) -> None:
    conn = _open_project_db(tmp_path)
    _ensure_execution_row(conn, "t-disc")

    # Two beads tied to the prior step.
    _insert_bead(
        conn,
        bead_id="bd-a001",
        task_id="t-disc",
        step_id="1.1",
        content="found a thing",
        affected_files=["agent_baton/x.py"],
    )
    _insert_bead(
        conn,
        bead_id="bd-a002",
        task_id="t-disc",
        step_id="1.1",
        content="and another",
    )
    # Unrelated bead from a different step — must NOT show up.
    _insert_bead(
        conn,
        bead_id="bd-a999",
        task_id="t-disc",
        step_id="0.9",
        content="other step",
    )

    prior = FakeStepResult(
        step_id="1.1", files_changed=["agent_baton/x.py"], task_id="t-disc"
    )
    nxt = PlanStep(
        step_id="1.2",
        agent_name="frontend-engineer",
        task_description="x",
        allowed_paths=["pmo-ui/src/App.tsx"],
    )
    out = HandoffSynthesizer().synthesize_for_dispatch(
        prior, nxt, conn, task_id="t-disc"
    )
    assert out is not None
    assert "Discoveries:" in out
    assert "bd-a001" in out
    assert "bd-a002" in out
    assert "bd-a999" not in out


def test_handoff_includes_blockers_in_overlap(tmp_path: Path) -> None:
    conn = _open_project_db(tmp_path)
    _ensure_execution_row(conn, "t-blk")

    # Open warning whose affected_files overlap the next step's allowed_paths.
    _insert_bead(
        conn,
        bead_id="bd-w100",
        task_id="t-blk",
        step_id="0.5",
        bead_type="warning",
        content="careful, this module has a race",
        affected_files=["agent_baton/api/foo.py"],
        tags=["warning"],
        status="open",
    )
    # Another warning that does NOT overlap the next step — should be ignored.
    _insert_bead(
        conn,
        bead_id="bd-w200",
        task_id="t-blk",
        step_id="0.6",
        bead_type="warning",
        content="unrelated subsystem warning",
        affected_files=["pmo-ui/src/legacy/old.tsx"],
        tags=["legacy"],
        status="open",
    )
    # Closed warning that overlaps — must NOT show (only OPEN).
    _insert_bead(
        conn,
        bead_id="bd-w300",
        task_id="t-blk",
        step_id="0.7",
        bead_type="warning",
        content="already resolved",
        affected_files=["agent_baton/api/foo.py"],
        status="closed",
    )

    prior = FakeStepResult(
        step_id="1.1", files_changed=["x"], task_id="t-blk"
    )
    nxt = PlanStep(
        step_id="1.2",
        agent_name="backend-engineer",
        task_description="touch foo",
        allowed_paths=["agent_baton/api/foo.py"],
    )
    out = HandoffSynthesizer().synthesize_for_dispatch(
        prior, nxt, conn, task_id="t-blk"
    )
    assert out is not None
    assert "Blockers" in out
    assert "bd-w100" in out
    assert "bd-w200" not in out
    assert "bd-w300" not in out


def test_handoff_caps_at_400_chars(tmp_path: Path) -> None:
    conn = _open_project_db(tmp_path)
    # A pile of long file paths to force the body over 400 chars
    # before the cap kicks in.
    big_files = [f"agent_baton/module_{i:04d}/very_long_filename.py" for i in range(50)]
    prior = FakeStepResult(
        step_id="1.1", files_changed=big_files, task_id="t-cap"
    )
    nxt = PlanStep(
        step_id="1.2",
        agent_name="backend-engineer",
        task_description="x",
        allowed_paths=["agent_baton/module_0001/very_long_filename.py"],
    )
    out = HandoffSynthesizer().synthesize_for_dispatch(
        prior, nxt, conn, task_id="t-cap"
    )
    assert out is not None
    assert len(out) <= HANDOFF_MAX_CHARS, (
        f"Expected <= {HANDOFF_MAX_CHARS} chars, got {len(out)}"
    )


def test_handoff_persisted_to_db(tmp_path: Path) -> None:
    conn = _open_project_db(tmp_path)
    prior = FakeStepResult(
        step_id="1.1",
        files_changed=["agent_baton/x.py"],
        task_id="t-persist",
    )
    nxt = PlanStep(
        step_id="1.2",
        agent_name="backend-engineer",
        task_description="x",
        allowed_paths=["agent_baton/x.py"],
    )
    out = HandoffSynthesizer().synthesize_for_dispatch(
        prior, nxt, conn, task_id="t-persist"
    )
    assert out is not None

    rows = conn.execute(
        "SELECT task_id, from_step_id, to_step_id, content FROM handoff_beads "
        "WHERE task_id = ?",
        ("t-persist",),
    ).fetchall()
    assert len(rows) == 1
    task_id, frm, to, content = rows[0]
    assert task_id == "t-persist"
    assert frm == "1.1"
    assert to == "1.2"
    assert content == out

    # Idempotent: synthesizing the same (from, to) pair again replaces.
    HandoffSynthesizer().synthesize_for_dispatch(
        prior, nxt, conn, task_id="t-persist"
    )
    rows2 = conn.execute(
        "SELECT COUNT(*) FROM handoff_beads WHERE task_id = ?",
        ("t-persist",),
    ).fetchone()
    assert rows2[0] == 1


def test_handoff_handles_no_files_changed(tmp_path: Path) -> None:
    """Edge case: prior step touched no files but had a known status."""
    conn = _open_project_db(tmp_path)
    prior = FakeStepResult(
        step_id="1.1",
        files_changed=[],
        status="complete",
        task_id="t-empty",
    )
    nxt = PlanStep(
        step_id="1.2",
        agent_name="auditor",
        task_description="review",
        allowed_paths=[],
    )
    out = HandoffSynthesizer().synthesize_for_dispatch(
        prior, nxt, conn, task_id="t-empty"
    )
    # Outcome is "passed" (status=complete), so the handoff should still
    # emit something — at minimum the outcome line.
    assert out is not None
    assert "Files: none" in out
    assert "passed" in out


# ---------------------------------------------------------------------------
# Dispatcher integration
# ---------------------------------------------------------------------------

def test_dispatcher_prepends_handoff_section(tmp_path: Path) -> None:
    """build_delegation_prompt must prepend a '## Handoff from Prior Step'
    section when prior_step_result is non-None and synthesis returns text."""
    conn = _open_project_db(tmp_path)

    prior = FakeStepResult(
        step_id="1.1",
        files_changed=["agent_baton/api/foo.py"],
        task_id="t-disp",
    )
    next_step = PlanStep(
        step_id="1.2",
        agent_name="backend-engineer",
        task_description="Refactor the foo endpoint.",
        allowed_paths=["agent_baton/api/foo.py"],
    )

    prompt = PromptDispatcher().build_delegation_prompt(
        next_step,
        shared_context="Some shared context here.",
        task_summary="Refactor",
        prior_step_result=prior,
        handoff_conn=conn,
        handoff_task_id="t-disp",
    )

    assert "## Handoff from Prior Step" in prompt
    assert "agent_baton/api/foo.py" in prompt
    # Section must come BEFORE Shared Context so the agent reads it first.
    assert prompt.index("## Handoff from Prior Step") < prompt.index("## Shared Context")


def test_dispatcher_no_handoff_when_no_prior(tmp_path: Path) -> None:
    """Sanity: no '## Handoff from Prior Step' section without a prior step."""
    next_step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer",
        task_description="First step.",
        allowed_paths=["agent_baton/api/foo.py"],
    )
    prompt = PromptDispatcher().build_delegation_prompt(
        next_step,
        shared_context="Some shared context here.",
        task_summary="First",
    )
    assert "## Handoff from Prior Step" not in prompt


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_handoffs_cli_lists_rows(tmp_path: Path) -> None:
    """`baton beads handoffs --task-id T` prints rows for the task."""
    # Synthesize one handoff so there is something to list.
    conn, db_path = _open_project_db_with_path(tmp_path)
    prior = FakeStepResult(
        step_id="1.1", files_changed=["agent_baton/api/foo.py"], task_id="t-cli"
    )
    nxt = PlanStep(
        step_id="1.2",
        agent_name="backend-engineer",
        task_description="x",
        allowed_paths=["agent_baton/api/foo.py"],
    )
    HandoffSynthesizer().synthesize_for_dispatch(
        prior, nxt, conn, task_id="t-cli"
    )
    # Ensure persistence survived across the connection (close to flush).
    conn.commit()

    # The CLI resolves the DB via ``_resolve_db_path``; we pin it via env.
    env = {
        "BATON_DB_PATH": str(db_path),
        "BATON_TASK_ID": "t-cli",
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "agent_baton.cli.main",
         "beads", "handoffs", "--task-id", "t-cli"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "Handoff beads for task t-cli" in out
    assert "1.1" in out and "1.2" in out
