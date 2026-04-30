"""CLI integration tests for `baton beads` subcommands.

All tests drive the CLI via `python -m agent_baton.cli.main beads ...`.
The baton.db is created in a temporary directory and pointed at via a
monkeypatched `_DEFAULT_DB_PATH` in `bead_cmd`, so tests are fully
isolated from any real project database.

Coverage:
- baton beads --help exits 0 and lists all subcommands
- baton beads list — no DB: prints informational message, exits 0
- baton beads list — with beads: prints bead table rows
- baton beads list --type / --status / --task / --tag filters
- baton beads list --limit respected
- baton beads show <bead-id> — prints JSON for known bead
- baton beads show <bead-id> — unknown bead: exits non-zero
- baton beads ready — returns only unblocked open beads
- baton beads ready -- bead blocked by open bead not shown
- baton beads ready -- no active task and no --task: exits non-zero
- baton beads close <bead-id> — transitions status to closed
- baton beads close <bead-id> --summary TEXT — stored summary
- baton beads close <unknown-id> — exits non-zero
- baton beads link --relates-to / --contradicts / --extends / --blocks / --validates
- baton beads link unknown source — exits non-zero
- baton beads is registered in the top-level baton --help
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.cli.commands import bead_cmd
from agent_baton.core.engine.bead_store import BeadStore
from agent_baton.models.bead import Bead, BeadLink


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_bead(
    bead_id: str = "bd-a1b2",
    task_id: str = "task-001",
    bead_type: str = "discovery",
    status: str = "open",
    content: str = "The auth module uses JWT RS256.",
    agent_name: str = "backend-engineer--python",
    tags: list[str] | None = None,
    **kwargs,
) -> Bead:
    return Bead(
        bead_id=bead_id,
        task_id=task_id,
        step_id="1.1",
        agent_name=agent_name,
        bead_type=bead_type,
        content=content,
        status=status,
        created_at=_utcnow(),
        tags=tags or [],
        **kwargs,
    )


def _build_db_with_execution(db_path: Path, task_id: str) -> None:
    """Create a minimal baton.db with an executions row so FK constraints pass."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=OFF")
    # Apply the schema so beads table exists
    from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL
    conn.executescript(PROJECT_SCHEMA_DDL)
    # Insert a schema version row
    count = conn.execute("SELECT COUNT(*) FROM _schema_version").fetchone()[0]
    if count == 0:
        from agent_baton.core.storage.schema import SCHEMA_VERSION
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


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A temporary baton.db path (file does not exist until explicitly created)."""
    return tmp_path / "baton.db"


@pytest.fixture
def populated_db(tmp_path: Path) -> tuple[Path, BeadStore, list[Bead]]:
    """A baton.db with three beads pre-populated."""
    path = tmp_path / "baton.db"
    _build_db_with_execution(path, "task-001")

    store = BeadStore(path)
    beads = [
        _make_bead("bd-0001", task_id="task-001", bead_type="discovery",
                   content="JWT uses RS256", tags=["auth", "jwt"]),
        _make_bead("bd-0002", task_id="task-001", bead_type="warning",
                   content="Port conflict possible", tags=["ci"]),
        _make_bead("bd-0003", task_id="task-001", bead_type="decision",
                   content="Use Redis", status="closed",
                   closed_at="2026-01-02T00:00:00Z", summary="Redis chosen"),
    ]
    for b in beads:
        store.write(b)
    return path, store, beads


# ---------------------------------------------------------------------------
# Handler test helper — invokes the handler() function directly
# ---------------------------------------------------------------------------


def _run_handler(db_path: Path, argv: list[str]) -> tuple[int, str]:
    """
    Parse *argv* with the bead_cmd parser and invoke handler().

    Returns (exit_code, captured_stdout).  SystemExit is caught and its code
    returned.  The caller can monkeypatch _get_bead_store to point at *db_path*.
    """
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


# ---------------------------------------------------------------------------
# Registration / help
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_beads_registered_in_top_level_help(self) -> None:
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "agent_baton.cli.main", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "beads" in result.stdout

    def test_beads_help_lists_all_subcommands(self) -> None:
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "agent_baton.cli.main", "beads", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        for sub in ("list", "show", "ready", "close", "link"):
            assert sub in result.stdout

    def test_beads_no_subcommand_prints_usage(self, db_path: Path) -> None:
        code, out = _run_handler(db_path, [])
        assert code == 0
        assert "Usage" in out or "usage" in out.lower() or "list" in out


# ---------------------------------------------------------------------------
# baton beads list
# ---------------------------------------------------------------------------


class TestBeadsList:
    def test_list_no_db_prints_message_exits_zero(self, db_path: Path) -> None:
        code, out = _run_handler(db_path, ["list"])
        assert code == 0
        assert "No baton.db" in out or "no beads" in out.lower()

    def test_list_shows_bead_rows(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, _, _ = populated_db
        code, out = _run_handler(path, ["list"])
        assert code == 0
        assert "bd-0001" in out
        assert "bd-0002" in out

    def test_list_shows_bead_count_line(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, _, _ = populated_db
        code, out = _run_handler(path, ["list"])
        assert code == 0
        assert "bead" in out.lower()

    def test_list_filter_by_type(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, _, _ = populated_db
        code, out = _run_handler(path, ["list", "--type", "warning"])
        assert code == 0
        assert "bd-0002" in out
        assert "bd-0001" not in out

    def test_list_filter_by_status(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, _, _ = populated_db
        code, out = _run_handler(path, ["list", "--status", "closed"])
        assert code == 0
        assert "bd-0003" in out
        assert "bd-0001" not in out

    def test_list_filter_by_task(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, store, _ = populated_db
        # Add a bead for a different task
        _build_db_with_execution(path, "task-999")
        other = _make_bead("bd-other", task_id="task-999")
        store.write(other)

        code, out = _run_handler(path, ["list", "--task", "task-001"])
        assert code == 0
        assert "bd-0001" in out
        assert "bd-other" not in out

    def test_list_filter_by_tag(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, _, _ = populated_db
        # bd-0001 has tags ["auth", "jwt"]; bd-0002 has ["ci"]
        code, out = _run_handler(path, ["list", "--tag", "jwt"])
        assert code == 0
        assert "bd-0001" in out
        assert "bd-0002" not in out

    def test_list_no_matches_prints_no_beads_message(self, db_path: Path) -> None:
        _build_db_with_execution(db_path, "task-empty")
        code, out = _run_handler(db_path, ["list"])
        assert code == 0
        assert "No beads" in out or "no beads" in out.lower()

    def test_list_limit_respected(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, _, _ = populated_db
        code, out = _run_handler(path, ["list", "--limit", "1"])
        assert code == 0
        # Only 1 bead ID should appear in the output (check for just one "bd-")
        bead_ids_shown = [line for line in out.splitlines() if "bd-" in line]
        assert len(bead_ids_shown) <= 1


# ---------------------------------------------------------------------------
# baton beads show
# ---------------------------------------------------------------------------


class TestBeadsShow:
    def test_show_known_bead_prints_json(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, _, _ = populated_db
        code, out = _run_handler(path, ["show", "bd-0001"])
        assert code == 0
        data = json.loads(out)
        assert data["bead_id"] == "bd-0001"

    def test_show_json_contains_all_expected_fields(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, _, _ = populated_db
        _, out = _run_handler(path, ["show", "bd-0001"])
        data = json.loads(out)
        for field in ("bead_id", "task_id", "bead_type", "content", "status",
                      "agent_name", "tags", "links", "created_at"):
            assert field in data, f"Missing field: {field}"

    def test_show_unknown_bead_exits_nonzero(self, db_path: Path) -> None:
        _build_db_with_execution(db_path, "task-x")
        BeadStore(db_path)  # ensure schema applied
        code, _ = _run_handler(db_path, ["show", "bd-doesnotexist"])
        assert code != 0

    def test_show_no_db_prints_message_exits_zero(self, db_path: Path) -> None:
        code, out = _run_handler(db_path, ["show", "bd-any"])
        assert code == 0
        assert "No baton.db" in out


# ---------------------------------------------------------------------------
# baton beads ready
# ---------------------------------------------------------------------------


class TestBeadsReady:
    def test_ready_returns_open_unblocked_beads(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, _, _ = populated_db
        # bd-0001 and bd-0002 are open, bd-0003 is closed
        code, out = _run_handler(path, ["ready", "--task", "task-001"])
        assert code == 0
        assert "bd-0001" in out
        assert "bd-0002" in out

    def test_ready_excludes_closed_beads(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, _, _ = populated_db
        code, out = _run_handler(path, ["ready", "--task", "task-001"])
        assert code == 0
        assert "bd-0003" not in out

    def test_ready_excludes_beads_blocked_by_open_dependency(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, store, _ = populated_db
        blocker = _make_bead("bd-blocker", task_id="task-001", status="open")
        blocked = _make_bead(
            "bd-blocked-open",
            task_id="task-001",
            status="open",
            links=[BeadLink(target_bead_id="bd-blocker", link_type="blocked_by")],
        )
        store.write(blocker)
        store.write(blocked)

        code, out = _run_handler(path, ["ready", "--task", "task-001"])
        assert code == 0
        assert "bd-blocked-open" not in out

    def test_ready_includes_beads_blocked_by_closed_dependency(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, store, _ = populated_db
        blocker = _make_bead("bd-blocker2", task_id="task-001", status="open")
        store.write(blocker)
        store.close("bd-blocker2", "done")
        blocked = _make_bead(
            "bd-now-ready",
            task_id="task-001",
            status="open",
            links=[BeadLink(target_bead_id="bd-blocker2", link_type="blocked_by")],
        )
        store.write(blocked)

        code, out = _run_handler(path, ["ready", "--task", "task-001"])
        assert code == 0
        assert "bd-now-ready" in out

    def test_ready_no_active_task_no_flag_exits_nonzero(self, db_path: Path) -> None:
        _build_db_with_execution(db_path, "task-r")
        BeadStore(db_path)  # ensure schema applied
        # No active_task row inserted, no --task flag
        with patch("agent_baton.cli.commands.bead_cmd._get_active_task_id",
                   return_value=None):
            code, _ = _run_handler(db_path, ["ready"])
        assert code != 0

    def test_ready_no_db_prints_message_exits_zero(self, db_path: Path) -> None:
        code, out = _run_handler(db_path, ["ready", "--task", "task-x"])
        assert code == 0
        assert "No baton.db" in out

    def test_ready_empty_task_prints_no_ready_message(self, db_path: Path) -> None:
        _build_db_with_execution(db_path, "task-empty")
        code, out = _run_handler(db_path, ["ready", "--task", "task-empty"])
        assert code == 0
        assert "No ready beads" in out or "no ready" in out.lower()


# ---------------------------------------------------------------------------
# baton beads close
# ---------------------------------------------------------------------------


class TestBeadsClose:
    def test_close_transitions_bead_to_closed(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, store, _ = populated_db
        code, out = _run_handler(path, ["close", "bd-0001"])
        assert code == 0
        fetched = store.read("bd-0001")
        assert fetched is not None
        assert fetched.status == "closed"

    def test_close_with_summary_stores_summary(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, store, _ = populated_db
        code, out = _run_handler(path, ["close", "bd-0001", "--summary", "JWT confirmed"])
        assert code == 0
        fetched = store.read("bd-0001")
        assert fetched is not None
        assert fetched.summary == "JWT confirmed"

    def test_close_prints_confirmation(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, _, _ = populated_db
        code, out = _run_handler(path, ["close", "bd-0001"])
        assert code == 0
        assert "bd-0001" in out

    def test_close_unknown_bead_exits_nonzero(self, db_path: Path) -> None:
        _build_db_with_execution(db_path, "task-c")
        BeadStore(db_path)  # ensure schema
        code, _ = _run_handler(db_path, ["close", "bd-nonexistent"])
        assert code != 0

    def test_close_no_db_prints_message_exits_zero(self, db_path: Path) -> None:
        code, out = _run_handler(db_path, ["close", "bd-any"])
        assert code == 0
        assert "No baton.db" in out


# ---------------------------------------------------------------------------
# baton beads annotate
# ---------------------------------------------------------------------------


class TestBeadsAnnotate:
    def test_annotate_appends_to_content(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, store, _ = populated_db
        code, out = _run_handler(
            path, ["annotate", "bd-0001", "--note", "Actually uses ES256 not RS256"]
        )
        assert code == 0
        fetched = store.read("bd-0001")
        assert fetched is not None
        assert "JWT uses RS256" in fetched.content
        assert "Actually uses ES256 not RS256" in fetched.content
        assert "--- annotated" in fetched.content

    def test_annotate_with_agent_includes_author(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, store, _ = populated_db
        code, out = _run_handler(
            path,
            ["annotate", "bd-0001", "--note", "Verified in prod", "--agent", "auditor"],
        )
        assert code == 0
        fetched = store.read("bd-0001")
        assert fetched is not None
        assert "(auditor)" in fetched.content

    def test_annotate_prints_confirmation(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, _, _ = populated_db
        code, out = _run_handler(
            path, ["annotate", "bd-0001", "--note", "test"]
        )
        assert code == 0
        assert "bd-0001" in out

    def test_annotate_unknown_bead_exits_nonzero(self, db_path: Path) -> None:
        _build_db_with_execution(db_path, "task-c")
        BeadStore(db_path)
        code, _ = _run_handler(db_path, ["annotate", "bd-nonexistent", "--note", "x"])
        assert code != 0

    def test_annotate_no_db_prints_message_exits_zero(self, db_path: Path) -> None:
        code, out = _run_handler(db_path, ["annotate", "bd-any", "--note", "x"])
        assert code == 0
        assert "No baton.db" in out

    def test_annotate_works_on_closed_bead(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, store, _ = populated_db
        code, out = _run_handler(
            path, ["annotate", "bd-0003", "--note", "Redis confirmed in load test"]
        )
        assert code == 0
        fetched = store.read("bd-0003")
        assert fetched is not None
        assert "Redis confirmed in load test" in fetched.content

    def test_annotate_multiple_notes_append_sequentially(
        self, populated_db: tuple[Path, BeadStore, list[Bead]]
    ) -> None:
        path, store, _ = populated_db
        _run_handler(path, ["annotate", "bd-0001", "--note", "First note"])
        _run_handler(path, ["annotate", "bd-0001", "--note", "Second note"])
        fetched = store.read("bd-0001")
        assert fetched is not None
        assert "First note" in fetched.content
        assert "Second note" in fetched.content
        assert fetched.content.count("--- annotated") == 2


# ---------------------------------------------------------------------------
# baton beads link
# ---------------------------------------------------------------------------


class TestBeadsLink:
    @pytest.fixture
    def two_bead_db(self, tmp_path: Path) -> tuple[Path, BeadStore]:
        path = tmp_path / "two_beads.db"
        _build_db_with_execution(path, "task-link")
        store = BeadStore(path)
        store.write(_make_bead("bd-src", task_id="task-link"))
        store.write(_make_bead("bd-tgt", task_id="task-link"))
        return path, store

    def test_link_relates_to(
        self, two_bead_db: tuple[Path, BeadStore]
    ) -> None:
        path, store = two_bead_db
        code, out = _run_handler(path, ["link", "bd-src", "--relates-to", "bd-tgt"])
        assert code == 0
        fetched = store.read("bd-src")
        assert fetched is not None
        assert any(lnk.link_type == "relates_to" for lnk in fetched.links)

    def test_link_contradicts(
        self, two_bead_db: tuple[Path, BeadStore]
    ) -> None:
        path, store = two_bead_db
        code, out = _run_handler(path, ["link", "bd-src", "--contradicts", "bd-tgt"])
        assert code == 0
        fetched = store.read("bd-src")
        assert fetched is not None
        assert any(lnk.link_type == "contradicts" for lnk in fetched.links)

    def test_link_extends(
        self, two_bead_db: tuple[Path, BeadStore]
    ) -> None:
        path, store = two_bead_db
        code, out = _run_handler(path, ["link", "bd-src", "--extends", "bd-tgt"])
        assert code == 0
        fetched = store.read("bd-src")
        assert fetched is not None
        assert any(lnk.link_type == "extends" for lnk in fetched.links)

    def test_link_blocks(
        self, two_bead_db: tuple[Path, BeadStore]
    ) -> None:
        path, store = two_bead_db
        code, out = _run_handler(path, ["link", "bd-src", "--blocks", "bd-tgt"])
        assert code == 0
        fetched = store.read("bd-src")
        assert fetched is not None
        assert any(lnk.link_type == "blocks" for lnk in fetched.links)

    def test_link_validates(
        self, two_bead_db: tuple[Path, BeadStore]
    ) -> None:
        path, store = two_bead_db
        code, out = _run_handler(path, ["link", "bd-src", "--validates", "bd-tgt"])
        assert code == 0
        fetched = store.read("bd-src")
        assert fetched is not None
        assert any(lnk.link_type == "validates" for lnk in fetched.links)

    def test_link_prints_confirmation(
        self, two_bead_db: tuple[Path, BeadStore]
    ) -> None:
        path, _ = two_bead_db
        code, out = _run_handler(path, ["link", "bd-src", "--relates-to", "bd-tgt"])
        assert code == 0
        assert "bd-src" in out
        assert "bd-tgt" in out

    def test_link_unknown_source_exits_nonzero(
        self, two_bead_db: tuple[Path, BeadStore]
    ) -> None:
        path, _ = two_bead_db
        code, _ = _run_handler(path, ["link", "bd-ghost", "--relates-to", "bd-tgt"])
        assert code != 0

    def test_link_unknown_target_exits_nonzero(
        self, two_bead_db: tuple[Path, BeadStore]
    ) -> None:
        path, _ = two_bead_db
        code, _ = _run_handler(path, ["link", "bd-src", "--relates-to", "bd-ghost"])
        assert code != 0

    def test_link_no_db_prints_message_exits_zero(self, db_path: Path) -> None:
        code, out = _run_handler(
            db_path, ["link", "bd-src", "--relates-to", "bd-tgt"]
        )
        assert code == 0
        assert "No baton.db" in out
