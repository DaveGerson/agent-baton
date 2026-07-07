"""Tests for Wave 3.2 HandoffSynthesizer (resolves bd-65d4 / bd-61a5)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.intel.handoff_synthesizer import (
    HANDOFF_MAX_CHARS,
    HandoffSynthesizer,
)
from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.derived_bead_store import DerivedBeadStore
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION
from agent_baton.models.execution import PlanStep


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------
#
# NOTE (ADR-13b WP-G / WP-2): the SQLite ``beads``/``bead_tags``/
# ``handoff_beads`` tables were removed from the per-project ``baton.db``
# schema — ``bd`` is now the sole bead system of record, and
# ``handoff_beads`` moved to the disposable ``DerivedBeadStore`` cache
# (``baton-derived.db``; see agent_baton/core/storage/derived_bead_store.py
# and agent_baton/cli/commands/bead_cmd.py::_handle_handoffs). Fixtures
# below reflect that: discoveries/blockers are supplied via a fake bead
# *store* (matching HandoffSynthesizer's ``bead_store.query()`` surface)
# rather than raw SQL rows in a project db that no longer has those tables.

@dataclass
class FakeStepResult:
    """Minimal StepResult-shaped fake."""

    step_id: str = "1.1"
    agent_name: str = "backend-engineer"
    status: str = "complete"
    outcome: str = ""
    files_changed: list[str] = field(default_factory=list)
    task_id: str = ""


@dataclass
class FakeBead:
    """Minimal Bead-shaped fake (matches BeadStore/BdBeadStore row attrs)."""

    bead_id: str
    task_id: str
    step_id: str = ""
    bead_type: str = "discovery"
    content: str = ""
    status: str = "open"
    agent_name: str = "test-agent"
    affected_files: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


class FakeBeadStore:
    """Stand-in for the ``bead_store`` param HandoffSynthesizer queries.

    Mirrors the ``.query(task_id=..., bead_type=..., status=..., limit=...)``
    surface used by ``_query_beads_for_step_via_store`` /
    ``_query_open_warnings_via_store`` in
    agent_baton/core/intel/handoff_synthesizer.py.
    """

    def __init__(self, beads: list[FakeBead]) -> None:
        self._beads = beads

    def query(
        self,
        *,
        task_id: str | None = None,
        bead_type: str | None = None,
        status: str | None = None,
        limit: int = 500,
    ) -> list[FakeBead]:
        out = list(self._beads)
        if task_id is not None:
            out = [b for b in out if b.task_id == task_id]
        if bead_type is not None:
            out = [b for b in out if b.bead_type == bead_type]
        if status is not None:
            out = [b for b in out if b.status == status]
        return out[:limit]


def _open_project_db(tmp_path: Path) -> sqlite3.Connection:
    """Initialize a fresh project baton.db at SCHEMA_VERSION and return a conn."""
    db_path = tmp_path / "baton.db"
    mgr = ConnectionManager(db_path)
    mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    return mgr.get_connection()


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

def test_migration_creates_table(tmp_path: Path) -> None:
    """HandoffSynthesizer's write target (DerivedBeadStore) has handoff_beads.

    ADR-13b WP-G dropped ``handoff_beads`` from the project ``baton.db``
    schema entirely (see migration v42 and
    ``tests/storage/test_v42_bead_tables_dropped.py``) — ``bd`` is now the
    sole bead system of record. ADR-13b WP-2 moved the table to the
    disposable ``DerivedBeadStore`` cache (``baton-derived.db``) instead, so
    that is what this test — and HandoffSynthesizer's persistence path —
    must target.
    """
    derived = DerivedBeadStore(tmp_path / "baton-derived.db")
    conn = sqlite3.connect(str(tmp_path / "baton-derived.db"))
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='handoff_beads'"
        ).fetchone()
        assert row is not None, "handoff_beads table missing from DerivedBeadStore schema"

        cols = {r[1] for r in conn.execute("PRAGMA table_info(handoff_beads)").fetchall()}
        assert {
            "handoff_id",
            "task_id",
            "from_step_id",
            "to_step_id",
            "content",
            "created_at",
        }.issubset(cols)
    finally:
        conn.close()

    # A fresh project baton.db must NOT carry the table (ADR-13b WP-G).
    project_conn = _open_project_db(tmp_path)
    project_row = project_conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='handoff_beads'"
    ).fetchone()
    assert project_row is None, (
        "handoff_beads unexpectedly present in PROJECT_SCHEMA_DDL — "
        "ADR-13b WP-G moved it to DerivedBeadStore; re-adding it to the "
        "project schema would reintroduce the dual bead-backend it removed."
    )


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
    # Two beads tied to the prior step.
    store = FakeBeadStore(
        [
            FakeBead(
                bead_id="bd-a001",
                task_id="t-disc",
                step_id="1.1",
                content="found a thing",
                affected_files=["agent_baton/x.py"],
            ),
            FakeBead(
                bead_id="bd-a002",
                task_id="t-disc",
                step_id="1.1",
                content="and another",
            ),
            # Unrelated bead from a different step — must NOT show up.
            FakeBead(
                bead_id="bd-a999",
                task_id="t-disc",
                step_id="0.9",
                content="other step",
            ),
        ]
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
        prior, nxt, None, task_id="t-disc", bead_store=store
    )
    assert out is not None
    assert "Discoveries:" in out
    assert "bd-a001" in out
    assert "bd-a002" in out
    assert "bd-a999" not in out


def test_handoff_includes_blockers_in_overlap(tmp_path: Path) -> None:
    store = FakeBeadStore(
        [
            # Open warning whose affected_files overlap the next step's
            # allowed_paths.
            FakeBead(
                bead_id="bd-w100",
                task_id="t-blk",
                step_id="0.5",
                bead_type="warning",
                content="careful, this module has a race",
                affected_files=["agent_baton/api/foo.py"],
                tags=["warning"],
                status="open",
            ),
            # Another warning that does NOT overlap the next step — should
            # be ignored.
            FakeBead(
                bead_id="bd-w200",
                task_id="t-blk",
                step_id="0.6",
                bead_type="warning",
                content="unrelated subsystem warning",
                affected_files=["pmo-ui/src/legacy/old.tsx"],
                tags=["legacy"],
                status="open",
            ),
            # Closed warning that overlaps — must NOT show (only OPEN).
            FakeBead(
                bead_id="bd-w300",
                task_id="t-blk",
                step_id="0.7",
                bead_type="warning",
                content="already resolved",
                affected_files=["agent_baton/api/foo.py"],
                status="closed",
            ),
        ]
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
        prior, nxt, None, task_id="t-blk", bead_store=store
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
    """HandoffSynthesizer persists via DerivedBeadStore (ADR-13b WP-2) —
    the project baton.db no longer carries handoff_beads (ADR-13b WP-G)."""
    derived = DerivedBeadStore(tmp_path / "baton-derived.db")
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
        prior, nxt, derived, task_id="t-persist"
    )
    assert out is not None

    rows = derived.handoffs("t-persist")
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == "t-persist"
    assert row["from_step_id"] == "1.1"
    assert row["to_step_id"] == "1.2"
    assert row["content"] == out

    # Idempotent: synthesizing the same (from, to) pair again replaces.
    HandoffSynthesizer().synthesize_for_dispatch(
        prior, nxt, derived, task_id="t-persist"
    )
    rows2 = derived.handoffs("t-persist")
    assert len(rows2) == 1


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

def _run_beads_cli(db_path: Path, argv: list[str]) -> tuple[int, str]:
    """Drive ``bead_cmd.handler`` in-process with a monkeypatched db path.

    Mirrors the pattern already established in
    ``tests/test_adr13b_wp2.py::TestCliDerivedStoreReads._run`` — invoking
    the CLI in-process (rather than via ``subprocess`` + ``sys.executable``)
    avoids depending on the test runner's ``python`` resolving a full
    interpreter environment (site-packages / PYTHONPATH) when spawned as a
    bare subprocess.
    """
    import argparse
    import io
    import sys as _sys
    from unittest.mock import patch

    from agent_baton.cli.commands import bead_cmd

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    bead_cmd.register(sub)
    args = parser.parse_args(["beads"] + argv)

    captured = io.StringIO()
    exit_code = 0
    with patch("agent_baton.cli.commands.bead_cmd._DEFAULT_DB_PATH", db_path):
        old_stdout = _sys.stdout
        _sys.stdout = captured
        try:
            bead_cmd.handler(args)
        except SystemExit as exc:
            exit_code = int(exc.code) if exc.code is not None else 0
        finally:
            _sys.stdout = old_stdout

    return exit_code, captured.getvalue()


def test_handoffs_cli_lists_rows(tmp_path: Path) -> None:
    """`baton beads handoffs --task-id T` prints rows for the task.

    ADR-13b WP-2: the CLI reads handoff rows from ``baton-derived.db`` via
    ``DerivedBeadStore`` (a sibling of ``baton.db``, resolved from the
    (possibly monkeypatched) db path's parent dir) — see
    ``agent_baton/cli/commands/bead_cmd.py::_handle_handoffs``.
    """
    # Synthesize one handoff so there is something to list.
    db_path = tmp_path / "baton.db"
    derived = DerivedBeadStore(tmp_path / "baton-derived.db")
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
        prior, nxt, derived, task_id="t-cli"
    )

    exit_code, out = _run_beads_cli(db_path, ["handoffs", "--task-id", "t-cli"])
    assert exit_code == 0
    assert "Handoff beads for task t-cli" in out
    assert "1.1" in out and "1.2" in out
