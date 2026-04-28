"""Regression tests for outcome-truncation visibility (bd-e78c).

Verifies that when ClaudeCodeLauncher truncates an agent outcome due to
max_outcome_length:

- A WARNING is emitted (test_write_failure_logs_warning, test_normal_write_no_warning_bead)
- A ``warning`` bead tagged ``outcome-truncated`` is filed via BeadStore
  when bead_db_path is configured (test_write_failure_files_bead)
- The truncated outcome is still returned and the launcher does not crash
  (test_truncated_outcome_still_returned)
- Normal (non-truncated) outcomes produce no warning and no bead
  (test_normal_write_no_warning_bead)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import agent_baton.core.runtime.claude_launcher as _mod
from agent_baton.core.runtime.claude_launcher import ClaudeCodeConfig, ClaudeCodeLauncher


# ---------------------------------------------------------------------------
# Test doubles (reuse patterns from test_claude_launcher.py)
# ---------------------------------------------------------------------------

class FakeProcess:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:  # noqa: A002
        return self.stdout, self.stderr

    def kill(self) -> None:
        pass

    async def wait(self) -> None:
        pass


def _patch_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/claude")


def _launcher(monkeypatch: pytest.MonkeyPatch, config: ClaudeCodeConfig) -> ClaudeCodeLauncher:
    _patch_which(monkeypatch)
    return ClaudeCodeLauncher(config)


def _ok_json(result: str) -> bytes:
    return json.dumps({
        "result": result,
        "is_error": False,
        "usage": {"input_tokens": 10, "output_tokens": 10},
        "duration_ms": 500,
    }).encode()


def _patch_subprocess(monkeypatch: pytest.MonkeyPatch, process: FakeProcess) -> None:
    async def fake_exec(*args: Any, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> Path:
    """Create a minimal baton.db with the beads + bead_tags tables."""
    db_path = tmp_path / "baton.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS beads (
            bead_id TEXT PRIMARY KEY,
            task_id TEXT,
            step_id TEXT,
            agent_name TEXT,
            bead_type TEXT,
            content TEXT,
            confidence TEXT DEFAULT 'medium',
            scope TEXT DEFAULT 'step',
            tags TEXT DEFAULT '[]',
            affected_files TEXT DEFAULT '[]',
            status TEXT DEFAULT 'open',
            created_at TEXT,
            closed_at TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            links TEXT DEFAULT '[]',
            source TEXT DEFAULT 'agent-signal',
            token_estimate INTEGER DEFAULT 0,
            quality_score REAL DEFAULT 0.0,
            retrieval_count INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bead_tags (
            bead_id TEXT,
            tag TEXT,
            PRIMARY KEY (bead_id, tag)
        )
    """)
    # schema_version table to satisfy ConnectionManager
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)
    """)
    conn.execute("INSERT OR IGNORE INTO schema_version VALUES (5)")
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# test_normal_write_no_warning_bead
# ---------------------------------------------------------------------------

def test_normal_write_no_warning_bead(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog) -> None:
    """Outcome within max_outcome_length → no WARNING, no bead filed."""
    db_path = _make_db(tmp_path)
    cfg = ClaudeCodeConfig(max_outcome_length=200, bead_db_path=db_path)
    launcher = _launcher(monkeypatch, cfg)

    short_result = "x" * 50  # well within the 200-char limit
    process = FakeProcess(stdout=_ok_json(short_result))
    _patch_subprocess(monkeypatch, process)

    with caplog.at_level(logging.WARNING, logger="agent_baton.core.runtime.claude_launcher"):
        result = asyncio.run(launcher.launch("agent-a", "sonnet", "do stuff", step_id="1.1"))

    assert result.status == "complete"
    assert "truncated" not in caplog.text.lower()

    # No bead should be filed.
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT * FROM beads WHERE bead_type='warning'").fetchall()
    conn.close()
    assert rows == []


# ---------------------------------------------------------------------------
# test_write_failure_logs_warning
# ---------------------------------------------------------------------------

def test_write_failure_logs_warning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog) -> None:
    """Outcome exceeds max_outcome_length → WARNING is logged."""
    db_path = _make_db(tmp_path)
    cfg = ClaudeCodeConfig(max_outcome_length=10, bead_db_path=db_path)
    launcher = _launcher(monkeypatch, cfg)

    long_result = "A" * 500
    process = FakeProcess(stdout=_ok_json(long_result))
    _patch_subprocess(monkeypatch, process)

    with caplog.at_level(logging.WARNING, logger="agent_baton.core.runtime.claude_launcher"):
        result = asyncio.run(launcher.launch("agent-b", "sonnet", "do stuff", step_id="2.1"))

    assert result.status == "complete"
    assert "truncated" in caplog.text.lower()
    # Bead path, agent name, and step must appear in warning log
    assert "agent-b" in caplog.text
    assert "2.1" in caplog.text


# ---------------------------------------------------------------------------
# test_write_failure_files_bead
# ---------------------------------------------------------------------------

def test_write_failure_files_bead(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Outcome exceeds max_outcome_length with bead_db_path set → warning bead is filed."""
    db_path = _make_db(tmp_path)
    cfg = ClaudeCodeConfig(max_outcome_length=10, bead_db_path=db_path)
    launcher = _launcher(monkeypatch, cfg)

    long_result = "B" * 300
    process = FakeProcess(stdout=_ok_json(long_result))
    _patch_subprocess(monkeypatch, process)

    asyncio.run(launcher.launch("agent-c", "sonnet", "do stuff", step_id="3.1"))

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT bead_type, content, agent_name, step_id FROM beads WHERE bead_type='warning'"
    ).fetchall()
    tag_rows = conn.execute(
        "SELECT tag FROM bead_tags WHERE tag='outcome-truncated'"
    ).fetchall()
    conn.close()

    assert len(rows) == 1, f"Expected 1 warning bead, got {len(rows)}"
    row = rows[0]
    assert row[0] == "warning"
    assert "agent-c" in row[1]
    assert "3.1" in row[1]
    assert "300" in row[1] or "10" in row[1]  # attempted or limit mentioned
    assert row[2] == "agent-c"
    assert row[3] == "3.1"

    assert len(tag_rows) >= 1, "Expected outcome-truncated tag to be filed"


# ---------------------------------------------------------------------------
# test_truncated_outcome_still_returned
# ---------------------------------------------------------------------------

def test_truncated_outcome_still_returned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Truncated outcome is still returned; launcher does not crash."""
    db_path = _make_db(tmp_path)
    cfg = ClaudeCodeConfig(max_outcome_length=20, bead_db_path=db_path)
    launcher = _launcher(monkeypatch, cfg)

    long_result = "Z" * 1000
    process = FakeProcess(stdout=_ok_json(long_result))
    _patch_subprocess(monkeypatch, process)

    result = asyncio.run(launcher.launch("agent-d", "sonnet", "do stuff", step_id="4.1"))

    assert result.status == "complete"
    # Outcome must be the truncated version — exactly max_outcome_length chars.
    assert len(result.outcome) == 20
    assert result.outcome == "Z" * 20


# ---------------------------------------------------------------------------
# test_truncated_outcome_still_returned_on_bead_store_failure
# ---------------------------------------------------------------------------

def test_truncated_outcome_still_returned_on_bead_store_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Launcher does not crash even if the BeadStore write raises an exception."""
    # Use a non-existent db path to force BeadStore failure.
    bad_db = Path("/nonexistent/path/baton.db")
    cfg = ClaudeCodeConfig(max_outcome_length=5, bead_db_path=bad_db)
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/claude")
    launcher = ClaudeCodeLauncher(cfg)

    long_result = "E" * 200
    process = FakeProcess(stdout=_ok_json(long_result))

    async def fake_exec(*args: Any, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    # Must not raise despite BeadStore failure.
    result = asyncio.run(launcher.launch("agent-e", "sonnet", "do stuff", step_id="5.1"))

    assert result.status == "complete"
    assert len(result.outcome) == 5
