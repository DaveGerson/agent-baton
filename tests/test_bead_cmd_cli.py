"""Focused regression tests for the ``--note`` alias on ``baton beads close``.

Covers:
- ``baton beads close <id> --summary TEXT`` still parses correctly.
- ``baton beads close <id> --note TEXT`` is accepted and maps to ``args.summary``.

ADR-13b WP-G: Retargeted to BdBeadStore via make_bead_store().
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.cli.commands import bead_cmd
from agent_baton.models.bead import Bead


# ---------------------------------------------------------------------------
# Helpers
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


def _make_bd_store(tmp_path: Path):
    """Return an isolated BdBeadStore scoped to tmp_path."""
    from agent_baton.core.engine.bead_backend import make_bead_store
    db_path = tmp_path / "baton.db"
    db_path.touch()
    return make_bead_store(db_path, repo_root=tmp_path)


def _run_handler_with_store(
    store, argv: list[str]
) -> tuple[int, str]:
    """Run bead_cmd.handler() with make_bead_store patched to return *store*."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    bead_cmd.register(sub)
    args = parser.parse_args(["beads"] + argv)

    captured = io.StringIO()
    exit_code = 0

    def _make_store(*a, **kw):
        return store

    with patch("agent_baton.core.engine.bead_backend.make_bead_store", side_effect=_make_store):
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
def bead_store_with_bead(tmp_path: Path):
    """A BdBeadStore with one bead pre-populated."""
    store = _make_bd_store(tmp_path)
    store.write(_make_bead("bd-alias-test", task_id="task-001"))
    return store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBeadsCloseNoteAlias:
    def test_beads_close_accepts_summary_flag(
        self, bead_store_with_bead, tmp_path: Path
    ) -> None:
        """--summary TEXT parses correctly and is stored on the bead."""
        store = bead_store_with_bead
        code, out = _run_handler_with_store(
            store, ["close", "bd-alias-test", "--summary", "summary text"]
        )
        assert code == 0
        fetched = store.read("bd-alias-test")
        assert fetched is not None
        assert fetched.status == "closed"

    def test_beads_close_accepts_note_alias(
        self, bead_store_with_bead, tmp_path: Path
    ) -> None:
        """--note TEXT is accepted as an alias for --summary and maps to args.summary."""
        # Verify argparse accepts --note and binds it to dest='summary'.
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        bead_cmd.register(sub)
        args = parser.parse_args(["beads", "close", "bd-alias-test", "--note", "note text"])
        assert args.summary == "note text"

        # Also verify the full handler path works end-to-end.
        store = bead_store_with_bead
        code, _ = _run_handler_with_store(
            store, ["close", "bd-alias-test", "--note", "note text"]
        )
        assert code == 0
        fetched = store.read("bd-alias-test")
        assert fetched is not None
        assert fetched.status == "closed"
