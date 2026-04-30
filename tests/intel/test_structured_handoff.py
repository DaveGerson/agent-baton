"""Tests for HandoffSynthesizer.synthesize_structured_for_dispatch (Tier 2)."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agent_baton.core.intel.handoff_synthesizer import (
    HANDOFF_MAX_CHARS,
    HANDOFF_STRUCTURED_MAX_CHARS,
    HandoffSynthesizer,
)
from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION
from agent_baton.models.execution import PlanStep


# ---------------------------------------------------------------------------
# Shared fakes / helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeStepResult:
    """Minimal StepResult-shaped fake for synthesizer tests."""

    step_id: str = "1.1"
    agent_name: str = "backend-engineer"
    status: str = "complete"
    outcome: str = ""
    files_changed: list[str] = field(default_factory=list)
    task_id: str = ""


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "baton.db"
    mgr = ConnectionManager(db_path)
    mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    return mgr.get_connection()


def _ensure_execution(conn: sqlite3.Connection, task_id: str) -> None:
    try:
        conn.execute(
            "INSERT OR IGNORE INTO executions (task_id, status, started_at) "
            "VALUES (?, ?, ?)",
            (task_id, "running", "2026-04-30T00:00:00Z"),
        )
        conn.commit()
    except Exception:
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
            "test-agent",
            bead_type,
            content,
            json.dumps(affected_files),
            status,
            "2026-04-30T00:00:00Z",
        ),
    )
    for tag in tags:
        conn.execute(
            "INSERT OR IGNORE INTO bead_tags (bead_id, tag) VALUES (?, ?)",
            (bead_id, tag),
        )
    conn.commit()


def _make_next_step(
    step_id: str = "1.2",
    agent_name: str = "test-engineer",
    allowed_paths: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description="do the thing",
        allowed_paths=allowed_paths or [],
    )


# ---------------------------------------------------------------------------
# Returns None when there is no prior step
# ---------------------------------------------------------------------------


def test_returns_none_when_prior_step_result_is_none(tmp_path: Path) -> None:
    conn = _open_db(tmp_path)
    nxt = _make_next_step()
    result = HandoffSynthesizer().synthesize_structured_for_dispatch(
        None, nxt, conn, task_id="t1"
    )
    assert result is None


def test_returns_none_when_next_step_is_none(tmp_path: Path) -> None:
    prior = FakeStepResult(step_id="1.1", task_id="t1")
    conn = _open_db(tmp_path)
    result = HandoffSynthesizer().synthesize_structured_for_dispatch(
        prior, None, conn, task_id="t1"
    )
    assert result is None


def test_returns_none_when_nothing_useful(tmp_path: Path) -> None:
    """No files, no beads, unknown status → None (mirrors compact behavior)."""
    conn = _open_db(tmp_path)
    prior = FakeStepResult(step_id="1.1", status="", files_changed=[], task_id="t-empty")
    nxt = _make_next_step()
    result = HandoffSynthesizer().synthesize_structured_for_dispatch(
        prior, nxt, conn, task_id="t-empty"
    )
    assert result is None


# ---------------------------------------------------------------------------
# All four sections present when sources exist
# ---------------------------------------------------------------------------


def test_all_four_sections_present(tmp_path: Path) -> None:
    """When files, decisions, warnings, and outcome all exist, all sections appear."""
    conn = _open_db(tmp_path)
    _ensure_execution(conn, "t-full")

    _insert_bead(
        conn,
        bead_id="bd-dec1",
        task_id="t-full",
        step_id="1.1",
        bead_type="decision",
        content="Chose SQLAlchemy 2.0 mapped_column style for type safety",
    )
    _insert_bead(
        conn,
        bead_id="bd-warn1",
        task_id="t-full",
        step_id="0.9",
        bead_type="warning",
        content="Rate limiting not yet enforced on /api/v1/users endpoint",
        affected_files=["agent_baton/api/routes/users.py"],
        status="open",
    )

    prior = FakeStepResult(
        step_id="1.1",
        status="complete",
        files_changed=["agent_baton/api/routes/users.py", "agent_baton/models/user.py"],
        task_id="t-full",
    )
    nxt = PlanStep(
        step_id="1.2",
        agent_name="test-engineer",
        task_description="write tests",
        allowed_paths=["agent_baton/api/routes/users.py"],
    )

    result = HandoffSynthesizer().synthesize_structured_for_dispatch(
        prior, nxt, conn, task_id="t-full"
    )

    assert result is not None
    # Files section
    assert "**Files changed" in result
    assert "agent_baton/api/routes/users.py" in result
    # Key decisions section
    assert "**Key decisions**" in result
    assert "SQLAlchemy" in result
    # Open questions section
    assert "**Open questions**" in result
    assert "Rate limiting" in result
    # Outcome section
    assert "**Outcome of 1.1**" in result
    assert "passed" in result


# ---------------------------------------------------------------------------
# Empty sections are omitted (not rendered as empty bullets)
# ---------------------------------------------------------------------------


def test_sections_omitted_when_source_empty_no_beads(tmp_path: Path) -> None:
    """With no beads at all, key decisions and open questions must not appear."""
    conn = _open_db(tmp_path)
    prior = FakeStepResult(
        step_id="1.1",
        status="complete",
        files_changed=["some/file.py"],
        task_id="t-nobeads",
    )
    nxt = _make_next_step(allowed_paths=["some/file.py"])

    result = HandoffSynthesizer().synthesize_structured_for_dispatch(
        prior, nxt, conn, task_id="t-nobeads"
    )

    assert result is not None
    assert "**Key decisions**" not in result
    assert "**Open questions**" not in result
    # Files and outcome are still present
    assert "**Files changed" in result
    assert "**Outcome of 1.1**" in result


def test_open_questions_omitted_when_no_overlapping_warnings(tmp_path: Path) -> None:
    """Warning beads that don't overlap next step's domain are excluded."""
    conn = _open_db(tmp_path)
    _ensure_execution(conn, "t-nooverlap")
    # Warning touches a completely different domain.
    _insert_bead(
        conn,
        bead_id="bd-w9",
        task_id="t-nooverlap",
        step_id="0.5",
        bead_type="warning",
        content="pmo-ui has an unresolved React hook warning",
        affected_files=["pmo-ui/src/Dashboard.tsx"],
        status="open",
    )
    prior = FakeStepResult(
        step_id="1.1",
        status="complete",
        files_changed=["agent_baton/core/engine/state.py"],
        task_id="t-nooverlap",
    )
    nxt = _make_next_step(allowed_paths=["agent_baton/core/engine/state.py"])

    result = HandoffSynthesizer().synthesize_structured_for_dispatch(
        prior, nxt, conn, task_id="t-nooverlap"
    )

    assert result is not None
    assert "**Open questions**" not in result


def test_key_decisions_omitted_when_no_decision_beads(tmp_path: Path) -> None:
    """Only discovery beads present — no key decisions section."""
    conn = _open_db(tmp_path)
    _ensure_execution(conn, "t-nodec")
    _insert_bead(
        conn,
        bead_id="bd-disc1",
        task_id="t-nodec",
        step_id="1.1",
        bead_type="discovery",
        content="Found a missing index on the beads table",
    )
    prior = FakeStepResult(
        step_id="1.1",
        status="complete",
        files_changed=["agent_baton/core/storage/schema.py"],
        task_id="t-nodec",
    )
    nxt = _make_next_step()

    result = HandoffSynthesizer().synthesize_structured_for_dispatch(
        prior, nxt, conn, task_id="t-nodec"
    )

    assert result is not None
    assert "**Key decisions**" not in result


# ---------------------------------------------------------------------------
# Decision bead tag fallback
# ---------------------------------------------------------------------------


def test_decision_tag_fallback(tmp_path: Path) -> None:
    """Beads tagged 'decision' (but typed 'discovery') appear as key decisions."""
    conn = _open_db(tmp_path)
    _ensure_execution(conn, "t-dectag")
    _insert_bead(
        conn,
        bead_id="bd-tagged-dec",
        task_id="t-dectag",
        step_id="1.1",
        bead_type="discovery",
        content="Decided to skip caching layer in v1",
        tags=["decision"],
    )
    prior = FakeStepResult(
        step_id="1.1",
        status="complete",
        files_changed=["agent_baton/core/caching.py"],
        task_id="t-dectag",
    )
    nxt = _make_next_step()

    result = HandoffSynthesizer().synthesize_structured_for_dispatch(
        prior, nxt, conn, task_id="t-dectag"
    )

    assert result is not None
    assert "**Key decisions**" in result
    assert "caching layer" in result


# ---------------------------------------------------------------------------
# 4KB cap is enforced
# ---------------------------------------------------------------------------


def test_4kb_cap_enforced(tmp_path: Path) -> None:
    conn = _open_db(tmp_path)
    _ensure_execution(conn, "t-4kb")
    # Many decision beads with long content to blow the cap.
    for i in range(50):
        _insert_bead(
            conn,
            bead_id=f"bd-dec-{i:03d}",
            task_id="t-4kb",
            step_id="1.1",
            bead_type="decision",
            content=f"Decision {i:03d}: " + "x" * 200,
        )
    # Lots of files.
    big_files = [f"agent_baton/module_{i:04d}/long_filename_here.py" for i in range(100)]
    prior = FakeStepResult(
        step_id="1.1",
        status="complete",
        files_changed=big_files,
        task_id="t-4kb",
    )
    nxt = _make_next_step()

    result = HandoffSynthesizer().synthesize_structured_for_dispatch(
        prior, nxt, conn, task_id="t-4kb"
    )

    assert result is not None
    assert len(result) <= HANDOFF_STRUCTURED_MAX_CHARS, (
        f"Expected <= {HANDOFF_STRUCTURED_MAX_CHARS} chars, got {len(result)}"
    )


# ---------------------------------------------------------------------------
# Compact Tier 1 bead is unchanged
# ---------------------------------------------------------------------------


def test_compact_bead_still_works(tmp_path: Path) -> None:
    """synthesize_for_dispatch (Tier 1) is unaffected by Tier 2 addition."""
    conn = _open_db(tmp_path)
    prior = FakeStepResult(
        step_id="1.1",
        status="complete",
        files_changed=["agent_baton/api/foo.py"],
        task_id="t-compact",
    )
    nxt = _make_next_step(allowed_paths=["agent_baton/api/foo.py"])

    compact = HandoffSynthesizer().synthesize_for_dispatch(
        prior, nxt, conn, task_id="t-compact"
    )

    assert compact is not None
    assert len(compact) <= HANDOFF_MAX_CHARS
    assert "Files (1)" in compact
    assert "passed" in compact


def test_compact_bead_persisted_when_structured_also_called(tmp_path: Path) -> None:
    """Compact bead is written to handoff_beads even when structured block is built."""
    conn = _open_db(tmp_path)
    prior = FakeStepResult(
        step_id="1.1",
        status="complete",
        files_changed=["agent_baton/api/foo.py"],
        task_id="t-both",
    )
    nxt = _make_next_step(step_id="1.2", allowed_paths=["agent_baton/api/foo.py"])
    hs = HandoffSynthesizer()

    # Call compact (persists) then structured (prompt-only).
    compact = hs.synthesize_for_dispatch(prior, nxt, conn, task_id="t-both")
    structured = hs.synthesize_structured_for_dispatch(prior, nxt, conn, task_id="t-both")

    assert compact is not None
    assert structured is not None

    # Verify row exists in handoff_beads.
    rows = conn.execute(
        "SELECT content FROM handoff_beads WHERE task_id = ?", ("t-both",)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == compact  # compact content, not structured


def test_structured_block_not_persisted_to_handoff_beads(tmp_path: Path) -> None:
    """Structured block must NOT be written to handoff_beads (prompt-only)."""
    conn = _open_db(tmp_path)
    prior = FakeStepResult(
        step_id="1.1",
        status="complete",
        files_changed=["agent_baton/api/foo.py"],
        task_id="t-nopersist",
    )
    nxt = _make_next_step(step_id="1.2", allowed_paths=["agent_baton/api/foo.py"])

    # Only call structured (no compact call).
    HandoffSynthesizer().synthesize_structured_for_dispatch(
        prior, nxt, conn, task_id="t-nopersist"
    )

    rows = conn.execute(
        "SELECT COUNT(*) FROM handoff_beads WHERE task_id = ?", ("t-nopersist",)
    ).fetchone()
    assert rows[0] == 0, "structured block must not be persisted to handoff_beads"


# ---------------------------------------------------------------------------
# Section header format
# ---------------------------------------------------------------------------


def test_structured_block_header(tmp_path: Path) -> None:
    """Block starts with the H3 header defined in the spec."""
    conn = _open_db(tmp_path)
    prior = FakeStepResult(
        step_id="2.1",
        status="failed",
        files_changed=["agent_baton/core/engine/state.py"],
        task_id="t-hdr",
    )
    nxt = _make_next_step()

    result = HandoffSynthesizer().synthesize_structured_for_dispatch(
        prior, nxt, conn, task_id="t-hdr"
    )

    assert result is not None
    assert result.startswith("### Handoff from Prior Step")
    assert "**Outcome of 2.1**" in result
    assert "failed" in result
