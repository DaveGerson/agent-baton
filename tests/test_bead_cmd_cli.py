"""Focused regression tests for the ``--note`` alias on ``baton beads close``.

Covers:
- ``baton beads close <id> --summary TEXT`` still parses correctly.
- ``baton beads close <id> --note TEXT`` is accepted and maps to ``args.summary``.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.cli.commands import bead_cmd
from agent_baton.core.engine.bead_store import BeadStore
from agent_baton.models.bead import Bead


# ---------------------------------------------------------------------------
# Helpers (mirrors test_bead_cli.py style)
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_bead(bead_id: str = "bd-test", task_id: str = "task-001") -> Bead:
    return Bead(
        bead_id=bead_id,
        task_id=task_id,
        step_id="1.1",
        agent_name="test-agent",
        bead_type="discovery",
        content="Test bead content.",
        status="open",
        created_at=_utcnow(),
        tags=[],
    )


def _build_db(db_path: Path, task_id: str) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION

    conn.executescript(PROJECT_SCHEMA_DDL)
    count = conn.execute("SELECT COUNT(*) FROM _schema_version").fetchone()[0]
    if count == 0:
        conn.execute("INSERT INTO _schema_version VALUES (?)", (SCHEMA_VERSION,))
    conn.execute(
        "INSERT OR IGNORE INTO executions "
        "(task_id, status, current_phase, current_step_index, started_at, created_at, updated_at) "
        "VALUES (?, 'running', 0, 0, '2026-01-01T00:00:00Z', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (task_id,),
    )
    conn.commit()
    conn.close()


def _run_handler(db_path: Path, argv: list[str]) -> tuple[int, str]:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    bead_cmd.register(sub)
    args = parser.parse_args(["beads"] + argv)

    import io
    import sys

    captured = io.StringIO()
    exit_code = 0
    with patch("agent_baton.cli.commands.bead_cmd._DEFAULT_DB_PATH", db_path):
        try:
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                bead_cmd.handler(args)
            finally:
                sys.stdout = old_stdout
        except SystemExit as exc:
            exit_code = int(exc.code) if exc.code is not None else 0

    return exit_code, captured.getvalue()


@pytest.fixture
def bead_db(tmp_path: Path) -> tuple[Path, BeadStore]:
    path = tmp_path / "baton.db"
    _build_db(path, "task-001")
    store = BeadStore(path)
    store.write(_make_bead("bd-alias-test", task_id="task-001"))
    return path, store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBeadsCloseNoteAlias:
    def test_beads_close_accepts_summary_flag(
        self, bead_db: tuple[Path, BeadStore]
    ) -> None:
        """--summary TEXT parses correctly and is stored on the bead."""
        path, store = bead_db
        code, out = _run_handler(
            path, ["close", "bd-alias-test", "--summary", "summary text"]
        )
        assert code == 0
        fetched = store.read("bd-alias-test")
        assert fetched is not None
        assert fetched.summary == "summary text"

    def test_beads_close_accepts_note_alias(
        self, bead_db: tuple[Path, BeadStore]
    ) -> None:
        """--note TEXT is accepted as an alias for --summary and maps to args.summary."""
        # Verify argparse accepts --note and binds it to dest='summary'.
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        bead_cmd.register(sub)
        args = parser.parse_args(["beads", "close", "bd-alias-test", "--note", "note text"])
        assert args.summary == "note text"

        # Also verify the full handler path works end-to-end.
        path, store = bead_db
        code, _ = _run_handler(
            path, ["close", "bd-alias-test", "--note", "note text"]
        )
        assert code == 0
        fetched = store.read("bd-alias-test")
        assert fetched is not None
        assert fetched.summary == "note text"
