"""CLI integration tests for ``baton release`` subcommands (R3.1).

The handler is invoked directly with a monkeypatched ``_DEFAULT_DB_PATH``
so tests are fully isolated from any real project database.

Coverage:
- create: emits a Created line; persists row.
- create then list: shows the new release.
- list --status filters.
- list with empty DB prints informational message.
- show: prints metadata + tagged plans.
- show: unknown release exits non-zero.
- tag / untag: affect plans.release_id; invalid plan exits non-zero.
- update-status: round-trips and validates.
- registration: ``baton release --help`` exits 0 and lists subcommands.
"""
from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.cli.commands import release_cmd
from agent_baton.core.storage.release_store import ReleaseStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_plan(db_path: Path, task_id: str, summary: str = "test plan") -> None:
    """Seed a minimal executions + plans row for tag tests."""
    # Trigger schema creation
    ReleaseStore(db_path)._conn()
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT OR IGNORE INTO executions "
        "(task_id, status, current_phase, current_step_index, started_at, "
        " created_at, updated_at) "
        "VALUES (?, 'running', 0, 0, '2026-01-01T00:00:00Z', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (task_id,),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO plans
            (task_id, task_summary, risk_level, budget_tier,
             execution_mode, git_strategy, shared_context,
             pattern_source, plan_markdown, created_at,
             explicit_knowledge_packs, explicit_knowledge_docs,
             intervention_level, task_type)
        VALUES (?, ?, 'LOW', 'standard', 'phased', 'commit-per-agent',
                '', NULL, '', '2026-01-01T00:00:00Z',
                '[]', '[]', 'low', NULL)
        """,
        (task_id, summary),
    )
    conn.commit()
    conn.close()


def _run_handler(db_path: Path, argv: list[str]) -> tuple[int, str, str]:
    """Parse argv via the release_cmd parser and invoke handler().

    Returns (exit_code, captured_stdout, captured_stderr).
    """
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    release_cmd.register(sub)
    args = parser.parse_args(["release"] + argv)

    captured_out = io.StringIO()
    captured_err = io.StringIO()
    exit_code = 0
    with patch(
        "agent_baton.cli.commands.release_cmd._DEFAULT_DB_PATH", db_path
    ):
        try:
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = captured_out, captured_err
            try:
                release_cmd.handler(args)
            finally:
                sys.stdout, sys.stderr = old_out, old_err
        except SystemExit as exc:
            exit_code = int(exc.code) if exc.code is not None else 0

    return exit_code, captured_out.getvalue(), captured_err.getvalue()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "baton.db"


# ---------------------------------------------------------------------------
# Registration / help
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_release_help_exits_zero(self) -> None:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "agent_baton.cli.main", "release", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        for sub_name in ("create", "list", "show", "tag", "untag"):
            assert sub_name in result.stdout

    def test_release_no_subcommand_prints_usage(self, db_path: Path) -> None:
        code, out, _err = _run_handler(db_path, [])
        assert code == 0
        assert "Usage" in out or "usage" in out.lower()


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


class TestCreate:
    def test_create_minimal(self, db_path: Path) -> None:
        code, out, _err = _run_handler(
            db_path, ["create", "--id", "v2.5.0"]
        )
        assert code == 0
        assert "v2.5.0" in out
        assert "planned" in out

        store = ReleaseStore(db_path)
        rel = store.get("v2.5.0")
        assert rel is not None
        assert rel.status == "planned"

    def test_create_with_name_and_date(self, db_path: Path) -> None:
        code, out, _err = _run_handler(
            db_path,
            [
                "create",
                "--id",
                "2026-Q2",
                "--name",
                "Q2 Stability",
                "--date",
                "2026-06-30",
            ],
        )
        assert code == 0
        assert "2026-Q2" in out
        assert "2026-06-30" in out

        store = ReleaseStore(db_path)
        rel = store.get("2026-Q2")
        assert rel is not None
        assert rel.name == "Q2 Stability"
        assert rel.target_date == "2026-06-30"

    def test_create_invalid_status_rejected(self, db_path: Path) -> None:
        # argparse choices catches this — SystemExit code 2
        with pytest.raises(SystemExit):
            _run_handler(
                db_path, ["create", "--id", "v1.0", "--status", "shipped"]
            )


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    def test_list_empty_db_message(self, db_path: Path) -> None:
        code, out, _err = _run_handler(db_path, ["list"])
        assert code == 0
        assert "No baton.db" in out or "no releases" in out.lower()

    def test_list_shows_releases(self, db_path: Path) -> None:
        _run_handler(db_path, ["create", "--id", "v1.0", "--name", "first"])
        _run_handler(db_path, ["create", "--id", "v2.0", "--name", "second"])
        code, out, _err = _run_handler(db_path, ["list"])
        assert code == 0
        assert "v1.0" in out
        assert "v2.0" in out
        assert "2 release" in out

    def test_list_filter_by_status(self, db_path: Path) -> None:
        _run_handler(
            db_path, ["create", "--id", "v1.0", "--status", "planned"]
        )
        _run_handler(
            db_path, ["create", "--id", "v2.0", "--status", "active"]
        )
        code, out, _err = _run_handler(db_path, ["list", "--status", "active"])
        assert code == 0
        assert "v2.0" in out
        assert "v1.0" not in out


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


class TestShow:
    def test_show_existing_release(self, db_path: Path) -> None:
        _run_handler(
            db_path,
            ["create", "--id", "v1.0", "--name", "alpha", "--notes", "first cut"],
        )
        code, out, _err = _run_handler(db_path, ["show", "v1.0"])
        assert code == 0
        assert "v1.0" in out
        assert "alpha" in out
        assert "first cut" in out
        assert "No plans tagged" in out

    def test_show_missing_release_exits_nonzero(self, db_path: Path) -> None:
        # create the DB so the no-DB path doesn't fire
        _run_handler(db_path, ["create", "--id", "exists"])
        code, _out, err = _run_handler(db_path, ["show", "ghost"])
        assert code == 1
        assert "not found" in err.lower()

    def test_show_lists_tagged_plans(self, db_path: Path) -> None:
        _run_handler(db_path, ["create", "--id", "v1.0", "--name", "alpha"])
        _seed_plan(db_path, "task-A", summary="alpha task")
        _seed_plan(db_path, "task-B", summary="beta task")
        _run_handler(db_path, ["tag", "task-A", "v1.0"])
        _run_handler(db_path, ["tag", "task-B", "v1.0"])

        code, out, _err = _run_handler(db_path, ["show", "v1.0"])
        assert code == 0
        assert "task-A" in out
        assert "task-B" in out
        assert "alpha task" in out


# ---------------------------------------------------------------------------
# tag / untag
# ---------------------------------------------------------------------------


class TestTagUntag:
    def test_tag_existing_plan(self, db_path: Path) -> None:
        _run_handler(db_path, ["create", "--id", "v1.0"])
        _seed_plan(db_path, "task-001")
        code, out, _err = _run_handler(db_path, ["tag", "task-001", "v1.0"])
        assert code == 0
        assert "Tagged" in out

        store = ReleaseStore(db_path)
        plans = store.list_plans_for_release("v1.0")
        assert any(p["task_id"] == "task-001" for p in plans)

    def test_tag_unknown_plan_exits_nonzero(self, db_path: Path) -> None:
        _run_handler(db_path, ["create", "--id", "v1.0"])
        code, _out, err = _run_handler(db_path, ["tag", "ghost", "v1.0"])
        assert code == 1
        assert "not found" in err.lower()

    def test_tag_unknown_release_warns_but_succeeds(
        self, db_path: Path
    ) -> None:
        _seed_plan(db_path, "task-002")
        code, _out, err = _run_handler(
            db_path, ["tag", "task-002", "ghost-release"]
        )
        assert code == 0
        assert "warning" in err.lower() or "does not exist" in err.lower()

    def test_untag_clears_release(self, db_path: Path) -> None:
        _run_handler(db_path, ["create", "--id", "v1.0"])
        _seed_plan(db_path, "task-003")
        _run_handler(db_path, ["tag", "task-003", "v1.0"])
        code, out, _err = _run_handler(db_path, ["untag", "task-003"])
        assert code == 0
        assert "Untagged" in out

        store = ReleaseStore(db_path)
        plans = store.list_plans_for_release("v1.0")
        assert all(p["task_id"] != "task-003" for p in plans)

    def test_untag_unknown_plan_exits_nonzero(self, db_path: Path) -> None:
        _run_handler(db_path, ["create", "--id", "v1.0"])
        code, _out, err = _run_handler(db_path, ["untag", "ghost"])
        assert code == 1
        assert "not found" in err.lower()


# ---------------------------------------------------------------------------
# update-status
# ---------------------------------------------------------------------------


class TestUpdateStatus:
    def test_round_trip(self, db_path: Path) -> None:
        _run_handler(db_path, ["create", "--id", "v1.0"])
        code, out, _err = _run_handler(
            db_path, ["update-status", "v1.0", "active"]
        )
        assert code == 0
        assert "active" in out

        store = ReleaseStore(db_path)
        assert store.get("v1.0").status == "active"

    def test_unknown_release_exits_nonzero(self, db_path: Path) -> None:
        _run_handler(db_path, ["create", "--id", "exists"])
        code, _out, err = _run_handler(
            db_path, ["update-status", "ghost", "active"]
        )
        assert code == 1
        assert "not found" in err.lower()

    def test_invalid_status_rejected_by_argparse(self, db_path: Path) -> None:
        _run_handler(db_path, ["create", "--id", "v1.0"])
        with pytest.raises(SystemExit):
            _run_handler(
                db_path, ["update-status", "v1.0", "shipped"]
            )
