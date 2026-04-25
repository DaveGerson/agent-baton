"""End-to-end tests for ``baton users`` (H3.1 / bd-0dea).

Exercises the CLI command directly via its handler -- there is no need
to spawn a subprocess to verify argparse wiring + storage round-trip.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from agent_baton.cli.commands.govern import users as users_cmd
from agent_baton.core.storage.user_store import UserStore
from agent_baton.models.identity import HumanRole


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def central_db(tmp_path: Path) -> Path:
    """A throwaway central.db inside the test tmp_path."""
    return tmp_path / "central.db"


def _make_args(*, action: str, user: str = "", role: str = "", db_path: Path | None = None) -> argparse.Namespace:
    """Build the argparse namespace the handler expects."""
    return argparse.Namespace(
        db_path=str(db_path) if db_path else None,
        users_action=action,
        _action=action,
        user=user,
        role=role,
    )


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    def test_list_empty_users_table(self, central_db: Path, capsys):
        users_cmd.handler(_make_args(action="list", db_path=central_db))
        out = capsys.readouterr().out
        assert "No users registered." in out

    def test_list_renders_assigned_users(self, central_db: Path, capsys):
        store = UserStore(db_path=central_db)
        store.assign_role("alice", HumanRole.TECH_LEAD)
        store.assign_role("bob", HumanRole.JUNIOR)

        users_cmd.handler(_make_args(action="list", db_path=central_db))
        out = capsys.readouterr().out
        assert "alice" in out
        assert "bob" in out
        assert "tech_lead" in out
        assert "junior" in out
        # Header row.
        assert "USER_ID" in out and "HUMAN_ROLE" in out

    def test_list_marks_unassigned(self, central_db: Path, capsys):
        store = UserStore(db_path=central_db)
        store.assign_role("alice", HumanRole.UNASSIGNED)

        users_cmd.handler(_make_args(action="list", db_path=central_db))
        out = capsys.readouterr().out
        assert "(unassigned)" in out


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


class TestShow:
    def test_show_unknown_exits_nonzero(self, central_db: Path, capsys):
        with pytest.raises(SystemExit) as excinfo:
            users_cmd.handler(
                _make_args(action="show", user="ghost", db_path=central_db)
            )
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "ghost" in err
        assert "not found" in err.lower()

    def test_show_prints_full_record(self, central_db: Path, capsys):
        store = UserStore(db_path=central_db)
        store.assign_role("alice", HumanRole.ARCHITECT)

        users_cmd.handler(
            _make_args(action="show", user="alice", db_path=central_db)
        )
        out = capsys.readouterr().out
        # Every field label should appear.
        for label in (
            "user_id", "display_name", "email", "role",
            "human_role", "created_at",
        ):
            assert label in out
        assert "alice" in out
        assert "architect" in out


# ---------------------------------------------------------------------------
# assign-role
# ---------------------------------------------------------------------------


class TestAssignRole:
    def test_assign_role_updates_sql_row(self, central_db: Path, capsys):
        users_cmd.handler(
            _make_args(
                action="assign-role",
                user="alice",
                role="senior",
                db_path=central_db,
            )
        )
        # Read back via a new store instance to confirm persistence.
        store = UserStore(db_path=central_db)
        loaded = store.get("alice")
        assert loaded is not None
        assert loaded.human_role is HumanRole.SENIOR

        out = capsys.readouterr().out
        assert "alice" in out
        assert "senior" in out

    def test_assign_role_accepts_human_friendly_value(
        self, central_db: Path, capsys
    ):
        users_cmd.handler(
            _make_args(
                action="assign-role",
                user="bob",
                role="Tech Lead",
                db_path=central_db,
            )
        )
        store = UserStore(db_path=central_db)
        loaded = store.get("bob")
        assert loaded is not None
        assert loaded.human_role is HumanRole.TECH_LEAD

    def test_assign_role_unknown_role_exits_with_help(
        self, central_db: Path, capsys
    ):
        with pytest.raises(SystemExit) as excinfo:
            users_cmd.handler(
                _make_args(
                    action="assign-role",
                    user="alice",
                    role="ceo",
                    db_path=central_db,
                )
            )
        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        assert "ceo" in err
        # The help message must enumerate the valid options so the
        # operator can self-correct.
        for canonical in ("junior", "senior", "tech_lead", "architect", "qa"):
            assert canonical in err

    def test_assign_role_overwrites_previous(self, central_db: Path):
        users_cmd.handler(
            _make_args(
                action="assign-role",
                user="alice",
                role="junior",
                db_path=central_db,
            )
        )
        users_cmd.handler(
            _make_args(
                action="assign-role",
                user="alice",
                role="engineering_manager",
                db_path=central_db,
            )
        )
        store = UserStore(db_path=central_db)
        loaded = store.get("alice")
        assert loaded is not None
        assert loaded.human_role is HumanRole.ENGINEERING_MANAGER

    def test_assign_role_unassigned_clears(self, central_db: Path):
        users_cmd.handler(
            _make_args(
                action="assign-role",
                user="alice",
                role="senior",
                db_path=central_db,
            )
        )
        users_cmd.handler(
            _make_args(
                action="assign-role",
                user="alice",
                role="unassigned",
                db_path=central_db,
            )
        )
        store = UserStore(db_path=central_db)
        loaded = store.get("alice")
        assert loaded is not None
        assert loaded.human_role is HumanRole.UNASSIGNED


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_returns_subparser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        sp = users_cmd.register(sub)
        # Subparser prog ends with 'users' so the dispatch table picks
        # up the right key.
        assert sp.prog.split()[-1] == "users"

    def test_register_supports_all_actions(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        users_cmd.register(sub)
        # Parsing each subcommand should succeed.
        ns = parser.parse_args(["users", "list"])
        assert ns._action == "list"
        ns = parser.parse_args(["users", "show", "alice"])
        assert ns._action == "show"
        assert ns.user == "alice"
        ns = parser.parse_args(["users", "assign-role", "alice", "tech_lead"])
        assert ns._action == "assign-role"
        assert ns.user == "alice"
        assert ns.role == "tech_lead"
