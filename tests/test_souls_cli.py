"""Tests for agent_baton.cli.commands.souls_cmd — CLI revocation subcommands.

v34 addition (end-user readiness concern #6).

Coverage:
- test_cli_revoke_command_writes_row: baton souls revoke writes to soul_revocations
- baton souls revoke exits non-zero without --reason
- baton souls revoke raises cleanly on double-revoke
- baton souls list-revocations shows tabular output
- baton souls rotate creates successor and revocation row
- baton souls rotate exits non-zero without --reason
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.cli.commands.souls_cmd import handler, register


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parser():
    """Build a minimal argparse parser with the souls subcommand registered."""
    import argparse
    top = argparse.ArgumentParser()
    sub = top.add_subparsers(dest="command")
    register(sub)
    return top


def _run(args_list: list[str], registry) -> tuple[str, str, int]:
    """Run the souls CLI with patched registry; return (stdout, stderr, exit_code)."""
    import io
    from contextlib import redirect_stdout, redirect_stderr

    parser = _make_parser()
    ns = parser.parse_args(args_list)

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    exit_code = 0

    with (
        patch("agent_baton.cli.commands.souls_cmd._get_registry", return_value=registry),
        redirect_stdout(stdout_buf),
        redirect_stderr(stderr_buf),
    ):
        try:
            handler(ns)
        except SystemExit as exc:
            exit_code = int(exc.code) if exc.code is not None else 0

    return stdout_buf.getvalue(), stderr_buf.getvalue(), exit_code


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry(tmp_path: Path):
    from agent_baton.core.engine.soul_registry import SoulRegistry
    return SoulRegistry(
        central_db_path=tmp_path / "central.db",
        souls_dir=tmp_path / "souls",
    )


@pytest.fixture()
def soul(registry):
    return registry.mint("code-reviewer", "auth", project="/test/proj")


# ---------------------------------------------------------------------------
# baton souls revoke
# ---------------------------------------------------------------------------


class TestCliRevoke:
    def test_cli_revoke_command_writes_row(self, registry, soul):
        """test_cli_revoke_command_writes_row: revoke subcommand inserts soul_revocations row."""
        stdout, stderr, code = _run(
            ["souls", "revoke", soul.soul_id, "--reason", "key found on pastebin"],
            registry,
        )
        assert code == 0, f"Expected exit 0, got {code}. stderr={stderr}"

        # Verify the row exists in the DB directly.
        conn = sqlite3.connect(str(registry._db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM soul_revocations WHERE soul_id = ?", (soul.soul_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["reason"] == "key found on pastebin"

    def test_cli_revoke_prints_bead_warning(self, registry, soul):
        stdout, stderr, code = _run(
            ["souls", "revoke", soul.soul_id, "--reason", "test reason"],
            registry,
        )
        assert code == 0
        assert "BEAD_WARNING" in stdout

    def test_cli_revoke_exits_nonzero_without_reason(self, tmp_path: Path):
        """--reason is mandatory; CLI exits 2 (argparse error) when omitted."""
        import argparse
        parser = _make_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["souls", "revoke", "some_soul_id"])
        assert exc_info.value.code != 0

    def test_cli_revoke_exits_nonzero_on_unknown_soul(self, registry):
        stdout, stderr, code = _run(
            ["souls", "revoke", "no_such_soul", "--reason", "test"],
            registry,
        )
        assert code != 0
        assert "ERROR" in stderr or "not found" in stderr.lower()

    def test_cli_revoke_exits_nonzero_on_double_revoke(self, registry, soul):
        # First revoke succeeds.
        _, _, code1 = _run(
            ["souls", "revoke", soul.soul_id, "--reason", "first"],
            registry,
        )
        assert code1 == 0

        # Second revoke should fail.
        stdout2, stderr2, code2 = _run(
            ["souls", "revoke", soul.soul_id, "--reason", "second attempt"],
            registry,
        )
        assert code2 != 0
        assert "already revoked" in stderr2.lower() or "ERROR" in stderr2

    def test_cli_revoke_with_successor_option(self, registry):
        s1 = registry.mint("code-reviewer", "auth")
        s2 = registry.mint("code-reviewer", "auth")
        stdout, stderr, code = _run(
            [
                "souls", "revoke", s1.soul_id,
                "--reason", "rotation",
                "--successor", s2.soul_id,
            ],
            registry,
        )
        assert code == 0
        conn = sqlite3.connect(str(registry._db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT successor_soul_id FROM soul_revocations WHERE soul_id = ?",
            (s1.soul_id,),
        ).fetchone()
        conn.close()
        assert row["successor_soul_id"] == s2.soul_id

    def test_cli_revoke_with_revoked_by(self, registry, soul):
        stdout, stderr, code = _run(
            [
                "souls", "revoke", soul.soul_id,
                "--reason", "ops test",
                "--revoked-by", "ci-pipeline",
            ],
            registry,
        )
        assert code == 0
        conn = sqlite3.connect(str(registry._db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT revoked_by FROM soul_revocations WHERE soul_id = ?",
            (soul.soul_id,),
        ).fetchone()
        conn.close()
        assert row["revoked_by"] == "ci-pipeline"


# ---------------------------------------------------------------------------
# baton souls list-revocations
# ---------------------------------------------------------------------------


class TestCliListRevocations:
    def test_list_revocations_empty(self, registry):
        stdout, stderr, code = _run(["souls", "list-revocations"], registry)
        assert code == 0
        assert "No revocations" in stdout

    def test_list_revocations_shows_revoked_soul(self, registry, soul):
        registry.revoke(soul.soul_id, reason="compromised in prod", revoked_by="sre")
        stdout, stderr, code = _run(["souls", "list-revocations"], registry)
        assert code == 0
        assert soul.soul_id in stdout
        assert "compromised in prod" in stdout

    def test_list_revocations_shows_successor(self, registry):
        s1 = registry.mint("code-reviewer", "auth")
        s2 = registry.mint("code-reviewer", "auth")
        registry.revoke(s1.soul_id, reason="rotation", successor_soul_id=s2.soul_id)
        stdout, _, _ = _run(["souls", "list-revocations"], registry)
        assert s2.soul_id in stdout


# ---------------------------------------------------------------------------
# baton souls rotate
# ---------------------------------------------------------------------------


class TestCliRotate:
    def test_cli_rotate_creates_successor(self, registry, soul):
        stdout, stderr, code = _run(
            ["souls", "rotate", soul.soul_id, "--reason", "scheduled rotation"],
            registry,
        )
        assert code == 0, f"Expected exit 0, got {code}. stderr={stderr}"
        assert "BEAD_WARNING" in stdout or "Rotated soul" in stdout

        # Original is revoked.
        assert registry.is_revoked(soul.soul_id) is True

        # A successor exists.
        revs = registry.list_revocations()
        rev = next((r for r in revs if r.soul_id == soul.soul_id), None)
        assert rev is not None
        assert rev.successor_soul_id is not None
        successor = registry.get(rev.successor_soul_id)
        assert successor is not None
        assert successor.parent_soul_id == soul.soul_id

    def test_cli_rotate_exits_nonzero_without_reason(self, tmp_path: Path):
        import argparse
        parser = _make_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["souls", "rotate", "some_soul_id"])
        assert exc_info.value.code != 0

    def test_cli_rotate_exits_nonzero_on_unknown_soul(self, registry):
        stdout, stderr, code = _run(
            ["souls", "rotate", "no_such_soul", "--reason", "test"],
            registry,
        )
        assert code != 0

    def test_cli_rotate_exits_nonzero_on_already_revoked(self, registry, soul):
        registry.revoke(soul.soul_id, reason="pre-revoked")
        stdout, stderr, code = _run(
            ["souls", "rotate", soul.soul_id, "--reason", "cannot rotate"],
            registry,
        )
        assert code != 0
