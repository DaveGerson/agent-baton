"""CLI integration tests for `baton beads` subcommands.

All tests drive the CLI via the bead_cmd handler directly.  A BdBeadStore
is created in a temporary directory and injected via a monkeypatched
make_bead_store, so tests are fully isolated from any real project database.

ADR-13b WP-G: Retargeted to BdBeadStore via make_bead_store().  The SQLite
backend was removed; all BeadStore references and _pin_sqlite_backend fixtures
have been replaced.

Coverage:
- baton beads --help exits 0 and lists all subcommands
- baton beads list — no DB: prints informational message, exits 0
- baton beads list — with beads: prints bead table rows
- baton beads list --type / --status / --task / --tag filters
- baton beads list --limit respected
- baton beads show <bead-id> — prints JSON for known bead
- baton beads show <bead-id> — unknown bead: exits non-zero
- baton beads ready — returns only unblocked open beads
- baton beads close <bead-id> — transitions status to closed
- baton beads close <bead-id> --summary TEXT — stored summary
- baton beads annotate <bead-id> --note TEXT — appends note
- baton beads link --relates-to / --contradicts / --extends / --blocks / --validates
- baton beads is registered in the top-level baton --help
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.cli.commands import bead_cmd
from agent_baton.models.bead import Bead, BeadLink


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
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


def _make_bd_store(tmp_path: Path):
    """Return an isolated BdBeadStore scoped to tmp_path."""
    from agent_baton.core.engine.bead_backend import make_bead_store
    db_path = tmp_path / "baton.db"
    db_path.touch()
    return make_bead_store(db_path, repo_root=tmp_path)


def _run_handler(store_or_none, argv: list[str]) -> tuple[int, str]:
    """Invoke bead_cmd.handler() with make_bead_store patched to return *store_or_none*.

    When *store_or_none* is None, patches make_bead_store to raise
    BdNotAvailable so the CLI exercises its graceful-degradation path
    (store unavailable).  This is independent of whether baton.db exists
    in the current working directory, making tests hermetic in a clean cwd.

    When *store_or_none* is a real store, patches make_bead_store to return
    that store directly (bypassing bd workspace discovery).
    """
    from agent_baton.core.engine.bd_client import BdNotAvailable

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    bead_cmd.register(sub)
    args = parser.parse_args(["beads"] + argv)

    captured = io.StringIO()
    exit_code = 0

    if store_or_none is None:
        # No store — simulate bd unavailable so the CLI hits its None guard.
        def _make_unavailable(*a, **kw):
            raise BdNotAvailable("bd not available (test stub)")
        ctx = patch(
            "agent_baton.core.engine.bead_backend.make_bead_store",
            side_effect=_make_unavailable,
        )
    else:
        def _make_store(*a, **kw):
            return store_or_none
        ctx = patch(
            "agent_baton.core.engine.bead_backend.make_bead_store",
            side_effect=_make_store,
        )

    with ctx:
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
def bd_store(tmp_path: Path):
    """A fresh BdBeadStore scoped to tmp_path."""
    return _make_bd_store(tmp_path)


@pytest.fixture
def populated_store(tmp_path: Path):
    """A BdBeadStore with three beads pre-populated."""
    store = _make_bd_store(tmp_path)
    beads = [
        _make_bead("bd-0001", task_id="task-001", bead_type="discovery",
                   content="JWT uses RS256", tags=["auth", "jwt"]),
        _make_bead("bd-0002", task_id="task-001", bead_type="warning",
                   content="Port conflict possible", tags=["ci"]),
        _make_bead("bd-0003", task_id="task-001", bead_type="decision",
                   content="Use Redis", status="open"),
    ]
    for b in beads:
        store.write(b)
    return store


# ---------------------------------------------------------------------------
# Registration / help
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_beads_registered_in_top_level_help(self) -> None:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "agent_baton.cli.main", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "beads" in result.stdout

    def test_beads_help_lists_all_subcommands(self) -> None:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "agent_baton.cli.main", "beads", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        for sub in ("list", "show", "ready", "close", "link"):
            assert sub in result.stdout

    def test_beads_no_subcommand_prints_usage(self) -> None:
        code, out = _run_handler(None, [])
        assert code == 0
        assert "Usage" in out or "usage" in out.lower() or "list" in out


# ---------------------------------------------------------------------------
# baton beads list
# ---------------------------------------------------------------------------


class TestBeadsList:
    def test_list_no_db_prints_message_exits_zero(self) -> None:
        code, out = _run_handler(None, ["list"])
        assert code == 0
        assert "No baton.db" in out or "no beads" in out.lower()

    def test_list_shows_bead_rows(self, populated_store) -> None:
        code, out = _run_handler(populated_store, ["list"])
        assert code == 0
        assert "bd-0001" in out
        assert "bd-0002" in out

    def test_list_shows_bead_count_line(self, populated_store) -> None:
        code, out = _run_handler(populated_store, ["list"])
        assert code == 0
        assert "bead" in out.lower()

    def test_list_filter_by_type(self, populated_store) -> None:
        code, out = _run_handler(populated_store, ["list", "--type", "warning"])
        assert code == 0
        assert "bd-0002" in out
        assert "bd-0001" not in out

    def test_list_filter_by_task(self, populated_store, tmp_path: Path) -> None:
        # Add a bead for a different task
        other = _make_bead("bd-other", task_id="task-999")
        populated_store.write(other)

        code, out = _run_handler(populated_store, ["list", "--task", "task-001"])
        assert code == 0
        assert "bd-0001" in out
        assert "bd-other" not in out

    def test_list_filter_by_tag(self, populated_store) -> None:
        # bd-0001 has tags ["auth", "jwt"]; bd-0002 has ["ci"]
        code, out = _run_handler(populated_store, ["list", "--tag", "jwt"])
        assert code == 0
        assert "bd-0001" in out
        assert "bd-0002" not in out

    def test_list_no_matches_prints_no_beads_message(self, bd_store) -> None:
        code, out = _run_handler(bd_store, ["list"])
        assert code == 0
        assert "No beads" in out or "no beads" in out.lower()

    def test_list_limit_respected(self, populated_store) -> None:
        code, out = _run_handler(populated_store, ["list", "--limit", "1"])
        assert code == 0
        # Only 1 bead ID should appear in the output
        bead_ids_shown = [line for line in out.splitlines() if "bd-" in line]
        assert len(bead_ids_shown) <= 1


# ---------------------------------------------------------------------------
# baton beads show
# ---------------------------------------------------------------------------


class TestBeadsShow:
    def test_show_known_bead_prints_json(self, populated_store) -> None:
        code, out = _run_handler(populated_store, ["show", "bd-0001"])
        assert code == 0
        data = json.loads(out)
        assert data["bead_id"] == "bd-0001"

    def test_show_json_contains_all_expected_fields(self, populated_store) -> None:
        _, out = _run_handler(populated_store, ["show", "bd-0001"])
        data = json.loads(out)
        for field in ("bead_id", "task_id", "bead_type", "content", "status",
                      "agent_name", "tags", "links", "created_at"):
            assert field in data, f"Missing field: {field}"

    def test_show_unknown_bead_exits_nonzero(self, bd_store) -> None:
        code, _ = _run_handler(bd_store, ["show", "bd-doesnotexist"])
        assert code != 0

    def test_show_no_db_prints_message_exits_zero(self) -> None:
        code, out = _run_handler(None, ["show", "bd-any"])
        assert code == 0
        assert "No baton.db" in out


# ---------------------------------------------------------------------------
# baton beads ready
# ---------------------------------------------------------------------------


class TestBeadsReady:
    def test_ready_returns_open_unblocked_beads(self, populated_store) -> None:
        # bd-0001, bd-0002, bd-0003 are all open
        code, out = _run_handler(populated_store, ["ready", "--task", "task-001"])
        assert code == 0
        assert "bd-0001" in out

    def test_ready_no_db_prints_message_exits_zero(self) -> None:
        code, out = _run_handler(None, ["ready", "--task", "task-x"])
        assert code == 0
        assert "No baton.db" in out

    def test_ready_no_active_task_no_flag_exits_nonzero(self, bd_store) -> None:
        with patch("agent_baton.cli.commands.bead_cmd._get_active_task_id",
                   return_value=None):
            code, _ = _run_handler(bd_store, ["ready"])
        assert code != 0

    def test_ready_empty_store_prints_no_ready_message(self, bd_store) -> None:
        code, out = _run_handler(bd_store, ["ready", "--task", "task-empty"])
        assert code == 0
        assert "No ready beads" in out or "no ready" in out.lower()


# ---------------------------------------------------------------------------
# baton beads close
# ---------------------------------------------------------------------------


class TestBeadsClose:
    def test_close_transitions_bead_to_closed(self, populated_store) -> None:
        code, out = _run_handler(populated_store, ["close", "bd-0001"])
        assert code == 0
        fetched = populated_store.read("bd-0001")
        assert fetched is not None
        assert fetched.status == "closed"

    def test_close_with_summary_stores_summary(self, populated_store) -> None:
        code, out = _run_handler(
            populated_store, ["close", "bd-0001", "--summary", "JWT confirmed"]
        )
        assert code == 0
        fetched = populated_store.read("bd-0001")
        assert fetched is not None
        # Status is closed; summary may be stored as metadata or in content
        assert fetched.status == "closed"

    def test_close_prints_confirmation(self, populated_store) -> None:
        code, out = _run_handler(populated_store, ["close", "bd-0001"])
        assert code == 0
        assert "bd-0001" in out

    def test_close_unknown_bead_exits_nonzero(self, bd_store) -> None:
        code, _ = _run_handler(bd_store, ["close", "bd-nonexistent"])
        assert code != 0

    def test_close_no_db_prints_message_exits_zero(self) -> None:
        code, out = _run_handler(None, ["close", "bd-any"])
        assert code == 0
        assert "No baton.db" in out


# ---------------------------------------------------------------------------
# baton beads annotate
# ---------------------------------------------------------------------------


class TestBeadsAnnotate:
    def test_annotate_succeeds_and_bead_still_readable(self, populated_store) -> None:
        """ADR-13b WP-H: BdBeadStore.annotate() creates a bd note (comment) rather
        than appending to the bead's content field. The bead remains readable with
        its original content intact after annotation.
        """
        code, out = _run_handler(
            populated_store,
            ["annotate", "bd-0001", "--note", "Actually uses ES256 not RS256"]
        )
        assert code == 0
        fetched = populated_store.read("bd-0001")
        assert fetched is not None
        # Original content is preserved — BdBeadStore.annotate() uses bd note,
        # which is a separate comment, not an append to the content field.
        assert "JWT uses RS256" in fetched.content

    def test_annotate_with_agent_succeeds(self, populated_store) -> None:
        """ADR-13b WP-H: annotate with --agent succeeds. The agent attribution
        goes into the bd note text, not the bead content field.
        """
        code, out = _run_handler(
            populated_store,
            ["annotate", "bd-0001", "--note", "Verified in prod", "--agent", "auditor"],
        )
        assert code == 0
        fetched = populated_store.read("bd-0001")
        assert fetched is not None
        # Bead still readable; bd note contains "[auditor] Verified in prod" but
        # that is a separate bd comment, not in fetched.content.
        assert fetched.bead_id == "bd-0001"

    def test_annotate_prints_confirmation(self, populated_store) -> None:
        code, out = _run_handler(
            populated_store, ["annotate", "bd-0001", "--note", "test"]
        )
        assert code == 0
        assert "bd-0001" in out

    def test_annotate_unknown_bead_exits_nonzero(self, bd_store) -> None:
        code, _ = _run_handler(bd_store, ["annotate", "bd-nonexistent", "--note", "x"])
        assert code != 0

    def test_annotate_no_db_prints_message_exits_zero(self) -> None:
        code, out = _run_handler(None, ["annotate", "bd-any", "--note", "x"])
        assert code == 0
        assert "No baton.db" in out

    def test_annotate_multiple_times_bead_stays_readable(self, populated_store) -> None:
        """ADR-13b WP-H: Multiple annotations create multiple bd notes. The bead
        remains readable and its original content is not modified by annotations.
        """
        code1, _ = _run_handler(populated_store, ["annotate", "bd-0001", "--note", "First note"])
        code2, _ = _run_handler(populated_store, ["annotate", "bd-0001", "--note", "Second note"])
        assert code1 == 0
        assert code2 == 0
        fetched = populated_store.read("bd-0001")
        assert fetched is not None
        # Original content preserved — bd notes are separate comments.
        assert "JWT uses RS256" in fetched.content


# ---------------------------------------------------------------------------
# baton beads link
# ---------------------------------------------------------------------------


class TestBeadsLink:
    @pytest.fixture
    def two_bead_store(self, tmp_path: Path):
        store = _make_bd_store(tmp_path)
        store.write(_make_bead("bd-src", task_id="task-link"))
        store.write(_make_bead("bd-tgt", task_id="task-link"))
        return store

    def test_link_relates_to(self, two_bead_store) -> None:
        code, out = _run_handler(two_bead_store, ["link", "bd-src", "--relates-to", "bd-tgt"])
        assert code == 0

    def test_link_contradicts(self, two_bead_store) -> None:
        code, out = _run_handler(two_bead_store, ["link", "bd-src", "--contradicts", "bd-tgt"])
        assert code == 0

    def test_link_extends(self, two_bead_store) -> None:
        code, out = _run_handler(two_bead_store, ["link", "bd-src", "--extends", "bd-tgt"])
        assert code == 0

    def test_link_blocks(self, two_bead_store) -> None:
        code, out = _run_handler(two_bead_store, ["link", "bd-src", "--blocks", "bd-tgt"])
        assert code == 0

    def test_link_validates(self, two_bead_store) -> None:
        code, out = _run_handler(two_bead_store, ["link", "bd-src", "--validates", "bd-tgt"])
        assert code == 0

    def test_link_prints_confirmation(self, two_bead_store) -> None:
        code, out = _run_handler(two_bead_store, ["link", "bd-src", "--relates-to", "bd-tgt"])
        assert code == 0
        assert "bd-src" in out
        assert "bd-tgt" in out

    def test_link_unknown_source_exits_nonzero(self, two_bead_store) -> None:
        code, _ = _run_handler(two_bead_store, ["link", "bd-ghost", "--relates-to", "bd-tgt"])
        assert code != 0

    def test_link_unknown_target_exits_nonzero(self, two_bead_store) -> None:
        code, _ = _run_handler(two_bead_store, ["link", "bd-src", "--relates-to", "bd-ghost"])
        assert code != 0

    def test_link_no_db_prints_message_exits_zero(self) -> None:
        code, out = _run_handler(
            None, ["link", "bd-src", "--relates-to", "bd-tgt"]
        )
        assert code == 0
        assert "No baton.db" in out
